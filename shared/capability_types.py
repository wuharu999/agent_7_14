from __future__ import annotations

from copy import deepcopy
from typing import Any


CAPABILITY_TYPES = {"building_block", "operational_behavior"}


def _legacy_verification_profile(entry: dict[str, Any]) -> dict[str, Any] | None:
    if entry.get("capability_type") != "operational_behavior":
        return None
    raw_references = entry.get("evidence_refs", entry.get("evidence", []))
    if isinstance(raw_references, str):
        raw_references = [raw_references]
    references = [
        str(value)
        for value in raw_references
        if isinstance(value, str) and value.strip()
    ]
    if not references:
        return None
    level = str(entry.get("evidence_level") or "")
    test_level = {"E5": "field", "E4": "pilot", "E3": "bench"}.get(
        level, "simulation"
    )
    return {
        "workspace_type": "legacy evidence; envelope not yet normalized",
        "lighting": "unknown",
        "terrain_weather": "unknown",
        "workspace_dynamics": "unknown",
        "object_payload_boundary": "unknown",
        "duty_cycle": "unknown",
        "versions": {},
        "test_level": test_level,
        "sample_size": 0,
        "passed_count": 0,
        "measured_values": [],
        "evidence_locator": references[0],
        "support_state": "conditional",
        "limitations": ["Legacy evidence requires operating-envelope backfill"],
        "unknowns": [
            "workspace boundary",
            "lighting boundary",
            "payload boundary",
            "duty-cycle boundary",
        ],
    }


def _backfill_legacy_profile(result: dict[str, Any], warnings: list[str]) -> None:
    if result.get("verification_profiles"):
        return
    profile = _legacy_verification_profile(result)
    result["verification_profiles"] = [profile] if profile else []
    if profile:
        warnings.append("legacy_verification_profile_backfilled_conditionally")


def migrate_legacy_capability(entry: dict[str, Any]) -> dict[str, Any]:
    """Map a legacy capability without silently treating a missing level as L0."""
    result = deepcopy(entry)
    warnings = [str(value) for value in result.get("migration_warnings", [])]
    existing = str(result.get("capability_type") or "")
    if existing in CAPABILITY_TYPES:
        _backfill_legacy_profile(result, warnings)
        result["migration_warnings"] = list(dict.fromkeys(warnings))
        return result

    legacy = str(result.get("abstraction_level") or result.get("abstraction") or "")
    if legacy == "L0_primitive_driver":
        result["capability_type"] = "building_block"
    elif legacy == "L2_composite_skill":
        result["capability_type"] = "operational_behavior"
    elif legacy == "L1_atomic_skill":
        interfaces = result.get("interfaces") or result.get("interface")
        effect = result.get("effect")
        if interfaces and effect:
            result["capability_type"] = "building_block"
        else:
            result["capability_type"] = "operational_behavior"
        warnings.append("ambiguous_legacy_l1_review_required")
    elif legacy == "L3_scenario_module":
        result["record_type"] = "solution_artifact"
        warnings.append("legacy_l3_moved_outside_capability_catalog")
    else:
        result["capability_type"] = "unclassified"
        warnings.append("missing_capability_type_review_required")
    if legacy:
        result["legacy_abstraction_level"] = legacy
    result.pop("abstraction_level", None)
    result.pop("abstraction", None)
    _backfill_legacy_profile(result, warnings)
    result["migration_warnings"] = list(dict.fromkeys(warnings))
    return result


def required_capability_type(requirement: dict[str, Any]) -> str:
    explicit = str(requirement.get("required_capability_type") or "")
    if explicit in CAPABILITY_TYPES:
        return explicit
    legacy = str(requirement.get("required_abstraction_level") or "")
    if legacy == "L1_atomic_skill":
        return "building_block"
    return "operational_behavior"
