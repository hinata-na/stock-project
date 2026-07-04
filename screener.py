"""事前計算済みの data/screener.csv をスクリーニング条件でフィルタする。"""

from pathlib import Path

import pandas as pd

from screening import ScreeningConditions

DATA_PATH = Path(__file__).parent / "data" / "screener.csv"

MAX_RESULTS = 10


def _format_market_cap(oku_yen: float) -> str:
    if oku_yen >= 10000:
        return f"{oku_yen / 10000:.1f}兆円"
    return f"{oku_yen:,.0f}億円"


def run_screening(conditions: ScreeningConditions) -> tuple[str, list[dict]]:
    """(表示用テキスト, 上位ヒット銘柄の生データ) を返す。生データは解説文生成に使う。"""
    if not DATA_PATH.exists():
        return "銘柄データが未生成です。夜間バッチの初回実行をお待ちください。", []

    df = pd.read_csv(DATA_PATH, dtype={"code": str})

    if conditions.sector:
        df = df[df["sector"].str.contains(conditions.sector, na=False)]
    if conditions.per_max is not None:
        df = df[(df["per"] > 0) & (df["per"] <= conditions.per_max)]
    if conditions.per_min is not None:
        df = df[df["per"] >= conditions.per_min]
    if conditions.pbr_max is not None:
        df = df[(df["pbr"] > 0) & (df["pbr"] <= conditions.pbr_max)]
    if conditions.pbr_min is not None:
        df = df[df["pbr"] >= conditions.pbr_min]
    if conditions.dividend_yield_min is not None:
        df = df[df["dividend_yield"] >= conditions.dividend_yield_min]
    if conditions.dividend_yield_max is not None:
        df = df[df["dividend_yield"] <= conditions.dividend_yield_max]
    if conditions.roe_min is not None:
        df = df[df["roe"] >= conditions.roe_min]
    if conditions.market_cap_min_oku_yen is not None:
        df = df[df["market_cap_oku_yen"] >= conditions.market_cap_min_oku_yen]
    if conditions.market_cap_max_oku_yen is not None:
        df = df[df["market_cap_oku_yen"] <= conditions.market_cap_max_oku_yen]
    if conditions.signal is not None:
        df = df[df["signal"] == conditions.signal]

    if df.empty:
        return "条件に合致する銘柄はありませんでした。条件を緩めてみてください。", []

    total = len(df)
    df = df.sort_values("market_cap_oku_yen", ascending=False).head(MAX_RESULTS)

    lines = [f"合致: {total}銘柄(時価総額上位{min(total, MAX_RESULTS)}件を表示)"]
    for i, row in enumerate(df.itertuples(), 1):
        details = []
        if pd.notna(row.per):
            details.append(f"PER {row.per:.1f}倍")
        if pd.notna(row.pbr):
            details.append(f"PBR {row.pbr:.1f}倍")
        if pd.notna(row.dividend_yield):
            details.append(f"配当 {row.dividend_yield:.1f}%")
        if pd.notna(row.roe):
            details.append(f"ROE {row.roe:.1f}%")
        if pd.notna(row.market_cap_oku_yen):
            details.append(_format_market_cap(row.market_cap_oku_yen))
        if pd.notna(row.rsi14):
            details.append(f"RSI {row.rsi14:.0f}")
        if pd.notna(row.signal):
            details.append(f"シグナル: {row.signal}")
        lines.append(f"{i}. {row.name} ({row.code}) {row.sector}")
        lines.append("   " + " / ".join(details))

    return "\n".join(lines), df.to_dict("records")
