#!/usr/bin/env python3
"""Dependency-free hard-gate validator for atomic capability Wiki changesets."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from validate_capability_entry import CAPABILITY_ID, MODEL_ID, validate_entry


CHANGESET_ID = re.compile(r"^CHG-[A-Z0-9-]+$")
ACTIONS = {
    "create",
    "update",
    "implementation-instance",
    "split",
    "merge-proposal",
    "conflict",
    "deprecate",
    "source-removed",
    "delete-proposal",
    "skip",
    "blocked",
}
WRITE_ENTRY_ACTIONS = {
    "create",
    "update",
    "implementation-instance",
    "deprecate",
}
REVIEW_ONLY_ACTIONS = {
    "split",
    "merge-proposal",
    "conflict",
    "source-removed",
    "delete-proposal",
    "blocked",
}
SOURCE_STATUSES = {
    "processed",
    "unchanged",
    "excluded",
    "blocked",
    "unprocessed",
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


def validate_changeset(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["$: expected object"]

    required = {
        "schema_version",
        "changeset_id",
        "model_id",
        "source_snapshot",
        "target",
        "operations",
        "coverage_report",
    }
    _require_keys(data, required, "$", errors)
    if errors:
        return errors

    if data["schema_version"] != "1.0":
        errors.append("$.schema_version: expected '1.0'")
    if not _nonempty_string(data["changeset_id"]) or not CHANGESET_ID.fullmatch(data["changeset_id"]):
        errors.append("$.changeset_id: expected CHG-[A-Z0-9-]+")
    if not _nonempty_string(data["model_id"]) or not MODEL_ID.fullmatch(data["model_id"]):
        errors.append("$.model_id: invalid normalized model id")

    snapshot = data["source_snapshot"]
    _require_keys(snapshot, {"snapshot_id", "sources"}, "$.source_snapshot", errors)
    source_statuses: Counter[str] = Counter()
    if isinstance(snapshot, dict):
        if not _nonempty_string(snapshot.get("snapshot_id")):
            errors.append("$.source_snapshot.snapshot_id: expected non-empty string")
        sources = snapshot.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append("$.source_snapshot.sources: expected non-empty array")
        else:
            source_ids: set[str] = set()
            for index, source in enumerate(sources):
                path = f"$.source_snapshot.sources[{index}]"
                keys = {"source_id", "version", "hash_or_revision", "status"}
                _require_keys(source, keys, path, errors)
                if not isinstance(source, dict):
                    continue
                source_id = source.get("source_id")
                if not _nonempty_string(source_id):
                    errors.append(f"{path}.source_id: expected non-empty string")
                elif source_id in source_ids:
                    errors.append(f"{path}.source_id: duplicate source id")
                else:
                    source_ids.add(source_id)
                status = source.get("status")
                if status not in SOURCE_STATUSES:
                    errors.append(f"{path}.status: invalid status")
                else:
                    source_statuses[status] += 1
                if status in {"excluded", "blocked"} and not _nonempty_string(source.get("reason")):
                    errors.append(f"{path}.reason: required for excluded or blocked source")

    target = data["target"]
    _require_keys(target, {"wiki_id", "section_id", "base_revision"}, "$.target", errors)
    if isinstance(target, dict):
        for key in ("wiki_id", "section_id", "base_revision"):
            if not _nonempty_string(target.get(key)):
                errors.append(f"$.target.{key}: expected non-empty string")

    operations = data["operations"]
    operation_counts: Counter[str] = Counter()
    if not isinstance(operations, list):
        errors.append("$.operations: expected array")
        operations = []
    operation_ids: set[str] = set()
    for index, operation in enumerate(operations):
        path = f"$.operations[{index}]"
        keys = {
            "operation_id",
            "action",
            "target_entry_id",
            "reason",
            "source_evidence_ids",
            "approval_required",
            "after_entry",
        }
        _require_keys(operation, keys, path, errors)
        if not isinstance(operation, dict):
            continue
        operation_id = operation.get("operation_id")
        if not _nonempty_string(operation_id):
            errors.append(f"{path}.operation_id: expected non-empty string")
        elif operation_id in operation_ids:
            errors.append(f"{path}.operation_id: duplicate operation id")
        else:
            operation_ids.add(operation_id)

        action = operation.get("action")
        if action not in ACTIONS:
            errors.append(f"{path}.action: invalid action")
            continue
        operation_counts[action] += 1

        target_entry_id = operation.get("target_entry_id")
        if target_entry_id is not None and (
            not _nonempty_string(target_entry_id)
            or not CAPABILITY_ID.fullmatch(target_entry_id)
        ):
            errors.append(f"{path}.target_entry_id: invalid capability id")
        if action in {"update", "deprecate", "source-removed", "delete-proposal"} and target_entry_id is None:
            errors.append(f"{path}.target_entry_id: required for {action}")

        if not _nonempty_string(operation.get("reason")):
            errors.append(f"{path}.reason: expected non-empty string")
        evidence_ids = operation.get("source_evidence_ids")
        if not isinstance(evidence_ids, list) or any(not _nonempty_string(x) for x in evidence_ids):
            errors.append(f"{path}.source_evidence_ids: expected string array")
        elif len(evidence_ids) != len(set(evidence_ids)):
            errors.append(f"{path}.source_evidence_ids: duplicate ids")

        if not isinstance(operation.get("approval_required"), bool):
            errors.append(f"{path}.approval_required: expected boolean")
        if action in REVIEW_ONLY_ACTIONS and operation.get("approval_required") is not True:
            errors.append(f"{path}.approval_required: {action} must require approval")

        after_entry = operation.get("after_entry")
        if action in WRITE_ENTRY_ACTIONS:
            if not isinstance(after_entry, dict):
                errors.append(f"{path}.after_entry: required for {action}")
            else:
                for entry_error in validate_entry(after_entry):
                    errors.append(f"{path}.after_entry{entry_error[1:]}")
                if after_entry.get("scope", {}).get("model_id") != data["model_id"]:
                    errors.append(f"{path}.after_entry.scope.model_id: changeset model mismatch")
                if target_entry_id and after_entry.get("capability_id") != target_entry_id:
                    errors.append(f"{path}.after_entry.capability_id: target entry id mismatch")
                if action == "deprecate" and after_entry.get("lifecycle", {}).get("status") != "deprecated":
                    errors.append(f"{path}.after_entry.lifecycle.status: deprecate must set deprecated")
        elif after_entry is not None:
            errors.append(f"{path}.after_entry: must be null for {action}")

    coverage = data["coverage_report"]
    coverage_keys = {
        "total_sources",
        "processed_sources",
        "unchanged_sources",
        "excluded_sources",
        "blocked_sources",
        "unprocessed_sources",
        "extracted_claims",
        "atomic_entries",
        "non_capability_candidates",
        "operation_counts",
        "is_complete",
    }
    _require_keys(coverage, coverage_keys, "$.coverage_report", errors)
    if isinstance(coverage, dict):
        numeric_keys = coverage_keys - {"operation_counts", "is_complete"}
        for key in numeric_keys:
            value = coverage.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"$.coverage_report.{key}: expected non-negative integer")

        if all(isinstance(coverage.get(key), int) for key in (
            "processed_sources",
            "unchanged_sources",
            "excluded_sources",
            "blocked_sources",
            "unprocessed_sources",
            "total_sources",
        )):
            counted = sum(
                coverage[key]
                for key in (
                    "processed_sources",
                    "unchanged_sources",
                    "excluded_sources",
                    "blocked_sources",
                    "unprocessed_sources",
                )
            )
            if counted != coverage["total_sources"]:
                errors.append("$.coverage_report: source status counts must equal total_sources")

        expected_by_status = {
            "processed_sources": source_statuses["processed"],
            "unchanged_sources": source_statuses["unchanged"],
            "excluded_sources": source_statuses["excluded"],
            "blocked_sources": source_statuses["blocked"],
            "unprocessed_sources": source_statuses["unprocessed"],
        }
        for key, expected in expected_by_status.items():
            if coverage.get(key) != expected:
                errors.append(f"$.coverage_report.{key}: expected {expected} from source manifest")

        declared_counts = coverage.get("operation_counts")
        if not isinstance(declared_counts, dict):
            errors.append("$.coverage_report.operation_counts: expected object")
        else:
            for action in set(declared_counts) | set(operation_counts):
                if declared_counts.get(action, 0) != operation_counts.get(action, 0):
                    errors.append(
                        f"$.coverage_report.operation_counts.{action}: "
                        f"expected {operation_counts.get(action, 0)}"
                    )

        expected_complete = (
            source_statuses["blocked"] == 0
            and source_statuses["unprocessed"] == 0
        )
        if coverage.get("is_complete") is not expected_complete:
            errors.append(
                "$.coverage_report.is_complete: must be false when blocked or unprocessed sources exist"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("changeset", type=Path, help="Wiki changeset JSON file")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        data = json.loads(args.changeset.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors = validate_changeset(data)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"VALID: {data['changeset_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
