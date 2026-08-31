"""Helpers for matching user product names to catalog product names."""

import re


def normalize_product_name(product_name):
    if not product_name or not str(product_name).strip():
        return None
    normalized = " ".join(re.findall(r"[a-z0-9]+", str(product_name).lower()))
    words = normalized.split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s"):
        words[-1] = words[-1][:-1]
    return " ".join(words)
