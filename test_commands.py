"""commands.parse_command の単体テスト(ネットワーク・DB不要)。
pytest 不要、`python test_commands.py` で実行。

銘柄判断の銘柄名解決(resolve_company)は screener.csv に依存するため、
ここでは銘柄コード形式のケースと台帳系コマンドのみを検証する。
"""

from commands import parse_command


def test_deposit_with_man_unit():
    cmd = parse_command("50万入金した")
    assert cmd.kind == "入金"
    assert cmd.amount == 500_000


def test_deposit_with_man_yen_decimal():
    cmd = parse_command("52.5万円入金")
    assert cmd.kind == "入金"
    assert cmd.amount == 525_000


def test_withdraw_with_plain_yen():
    cmd = parse_command("100,000円出金した")
    assert cmd.kind == "出金"
    assert cmd.amount == 100_000


def test_deposit_without_amount_still_parses():
    # 金額なしはイベントとして成立させ、ledger側でエラーメッセージを返す
    cmd = parse_command("入金した")
    assert cmd.kind == "入金"
    assert cmd.amount is None


def test_buy_with_code_price_shares():
    cmd = parse_command("7203を1,880円で100株買った")
    assert cmd.kind == "買い"
    assert cmd.company == "7203"
    assert cmd.shares == 100
    assert cmd.price == 1880


def test_sell_with_company_name():
    cmd = parse_command("トヨタを1950円で200株売った")
    assert cmd.kind == "売り"
    assert cmd.company == "トヨタ"  # 価格の1950を銘柄コードと誤認しない
    assert cmd.shares == 200
    assert cmd.price == 1950


def test_adjustment_absolute_amount():
    cmd = parse_command("余力を52万円に修正")
    assert cmd.kind == "調整"
    assert cmd.amount == 520_000


def test_cancel():
    assert parse_command("さっきの取り消して").kind == "取消"
    assert parse_command("直前の取消").kind == "取消"


def test_balance_inquiry():
    assert parse_command("余力いくら?").kind == "余力照会"
    assert parse_command("保有見せて").kind == "余力照会"


def test_judge_by_code():
    cmd = parse_command("7203は今買い時?")
    assert cmd.kind == "銘柄判断"
    assert cmd.company == "7203"


def test_judge_question_is_not_buy_event():
    # 「買い」を含んでも株数・単価がなければ台帳報告にしない
    cmd = parse_command("7203買っていい?")
    assert cmd.kind == "銘柄判断"


def test_unknown_text_returns_help_kind():
    assert parse_command("PER15倍以下で配当3%以上の株").kind == "不明"
    assert parse_command("こんにちは").kind == "不明"


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
