"""夜間バッチのスイング候補抽出とシャドーラン(Phase 8)。設計は DESIGN.md 参照。

batch.py の最後から呼ばれ、以下を行う:
1. 過去の候補の答え合わせ(約定・出口の判定を swing_rules.evaluate_after_signal で更新)
2. 地合いフィルタ → 当日のスイング買い候補の抽出(swing_rules.find_setup)
3. 決算接近銘柄の除外(yfinance calendar でのベストエフォート)
4. data/candidates.csv と data/swing_status.json への保存

「候補なし」の日も swing_status.json に理由(地合い悪化/条件該当なし)を残し、
バッチが動いたこと自体が後から分かるようにする。
"""

import json
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from curl_cffi import requests as cffi_requests

from swing_rules import (
    DEFAULT_PARAMS,
    SwingParams,
    evaluate_after_signal,
    find_setup,
    market_regime_ok,
    rank_candidates,
)

CANDIDATES_PATH = Path(__file__).parent / "data" / "candidates.csv"
STATUS_PATH = Path(__file__).parent / "data" / "swing_status.json"
# 実保有などの資産情報。data/ は .gitignore 済みで、ワークフローも
# 特定ファイルのみ add するため、このファイルはコミットされない
PRIVATE_PATH = Path(__file__).parent / "data" / "swing_private.json"

INDEX_TICKER = "^N225"       # 地合い判定に使う指数
MARKET_CAP_MIN_OKU = 300     # 候補のユニバース(バックテストと同一)
MAX_CANDIDATES = 3           # 1日の候補数上限
EARNINGS_EXCLUDE_DAYS = 14   # 決算発表がこの日数以内なら候補から除外(暦日)
SLIPPAGE_PCT = 0.1           # シャドーランの出口に適用(バックテストと同一)

# candidates.csv の列。答え合わせで更新される列は後半
CSV_COLUMNS = [
    "signal_date", "code", "name",
    "entry_limit", "take_profit", "stop_loss", "time_stop_days",
    "volume_ratio", "rsi14", "ma25", "daily_gain_pct", "turnover_oku_yen",
    "earnings_date",
    "status", "entry_date", "entry_price", "exit_date", "exit_price",
    "result", "pnl_pct", "days_held",
]


def _fetch_index_hist() -> pd.DataFrame:
    session = cffi_requests.Session(impersonate="chrome")
    hist = yf.Ticker(INDEX_TICKER, session=session).history(period="6mo", interval="1d")
    hist.index = hist.index.tz_localize(None)
    return hist


def _fetch_earnings_date(code: str) -> date | None:
    """次回の決算発表日(取れなければ None)。候補銘柄のみに使う。"""
    try:
        session = cffi_requests.Session(impersonate="chrome")
        cal = yf.Ticker(f"{code}.T", session=session).calendar
        dates = cal.get("Earnings Date") if cal else None
        return min(dates) if dates else None
    except Exception:
        return None


def _load_candidates() -> pd.DataFrame:
    """全列を文字列として読む(空列がfloat64になると日付等を代入できないため)。
    数値が必要な箇所は使用時に float()/int() で明示変換する。"""
    if CANDIDATES_PATH.exists():
        return pd.read_csv(CANDIDATES_PATH, dtype=str)
    return pd.DataFrame(columns=CSV_COLUMNS)


def _update_open_candidates(df: pd.DataFrame, hists: dict[str, pd.DataFrame]) -> int:
    """未決着の候補を最新の日足で答え合わせし、更新行数を返す。"""
    updated = 0
    open_mask = df["status"].isin(["約定待ち", "保有中"])
    for idx in df[open_mask].index:
        row = df.loc[idx]
        hist = hists.get(row["code"])
        if hist is None:
            continue
        setup = {
            "entry_limit": float(row["entry_limit"]),
            "take_profit": float(row["take_profit"]),
            "stop_loss": float(row["stop_loss"]),
            "time_stop_days": int(row["time_stop_days"]),
        }
        signal_date = pd.Timestamp(row["signal_date"]).date()
        ev = evaluate_after_signal(hist, signal_date, setup, slippage_pct=SLIPPAGE_PCT)
        if ev["status"] == row["status"] and ev["status"] != "保有中":
            continue
        df.loc[idx, "status"] = ev["status"]
        for col in ("entry_date", "entry_price", "exit_date", "exit_price", "result", "pnl_pct", "days_held"):
            if col in ev:
                df.loc[idx, col] = str(ev[col])  # 全列str運用のため文字列化
        updated += 1
    return updated


