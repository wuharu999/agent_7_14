from __future__ import annotations

import re

_SEPARATOR = r"[\s_-]{0,4}"

_CANONICAL_TERM_ALIASES = (
    (
        re.compile(
            rf"(?:天工|TianGong){_SEPARATOR}2\.0{_SEPARATOR}"
            rf"(?:雷达头版|Radar{_SEPARATOR}Edition)",
            re.IGNORECASE,
        ),
        "天工行者雷达头版",
    ),
    (
        re.compile(
            rf"(?:天工|TianGong){_SEPARATOR}2\.0{_SEPARATOR}Lite(?:版)?",
            re.IGNORECASE,
        ),
        "天工行者基础版",
    ),
    (
        re.compile(
            rf"(?:天工|TianGong){_SEPARATOR}2\.0{_SEPARATOR}Plus(?:版)?",
            re.IGNORECASE,
        ),
        "天工行者无界",
    ),
    (
        re.compile(
            rf"(?:天工|TianGong){_SEPARATOR}2\.0{_SEPARATOR}Pro(?:版)?",
            re.IGNORECASE,
        ),
        "天工行者无疆",
    ),
    (
        re.compile(
            rf"(?:天工|TianGong){_SEPARATOR}3\.0(?:{_SEPARATOR}dex)?",
            re.IGNORECASE,
        ),
        "天工行者dex",
    ),
    (
        re.compile(
            rf"(?:天工|TianGong){_SEPARATOR}2\.0",
            re.IGNORECASE,
        ),
        "天工行者",
    ),
    (re.compile(r"慧思开物平台"), "Thinkerstudio遥操数采平台"),
    (re.compile(r"慧思开物"), "Thinkerstudio"),
    (re.compile(r"慧思宇宙平台"), "Thinkercosmos平台"),
    (re.compile(r"慧思宇宙"), "Thinkercosmos"),
)

_LEGACY_TERM_EXAMPLES = (
    "TianGong 2.0 Radar Edition",
    "TianGong 2.0 Lite",
    "TianGong 2.0 Plus",
    "TianGong 2.0 Pro",
    "TianGong 3.0 dex",
    "TianGong 2.0",
    "天工2.0雷达头版",
    "慧思开物平台",
    "慧思宇宙平台",
)

TERMINOLOGY_HOLDBACK = max(len(term) for term in _LEGACY_TERM_EXAMPLES) - 1

CANONICAL_TERMINOLOGY_PROMPT = """Mandatory product terminology:
- Never output a legacy TianGong 2.0 or TianGong 3.0 name, even when it appears in the question,
  conversation history, or supplied Wiki pages.
- Always write `天工行者雷达头版` for the former radar-edition name.
- Always write `天工行者基础版` for the former 2.0 Lite name.
- Always write `天工行者无界` for the former 2.0 Plus name.
- Always write `天工行者无疆` for the former 2.0 Pro name.
- Always write `天工行者dex` for the former 3.0 name.
- Always write `天工行者` for the remaining former 2.0 name.
- Apply the most specific mapping first. If historical naming must be discussed, say `the former
  product name` without reproducing a legacy name."""


def canonicalize_product_names(text: str) -> str:
    """Restore canonical product names without changing stored source or Wiki files."""
    for pattern, replacement in _CANONICAL_TERM_ALIASES:
        text = pattern.sub(replacement, text)
    return text
