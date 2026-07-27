from __future__ import annotations

import re

_CANONICAL_TERM_ALIASES = (
    (re.compile(r"慧思开物平台"), "Thinkerstudio遥操数采平台"),
    (re.compile(r"慧思开物"), "Thinkerstudio"),
    (re.compile(r"慧思宇宙平台"), "Thinkercosmos平台"),
    (re.compile(r"慧思宇宙"), "Thinkercosmos"),
)

TERMINOLOGY_HOLDBACK = max(
    len(pattern.pattern) for pattern, _replacement in _CANONICAL_TERM_ALIASES
) - 1


def canonicalize_product_names(text: str) -> str:
    """Restore canonical product names for known generated translations."""
    for pattern, replacement in _CANONICAL_TERM_ALIASES:
        text = pattern.sub(replacement, text)
    return text
