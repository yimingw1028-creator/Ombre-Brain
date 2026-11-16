# ruff: noqa
from difflib import SequenceMatcher
def ratio(a, b, **_):
    return 100.0 * SequenceMatcher(None, a or "", b or "").ratio()
def partial_ratio(a, b, **_):
    a, b = a or "", b or ""
    if not a or not b: return 0.0
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    if s in l: return 100.0
    return 100.0 * SequenceMatcher(None, s, l).ratio()
token_sort_ratio = token_set_ratio = WRatio = QRatio = partial_ratio
