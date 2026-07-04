import os
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

SIGNALS = ("ゴールデンクロス", "デッドクロス", "売られすぎ", "買われすぎ", "中立")

# 東証33業種区分。sector はこのいずれかに正規化させる
_SECTORS = (
    "水産・農林業, 鉱業, 建設業, 食料品, 繊維製品, パルプ・紙, 化学, 医薬品, "
    "石油・石炭製品, ゴム製品, ガラス・土石製品, 鉄鋼, 非鉄金属, 金属製品, 機械, "
    "電気機器, 輸送用機器, 精密機器, その他製品, 電気・ガス業, 陸運業, 海運業, "
    "空運業, 倉庫・運輸関連業, 情報・通信業, 卸売業, 小売業, 銀行業, "
    "証券、商品先物取引業, 保険業, その他金融業, 不動産業, サービス業"
)

_SYSTEM_PROMPT = f"""あなたは日本株のスクリーニング条件を抽出するアシスタントです。
ユーザーの自然言語の発言から、該当するフィールドのみを埋めた条件を返してください。
言及されていない項目は null のままにしてください。
「15倍以下」「3%以上」「時価総額100億円以上」のような日本語の単位表現は数値に変換してください。
sector は次の東証33業種区分のいずれかに正規化してください(例:「自動車」→「輸送用機器」):
{_SECTORS}

signal はテクニカルな売買シグナルの言及がある場合のみ、次のいずれかに正規化してください:
- ゴールデンクロス(「上昇トレンド入り」「買いサイン」なども含む)
- デッドクロス(「下降トレンド入り」「売りサイン」なども含む)
- 売られすぎ(「底値圏」「反発期待」なども含む)
- 買われすぎ(「過熱感」「高値圏」なども含む)

ニュース(適時開示)に関する言及がある場合のみ設定してください:
- news_sentiment_min: 「好材料」「ポジティブなニュース」「良いニュースが出ている」等なら 0.3。
  「強い好材料」「大きな好材料」なら 0.6。ネガティブ除外の意図が読めない限り設定しない。
- has_recent_news: 「最近開示があった」「話題の」「ニュースが出ている」等、
  材料の方向を問わず動きのある銘柄を求めている場合に true。"""


class ScreeningConditions(BaseModel):
    sector: Optional[str] = None
    per_max: Optional[float] = None
    per_min: Optional[float] = None
    pbr_max: Optional[float] = None
    pbr_min: Optional[float] = None
    dividend_yield_min: Optional[float] = None
    dividend_yield_max: Optional[float] = None
    roe_min: Optional[float] = None
    market_cap_min_oku_yen: Optional[float] = None
    market_cap_max_oku_yen: Optional[float] = None
    signal: Optional[Literal["ゴールデンクロス", "デッドクロス", "売られすぎ", "買われすぎ"]] = None
    news_sentiment_min: Optional[float] = None
    has_recent_news: Optional[bool] = None


_FIELD_LABELS: dict[str, tuple[str, str]] = {
    "sector": ("業種", "{}"),
    "per_max": ("PER", "{}倍以下"),
    "per_min": ("PER", "{}倍以上"),
    "pbr_max": ("PBR", "{}倍以下"),
    "pbr_min": ("PBR", "{}倍以上"),
    "dividend_yield_min": ("配当利回り", "{}%以上"),
    "dividend_yield_max": ("配当利回り", "{}%以下"),
    "roe_min": ("ROE", "{}%以上"),
    "market_cap_min_oku_yen": ("時価総額", "{}億円以上"),
    "market_cap_max_oku_yen": ("時価総額", "{}億円以下"),
    "signal": ("シグナル", "{}"),
    "news_sentiment_min": ("ニュース感情", "スコア{}以上"),
    "has_recent_news": ("直近の適時開示", "あり"),
}


def parse_screening_conditions(user_text: str) -> ScreeningConditions:
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=user_text,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ScreeningConditions,
        ),
    )
    return ScreeningConditions.model_validate_json(response.text)


def format_conditions(conditions: ScreeningConditions) -> str:
    lines = []
    for field, (label, fmt) in _FIELD_LABELS.items():
        value = getattr(conditions, field)
        if value is None or value is False:
            continue
        lines.append(f"・{label}: {fmt.format(value)}")
    if not lines:
        return ""
    return "\n".join(lines)


_COMMENTARY_PROMPT = """あなたは投資初心者向けに株式の材料を解説するアシスタントです。
以下はスクリーニングでヒットした銘柄のデータです。
- ma25/ma75: 25日/75日移動平均、rsi14: 14日RSI、signal: 自動判定シグナル
- news_sentiment: 直近の適時開示(TDnet)の感情スコア(-1〜1)、news_label: その分類
全体の傾向を3〜4文程度で、専門用語には簡単な補足を添えて平易に解説してください。
テクニカルとニュースの両面に触れられる場合は両方に言及してください。
個別銘柄への断定的な売買推奨は行わず、あくまで「材料」として提示する書き方にしてください。
最後に一言、投資助言ではない旨を添えてください。"""


def generate_commentary(rows: list[dict]) -> str:
    """スクリーニング結果から初心者向けの解説文を1回のGemini呼び出しで生成する。"""
    relevant = [
        {
            "name": row.get("name"),
            "ma25": row.get("ma25"),
            "ma75": row.get("ma75"),
            "rsi14": row.get("rsi14"),
            "signal": row.get("signal"),
            "news_sentiment": row.get("news_sentiment"),
            "news_label": row.get("news_label"),
        }
        for row in rows
        if row.get("signal") or row.get("news_label")
    ]
    if not relevant:
        return ""

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=str(relevant),
        config=types.GenerateContentConfig(system_instruction=_COMMENTARY_PROMPT),
    )
    return response.text.strip()
