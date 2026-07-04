"""swing_rules.py のルールを過去データで検証するローカル用スクリプト(Phase 7)。

本番バッチでは実行しない。使い方:

  python backtest.py fetch            # 3年分の日足を data/backtest_cache/ に保存(約20分)
  python backtest.py run              # 全期間で検証
  python backtest.py run --start 2023-11-01 --end 2025-06-30   # 調整期間のみ
  python backtest.py run --entry open # 寄り成行エントリーで比較

判定ロジックは swing_rules.find_setup() をそのまま呼ぶ(バックテスト専用の
再実装をしない)ことで、検証した通りのルールが本番で動くことを保証する。

高速化のため「ブレイク+出来高」の粗い前処理フィルタで候補日を絞ってから
find_setup() を呼ぶが、前処理は find_setup の条件の上位集合(緩い側)なので
判定結果は変わらない。

制約(結果の解釈時に注意):
- ユニバースは現在の screener.csv 由来のため、上場廃止銘柄が含まれない
  (生存者バイアスで実際よりやや良い数字が出る)
- 決算またぎ除外フィルタは過去の発表日データが取れないため未適用
  (本番では追加の安全装置として働くぶんには問題ない)
"""

import argparse
import time
from pathlib import Path

import pandas as pd

from swing_rules import (
    DEFAULT_PARAMS,
    SwingParams,
    find_setup,
    market_regime_ok,
    rank_candidates,
)

CACHE_DIR = Path(__file__).parent / "data" / "backtest_cache"
SCREENER_CSV = Path(__file__).parent / "data" / "screener.csv"
INDEX_TICKER = "^N225"

MARKET_CAP_MIN_OKU = 300  # バックテスト対象: 時価総額300億円以上
SLIPPAGE_PCT = 0.1        # 出口とエントリー(成行時)に不利方向へ適用
MAX_PER_DAY = 3           # 1日に取る新規候補の上限
MAX_POSITIONS = 5         # 同時保有の上限
POSITION_WEIGHT = 0.2     # 1銘柄あたり資金の20%


