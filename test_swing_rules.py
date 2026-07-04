"""swing_rules.py の単体テスト。pytest 不要、`python test_swing_rules.py` で実行。"""

import numpy as np
import pandas as pd

from swing_rules import DEFAULT_PARAMS, find_setup, market_regime_ok, rank_candidates


def _make_hist(days: int = 200, price: float = 1000.0, volume: float = 100_000) -> pd.DataFrame:
    """横ばい相場のダミー日足(終値1000円前後で微小に上下)を作る。"""
    rng = np.random.default_rng(42)
    close = price + np.cumsum(rng.normal(0, 12, days))  # 日々±1%程度のボラティリティ
    close = np.clip(close, price * 0.90, price * 1.05)  # 高値更新しないレンジに収める
    dates = pd.bdate_range("2024-01-01", periods=days)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.005,
            "Low": close * 0.995,
            "Close": close,
            "Volume": np.full(days, volume),
        },
        index=dates,
    )


def _add_breakout(hist: pd.DataFrame, gain: float = 0.03, vol_mult: float = 3.0) -> pd.DataFrame:
    """高値ブレイクの典型形を作る。

    直近30日は上げ下げを交えながら過去の高値直下まで接近(RSIが過熱しない形)、
    最終日に出来高急増を伴って高値を gain 分だけ上抜ける。
    """
    hist = hist.copy()
    seg = 30
    prior_max = float(hist["Close"].iloc[:-seg].max())
    # 高値の90%→99%へ、1日おきに小さな押しを入れつつ接近する
    base = np.linspace(prior_max * 0.90, prior_max * 0.99, seg - 1)
    base *= 1 + 0.012 * np.where(np.arange(seg - 1) % 2 == 0, 1, -1)
    new_close = prior_max * (1 + gain)
    closes = np.append(base, new_close)
    idx = hist.columns.get_loc("Close")
    hist.iloc[-seg:, idx] = closes
    hist.iloc[-seg:, hist.columns.get_loc("Open")] = closes
    hist.iloc[-seg:, hist.columns.get_loc("High")] = closes * 1.005
    hist.iloc[-seg:, hist.columns.get_loc("Low")] = closes * 0.995
    hist.iloc[-1, hist.columns.get_loc("Volume")] = float(hist["Volume"].iloc[-2]) * vol_mult
    return hist


def test_breakout_detected():
    hist = _add_breakout(_make_hist())
    setup = find_setup(hist)
    assert setup is not None, "ブレイク+出来高急増でセットアップが出るべき"
    assert setup["take_profit"] > setup["entry_limit"] > setup["stop_loss"]
    tp_width = setup["take_profit"] / setup["entry_limit"] - 1
    sl_width = 1 - setup["stop_loss"] / setup["entry_limit"]
    assert tp_width > sl_width, "利確幅 > 損切り幅(損小利大)"


def test_no_breakout_no_setup():
    assert find_setup(_make_hist()) is None, "レンジ相場ではセットアップなし"


def test_low_volume_rejected():
    hist = _add_breakout(_make_hist(), vol_mult=1.2)  # 出来高が足りない
    assert find_setup(hist) is None


def test_spike_rejected():
    hist = _add_breakout(_make_hist(), gain=0.20)  # ストップ高級の急騰
    assert find_setup(hist) is None


def test_short_history_rejected():
    assert find_setup(_make_hist(days=50)) is None


def test_illiquid_rejected():
    hist = _add_breakout(_make_hist(volume=1_000))  # 売買代金が1億円未満
    assert find_setup(hist) is None


def test_regime_filter():
    up = _make_hist()
    up["Close"] = np.linspace(900, 1100, len(up))  # 上昇トレンド
    assert market_regime_ok(up)
    down = _make_hist()
    down["Close"] = np.linspace(1100, 900, len(down))  # 下降トレンド
    assert not market_regime_ok(down)


def test_rank_candidates():
    setups = [{"volume_ratio": v} for v in (2.0, 5.0, 3.0, 4.0)]
    top = rank_candidates(setups, max_count=3)
    assert [s["volume_ratio"] for s in top] == [5.0, 4.0, 3.0]


if __name__ == "__main__":
    import sys

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK   {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
