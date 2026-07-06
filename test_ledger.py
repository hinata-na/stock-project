"""ledger.py の純粋関数(compute_state)の単体テスト。DB接続は不要。
pytest 不要、`python test_ledger.py` で実行。"""

from ledger import compute_state


def _ev(id_, type_, amount=None, code=None, name=None, shares=None, price=None, ref_id=None, created_at=None):
    return {
        "id": id_, "type": type_, "amount": amount, "code": code,
        "name": name, "shares": shares, "price": price, "ref_id": ref_id,
        "created_at": created_at or f"2026-07-{id_:02d}T20:00:00+09:00",
    }


def test_deposit_withdraw():
    state = compute_state([_ev(1, "入金", 500_000), _ev(2, "出金", 100_000)])
    assert state["cash"] == 400_000
    assert state["positions"] == {}


def test_buy_reduces_cash_and_adds_position():
    state = compute_state(
        [_ev(1, "入金", 500_000), _ev(2, "買い", code="7203", name="トヨタ", shares=100, price=1880)]
    )
    assert state["cash"] == 500_000 - 188_000
    assert state["positions"]["7203"]["shares"] == 100
    assert state["positions"]["7203"]["avg_price"] == 1880


def test_average_price_on_additional_buy():
    state = compute_state(
        [
            _ev(1, "入金", 1_000_000),
            _ev(2, "買い", code="7203", shares=100, price=1800),
            _ev(3, "買い", code="7203", shares=100, price=2000),
        ]
    )
    assert state["positions"]["7203"]["shares"] == 200
    assert state["positions"]["7203"]["avg_price"] == 1900


def test_sell_adds_cash_and_closes_position():
    state = compute_state(
        [
            _ev(1, "入金", 500_000),
            _ev(2, "買い", code="7203", shares=100, price=1880),
            _ev(3, "売り", code="7203", shares=100, price=1950),
        ]
    )
    assert state["cash"] == 500_000 - 188_000 + 195_000
    assert state["positions"] == {}


def test_adjustment_is_signed_delta():
    state = compute_state([_ev(1, "入金", 500_000), _ev(2, "調整", -12_345)])
    assert state["cash"] == 487_655


def test_cancel_negates_referenced_event():
    state = compute_state(
        [
            _ev(1, "入金", 500_000),
            _ev(2, "買い", code="7203", shares=100, price=1880),
            _ev(3, "取消", ref_id=2),
        ]
    )
    assert state["cash"] == 500_000
    assert state["positions"] == {}


def test_events_out_of_order_are_sorted_by_id():
    state = compute_state([_ev(2, "出金", 100_000), _ev(1, "入金", 500_000)])
    assert state["cash"] == 400_000


def test_opened_date_tracks_position_opening():
    state = compute_state(
        [
            _ev(1, "入金", 1_000_000),
            _ev(2, "買い", code="7203", shares=100, price=1800, created_at="2026-07-02T20:00:00+09:00"),
            _ev(3, "買い", code="7203", shares=100, price=1900, created_at="2026-07-04T20:00:00+09:00"),
        ]
    )
    # 買い増ししても建玉日は最初の買いの日のまま
    assert state["positions"]["7203"]["opened_date"] == "2026-07-02"


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
