#!/usr/bin/env python3
"""Dependency-free hard-gate validator for atomic capability Wiki entries."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


CAPABILITY_ID = re.compile(r"^CAP-[A-Z0-9-]+$")
SEMANTIC_KEY = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
MODEL_ID = SEMANTIC_KEY
EVIDENCE_RANK = {"E1": 1, "E2": 2, "E3": 3, "E4": 4, "E5": 5}
LIFECYCLE_MIN_EVIDENCE = {
    "draft": 2,
    "reviewed": 3,
    "verified": 4,
    "deprecated": 2,
}

REQUIRED = {
    "schema_version",
    "capability_id",
    "semantic_key",
    "name",
    "effect",
    "scope",
    "trigger",
    "inputs",
    "outputs",
    "preconditions",
    "hold_conditions",
    "postconditions",
    "constraints",
    "quality_metrics",
    "failure_modes",
    "interfaces",
    "dependencies",
    "incompatible_resources",
    "evidence",
    "confidence",
    "unknowns",
    "lifecycle",
}


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_keys(value: Any, keys: set[str], path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path}: expected object")
        return
    missing = sorted(keys - value.keys())
    if missing:
        errors.append(f"{path}: missing keys {', '.join(missing)}")


def _unique_strings(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{path}: expected array")
        return
    if any(not _nonempty_string(item) for item in value):
        errors.append(f"{path}: every item must be a non-empty string")
    elif len(value) != len(set(value)):
        errors.append(f"{path}: duplicate items are not allowed")


def validate_entry(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["$: expected object"]

    _require_keys(data, REQUIRED, "$", errors)
    if errors:
        return errors

    if data["schema_version"] != "1.0":
        errors.append("$.schema_version: expected '1.0'")

    capability_id = data["capability_id"]
    if not _nonempty_string(capability_id) or not CAPABILITY_ID.fullmatch(capability_id):
        errors.append("$.capability_id: expected CAP-[A-Z0-9-]+")

    semantic_key = data["semantic_key"]
    if not _nonempty_string(semantic_key) or not SEMANTIC_KEY.fullmatch(semantic_key):
        errors.append("$.semantic_key: invalid vendor-independent semantic key")

    name = data["name"]
    if not _nonempty_string(name) or "_" not in name:
        errors.append("$.name: use 动词_对象_必要限定 form")
    if any(token in str(name) for token in ("并且", "同时还", "以及")):
        errors.append("$.name: appears to contain multiple primary effects")

    effect = data["effect"]
    _require_keys(effect, {"action", "object", "observable_result"}, "$.effect", errors)
    if isinstance(effect, dict):
        for key in ("action", "object", "observable_result"):
            if key in effect and not _nonempty_string(effect[key]):
                errors.append(f"$.effect.{key}: expected non-empty string")

    scope = data["scope"]
    scope_keys = {
        "vendor",
        "model_id",
        "source_model_names",
        "body_parts",
        "environment",
        "selector",
        "resolution_status",
    }
    _require_keys(scope, scope_keys, "$.scope", errors)
    if isinstance(scope, dict):
        if not _nonempty_string(scope.get("model_id")) or not MODEL_ID.fullmatch(scope["model_id"]):
            errors.append("$.scope.model_id: invalid normalized model id")
        _unique_strings(scope.get("source_model_names"), "$.scope.source_model_names", errors)
        _unique_strings(scope.get("body_parts"), "$.scope.body_parts", errors)
        if scope.get("resolution_status") not in {"resolved", "ambiguous", "conflicted"}:
            errors.append("$.scope.resolution_status: invalid value")
        for key in ("vendor", "environment", "selector"):
            if not _nonempty_string(scope.get(key)):
                errors.append(f"$.scope.{key}: expected non-empty string")

    if not _nonempty_string(data["trigger"]):
        errors.append("$.trigger: a stable invocation trigger is required")

    for key in (
        "inputs",
        "outputs",
        "preconditions",
        "hold_conditions",
        "postconditions",
        "quality_metrics",
        "failure_modes",
        "interfaces",
        "dependencies",
        "incompatible_resources",
        "evidence",
        "unknowns",
    ):
        if not isinstance(data[key], list):
            errors.append(f"$.{key}: expected array")

    constraints = data["constraints"]
    _require_keys(constraints, {"time", "space", "information", "energy"}, "$.constraints", errors)
    if isinstance(constraints, dict):
        for dimension in ("time", "space", "information", "energy"):
            if dimension in constraints and not isinstance(constraints[dimension], list):
                errors.append(f"$.constraints.{dimension}: expected array")

    interfaces = data["interfaces"]
    if isinstance(interfaces, list):
        if not interfaces:
            errors.append("$.interfaces: at least one stable invocation surface is required")
        for index, interface in enumerate(interfaces):
            path = f"$.interfaces[{index}]"
            _require_keys(interface, {"type", "reference", "version"}, path, errors)
            if isinstance(interface, dict) and not _nonempty_string(interface.get("reference")):
                errors.append(f"{path}.reference: expected non-empty string")

    evidence = data["evidence"]
    evidence_ids: set[str] = set()
    evidence_ranks: list[int] = []
    if isinstance(evidence, list):
        if not evidence:
            errors.append("$.evidence: at least one evidence record is required")
        for index, item in enumerate(evidence):
            path = f"$.evidence[{index}]"
            keys = {
                "evidence_id",
                "source_type",
                "source_id",
                "source_version",
                "source_hash",
                "locator",
                "claim",
                "evidence_level",
                "excerpt",
            }
            _require_keys(item, keys, path, errors)
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("evidence_id")
            if not _nonempty_string(evidence_id):
                errors.append(f"{path}.evidence_id: expected non-empty string")
            elif evidence_id in evidence_ids:
                errors.append(f"{path}.evidence_id: duplicate evidence id")
            else:
                evidence_ids.add(evidence_id)
            for key in ("source_id", "locator", "claim"):
                if not _nonempty_string(item.get(key)):
                    errors.append(f"{path}.{key}: expected non-empty string")
            level = item.get("evidence_level")
            if level not in EVIDENCE_RANK:
                errors.append(f"{path}.evidence_level: expected E1-E5")
            else:
                evidence_ranks.append(EVIDENCE_RANK[level])

    for dependency in data["dependencies"] if isinstance(data["dependencies"], list) else []:
        if not _nonempty_string(dependency) or not CAPABILITY_ID.fullmatch(dependency):
            errors.append(f"$.dependencies: invalid capability id {dependency!r}")
        if dependency == capability_id:
            errors.append("$.dependencies: capability cannot depend on itself")
    deps = data["dependencies"]
    if isinstance(deps, list) and all(isinstance(x, str) for x in deps) and len(deps) != len(set(deps)):
        errors.append("$.dependencies: duplicate dependency ids")

    confidence = data["confidence"]
    _require_keys(confidence, {"extraction_score", "basis"}, "$.confidence", errors)
    if isinstance(confidence, dict):
        score = confidence.get("extraction_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
            errors.append("$.confidence.extraction_score: expected number from 0 to 1")
        if not _nonempty_string(confidence.get("basis")):
            errors.append("$.confidence.basis: expected non-empty string")

    lifecycle = data["lifecycle"]
    lifecycle_keys = {"status", "supersedes", "replaced_by", "deprecation_reason"}
    _require_keys(lifecycle, lifecycle_keys, "$.lifecycle", errors)
    if isinstance(lifecycle, dict):
        status = lifecycle.get("status")
        if status not in LIFECYCLE_MIN_EVIDENCE:
            errors.append("$.lifecycle.status: invalid lifecycle status")
        else:
            strongest = max(evidence_ranks, default=0)
            if strongest < LIFECYCLE_MIN_EVIDENCE[status]:
                errors.append(
                    f"$.lifecycle.status: {status} requires evidence level "
                    f"E{LIFECYCLE_MIN_EVIDENCE[status]} or higher"
                )
        for key in ("supersedes", "replaced_by"):
            _unique_strings(lifecycle.get(key), f"$.lifecycle.{key}", errors)
        reason = lifecycle.get("deprecation_reason")
        if status == "deprecated" and not _nonempty_string(reason):
            errors.append("$.lifecycle.deprecation_reason: required for deprecated entries")
        if status != "deprecated" and reason is not None:
            errors.append("$.lifecycle.deprecation_reason: must be null unless deprecated")

    referenced_evidence: set[str] = set()
    for dimension in ("time", "space", "information", "energy"):
        if not isinstance(constraints, dict) or not isinstance(constraints.get(dimension), list):
            continue
        for item in constraints[dimension]:
            if isinstance(item, dict) and isinstance(item.get("evidence_ids"), list):
                referenced_evidence.update(item["evidence_ids"])
    for collection in ("quality_metrics", "failure_modes"):
        if isinstance(data[collection], list):
            for item in data[collection]:
                if isinstance(item, dict) and isinstance(item.get("evidence_ids"), list):
                    referenced_evidence.update(item["evidence_ids"])
    missing_evidence = sorted(referenced_evidence - evidence_ids)
    if missing_evidence:
        errors.append(
            "$: constraint, metric, or failure references missing evidence ids "
            + ", ".join(missing_evidence)
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", type=Path, help="Atomic capability entry JSON file")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        data = json.loads(args.entry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors = validate_entry(data)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"VALID: {data['capability_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
