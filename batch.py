"""夜間バッチ: 東証プライム全銘柄の指標を取得して data/screener.csv に保存する。

GitHub Actions から毎営業日実行し、生成した CSV をリポジトリにコミットする。
Render は push を検知して自動再デプロイするため、Web アプリは常に前日データを持つ。
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from indicators import compute_technicals

# JPX が毎月更新している東証上場銘柄一覧(Excel)
JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

DATA_PATH = Path(__file__).parent / "data" / "screener.csv"


def fetch_universe() -> pd.DataFrame:
    """JPX の上場銘柄一覧からプライム市場の普通株を抽出する。"""
    df = pd.read_excel(JPX_LIST_URL, dtype=str)
    prime = df[df["市場・商品区分"].str.contains("プライム", na=False)]
    return pd.DataFrame(
        {
            "code": prime["コード"].str.strip(),
            "name": prime["銘柄名"].str.strip(),
            "sector": prime["33業種区分"].str.strip(),
        }
    )


def fetch_metrics(code: str) -> dict | None:
    """yfinance から 1 銘柄分の指標(ファンダメンタル + テクニカル)を取得する。"""
    try:
        ticker = yf.Ticker(f"{code}.T")
        info = ticker.info
    except Exception:
        return None

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return None

    # 配当利回りは dividendYield の単位が yfinance のバージョンで揺れるため、
    # 年間配当額 ÷ 株価から自前で計算する
    dividend_rate = info.get("dividendRate")
    dividend_yield = round(dividend_rate / price * 100, 2) if dividend_rate else None

    roe = info.get("returnOnEquity")
    market_cap = info.get("marketCap")

    try:
        technicals = compute_technicals(ticker)
    except Exception:
        technicals = {"ma25": None, "ma75": None, "rsi14": None, "signal": None}

    return {
        "code": code,
        "price": price,
        "per": info.get("trailingPE"),
        "pbr": info.get("priceToBook"),
        "dividend_yield": dividend_yield,
        "roe": round(roe * 100, 2) if roe is not None else None,
        "market_cap_oku_yen": round(market_cap / 1e8, 1) if market_cap else None,
        **technicals,
    }


def fetch_all(codes: list[str], workers: int) -> tuple[list[dict], list[str]]:
    """並列で指標を取得し、(成功データ, 失敗銘柄コード) を返す。"""
    start = time.time()
    rows: list[dict] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_metrics, code): code for code in codes}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                rows.append(result)
            else:
                failed.append(futures[future])
            if i % 100 == 0:
                print(f"{i}/{len(codes)} 件処理 ({time.time() - start:.0f}秒)", flush=True)
    return rows, failed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="取得銘柄数の上限(動作確認用)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    universe = fetch_universe()
    if args.limit:
        universe = universe.head(args.limit)
    print(f"対象: {len(universe)} 銘柄", flush=True)

    start = time.time()
    rows, failed = fetch_all(list(universe["code"]), args.workers)

    # Yahoo のレート制限(401)で落ちた分は、間隔を空けて低並列で取り直す
    for attempt in range(1, args.retries + 1):
        if not failed:
            break
        wait = 30 * attempt
        print(f"リトライ {attempt}: 残り {len(failed)} 銘柄({wait}秒待機後)", flush=True)
        time.sleep(wait)
        recovered, failed = fetch_all(failed, workers=2)
        rows.extend(recovered)

    if failed:
        print(f"取得できなかった銘柄: {len(failed)} 件", flush=True)

    if not rows:
        print("1件も取得できませんでした(レート制限の可能性)。既存データを保持して中断", file=sys.stderr)
        sys.exit(1)

    metrics = pd.DataFrame(rows)
    merged = universe.merge(metrics, on="code", how="inner")

    if len(merged) < len(universe) * 0.8:
        # 取得成功が8割を下回るときは Yahoo 側の障害・規制の可能性が高いので、
        # 中途半端なデータで上書きせず異常終了させる
        print(f"取得成功が {len(merged)}/{len(universe)} 件のみのため中断", file=sys.stderr)
        sys.exit(1)

    DATA_PATH.parent.mkdir(exist_ok=True)
    merged.to_csv(DATA_PATH, index=False)
    print(f"保存完了: {DATA_PATH} ({len(merged)} 銘柄, {time.time() - start:.0f}秒)")


if __name__ == "__main__":
    main()
