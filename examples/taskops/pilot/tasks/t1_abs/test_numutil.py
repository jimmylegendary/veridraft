from numutil import absolute


def test_positive():
    assert absolute(5) == 5


def test_negative():
    assert absolute(-5) == 5


def test_zero():
    assert absolute(0) == 0
