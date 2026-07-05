"""個別銘柄を名指しした「買い時・売り時・様子見」の判断(Phase 6 → Phase 10cで置換)。

かつては生データをGeminiに渡して判定させていたが、以下の理由でルールエンジン
(swing_rules、スイング候補と同一の条件)による決定論的な判定に置き換えた:
- 同じデータでも判定が揺れる/バックテストできない(DESIGN.md「設計思想の核」)
- Gemini無料枠が20リクエスト/日に縮小され、判定用の呼び出し(1回/質問)が惜しい

台帳(ledger)に保有が登録されている銘柄は「出口」の観点
(利確/損切りライン・時間切れ)で判定し、未保有の銘柄は「入口」の観点
(買い候補の条件を満たすか)で判定する。
"""

import re
from pathlib import Path

import pandas as pd
import yfinance as yf
from curl_cffi import requests as cffi_requests

from swing_rules import DEFAULT_PARAMS, SwingParams, check_setup, market_regime_ok

DATA_PATH = Path(__file__).parent / "data" / "screener.csv"

_CODE_PATTERN = re.compile(r"^[0-9][0-9A-Z]{3}$")

_DISCLAIMER = "※機械的なルールによる判定材料の提示であり、投資助言ではありません。"


def _load_universe() -> pd.DataFrame | None:
    if not DATA_PATH.exists():
        return None
    return pd.read_csv(DATA_PATH, dtype={"code": str})


def resolve_company(query: str) -> list[dict]:
    """銘柄名または銘柄コードから候補行(screener.csvの1行分)を返す。

    0件なら空リスト、複数件なら曖昧一致として全候補を返す。
    """
    df = _load_universe()
    if df is None:
        return []

    query = query.strip()
    if _CODE_PATTERN.match(query):
        matches = df[df["code"] == query.upper()]
    else:
        matches = df[df["name"].str.contains(query, na=False)]

    return matches.to_dict("records")


def _fetch_hist(code: str) -> pd.DataFrame | None:
    try:
        session = cffi_requests.Session(impersonate="chrome")
        return yf.Ticker(f"{code}.T", session=session).history(period="6mo", interval="1d")
    except Exception:
        return None


def _fetch_index_hist() -> pd.DataFrame | None:
    try:
        session = cffi_requests.Session(impersonate="chrome")
        return yf.Ticker("^N225", session=session).history(period="6mo", interval="1d")
    except Exception:
        return None


def compose_judgement(
    name: str,
    code: str,
    hist: pd.DataFrame | None,
    index_hist: pd.DataFrame | None,
    position: dict | None = None,
    params: SwingParams = DEFAULT_PARAMS,
) -> str:
    """判定文を組み立てる(純粋関数、ネットワーク・Gemini不使用)。"""
    header = f"■{name}({code})の判断"
    if hist is None or len(hist) < 2:
        return f"{header}\n\n株価データを取得できませんでした。時間をおいてもう一度お試しください。"

    close = float(hist["Close"].iloc[-1])
    lines = [header, ""]

    if position:
        # --- 保有中: 出口の観点で判定 ---
        avg = float(position["avg_price"])
        tp = avg * (1 + params.take_profit_pct / 100)
        sl = avg * (1 - params.stop_loss_pct / 100)
        pnl = (close / avg - 1) * 100

        days_held = None
        if position.get("opened_date"):
            days_held = int((hist.index.date > pd.Timestamp(position["opened_date"]).date()).sum())

        if close >= tp:
            lines.append("判定: 売り時(利確ラインに到達)")
        elif close <= sl:
            lines.append("判定: 売り時(損切りラインに到達)")
        elif days_held is not None and days_held >= params.time_stop_days:
            lines.append(f"判定: 売り時(時間切れ: {params.time_stop_days}営業日が経過)")
            lines.append("短期の勢いを取る戦略なので、動かない株を持ち続けるのは資金の無駄です。")
        else:
            lines.append("判定: 様子見(保有継続)")
        lines.append(f"建値 {avg:,.1f}円 → 現在 {close:,.1f}円({pnl:+.1f}%)")
        lines.append(f"利確ライン {tp:,.1f}円 / 損切りライン {sl:,.1f}円(OCO注文を推奨)")
        if days_held is not None:
            lines.append(f"保有 {days_held}営業日(時間切れまで残り {max(params.time_stop_days - days_held, 0)}営業日)")
    else:
        # --- 未保有: 入口(スイング買いルール)の観点で判定 ---
        setup, checks = check_setup(hist, params)
        regime_ok = market_regime_ok(index_hist, params) if index_hist is not None and len(index_hist) else None

        if setup and regime_ok:
            lines.append("判定: 買い候補(スイングルールの全条件を満たしています)")
            lines.append(f"根拠: {setup['breakout_days']}日ぶり高値を出来高{setup['volume_ratio']}倍で更新、"
                         f"RSI14={setup['rsi14']}、当日{setup['daily_gain_pct']:+.1f}%")
            lines.append("《注文レシピ》")
            lines.append(f"・必要資金: 約{setup['unit_cost_yen'] / 10000:,.1f}万円(100株)")
            lines.append(f"・買い指値 {setup['entry_limit']:,.1f}円(これより高くは買わない)")
            lines.append(f"・利確 {setup['take_profit']:,.1f}円 / 損切り {setup['stop_loss']:,.1f}円 をOCOで")
            lines.append(f"・{setup['time_stop_days']}営業日で決着しなければ手仕舞い")
        elif setup and regime_ok is False:
            lines.append("判定: 様子見(銘柄の条件は満たすが、地合いがNG)")
            lines.append("日経平均が25日移動平均を下回っており、全体が下げやすい局面です。"
                         "雨の日に洗濯物を干さないのと同じで、地合いの回復を待ちます。")
        else:
            lines.append("判定: 様子見(買いルールの条件を満たしていません)")
            failed = [c for c in checks if not c["ok"]]
            if failed:
                lines.append("満たしていない条件:")
                for c in failed:
                    lines.append(f"・{c['label']}({c['detail']})")

    lines.append("")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def judge_timing(query: str, user_id: str = "") -> str:
    """銘柄名/コードを受け取り、買い時・売り時・様子見の判定文を返す。

    user_id は発言者(LINEのuser_id)。保有判定・予算は本人の台帳だけを見る。
    """
    candidates = resolve_company(query)

    if not candidates:
        return f"「{query}」に該当する銘柄が見つかりませんでした。銘柄名か証券コードを確認してください。"
    if len(candidates) > 1:
        names = "、".join(f"{c['name']}({c['code']})" for c in candidates[:5])
        return f"候補が複数見つかりました。銘柄名か証券コードで具体的に指定してください: {names}"

    row = candidates[0]
    code = str(row["code"])
    hist = _fetch_hist(code)
    index_hist = _fetch_index_hist()

    position = None
    try:
        import ledger

        if ledger.is_configured() and user_id:
            position = ledger.current_state(user_id)["positions"].get(code)
    except Exception:
        position = None  # 台帳が読めなくても判定自体は続行する

    # 予算(本人の台帳の余力 or 固定値)は夜間バッチの候補選定と同じロジックを使う
    try:
        from swing_batch import _params_with_budget

        params = _params_with_budget(user_id)[0]
    except Exception:
        params = DEFAULT_PARAMS

    return compose_judgement(row["name"], code, hist, index_hist, position, params)
