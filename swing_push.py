"""スイング候補カードのLINEプッシュ配信(Phase 9)。設計は DESIGN.md 参照。

夜間バッチ(batch.py)の最後に呼ばれ、swing_batch が生成した
data/swing_status.json と data/candidates.csv から通知文を組み立てて、
ALLOWED_USER_IDS の全員にプッシュする。

- 判定・数値はルールエンジン(swing_rules)が出したものをそのまま表示する。
  Gemini は「やさしい説明」「専門用語での説明」の文章化のみを担当し、
  失敗した場合は数値だけのカードで配信を続行する(配信自体は止めない)
- ローカル確認: python swing_push.py --dry-run (LINEに送らず本文を表示)

LINE Push API は無料プランで月200通まで。1晩1通×ユーザー数なので
個人利用(1〜2人)なら無料枠に収まる。
"""

import json
import os
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

STATUS_PATH = Path(__file__).parent / "data" / "swing_status.json"
CANDIDATES_PATH = Path(__file__).parent / "data" / "candidates.csv"

# シャドーランの決済がこの件数に達するまでは、参考値としてバックテストの数字を出す
MIN_SHADOW_TRADES = 20
BACKTEST_REFERENCE = "勝率44.3%・平均+0.49%(バックテスト2023-11〜2026-07)"


class CardText(BaseModel):
    code: str
    easy: str       # やさしい説明(専門用語なし)
    technical: str  # 専門用語での説明(同じ根拠を指標名で)


def _generate_explanations(candidates: list[dict]) -> dict[str, CardText]:
    """Gemini で二層の説明文を生成する(1晩1回の呼び出し)。失敗時は空dict。"""
    from google import genai
    from google.genai import types

    from screening import GEMINI_MODEL

    prompt = """あなたは投資初心者向けに株の買い候補を説明するアシスタントです。
以下は機械的なルールで抽出された「高値ブレイクアウト」の買い候補です。
各銘柄について、同一の根拠を2通りで説明してください:
- easy: 専門用語を使わず日常の言葉で2文程度。「大勢が注目し始めた瞬間は数日上がりやすい」のような
  値動きの背景がイメージできる説明にする
- technical: 移動平均・RSI・出来高倍率などの用語を使って2文程度

データの見方: breakout_days=何日ぶりの高値更新か, volume_ratio=出来高が20日平均の何倍か,
rsi14=RSI(70超で過熱気味), ma25=25日移動平均, daily_gain_pct=当日の上昇率%,
turnover_oku_yen=1日の平均売買代金(億円)。
easy と technical は必ず同じ事実(高値更新と出来高急増)に言及してください。
出力はプレーンテキスト(マークダウン記法禁止)。"""

    payload = [
        {k: c[k] for k in ("code", "name", "breakout_days", "volume_ratio", "rsi14", "ma25", "daily_gain_pct", "turnover_oku_yen") if k in c}
        for c in candidates
    ]
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=str(payload),
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                response_mime_type="application/json",
                response_schema=list[CardText],
            ),
        )
        cards = [CardText.model_validate(c) for c in json.loads(response.text)]
        return {c.code: c for c in cards}
    except Exception:
        return {}


def _yen(value) -> str:
    v = float(value)
    return f"{v:,.1f}円" if v % 1 else f"{v:,.0f}円"


def _shadow_stats() -> str:
    """シャドーランの実績行。件数が少ないうちはバックテスト参考値を添える。"""
    if not CANDIDATES_PATH.exists():
        return f"実績: 蓄積中(参考: {BACKTEST_REFERENCE})"
    df = pd.read_csv(CANDIDATES_PATH, dtype=str)
    closed = df[df["status"] == "決済済み"].copy()
    if len(closed) < MIN_SHADOW_TRADES:
        return f"実績: 蓄積中({len(closed)}件決済)/ 参考: {BACKTEST_REFERENCE}"
    pnl = closed["pnl_pct"].astype(float)
    win_rate = (pnl > 0).mean() * 100
    return f"実績(シャドーラン{len(closed)}件): 勝率{win_rate:.0f}%・平均{pnl.mean():+.2f}%"


def _tracking_lines() -> list[str]:
    """追跡中(約定待ち・保有中)の候補の現状を1行ずつ。"""
    if not CANDIDATES_PATH.exists():
        return []
    df = pd.read_csv(CANDIDATES_PATH, dtype=str)
    lines = []
    for _, row in df[df["status"].isin(["約定待ち", "保有中"])].iterrows():
        if row["status"] == "約定待ち":
            lines.append(f"・{row['name']}({row['code']}): 約定待ち(指値 {_yen(row['entry_limit'])})")
        else:
            held = row.get("days_held", "")
            held_txt = f"保有{int(float(held)) + 1}日目" if held and held == held else "保有中"
            lines.append(
                f"・{row['name']}({row['code']}): {held_txt}"
                f"(利確 {_yen(row['take_profit'])} / 損切り {_yen(row['stop_loss'])}"
                f" / 残り{max(0, int(row['time_stop_days']) - int(float(held or 0)))}営業日)"
            )
    return lines


