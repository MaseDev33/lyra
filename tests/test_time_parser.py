from lyra.utils.time_parser import parse_duration


def test_parse_duration_handles_common_units():
    assert parse_duration("30m") == 1800
    assert parse_duration("1h 30m") == 5400
    assert parse_duration("2d") == 172800


def test_parse_duration_rejects_invalid_input():
    try:
        parse_duration("banana")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for invalid duration")