def fetch(delay: float = 0.3) -> None:
    """ユニバース+日経平均の3年分日足をキャッシュする。"""
    import yfinance as yf
    from curl_cffi import requests as cffi_requests

    df = pd.read_csv(SCREENER_CSV, dtype={"code": str})
    codes = list(df[df["market_cap_oku_yen"] >= MARKET_CAP_MIN_OKU]["code"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    targets = [(c, f"{c}.T") for c in codes] + [("N225", INDEX_TICKER)]
    start = time.time()
    ok, ng = 0, 0
    for i, (name, ticker) in enumerate(targets, 1):
        path = CACHE_DIR / f"{name}.csv"
        if path.exists():
            continue
        try:
            session = cffi_requests.Session(impersonate="chrome")
            hist = yf.Ticker(ticker, session=session).history(period="3y", interval="1d")
        except Exception:
            hist = None
        if hist is not None and len(hist) >= 100:
            hist.index = hist.index.tz_localize(None)
            hist[["Open", "High", "Low", "Close", "Volume"]].to_csv(path)
            ok += 1
        else:
            ng += 1
        if i % 100 == 0:
            print(f"{i}/{len(targets)} ({time.time() - start:.0f}秒, 失敗{ng})", flush=True)
        time.sleep(delay)
    print(f"完了: 成功{ok} 失敗{ng} 既存スキップ{len(targets) - ok - ng} ({time.time() - start:.0f}秒)")


def _load_cache() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """キャッシュ直下のCSVのみ読む(results/ 等の出力物は対象外)。"""
    hists = {}
    for path in CACHE_DIR.glob("[0-9A-Z]*.csv"):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if path.stem == "N225":
            index_hist = df
        else:
            hists[path.stem] = df
    return hists, index_hist


def _prefilter_signal_days(hist: pd.DataFrame, params: SwingParams) -> list[int]:
    """find_setup の条件の上位集合(ブレイク+出来高のみ)で候補日を粗く絞る。"""
    close, volume = hist["Close"], hist["Volume"]
    prior_high = close.shift(1).rolling(params.breakout_days).max()
    vol_avg20 = volume.shift(1).rolling(20).mean()
    mask = (close > prior_high) & (volume >= vol_avg20 * params.volume_ratio_min)
    return [i for i in mask.to_numpy().nonzero()[0] if i >= params.breakout_days + 20]


def _regime_days(index_hist: pd.DataFrame, params: SwingParams) -> set:
    """地合いフィルタを通過する日付の集合(シグナル日の判定に使う)。"""
    ok = set()
    for i in range(params.regime_ma_days, len(index_hist)):
        if market_regime_ok(index_hist.iloc[: i + 1], params):
            ok.add(index_hist.index[i].date())
    return ok


def _simulate_exit(
    hist: pd.DataFrame, entry_idx: int, entry_price: float, setup: dict
) -> tuple[int, float, str] | None:
    """エントリー後の値動きから出口を決める。(出口日index, 出口価格, 理由) を返す。

    利確と損切りが同日に両方かかり得る場合は損切りを優先(保守的)。
    データが尽きた場合は None(建玉未決着として集計から除外)。
    """
    tp, sl = setup["take_profit"], setup["stop_loss"]
    deadline = entry_idx + setup["time_stop_days"]
    for j in range(entry_idx, min(deadline + 1, len(hist))):
        o, h, low = hist["Open"].iloc[j], hist["High"].iloc[j], hist["Low"].iloc[j]
        if j > entry_idx and o <= sl:
            return j, o, "損切り(ギャップ)"
        if low <= sl:
            return j, sl, "損切り"
        if j > entry_idx and o >= tp:
            return j, o, "利確(ギャップ)"
        if h >= tp:
            return j, tp, "利確"
        if j == deadline:
            return j, float(hist["Close"].iloc[j]), "時間切れ"
    return None


def simulate(
    hists: dict[str, pd.DataFrame],
    index_hist: pd.DataFrame,
    params: SwingParams,
    start: str | None,
    end: str | None,
    entry_model: str,
) -> list[dict]:
    """パラメータ一式でシミュレーションし、取引リストを返す(表示・保存はしない)。"""
    regime_ok_days = _regime_days(index_hist, params)

    # 1) 全銘柄のシグナル日を洗い出し(find_setup は候補日のみ呼ぶ)
    signals = []  # (date, code, setup)
    for code, hist in hists.items():
        for i in _prefilter_signal_days(hist, params):
            date = hist.index[i].date()
            if start and date < pd.Timestamp(start).date():
                continue
            if end and date > pd.Timestamp(end).date():
                continue
            if date not in regime_ok_days:
                continue
            if i + 1 >= len(hist):
                continue  # 翌営業日がまだない
            setup = find_setup(hist.iloc[: i + 1], params)
            if setup:
                signals.append({"date": date, "code": code, "idx": i, "setup": setup})

    # 2) 日毎に候補を絞り、約定と出口をシミュレート
    by_day: dict = {}
    for s in signals:
        by_day.setdefault(s["date"], []).append(s)

    trades = []
    open_until: dict[str, object] = {}  # code -> 出口日(同一銘柄の重複保有を防ぐ)
    position_exits: list = []           # 保有中ポジションの出口日
    for date in sorted(by_day):
        position_exits = [d for d in position_exits if d >= date]
        picked = rank_candidates([s["setup"] for s in by_day[date]], MAX_PER_DAY)
        day_signals = [s for s in by_day[date] if s["setup"] in picked]
        for s in day_signals:
            if len(position_exits) >= MAX_POSITIONS:
                break
            code, i, setup = s["code"], s["idx"], s["setup"]
            if code in open_until and open_until[code] >= date:
                continue
            hist = hists[code]
            o = float(hist["Open"].iloc[i + 1])
            low = float(hist["Low"].iloc[i + 1])
            limit = setup["entry_limit"]
            if entry_model == "open":
                entry_price = o * (1 + SLIPPAGE_PCT / 100)
            elif o <= limit:
                entry_price = o
            elif low <= limit:
                entry_price = limit
            else:
                trades.append({"date": date, "code": code, "result": "未約定", "pnl_pct": None})
                continue
            exit_info = _simulate_exit(hist, i + 1, entry_price, setup)
            if exit_info is None:
                continue
            j, exit_price, reason = exit_info
            exit_price *= 1 - SLIPPAGE_PCT / 100
            pnl_pct = (exit_price / entry_price - 1) * 100
            exit_date = hist.index[j].date()
            open_until[code] = exit_date
            position_exits.append(exit_date)
            trades.append(
                {
                    "date": date,
                    "code": code,
                    "entry": round(entry_price, 1),
                    "exit": round(exit_price, 1),
                    "days": j - (i + 1),
                    "result": reason,
                    "pnl_pct": round(pnl_pct, 2),
                }
            )
    return trades


def stats(trades: list[dict]) -> dict:
    """取引リストから成績サマリを計算する。"""
    df = pd.DataFrame(trades)
    if df.empty:
        return {"signals": 0}
    filled = df[df["pnl_pct"].notna()] if "pnl_pct" in df else pd.DataFrame()
    result = {"signals": len(df), "unfilled": len(df) - len(filled), "filled": len(filled)}
    if filled.empty:
        return result
    wins = filled[filled["pnl_pct"] > 0]
    gross_win = wins["pnl_pct"].sum()
    gross_loss = -filled[filled["pnl_pct"] <= 0]["pnl_pct"].sum()

    # 資金曲線(1銘柄20%、時系列に約定順で適用)と最大ドローダウン
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for pnl in filled.sort_values("date")["pnl_pct"]:
        equity *= 1 + POSITION_WEIGHT * pnl / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    result.update(
        {
            "win_rate": round(len(wins) / len(filled) * 100, 1),
            "avg_pnl": round(filled["pnl_pct"].mean(), 2),
            "median_pnl": round(filled["pnl_pct"].median(), 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "exits": dict(filled["result"].value_counts()),
            "total_return": round((equity - 1) * 100, 1),
            "max_dd": round(max_dd * 100, 1),
        }
    )
    return result


def run(start: str | None, end: str | None, entry_model: str) -> None:
    hists, index_hist = _load_cache()
    print(f"銘柄数: {len(hists)}, 期間: {start or '最古'} 〜 {end or '最新'}, エントリー: {entry_model}")
    trades = simulate(hists, index_hist, DEFAULT_PARAMS, start, end, entry_model)
    _report(trades)
    out_dir = CACHE_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"trades_{entry_model}_{start or 'all'}_{end or 'all'}.csv"
    pd.DataFrame(trades).to_csv(out, index=False)
    print(f"\n取引明細: {out}")


def _report(trades: list[dict]) -> None:
    s = stats(trades)
    if not s.get("signals"):
        print("シグナルなし")
        return
    print(f"\nシグナル: {s['signals']}件(未約定 {s['unfilled']} = {s['unfilled'] / s['signals'] * 100:.0f}%)")
    if not s.get("filled"):
        return
    print(f"約定: {s['filled']}件, 勝率: {s['win_rate']}%")
    print(f"平均損益: {s['avg_pnl']:+.2f}% / 中央値: {s['median_pnl']:+.2f}%")
    print(f"プロフィットファクター: {s['profit_factor']}")
    print(f"出口内訳: {s['exits']}")
    print(f"総リターン(1銘柄20%複利): {s['total_return']:+.1f}%, 最大DD: {s['max_dd']}%")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("--delay", type=float, default=0.3)
    p_run = sub.add_parser("run")
    p_run.add_argument("--start")
    p_run.add_argument("--end")
    p_run.add_argument("--entry", choices=["limit", "open"], default="limit")
    args = parser.parse_args()

    if args.cmd == "fetch":
        fetch(args.delay)
    else:
        run(args.start, args.end, args.entry)


if __name__ == "__main__":
    main()
