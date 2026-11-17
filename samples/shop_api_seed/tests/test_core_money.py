from app.core.money import format_amount, parse_amount, round_money, split_amount


def test_round_money():
    assert round_money(10.0) == 10.0
    assert round_money(1.234) == 1.23


def test_parse_amount():
    assert parse_amount(" 12.344 ") == 12.34


def test_format_amount():
    assert format_amount(12.3) == "CNY 12.30"
    assert format_amount(7.5, "EUR") == "EUR 7.50"


def test_split_amount():
    assert split_amount(100.0, 4) == [25.0, 25.0, 25.0, 25.0]