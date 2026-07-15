from __future__ import annotations

from pathlib import PurePosixPath

SECURITY_WARNING_CATEGORIES = {
    "instruction_override",
    "prompt_exfiltration",
    "secret_exfiltration",
    "tool_escalation",
    "encoded_execution",
    "scan_incomplete_size",
    "scan_incomplete_encoding",
    "scan_incomplete_total",
    "scan_incomplete_read",
}


def validated_security_warnings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    merged: dict[str, set[str]] = {}
    for item in value[:10_000]:
        if not isinstance(item, dict):
            continue
        identity = str(item.get("source_identity") or "")
        path = PurePosixPath(identity)
        if (
            not identity
            or len(identity) > 1000
            or "\\" in identity
            or path.is_absolute()
            or ".." in path.parts
        ):
            continue
        categories = sorted(
            {
                str(category)
                for category in item.get("categories") or []
                if str(category) in SECURITY_WARNING_CATEGORIES
            }
        )
        if categories:
            merged.setdefault(identity, set()).update(categories)
    return [
        {"source_identity": identity, "categories": sorted(categories)}
        for identity, categories in sorted(merged.items())
    ]
