"""移動平均・RSIから売買シグナルを判定する。"""

import pandas as pd
import yfinance as yf

MA_SHORT = 25
MA_LONG = 75
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_technicals(ticker: yf.Ticker) -> dict:
    """MA25/MA75/RSI14 と、直近1日で発生したシグナルを返す。

    半年分の日足が無い(新規上場など)場合は全項目 None。
    """
    hist = ticker.history(period="6mo", interval="1d")
    if len(hist) < MA_LONG + 1:
        return {"ma25": None, "ma75": None, "rsi14": None, "signal": None}

    close = hist["Close"]
    ma_short = close.rolling(MA_SHORT).mean()
    ma_long = close.rolling(MA_LONG).mean()
    rsi = _rsi(close, RSI_PERIOD)

    prev_diff = ma_short.iloc[-2] - ma_long.iloc[-2]
    curr_diff = ma_short.iloc[-1] - ma_long.iloc[-1]
    latest_rsi = rsi.iloc[-1]

    if prev_diff <= 0 < curr_diff:
        signal = "ゴールデンクロス"
    elif prev_diff >= 0 > curr_diff:
        signal = "デッドクロス"
    elif latest_rsi < RSI_OVERSOLD:
        signal = "売られすぎ"
    elif latest_rsi > RSI_OVERBOUGHT:
        signal = "買われすぎ"
    else:
        signal = "中立"

    return {
        "ma25": round(ma_short.iloc[-1], 1),
        "ma75": round(ma_long.iloc[-1], 1),
        "rsi14": round(latest_rsi, 1),
        "signal": signal,
    }