def _params_with_budget() -> tuple[SwingParams, str, list[str]]:
    """台帳(Supabase)が設定済みなら余力を予算に使う(Phase 10b)。

    (使用パラメータ, 予算の説明文, 注記) を返す。
    台帳未設定・イベント0件・取得失敗時は固定予算にフォールバックする。
    """
    fixed = f"固定{DEFAULT_PARAMS.budget_yen / 10000:,.0f}万円"
    try:
        import ledger

        if not ledger.is_configured():
            return DEFAULT_PARAMS, fixed, []
        events = ledger.fetch_events()
        if not events:
            return DEFAULT_PARAMS, f"{fixed}(台帳が空)", []
        cash = ledger.compute_state(events)["cash"]
        if cash <= 0:
            return (
                replace(DEFAULT_PARAMS, budget_yen=0.0),
                "余力0円(台帳)",
                [f"台帳の余力が{cash:,.0f}円のため新規候補なし"],
            )
        return replace(DEFAULT_PARAMS, budget_yen=float(cash)), f"余力{cash / 10000:,.1f}万円(台帳)", []
    except Exception as exc:  # noqa: BLE001
        print(f"台帳の取得に失敗(固定予算にフォールバック): {exc}", file=sys.stderr)
        return DEFAULT_PARAMS, f"{fixed}(台帳取得失敗)", []


def _find_today_candidates(
    hists: dict[str, pd.DataFrame],
    market_caps: dict[str, float],
    holding_codes: set[str],
    params: SwingParams,
) -> tuple[list[dict], list[str]]:
    """当日のセットアップを全銘柄から探し、(採用候補, 除外メモ) を返す。"""
    setups = []
    for code, hist in hists.items():
        cap = market_caps.get(code)
        if cap is None or pd.isna(cap) or cap < MARKET_CAP_MIN_OKU:
            continue
        if code in holding_codes:
            continue  # すでに追跡中の銘柄は重複して出さない
        setup = find_setup(hist, params)
        if setup:
            setup["code"] = code
            setups.append(setup)

    # 出来高倍率順に見て、決算接近を除外しながら最大数まで採用
    notes = []
    picked = []
    for setup in rank_candidates(setups, max_count=len(setups)):
        if len(picked) >= MAX_CANDIDATES:
            break
        earnings = _fetch_earnings_date(setup["code"])
        if earnings and (earnings - date.today()).days <= EARNINGS_EXCLUDE_DAYS:
            notes.append(f"{setup['code']}: 決算接近({earnings})のため除外")
            continue
        setup["earnings_date"] = earnings  # None なら「決算日不明」としてカードで警告
        picked.append(setup)
    return picked, notes


def _check_real_holdings(hists: dict[str, pd.DataFrame]) -> list[dict]:
    """台帳の実保有に対する毎晩の出口判定(Phase 10c)。

    実際の建値(平均取得単価)を基準に、利確/損切りライン・時間切れ(登録日から
    20営業日)・決算接近を判定し、警告付きの保有リストを返す。
    台帳未設定・保有なし・取得失敗なら空リスト(他の処理に影響させない)。
    """
    try:
        import ledger

        if not ledger.is_configured():
            return []
        positions = ledger.current_state()["positions"]
    except Exception as exc:  # noqa: BLE001
        print(f"実保有の取得に失敗(スキップ): {exc}", file=sys.stderr)
        return []

    p = DEFAULT_PARAMS
    holdings = []
    for code, pos in positions.items():
        avg = float(pos["avg_price"])
        tp = avg * (1 + p.take_profit_pct / 100)
        sl = avg * (1 - p.stop_loss_pct / 100)
        h = {
            "code": code,
            "name": pos["name"],
            "shares": pos["shares"],
            "avg_price": round(avg, 1),
            "take_profit": round(tp, 1),
            "stop_loss": round(sl, 1),
            "alerts": [],
        }
        hist = hists.get(code)
        if hist is not None and len(hist):
            close = float(hist["Close"].iloc[-1])
            h["close"] = round(close, 1)
            h["pnl_pct"] = round((close / avg - 1) * 100, 1)
            if close >= tp:
                h["alerts"].append("利確ラインに到達。翌朝の手仕舞いを検討")
            elif close <= sl:
                h["alerts"].append("損切りラインに到達。翌朝の手仕舞いを検討")
            opened = pos.get("opened_date")
            if opened:
                days_held = int((hist.index.date > pd.Timestamp(opened).date()).sum())
                h["days_held"] = days_held
                remain = p.time_stop_days - days_held
                if remain <= 0:
                    h["alerts"].append(f"時間切れ({p.time_stop_days}営業日経過)。翌朝の手仕舞いを検討")
                elif remain <= 2:
                    h["alerts"].append(f"時間切れまで残り{remain}営業日")
        earnings = _fetch_earnings_date(code)
        if earnings and (earnings - date.today()).days <= EARNINGS_EXCLUDE_DAYS:
            h["alerts"].append(f"決算発表({earnings})が近い。またぎたくなければ事前に手仕舞いを")
        holdings.append(h)
    return holdings


