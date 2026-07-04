"""夜間バッチ: 東証全市場(プライム/スタンダード/グロース)の指標を取得して data/screener.csv に保存する。

GitHub Actions から毎営業日実行し、生成した CSV をリポジトリにコミットする。
Render は push を検知して自動再デプロイするため、Web アプリは常に前日データを持つ。
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf
from curl_cffi import requests as cffi_requests

from indicators import compute_technicals

# JPX が毎月更新している東証上場銘柄一覧(Excel)
JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

DATA_PATH = Path(__file__).parent / "data" / "screener.csv"

# 東証の内国普通株のみ対象(外国株式・ETF・REIT・PRO Market等は除外)
_TARGET_MARKETS = ("プライム（内国株式）", "スタンダード（内国株式）", "グロース（内国株式）")


def fetch_universe() -> pd.DataFrame:
    """JPX の上場銘柄一覧からプライム/スタンダード/グロース市場の内国普通株を抽出する。"""
    df = pd.read_excel(JPX_LIST_URL, dtype=str)
    universe = df[df["市場・商品区分"].isin(_TARGET_MARKETS)]
    return pd.DataFrame(
        {
            "code": universe["コード"].str.strip(),
            "name": universe["銘柄名"].str.strip(),
            "sector": universe["33業種区分"].str.strip(),
        }
    )


def fetch_metrics(code: str) -> dict | None:
    """yfinance から 1 銘柄分の指標(ファンダメンタル + テクニカル)を取得する。

    yfinance の既定セッションは全スレッドで共有されており、並列アクセス時に
    Yahoo 側の認証(crumb)が壊れて 401 が連鎖することがあるため、
    ブラウザを偽装した独立セッション(curl_cffi)を銘柄ごとに使う。
    """
    try:
        session = cffi_requests.Session(impersonate="chrome")
        ticker = yf.Ticker(f"{code}.T", session=session)
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


def fetch_all(codes: list[str], delay: float) -> tuple[list[dict], list[str]]:
    """順番に指標を取得し、(成功データ, 失敗銘柄コード) を返す。

    並列実行するとリクエストが短時間に集中し、Yahoo 側のレート制限に
    引っかかって以後のリクエストが全滅する(プロセス内では回復しない)
    ことが分かったため、銘柄ごとに間隔を空けて順番に取得する。
    """
    start = time.time()
    rows: list[dict] = []
    failed: list[str] = []
    for i, code in enumerate(codes, 1):
        result = fetch_metrics(code)
        if result:
            rows.append(result)
        else:
            failed.append(code)
        if i % 100 == 0:
            print(f"{i}/{len(codes)} 件処理 ({time.time() - start:.0f}秒)", flush=True)
        time.sleep(delay)
    return rows, failed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="取得銘柄数の上限(動作確認用)")
    parser.add_argument("--delay", type=float, default=0.8, help="銘柄ごとの待機秒数")
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()

    universe = fetch_universe()
    if args.limit:
        universe = universe.head(args.limit)
    print(f"対象: {len(universe)} 銘柄", flush=True)

    start = time.time()
    rows, failed = fetch_all(list(universe["code"]), args.delay)

    # 稀に発生する一時的な失敗分だけを、待機時間を延ばして拾い直す
    for attempt in range(1, args.retries + 1):
        if not failed:
            break
        wait = 120 * attempt
        print(f"リトライ {attempt}: 残り {len(failed)} 銘柄({wait}秒待機後)", flush=True)
        time.sleep(wait)
        recovered, failed = fetch_all(failed, delay=args.delay * 2)
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
