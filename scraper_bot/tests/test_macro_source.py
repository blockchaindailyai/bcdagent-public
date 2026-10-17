from scraper_bot.sources.macro_source import _format_numeric_value, _label_with_code


def test_format_numeric_value_adds_thousands_separators_for_integers() -> None:
    assert _format_numeric_value("31490070") == "31,490,070"


def test_format_numeric_value_preserves_up_to_three_decimals() -> None:
    assert _format_numeric_value("325.252") == "325.252"


def test_format_numeric_value_trims_trailing_zeros() -> None:
    assert _format_numeric_value("1000.5000") == "1,000.5"


def test_label_with_code_returns_human_readable_label_when_known() -> None:
    label_map = {"CUUR0000SA0": "Consumer Price Index"}
    assert _label_with_code(label_map, "CUUR0000SA0") == "Consumer Price Index (CUUR0000SA0)"


def test_label_with_code_falls_back_to_code_when_unknown() -> None:
    assert _label_with_code({}, "UNKNOWN") == "UNKNOWN"
