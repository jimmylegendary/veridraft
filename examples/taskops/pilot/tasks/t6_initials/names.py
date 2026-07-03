def initials(name):
    """Return uppercase initials, e.g. 'ada lovelace' -> 'AL'."""
    return "".join(w[0] for w in name.split(" ")).upper()
