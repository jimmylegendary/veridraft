from names import initials


def test_basic():
    assert initials("ada lovelace") == "AL"


def test_three():
    assert initials("grace brewster hopper") == "GBH"


def test_extra_spaces():
    assert initials("  alan   turing ") == "AT"