def run_nightly(
    hists: dict[str, pd.DataFrame],
    names: dict[str, str],
    market_caps: dict[str, float],
) -> list[str]:
    """batch.py から呼ばれるエントリポイント。

    当日候補+追跡中(約定待ち/保有中)の銘柄コードを返す。
    batch.py はこの銘柄だけをニュース感情採点の対象にする(Gemini無料枠対策)。
    """
    df = _load_candidates()
    n_updated = _update_open_candidates(df, hists)

    index_hist = _fetch_index_hist()
    data_date = max((h.index[-1].date() for h in hists.values()), default=date.today())
    params, budget_label, budget_notes = _params_with_budget()
    regime_ok = market_regime_ok(index_hist, params)

    real_holdings = _check_real_holdings(hists)

    # swing_status.json はパブリックリポジトリにコミットされるため、
    # 実保有(資産情報)は入れない。保有は swing_private.json 側に書く
    status = {
        "date": str(data_date),
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "regime_ok": bool(regime_ok),
        "budget": budget_label,
        "evaluated": n_updated,
        "candidates": [],
        "notes": list(budget_notes),
    }

    if not regime_ok:
        status["reason"] = "地合い悪化(日経平均がMA25未満)のため候補なし"
    else:
        # シャドーランの追跡中銘柄と、台帳上の実保有銘柄は新規候補から除外
        holding = set(df[df["status"].isin(["約定待ち", "保有中"])]["code"])
        holding |= {h["code"] for h in real_holdings}
        picked, notes = _find_today_candidates(hists, market_caps, holding, params)
        status["notes"].extend(notes)
        if not picked:
            status["reason"] = "条件に該当する銘柄なし"
        else:
            new_rows = []
            for setup in picked:
                code = setup["code"]
                new_rows.append(
                    {
                        "signal_date": str(data_date),
                        "code": code,
                        "name": names.get(code, ""),
                        "entry_limit": setup["entry_limit"],
                        "take_profit": setup["take_profit"],
                        "stop_loss": setup["stop_loss"],
                        "time_stop_days": setup["time_stop_days"],
                        "volume_ratio": setup["volume_ratio"],
                        "rsi14": setup["rsi14"],
                        "ma25": setup["ma25"],
                        "daily_gain_pct": setup["daily_gain_pct"],
                        "turnover_oku_yen": setup["turnover_oku_yen"],
                        "earnings_date": str(setup["earnings_date"]) if setup["earnings_date"] else "",
                        "status": "約定待ち",
                    }
                )
                status["candidates"].append({"code": code, "name": names.get(code, ""), **{k: setup[k] for k in ("entry_limit", "take_profit", "stop_loss", "volume_ratio")}})
            # バッチ再実行時の重複防止: 同じシグナル日の既存行は総入れ替え
            # (シグナル当日の行は翌営業日まで動きようがないため安全に消せる)
            df = df[df["signal_date"] != str(data_date)]
            df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)

    df.reindex(columns=CSV_COLUMNS).to_csv(CANDIDATES_PATH, index=False)
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    PRIVATE_PATH.write_text(
        json.dumps({"holdings": real_holdings}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    print(
        f"スイング候補: {len(status['candidates'])}件"
        + (f"(理由: {status.get('reason')})" if status.get("reason") else "")
        + f", 答え合わせ更新: {n_updated}件",
        flush=True,
    )

    tracked = set(df[df["status"].isin(["約定待ち", "保有中"])]["code"])
    real = {h["code"] for h in real_holdings}
    return sorted(tracked | real | {c["code"] for c in status["candidates"]})
