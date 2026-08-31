"""A tiny tree to pin, and to break on purpose. `tests/test_ladderpin.py` uses it."""


def total(items):
    return sum(item["price"] * item["quantity"] for item in items)


def slugify(text):
    return "-".join(str(text).lower().split())


def truncate(text, limit):
    return text[:limit]


def label(text):
    return str(text).upper()
