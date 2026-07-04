"""TDnet 適時開示を取得し、Gemini で感情スコア化する(夜間バッチ用)。

株価データ(数値)にニュース(テキスト)を「数値特徴量」として合流させる。
リクエスト時ではなく夜間バッチで実行するため、Render 無料枠の制約は受けない。
"""

import os
from collections import defaultdict
from datetime import date, timedelta

from curl_cffi import requests as cffi_requests
from google import genai
from google.genai import types
from pydantic import BaseModel

from screening import GEMINI_MODEL

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# JPX/東証の TDnet 適時開示を JSON で返す無料ラッパー(yanoshin WebAPI)
TDNET_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{start}-{end}.json"
LOOKBACK_DAYS = 7
MAX_TITLES_PER_CODE = 10  # 1銘柄あたり Gemini に渡すタイトル数の上限
GEMINI_BATCH = 100  # 1回の Gemini 呼び出しで採点する銘柄数(呼び出し回数削減のため大きめ)


def fetch_disclosures(days: int = LOOKBACK_DAYS) -> dict[str, dict]:
    """直近 days 日の適時開示を取得し、JPXコード -> {titles, count, latest} を返す。"""
    end = date.today()
    start = end - timedelta(days=days - 1)
    url = TDNET_URL.format(start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    resp = cffi_requests.get(url, params={"limit": 5000}, timeout=60)
    resp.raise_for_status()
    items = resp.json().get("items", [])

    by_code: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in items:
        t = item.get("Tdnet", {})
        code5 = (t.get("company_code") or "").strip()
        title = (t.get("title") or "").strip()
        pubdate = (t.get("pubdate") or "").strip()
        # company_code は5桁(4桁のJPXコード + 末尾1桁)。先頭4文字で紐付ける
        if len(code5) >= 4 and title:
            by_code[code5[:4]].append((pubdate, title))

    result: dict[str, dict] = {}
    for code, entries in by_code.items():
        entries.sort(reverse=True)  # 新しい順
        result[code] = {
            "titles": [title for _, title in entries[:MAX_TITLES_PER_CODE]],
            "count": len(entries),
            "latest": entries[0][0][:10] if entries else "",
        }
    return result


class _Sentiment(BaseModel):
    code: str
    sentiment: float  # -1.0(強いネガティブ) 〜 1.0(強いポジティブ)
    label: str  # ポジティブ / 中立 / ネガティブ


_SENTIMENT_PROMPT = """あなたは日本株の適時開示(TDnet)のタイトルから、投資家目線での材料の強さを判定するアシスタントです。
各銘柄について、与えられた開示タイトル群を総合し、株価にとってポジティブかネガティブかを
-1.0(強いネガティブ)〜1.0(強いポジティブ)で採点してください。0.0付近は中立です。
label は「ポジティブ」「中立」「ネガティブ」のいずれかにしてください。
判断の目安:
- プラス材料: 上方修正、増配、自社株買い、業務提携、好決算
- マイナス材料: 下方修正、減配、業績悪化、不祥事、訴訟
- 中立: 事務的・定型的な開示(役員異動の届出、書類の訂正など)
入力の code はそのまま返してください。"""


def score_sentiments(disclosures: dict[str, dict]) -> dict[str, dict]:
    """{code: {titles,...}} を Gemini で採点し、code -> {sentiment, label} を返す。"""
    if not disclosures:
        return {}

    client = genai.Client(api_key=GEMINI_API_KEY)
    scores: dict[str, dict] = {}
    codes = list(disclosures)

    for i in range(0, len(codes), GEMINI_BATCH):
        chunk = codes[i : i + GEMINI_BATCH]
        payload = [{"code": c, "titles": disclosures[c]["titles"]} for c in chunk]
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=str(payload),
                config=types.GenerateContentConfig(
                    system_instruction=_SENTIMENT_PROMPT,
                    response_mime_type="application/json",
                    response_schema=list[_Sentiment],
                ),
            )
            for s in resp.parsed or []:
                scores[s.code] = {
                    "sentiment": round(max(-1.0, min(1.0, s.sentiment)), 2),
                    "label": s.label,
                }
        except Exception as exc:  # noqa: BLE001
            # 一部バッチが失敗しても他銘柄の採点は残すが、原因は必ず記録する
            import sys

            print(f"ニュース採点バッチ失敗({len(chunk)}銘柄分をスキップ): {exc}", file=sys.stderr)
            continue

    return scores


def build_news_features(
    codes: set[str] | None = None, days: int = LOOKBACK_DAYS
) -> dict[str, dict]:
    """銘柄コード -> {news_count, news_sentiment, news_label, news_latest} を返す。

    codes を渡すと、その銘柄(=スクリーニング対象のプライム銘柄)だけを
    Gemini で採点する。全市場を採点すると呼び出し回数が無駄に増えるため。
    """
    disclosures = fetch_disclosures(days)
    if codes is not None:
        disclosures = {c: v for c, v in disclosures.items() if c in codes}
    scores = score_sentiments(disclosures)

    features: dict[str, dict] = {}
    for code, info in disclosures.items():
        score = scores.get(code, {})
        features[code] = {
            "news_count": info["count"],
            "news_sentiment": score.get("sentiment"),
            "news_label": score.get("label"),
            "news_latest": info["latest"],
        }
    return features
