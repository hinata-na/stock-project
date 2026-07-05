"""ledger.py の純粋関数(compute_state)の単体テスト。DB接続は不要。
pytest 不要、`python test_ledger.py` で実行。"""

from ledger import compute_state


def _ev(id_, type_, amount=None, code=None, name=None, shares=None, price=None, ref_id=None):
    return {
        "id": id_, "type": type_, "amount": amount, "code": code,
        "name": name, "shares": shares, "price": price, "ref_id": ref_id,
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


def test_fill_shares_and_price_from_text():
    from ledger import _fill_from_text
    from screening import ScreeningConditions

    c = ScreeningConditions(ledger_event="買い", company_name="7203")
    c = _fill_from_text(c, "7203を1,880円で100株買った")
    assert c.ledger_shares == 100
    assert c.ledger_price == 1880

    # Gemini が抽出済みの値は上書きしない
    c2 = ScreeningConditions(ledger_event="売り", ledger_price=1950.0)
    c2 = _fill_from_text(c2, "トヨタを1900円で200株売った")
    assert c2.ledger_price == 1950.0
    assert c2.ledger_shares == 200


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
