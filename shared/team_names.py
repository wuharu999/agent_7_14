from __future__ import annotations

import re

_TEAM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RESERVED_TEAM_NAMES = frozenset({"all", "default"})


def normalize_team_name(value: str, *, allow_reserved: bool = True) -> str:
    name = (value or "").strip()
    if not _TEAM_NAME.fullmatch(name):
        raise ValueError(
            "Robot name must contain 1 to 64 letters, numbers, underscores, or hyphens"
        )
    if not allow_reserved and name.casefold() in RESERVED_TEAM_NAMES:
        raise ValueError(f"Robot name '{name}' is reserved")
    return name
