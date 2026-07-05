"""短期スイングの売買ルールエンジン(Phase 7)。

設計は DESIGN.md を参照。判定はすべてこのモジュールの決定論的な純粋関数で行い、
Gemini は説明文の生成のみに使う(このモジュールは Gemini に依存しない)。

夜間バッチ(本番)とバックテストの両方から同じ関数を呼ぶことで、
「検証した通りのルールで運用する」ことを保証する。

入力の hist は yfinance の history() が返す形式の DataFrame
(columns: Open/High/Low/Close/Volume、index: 日付昇順)を想定する。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SwingParams:
    """売買ルールの全パラメータ。バックテストで調整し、本番も同じ値を使う。"""

    # 数値は2026-07-05のバックテストで決定(検証結果は DESIGN.md の Phase 7 を参照)
    # --- セットアップ(買い候補の条件) ---
    breakout_days: int = 75      # この日数の終値高値を更新したらブレイクとみなす
    volume_ratio_min: float = 2.0  # 出来高が20日平均の何倍以上か
    rsi_max: float = 75.0        # RSI14 がこれ以上は過熱として除外
    ma_days: int = 25            # 終値がこの移動平均より上にあること
    # --- 除外条件 ---
    daily_gain_max_pct: float = 15.0   # 当日の上昇率がこれ以上(ストップ高級)は除外
    turnover_min_yen: float = 1e8      # 20日平均売買代金がこれ未満は流動性不足で除外
    # --- 出口 ---
    take_profit_pct: float = 7.0   # 利確ライン(エントリー価格比)
    stop_loss_pct: float = 7.0     # 損切りライン(エントリー価格比)
    time_stop_days: int = 20       # エントリー後この営業日数で決着しなければ手仕舞い
    # --- 地合いフィルタ ---
    regime_ma_days: int = 25       # 指数の終値がこの移動平均を下回る日は候補なし


DEFAULT_PARAMS = SwingParams()

# セットアップ判定に最低限必要な日足の本数
MIN_HISTORY_ROWS = 100


def _rsi_last(close, period: int = 14) -> float | None:
    """indicators.py と同じ計算式(Wilder平滑)で最新のRSIを返す。"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1] if avg_loss.iloc[-1] > 0 else float("inf")
    return 100 - (100 / (1 + rs))


def market_regime_ok(index_hist, params: SwingParams = DEFAULT_PARAMS) -> bool:
    """地合いフィルタ。指数(日経平均等)の終値がMA25以上なら True。"""
    close = index_hist["Close"]
    if len(close) < params.regime_ma_days:
        return False
    ma = close.rolling(params.regime_ma_days).mean()
    return bool(close.iloc[-1] >= ma.iloc[-1])


def find_setup(hist, params: SwingParams = DEFAULT_PARAMS) -> dict | None:
    """日足の最終日を評価し、買い候補の条件を満たせばセットアップ情報を返す。

    返り値の dict は「注文レシピ」の数値と、説明文生成に使う根拠数値を含む。
    条件を満たさなければ None。
    """
    if hist is None or len(hist) < max(MIN_HISTORY_ROWS, params.breakout_days + 1):
        return None

    close = hist["Close"]
    volume = hist["Volume"]
    today_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])

    if today_close <= 0 or prev_close <= 0:
        return None

    # 1) ブレイクアウト: 直近 breakout_days 日(当日除く)の終値高値を更新
    prior_high = float(close.iloc[-(params.breakout_days + 1):-1].max())
    if today_close <= prior_high:
        return None

    # 2) 出来高急増: 当日出来高が20日平均(当日除く)の volume_ratio_min 倍以上
    vol_avg20 = float(volume.iloc[-21:-1].mean())
    if vol_avg20 <= 0:
        return None
    volume_ratio = float(volume.iloc[-1]) / vol_avg20
    if volume_ratio < params.volume_ratio_min:
        return None

    # 3) トレンド: 終値がMA25より上
    ma = float(close.rolling(params.ma_days).mean().iloc[-1])
    if today_close < ma:
        return None

    # 4) 過熱の除外: RSI が上限未満、当日上昇率がストップ高級でない
    rsi = _rsi_last(close)
    if rsi is None or rsi >= params.rsi_max:
        return None
    daily_gain_pct = (today_close / prev_close - 1) * 100
    if daily_gain_pct >= params.daily_gain_max_pct:
        return None

    # 5) 流動性: 20日平均売買代金
    turnover = float((close * volume).iloc[-20:].mean())
    if turnover < params.turnover_min_yen:
        return None

    entry = today_close
    return {
        # --- 注文レシピ ---
        "entry_limit": round(entry, 1),          # 翌日の指値(当日終値)
        "take_profit": round(entry * (1 + params.take_profit_pct / 100), 1),
        "stop_loss": round(entry * (1 - params.stop_loss_pct / 100), 1),
        "time_stop_days": params.time_stop_days,
        # --- 根拠数値(説明文の材料) ---
        "breakout_days": params.breakout_days,
        "volume_ratio": round(volume_ratio, 1),
        "rsi14": round(rsi, 1),
        "ma25": round(ma, 1),
        "daily_gain_pct": round(daily_gain_pct, 1),
        "turnover_oku_yen": round(turnover / 1e8, 1),
    }


