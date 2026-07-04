import os
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

_SYSTEM_PROMPT = """あなたは日本株のスクリーニング条件を抽出するアシスタントです。
ユーザーの自然言語の発言から、該当するフィールドのみを埋めた条件を返してください。
言及されていない項目は null のままにしてください。
「15倍以下」「3%以上」「時価総額100億円以上」のような日本語の単位表現は数値に変換してください。"""


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
        if value is not None:
            lines.append(f"・{label}: {fmt.format(value)}")
    if not lines:
        return "条件を認識できませんでした。もう少し具体的に教えてください。"
    return "条件を認識しました:\n" + "\n".join(lines) + "\n\n※ 銘柄の絞り込み機能は準備中です(Phase 3)"