def build_message(status: dict, explanations: dict[str, CardText]) -> str:
    """swing_status.json の内容から通知本文を組み立てる(純粋関数、テスト可能)。"""
    candidates = status.get("candidates", [])
    parts = [f"【スイング買い候補】{status['date']} 大引けデータ"]

    if not candidates:
        parts.append(f"\n本日は候補なし。\n理由: {status.get('reason', '不明')}")
        parts.append("(スクリーニング自体は正常に実行されています)")
    else:
        df = pd.read_csv(CANDIDATES_PATH, dtype=str) if CANDIDATES_PATH.exists() else None
        for c in candidates:
            code = c["code"]
            row = None
            if df is not None:
                m = df[(df["code"] == code) & (df["signal_date"] == status["date"])]
                row = m.iloc[0] if len(m) else None
            parts.append(f"\n■{c['name']}({code})")
            ex = explanations.get(code)
            if ex:
                parts.append(f"{ex.easy}")
                parts.append(f"《専門的には》{ex.technical}")
            parts.append("《注文レシピ》")
            parts.append(f"・買い指値: {_yen(c['entry_limit'])}(これより高くは買わない)")
            parts.append(f"・利確 {_yen(c['take_profit'])} / 損切り {_yen(c['stop_loss'])} をOCOで同時に")
            time_stop = row["time_stop_days"] if row is not None else "8"
            parts.append(f"・{time_stop}営業日たって決着しなければ翌朝手仕舞い")
            if row is not None and isinstance(row.get("earnings_date"), str) and row["earnings_date"]:
                parts.append(f"※次回決算: {row['earnings_date']}")
            else:
                parts.append("※決算日を取得できませんでした。近日の決算予定がないかIR等で確認を")

    tracking = _tracking_lines()
    # 当日の新規候補は「約定待ち」として追跡にも載るため、重複表示を避ける
    new_codes = {c["code"] for c in candidates}
    tracking = [t for t in tracking if not any(f"({code})" in t for code in new_codes)]
    if tracking:
        parts.append("\n【追跡中】")
        parts.extend(tracking)

    parts.append("")
    parts.append(f"地合い: {'OK(日経平均はMA25上)' if status.get('regime_ok') else 'NG(日経平均がMA25未満)'}")
    parts.append(_shadow_stats())
    return "\n".join(parts)


def _push_line(text: str) -> bool:
    """ALLOWED_USER_IDS 全員にプッシュ。設定が無ければ False(スキップ)。"""
    from linebot.v3.messaging import (
        ApiClient,
        Configuration,
        MessagingApi,
        PushMessageRequest,
        TextMessage,
    )

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    user_ids = [u.strip() for u in os.environ.get("ALLOWED_USER_IDS", "").split(",") if u.strip()]
    if not token or not user_ids:
        print("LINE設定(LINE_CHANNEL_ACCESS_TOKEN / ALLOWED_USER_IDS)が無いため配信スキップ")
        return False

    configuration = Configuration(access_token=token)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        for uid in user_ids:
            api.push_message(PushMessageRequest(to=uid, messages=[TextMessage(text=text)]))
    print(f"LINE配信完了: {len(user_ids)}人")
    return True


def deliver(dry_run: bool = False) -> None:
    """batch.py から呼ばれるエントリポイント。"""
    if not STATUS_PATH.exists():
        print("swing_status.json が無いため配信スキップ")
        return
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))

    explanations = {}
    if status.get("candidates"):
        explanations = _generate_explanations(_full_candidates(status))

    text = build_message(status, explanations)
    if dry_run:
        print("----- 配信内容(dry-run) -----")
        print(text)
        return
    _push_line(text)


def _full_candidates(status: dict) -> list[dict]:
    """説明文生成用に、candidates.csv から根拠数値も含めた候補情報を集める。"""
    from swing_rules import DEFAULT_PARAMS

    df = pd.read_csv(CANDIDATES_PATH, dtype=str)
    result = []
    for c in status["candidates"]:
        m = df[(df["code"] == c["code"]) & (df["signal_date"] == status["date"])]
        if len(m):
            row = m.iloc[0].to_dict()
            row["breakout_days"] = DEFAULT_PARAMS.breakout_days
            result.append({**row, **c})
    return result


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="LINEに送らず本文を表示")
    args = parser.parse_args()
    deliver(dry_run=args.dry_run)
