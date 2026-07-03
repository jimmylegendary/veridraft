def parse_range(s):
    """Parse a range like '1-5' into [1, 2, 3, 4, 5]."""
    a, b = s.split("-")
    return list(range(int(a), int(b)))
