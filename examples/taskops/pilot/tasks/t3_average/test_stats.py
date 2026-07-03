from stats import average


def test_two():
    assert average([1, 2]) == 1.5


def test_ints():
    assert average([2, 4, 6]) == 4.0


def test_mixed():
    assert average([1, 2, 3, 4]) == 2.5
