import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.money import format_brl, parse_amount_brl


def test_plain_integer():
    assert parse_amount_brl("50") == 5000


def test_comma_decimal():
    assert parse_amount_brl("50,90") == 5090


def test_dot_decimal():
    assert parse_amount_brl("50.90") == 5090


def test_thousands_with_comma_decimal():
    assert parse_amount_brl("R$ 1.234,56") == 123456


def test_thousands_only():
    assert parse_amount_brl("1.234") == 123400


def test_thousands_dot_grouping_no_decimal_ambiguous_two_digit_group():
    # "1.234.567" -- pure thousands grouping, no decimal part
    assert parse_amount_brl("1.234.567") == 123456700


def test_reais_word():
    assert parse_amount_brl("50 reais") == 5000


def test_conto_slang():
    assert parse_amount_brl("50 conto") == 5000


def test_currency_prefix():
    assert parse_amount_brl("R$50,00") == 5000


def test_no_number_returns_none():
    assert parse_amount_brl("no amount here") is None


def test_format_brl_basic():
    assert format_brl(5000) == "R$ 50,00"


def test_format_brl_thousands_grouping():
    assert format_brl(123456) == "R$ 1.234,56"


def test_format_brl_negative():
    assert format_brl(-500) == "-R$ 5,00"


def test_format_brl_none():
    assert format_brl(None) == "—"
