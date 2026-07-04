"""個別銘柄を名指しした「買い時・売り時」判断(Phase 6)。

screener.csv(ファンダメンタル・テクニカル・ニュース)に加え、
その場で取得したチャート形状の数値データを合わせて Gemini に判定させる。
"""

import os
import re
from pathlib import Path

import pandas as pd
import yfinance as yf
from curl_cffi import requests as cffi_requests
from google import genai
from google.genai import types

from screening import GEMINI_MODEL

DATA_PATH = Path(__file__).parent / "data" / "screener.csv"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

_CODE_PATTERN = re.compile(r"^[0-9][0-9A-Z]{3}$")


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


def compute_chart_shape(code: str) -> dict:
    """直近の日足からチャート形状を数値化する(単一銘柄のみの都度取得)。"""
    try:
        session = cffi_requests.Session(impersonate="chrome")
        hist = yf.Ticker(f"{code}.T", session=session).history(period="2mo", interval="1d")
    except Exception:
        return {}

    if len(hist) < 20:
        return {}

    close = hist["Close"]
    recent20 = close.tail(20)
    current = close.iloc[-1]
    high20, low20 = recent20.max(), recent20.min()
    # 20日レンジの中で現在値が何%の位置にあるか(0%=安値、100%=高値)
    position_pct = 0.0 if high20 == low20 else (current - low20) / (high20 - low20) * 100

    ma25 = close.rolling(25).mean()
    ma25_slope_pct = (
        (ma25.iloc[-1] - ma25.iloc[-6]) / ma25.iloc[-6] * 100
        if len(ma25.dropna()) >= 6
        else None
    )

    recent5_diff = close.tail(5).diff().dropna()
    up_days = int((recent5_diff > 0).sum())
    down_days = int((recent5_diff < 0).sum())

    return {
        "position_in_20d_range_pct": round(position_pct, 1),
        "ma25_slope_5d_pct": round(ma25_slope_pct, 2) if ma25_slope_pct is not None else None,
        "up_days_last_5": up_days,
        "down_days_last_5": down_days,
    }


def compute_sector_baseline(sector: str) -> dict:
    """同業種のPER/PBR中央値を返す(既存データのみ、新規取得なし)。"""
    df = _load_universe()
    if df is None:
        return {}

    peers = df[df["sector"] == sector]
    if peers.empty:
        return {}

    return {
        "sector_median_per": round(peers["per"][peers["per"] > 0].median(), 1),
        "sector_median_pbr": round(peers["pbr"][peers["pbr"] > 0].median(), 1),
    }


_JUDGE_PROMPT = """あなたは投資初心者向けに、個別銘柄の「買い時・売り時・様子見」を判断するアシスタントです。
以下のデータをもとに、買い時/売り時/様子見のいずれかを判定し、3〜4文程度で理由を説明してください。

データの見方:
- per/pbr と sector_median_per/sector_median_pbr: 業種平均と比べて割安か割高か
- ma25/ma75/rsi14/signal: テクニカル指標(移動平均・RSI・自動判定シグナル)
- position_in_20d_range_pct: 直近20日の値幅の中での現在地(0%に近いほど安値圏、100%に近いほど高値圏)
- ma25_slope_5d_pct: 25日移動平均の直近5日の傾き(プラスなら上向き)
- up_days_last_5/down_days_last_5: 直近5日の値上がり/値下がり日数
- news_sentiment/news_label: 直近の適時開示(TDnet)の感情スコアと分類

判断が割れる材料がある場合は無理に断定せず「様子見」としてよい。
個別銘柄への断定的な売買指示ではなく、あくまで「判断材料の整理」として提示してください。
最後に一言、投資助言ではない旨を添えてください。
出力はLINEに表示されるため、マークダウン記法(#や*など)は使わずプレーンテキストで書いてください。
1行目は「判定: 買い時」「判定: 売り時」「判定: 様子見」のいずれかにしてください。"""


def judge_timing(query: str) -> str:
    """銘柄名/コードを受け取り、買い時・売り時・様子見の判定文を返す。"""
    candidates = resolve_company(query)

    if not candidates:
        return f"「{query}」に該当する銘柄が見つかりませんでした。銘柄名か証券コードを確認してください。"
    if len(candidates) > 1:
        names = "、".join(f"{c['name']}({c['code']})" for c in candidates[:5])
        return f"候補が複数見つかりました。銘柄名か証券コードで具体的に指定してください: {names}"

    row = candidates[0]
    payload = {
        "name": row.get("name"),
        "code": row.get("code"),
        "sector": row.get("sector"),
        "per": row.get("per"),
        "pbr": row.get("pbr"),
        "dividend_yield": row.get("dividend_yield"),
        "roe": row.get("roe"),
        "ma25": row.get("ma25"),
        "ma75": row.get("ma75"),
        "rsi14": row.get("rsi14"),
        "signal": row.get("signal"),
        "news_sentiment": row.get("news_sentiment"),
        "news_label": row.get("news_label"),
        **compute_sector_baseline(row.get("sector", "")),
        **compute_chart_shape(row.get("code", "")),
    }

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=str(payload),
        config=types.GenerateContentConfig(system_instruction=_JUDGE_PROMPT),
    )
    return f"■{row.get('name')}({row.get('code')})の判断\n\n{response.text.strip()}"
