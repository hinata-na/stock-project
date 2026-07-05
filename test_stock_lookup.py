"""stock_lookup.compose_judgement の単体テスト(ネットワーク・Gemini不使用)。
pytest 不要、`python test_stock_lookup.py` で実行。"""

import numpy as np
import pandas as pd

from stock_lookup import compose_judgement
from test_swing_rules import _add_breakout, _make_hist


def _up_index():
    idx = _make_hist()
    idx["Close"] = np.linspace(30000, 42000, len(idx))
    return idx


def _down_index():
    idx = _make_hist()
    idx["Close"] = np.linspace(42000, 30000, len(idx))
    return idx


def test_buy_candidate_when_all_conditions_met():
    text = compose_judgement("テスト製作所", "9999", _add_breakout(_make_hist()), _up_index())
    assert "判定: 買い候補" in text
    assert "注文レシピ" in text and "OCO" in text
    assert "投資助言ではありません" in text


def test_wait_when_regime_ng():
    text = compose_judgement("テスト製作所", "9999", _add_breakout(_make_hist()), _down_index())
    assert "判定: 様子見" in text and "地合いがNG" in text


def test_wait_with_failed_conditions_listed():
    text = compose_judgement("ダミー商事", "8888", _make_hist(), _up_index())
    assert "判定: 様子見" in text
    assert "満たしていない条件" in text
    assert "高値ブレイク" in text  # レンジ相場なのでブレイク条件が並ぶはず


def test_held_take_profit_reached():
    hist = _make_hist()
    hist.iloc[-1, hist.columns.get_loc("Close")] = 1080.0  # 建値1000の+8%
    position = {"avg_price": 1000.0, "opened_date": str(hist.index[-5].date())}
    text = compose_judgement("テスト製作所", "9999", hist, _up_index(), position)
    assert "判定: 売り時(利確ライン" in text
    assert "建値 1,000.0円" in text


def test_held_stop_loss_reached():
    hist = _make_hist()
    hist.iloc[-1, hist.columns.get_loc("Close")] = 920.0  # 建値1000の−8%
    position = {"avg_price": 1000.0}
    text = compose_judgement("テスト製作所", "9999", hist, _up_index(), position)
    assert "判定: 売り時(損切りライン" in text


def test_held_time_stop_expired():
    hist = _make_hist()
    hist.iloc[-1, hist.columns.get_loc("Close")] = 1010.0  # ライン未達
    position = {"avg_price": 1000.0, "opened_date": str(hist.index[-30].date())}  # 29営業日経過
    text = compose_judgement("テスト製作所", "9999", hist, _up_index(), position)
    assert "判定: 売り時(時間切れ" in text


def test_held_hold_continues():
    hist = _make_hist()
    hist.iloc[-1, hist.columns.get_loc("Close")] = 1010.0
    position = {"avg_price": 1000.0, "opened_date": str(hist.index[-3].date())}
    text = compose_judgement("テスト製作所", "9999", hist, _up_index(), position)
    assert "判定: 様子見(保有継続)" in text
    assert "残り" in text


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
