from dates import is_leap


def test_common():
    assert is_leap(2023) is False


def test_div4():
    assert is_leap(2024) is True


def test_century_not():
    assert is_leap(1900) is False


def test_century_yes():
    assert is_leap(2000) is True


def test_2100():
    assert is_leap(2100) is False
