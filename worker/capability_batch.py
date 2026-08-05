from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("worker.capability_batch")

PIPELINE_VERSION = "capability-batch-v1"


@dataclass(frozen=True)
class EvidenceUnit:
    unit_id: str
    source_id: str
    part_index: int
    part_count: int
    content: str
    content_sha256: str
    size_bytes: int


BATCH_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "batch_id",
        "sources",
        "candidates",
        "non_capability_candidates",
    ],
    "properties": {
        "batch_id": {"type": "string", "minLength": 8},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "unit_id",
                    "source_id",
                    "status",
                    "reason",
                    "extracted_claims",
                ],
                "properties": {
                    "unit_id": {"type": "string", "minLength": 1},
                    "source_id": {"type": "string", "minLength": 1},
                    "status": {
                        "enum": ["processed", "excluded", "blocked"]
                    },
                    "reason": {"type": "string"},
                    "extracted_claims": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "candidate_id",
                    "name",
                    "semantic_key",
                    "effect",
                    "trigger",
                    "interface_reference",
                    "body_parts",
                    "environment",
                    "evidence",
                    "unknowns",
                ],
                "properties": {
                    "candidate_id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 3},
                    "semantic_key": {"type": "string", "minLength": 1},
                    "effect": {
                        "type": "object",
                        "required": ["action", "object", "observable_result"],
                        "properties": {
                            "action": {"type": "string", "minLength": 1},
                            "object": {"type": "string", "minLength": 1},
                            "observable_result": {"type": "string", "minLength": 3},
                        },
                        "additionalProperties": False,
                    },
                    "trigger": {"type": "string"},
                    "interface_reference": {"type": "string"},
                    "body_parts": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "environment": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "unit_id",
                                "source_id",
                                "locator",
                                "claim",
                                "excerpt",
                            ],
                            "properties": {
                                "unit_id": {"type": "string", "minLength": 1},
                                "source_id": {"type": "string", "minLength": 1},
                                "locator": {"type": "string", "minLength": 1},
                                "claim": {"type": "string", "minLength": 3},
                                "excerpt": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "unknowns": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                "additionalProperties": False,
            },
        },
        "non_capability_candidates": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}

REDUCTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reducer_id", "decisions"],
    "properties": {
        "reducer_id": {"type": "string", "minLength": 8},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "candidate_ids",
                    "action",
                    "target_entry_id",
                    "reason",
                    "after_entry",
                ],
                "properties": {
                    "candidate_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                    "action": {
                        "enum": [
                            "create",
                            "update",
                            "implementation-instance",
                            "skip",
                            "blocked",
                        ]
                    },
                    "target_entry_id": {"type": ["string", "null"]},
                    "reason": {"type": "string", "minLength": 3},
                    "after_entry": {
                        "oneOf": [{"type": "object"}, {"type": "null"}]
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _split_text(text: str, max_bytes: int) -> list[str]:
    if max_bytes < 1:
        raise ValueError("Evidence unit size must be positive")
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in text:
        character_bytes = len(character.encode("utf-8"))
        if current and current_bytes + character_bytes > max_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += character_bytes
    if current:
        chunks.append("".join(current))
    return chunks


def load_evidence_units(
    wiki_root: Path,
    relative_paths: list[str],
    *,
    max_unit_bytes: int,
) -> list[EvidenceUnit]:
    units: list[EvidenceUnit] = []
    for relative_path in sorted(relative_paths):
        path = wiki_root / relative_path
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Wiki evidence file is unavailable: {relative_path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        parts = _split_text(text, max_unit_bytes)
        source_id = f"wiki/{relative_path}"
        for index, content in enumerate(parts, start=1):
            unit_id = (
                source_id
                if len(parts) == 1
                else f"{source_id}#part={index}/{len(parts)}"
            )
            encoded = content.encode("utf-8")
            units.append(
                EvidenceUnit(
                    unit_id=unit_id,
                    source_id=source_id,
                    part_index=index,
                    part_count=len(parts),
                    content=content,
                    content_sha256=hashlib.sha256(encoded).hexdigest(),
                    size_bytes=len(encoded),
                )
            )
    return units


def partition_evidence_units(
    units: list[EvidenceUnit], *, max_batch_bytes: int
) -> list[list[EvidenceUnit]]:
    if max_batch_bytes < 1:
        raise ValueError("Capability batch size must be positive")
    batches: list[list[EvidenceUnit]] = []
    current: list[EvidenceUnit] = []
    current_bytes = 0
    for unit in units:
        estimated_bytes = unit.size_bytes + len(unit.unit_id.encode("utf-8")) + 256
        if current and current_bytes + estimated_bytes > max_batch_bytes:
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(unit)
        current_bytes += estimated_bytes
    if current:
        batches.append(current)
    return batches


def batch_id(model: str, units: list[EvidenceUnit]) -> str:
    digest = hashlib.sha256()
    digest.update(PIPELINE_VERSION.encode("utf-8"))
    digest.update(model.encode("utf-8"))
    for unit in units:
        digest.update(unit.unit_id.encode("utf-8"))
        digest.update(unit.content_sha256.encode("ascii"))
    return f"CB-{digest.hexdigest()[:20].upper()}"


def batch_prompt_payload(units: list[EvidenceUnit]) -> str:
    payload = [
        {
            "unit_id": unit.unit_id,
            "source_id": unit.source_id,
            "part_index": unit.part_index,
            "part_count": unit.part_count,
            "content_sha256": unit.content_sha256,
            "content": unit.content,
        }
        for unit in units
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _find_payload(value: Any, required: set[str]) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if required <= value.keys():
            return value
        for key in ("structured_output", "result", "content"):
            found = _find_payload(value.get(key), required)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_payload(item, required)
            if found is not None:
                return found
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith("```json") and candidate.endswith("```"):
            candidate = candidate[7:-3].strip()
        elif candidate.startswith("```") and candidate.endswith("```"):
            candidate = candidate[3:-3].strip()
        try:
            return _find_payload(json.loads(candidate), required)
        except json.JSONDecodeError:
            return None
    return None


def parse_batch_extraction(
    raw: str,
    *,
    expected_batch_id: str,
    units: list[EvidenceUnit],
) -> dict[str, Any]:
    required = {"batch_id", "sources", "candidates", "non_capability_candidates"}
    payload = _find_payload(raw, required)
    if payload is None:
        raise ValueError("Claude returned no complete capability batch extraction")
    if payload.get("batch_id") != expected_batch_id:
        raise ValueError("Capability batch response ID does not match the request")
    sources = payload.get("sources")
    candidates = payload.get("candidates")
    if not isinstance(sources, list) or not isinstance(candidates, list):
        raise ValueError("Capability batch response arrays are invalid")
    expected = {unit.unit_id: unit for unit in units}
    reported_ids = [str(item.get("unit_id") or "") for item in sources if isinstance(item, dict)]
    if len(reported_ids) != len(set(reported_ids)) or set(reported_ids) != set(expected):
        raise ValueError("Capability batch response did not account for every evidence unit")
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError("Capability batch source report is invalid")
        unit = expected[str(item["unit_id"])]
        if item.get("source_id") != unit.source_id:
            raise ValueError("Capability batch source ID does not match its evidence unit")
        status = item.get("status")
        if status not in {"processed", "excluded", "blocked"}:
            raise ValueError("Capability batch source status is invalid")
        extracted_claims = item.get("extracted_claims")
        if (
            not isinstance(extracted_claims, int)
            or isinstance(extracted_claims, bool)
            or extracted_claims < 0
        ):
            raise ValueError("Capability batch extracted claim count is invalid")
        if status in {"excluded", "blocked"} and not str(item.get("reason") or "").strip():
            raise ValueError("Excluded or blocked evidence requires a reason")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Capability batch candidate is invalid")
        required_candidate_fields = {
            "candidate_id",
            "name",
            "semantic_key",
            "effect",
            "trigger",
            "interface_reference",
            "body_parts",
            "environment",
            "evidence",
            "unknowns",
        }
        if not required_candidate_fields <= candidate.keys():
            raise ValueError("Capability batch candidate is incomplete")
        if any(
            not isinstance(candidate.get(field), str)
            for field in (
                "candidate_id",
                "name",
                "semantic_key",
                "trigger",
                "interface_reference",
                "environment",
            )
        ):
            raise ValueError("Capability batch candidate text fields are invalid")
        effect = candidate.get("effect")
        if not isinstance(effect, dict) or any(
            not isinstance(effect.get(field), str) or not effect[field].strip()
            for field in ("action", "object", "observable_result")
        ):
            raise ValueError("Capability batch candidate effect is invalid")
        for field in ("body_parts", "unknowns"):
            values = candidate.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f"Capability batch candidate {field} is invalid")
        evidence = candidate.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Capability candidates require evidence")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("Capability candidate evidence is invalid")
            unit_id = str(item.get("unit_id") or "")
            if unit_id not in expected:
                raise ValueError("Capability candidate cites evidence outside its batch")
            if item.get("source_id") != expected[unit_id].source_id:
                raise ValueError("Capability candidate evidence source mismatch")
            if any(
                not isinstance(item.get(field), str)
                for field in ("unit_id", "source_id", "locator", "claim", "excerpt")
            ):
                raise ValueError("Capability candidate evidence fields are invalid")
            if not str(item.get("locator") or "").strip() or not str(
                item.get("claim") or ""
            ).strip():
                raise ValueError("Capability candidate evidence locator and claim are required")
    non_capability = payload.get("non_capability_candidates")
    if not isinstance(non_capability, int) or isinstance(non_capability, bool) or non_capability < 0:
        raise ValueError("Capability batch non-capability count is invalid")
    return payload


def normalize_candidate_ids(identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
    for index, candidate in enumerate(payload.get("candidates") or [], start=1):
        candidate["candidate_id"] = f"{identifier}-C{index:04d}"
    return payload


def parse_reduction(
    raw: str,
    *,
    expected_reducer_id: str,
    candidate_ids: set[str],
) -> dict[str, Any]:
    payload = _find_payload(raw, {"reducer_id", "decisions"})
    if payload is None:
        raise ValueError("Claude returned no complete capability reduction")
    if payload.get("reducer_id") != expected_reducer_id:
        raise ValueError("Capability reduction ID does not match the request")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("Capability reduction decisions are invalid")
    reported: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("Capability reduction decision is invalid")
        identifiers = decision.get("candidate_ids")
        if not isinstance(identifiers, list) or not identifiers:
            raise ValueError("Capability reduction decision requires candidate IDs")
        reported.extend(str(identifier) for identifier in identifiers)
        action = decision.get("action")
        if action not in {
            "create",
            "update",
            "implementation-instance",
            "skip",
            "blocked",
        }:
            raise ValueError("Capability reduction action is invalid")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ValueError("Capability reduction reason is invalid")
        target_entry_id = decision.get("target_entry_id")
        if target_entry_id is not None and not isinstance(target_entry_id, str):
            raise ValueError("Capability reduction target entry ID is invalid")
        after_entry = decision.get("after_entry")
        if action in {"create", "update", "implementation-instance"}:
            if not isinstance(after_entry, dict):
                raise ValueError("Writable capability reduction requires an entry")
        elif after_entry is not None:
            raise ValueError("Non-writing capability reduction must not include an entry")
        if action == "update" and not str(target_entry_id or ""):
            raise ValueError("Capability update requires a target entry ID")
    if len(reported) != len(set(reported)) or set(reported) != candidate_ids:
        raise ValueError("Capability reduction did not decide every extracted candidate exactly once")
    return payload


def checkpoint_path(root: Path, model: str, identifier: str) -> Path:
    return root / ".agent1-worker" / "capability-batch-cache" / model / f"{identifier}.json"


def load_checkpoint(
    root: Path,
    model: str,
    identifier: str,
    units: list[EvidenceUnit],
) -> dict[str, Any] | None:
    path = checkpoint_path(root, model, identifier)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = payload["result"]
        if payload.get("pipeline_version") != PIPELINE_VERSION:
            return None
        return parse_batch_extraction(
            json.dumps(result, ensure_ascii=False),
            expected_batch_id=identifier,
            units=units,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        log.warning("Ignoring invalid capability batch checkpoint %s", path)
        return None


def save_checkpoint(
    root: Path,
    model: str,
    identifier: str,
    result: dict[str, Any],
) -> None:
    path = checkpoint_path(root, model, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "pipeline_version": PIPELINE_VERSION,
                "batch_id": identifier,
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def aggregate_source_reports(
    relative_paths: list[str],
    units: list[EvidenceUnit],
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    reports_by_unit: dict[str, dict[str, Any]] = {}
    for result in results:
        for report in result["sources"]:
            reports_by_unit[str(report["unit_id"])] = report
    units_by_source: dict[str, list[EvidenceUnit]] = {}
    for unit in units:
        units_by_source.setdefault(unit.source_id, []).append(unit)
    sources: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for relative_path in sorted(relative_paths):
        source_id = f"wiki/{relative_path}"
        source_units = units_by_source[source_id]
        reports = [reports_by_unit[unit.unit_id] for unit in source_units]
        statuses = {str(report["status"]) for report in reports}
        if "blocked" in statuses:
            status = "blocked"
        elif "processed" in statuses:
            status = "processed"
        else:
            status = "excluded"
        reasons = sorted(
            {
                str(report.get("reason") or "").strip()
                for report in reports
                if str(report.get("reason") or "").strip()
            }
        )
        source: dict[str, Any] = {
            "source_id": source_id,
            "version": None,
            "hash_or_revision": hashlib.sha256(
                "".join(unit.content_sha256 for unit in source_units).encode("ascii")
            ).hexdigest(),
            "status": status,
        }
        if status in {"excluded", "blocked"}:
            source["reason"] = "; ".join(reasons) or "No usable atomic capability evidence"
        sources.append(source)
        totals[status] += 1
        totals["extracted_claims"] += sum(
            int(report.get("extracted_claims") or 0) for report in reports
        )
    totals["non_capability_candidates"] = sum(
        int(result.get("non_capability_candidates") or 0) for result in results
    )
    return sources, dict(totals)
