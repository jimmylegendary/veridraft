from ranges import parse_range


def test_basic():
    assert parse_range("1-5") == [1, 2, 3, 4, 5]


def test_same():
    assert parse_range("3-3") == [3]


def test_single():
    assert parse_range("7") == [7]


def test_zero():
    assert parse_range("0-2") == [0, 1, 2]