def rank_candidates(setups: list[dict], max_count: int = 3) -> list[dict]:
    """候補が多い日は出来高倍率の高い順に絞る(注目度の代理指標)。"""
    return sorted(setups, key=lambda s: s["volume_ratio"], reverse=True)[:max_count]


def evaluate_after_signal(hist, signal_date, setup: dict, slippage_pct: float = 0.0) -> dict:
    """シグナル日以降の日足から、約定と出口を判定する。

    シャドーラン(夜間バッチ)とバックテストの両方がこの関数を使うことで、
    「検証した通りの約定・出口モデル」で答え合わせされることを保証する。

    約定モデル: シグナル翌営業日、始値が指値以下なら始値で、安値が指値以下なら指値で約定。
    出口: 損切り優先(同日に利確と損切りの両方がかかり得る場合は保守的に損切り)、
          ギャップは始値で約定、time_stop_days 営業日で時間切れ(終値手仕舞い)。
    slippage_pct は出口にのみ不利方向へ適用する(エントリーは指値・板寄せのため価格保証)。

    返り値: {"status": "約定待ち" | "未約定" | "保有中" | "決済済み", ...}
    決済済みなら result(利確/損切り/時間切れ 等)、pnl_pct などを含む。
    """
    dates = [d.date() if hasattr(d, "date") else d for d in hist.index]
    try:
        signal_idx = dates.index(signal_date)
    except ValueError:
        return {"status": "約定待ち"}  # シグナル日がデータに無い(データ未更新)

    entry_idx = signal_idx + 1
    if entry_idx >= len(hist):
        return {"status": "約定待ち"}  # 翌営業日がまだ来ていない

    limit = setup["entry_limit"]
    o = float(hist["Open"].iloc[entry_idx])
    low = float(hist["Low"].iloc[entry_idx])
    if o <= limit:
        entry_price = o
    elif low <= limit:
        entry_price = limit
    else:
        return {"status": "未約定"}

    result = {
        "status": "保有中",
        "entry_date": dates[entry_idx],
        "entry_price": round(entry_price, 1),
    }

    tp, sl = setup["take_profit"], setup["stop_loss"]
    deadline = entry_idx + setup["time_stop_days"]
    for j in range(entry_idx, min(deadline + 1, len(hist))):
        o, h, low = (float(hist[c].iloc[j]) for c in ("Open", "High", "Low"))
        exit_price, reason = None, None
        if j > entry_idx and o <= sl:
            exit_price, reason = o, "損切り(ギャップ)"
        elif low <= sl:
            exit_price, reason = sl, "損切り"
        elif j > entry_idx and o >= tp:
            exit_price, reason = o, "利確(ギャップ)"
        elif h >= tp:
            exit_price, reason = tp, "利確"
        elif j == deadline:
            exit_price, reason = float(hist["Close"].iloc[j]), "時間切れ"
        if exit_price is not None:
            exit_price *= 1 - slippage_pct / 100
            result.update(
                {
                    "status": "決済済み",
                    "exit_date": dates[j],
                    "exit_price": round(exit_price, 1),
                    "result": reason,
                    "pnl_pct": round((exit_price / entry_price - 1) * 100, 2),
                    "days_held": j - entry_idx,
                }
            )
            return result

    return result  # 出口条件にまだ達していない(保有中)
