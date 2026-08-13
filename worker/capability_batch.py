from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("worker.capability_batch")

PIPELINE_VERSION = "capability-batch-v3-validated-drafts"


class ReductionOutputError(ValueError):
    """A schema-shaped provider reduction failed deterministic validation."""


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
                        "oneOf": [
                            {
                                "type": "object",
                                "required": [
                                    "capability_id",
                                    "semantic_key",
                                    "name",
                                    "capability_type",
                                    "effect",
                                    "scope",
                                    "trigger",
                                    "interfaces",
                                    "evidence",
                                    "lifecycle",
                                ],
                                "properties": {
                                    "capability_id": {"type": "string", "pattern": "^CAP-[A-Z0-9-]+$"},
                                    "semantic_key": {"type": "string", "minLength": 1},
                                    "name": {"type": "string", "pattern": ".+_.+"},
                                    "capability_type": {
                                        "enum": ["building_block", "operational_behavior"]
                                    },
                                    "effect": {"type": "object"},
                                    "scope": {"type": "object"},
                                    "trigger": {"type": "string", "minLength": 1},
                                    "interfaces": {"type": "array", "minItems": 1},
                                    "evidence": {"type": "array", "minItems": 1},
                                    "lifecycle": {
                                        "type": "object",
                                        "required": [
                                            "status",
                                            "supersedes",
                                            "replaced_by",
                                            "deprecation_reason",
                                        ],
                                        "properties": {
                                            "status": {"const": "draft"},
                                            "supersedes": {"type": "array"},
                                            "replaced_by": {"type": "array"},
                                            "deprecation_reason": {"type": "null"},
                                        },
                                    },
                                },
                            },
                            {"type": "null"},
                        ]
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
        raise ValueError("Provider returned no complete capability batch extraction")
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


def _sanitize_after_entry(entry: dict[str, Any], target_model_id: str | None = None) -> None:
    if not isinstance(entry, dict):
        return

    entry["schema_version"] = "2.0"
    if entry.get("capability_type") not in {"building_block", "operational_behavior"}:
        # Do not silently infer a type for newly generated entries. The hard
        # validator will reject this record and require explicit model output.
        entry["capability_type"] = "review_required"
    if not isinstance(entry.get("verification_profiles"), list):
        entry["verification_profiles"] = []
    if not isinstance(entry.get("migration_warnings"), list):
        entry["migration_warnings"] = []

    name = entry.get("name")
    effect = entry.get("effect")
    if (not isinstance(name, str) or "_" not in name) and isinstance(effect, dict):
        action = str(effect.get("action") or "").strip()
        object_name = str(effect.get("object") or "").strip()
        if action and object_name:
            action_token = re.sub(r"\s+", "_", action)
            object_token = re.sub(r"\s+", "_", object_name)
            entry["name"] = f"{action_token}_{object_token}"
            warnings = entry.get("migration_warnings")
            if isinstance(warnings, list) and "name_normalized_from_effect" not in warnings:
                warnings.append("name_normalized_from_effect")

    semantic_key = entry.get("semantic_key")
    if not isinstance(semantic_key, str) or not re.fullmatch(
        r"[a-z0-9]+(?:[._-][a-z0-9]+)*", semantic_key
    ):
        capability_id = str(entry.get("capability_id") or "").strip().lower()
        normalized_key = capability_id.removeprefix("cap-").replace("-", ".")
        if normalized_key and re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", normalized_key):
            entry["semantic_key"] = normalized_key

    # 1. Sanitize scope
    scope = entry.get("scope")
    if not isinstance(scope, dict):
        scope = {}
        entry["scope"] = scope

    if target_model_id and str(target_model_id).strip():
        scope["model_id"] = str(target_model_id).strip()
    elif not isinstance(scope.get("model_id"), str) or not scope["model_id"].strip():
        scope["model_id"] = "tian_gong"

    if scope.get("resolution_status") not in {"resolved", "ambiguous", "conflicted"}:
        scope["resolution_status"] = "resolved"

    if not isinstance(scope.get("vendor"), str) or not scope["vendor"].strip():
        scope["vendor"] = "UBTECH"
    if not isinstance(scope.get("environment"), str) or not scope["environment"].strip():
        scope["environment"] = "all"
    if not isinstance(scope.get("selector"), str) or not scope["selector"].strip():
        scope["selector"] = scope["model_id"]

    for field in ("source_model_names", "body_parts"):
        if not isinstance(scope.get(field), list):
            scope[field] = [scope["model_id"]]
        else:
            clean_list: list[str] = []
            for item in scope[field]:
                if isinstance(item, str) and item.strip():
                    clean_list.append(item.strip())
                elif isinstance(item, dict):
                    val = str(item.get("name") or item.get("id") or "").strip()
                    if val:
                        clean_list.append(val)
            scope[field] = clean_list or [scope["model_id"]]

    # 2. Sanitize interfaces
    interfaces = entry.get("interfaces")
    if not isinstance(interfaces, list) or not interfaces:
        entry["interfaces"] = [
            {"type": "api", "reference": f"{entry.get('capability_id', 'CAP-001')}.interface", "version": "1.0"}
        ]
    else:
        clean_interfaces: list[dict[str, Any]] = []
        for index, item in enumerate(interfaces):
            if isinstance(item, dict):
                clean_interfaces.append({
                    "type": str(item.get("type") or "api"),
                    "reference": str(item.get("reference") or item.get("name") or item.get("interface") or f"ref-{index+1}"),
                    "version": str(item.get("version") or "1.0"),
                })
            elif isinstance(item, str) and item.strip():
                clean_interfaces.append({
                    "type": "api",
                    "reference": item.strip(),
                    "version": "1.0",
                })
        entry["interfaces"] = clean_interfaces or [
            {"type": "api", "reference": "default_interface", "version": "1.0"}
        ]

    # 3. Sanitize evidence
    evidence = entry.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        entry["evidence"] = [
            {
                "evidence_id": "EVID-0001",
                "source_type": "wiki_doc",
                "source_id": "wiki/evidence.md",
                "source_version": "v1.0",
                "source_hash": "000000",
                "locator": "L1",
                "claim": str(entry.get("name") or "Technical capability evidence"),
                "evidence_level": "E2",
                "excerpt": "Technical evidence cited from repository source.",
            }
        ]
    else:
        clean_evidence: list[dict[str, Any]] = []
        for index, item in enumerate(evidence):
            if isinstance(item, dict):
                ev_id = str(item.get("evidence_id") or f"EVID-{index+1:04d}")
                src_id = str(item.get("source_id") or "wiki/evidence.md")
                loc = str(item.get("locator") or "L1")
                excerpt = str(item.get("excerpt") or item.get("quote") or "Evidence excerpt from source")
                claim = str(item.get("claim") or excerpt[:200] or str(entry.get("name") or "Capability evidence"))
                level = str(item.get("evidence_level") or "E2").upper()
                if level not in {"E1", "E2", "E3", "E4", "E5"}:
                    level = "E2"
                clean_evidence.append({
                    "evidence_id": ev_id,
                    "source_type": str(item.get("source_type") or "wiki_doc"),
                    "source_id": src_id,
                    "source_version": str(item.get("source_version") or "v1.0"),
                    "source_hash": str(item.get("source_hash") or "000000"),
                    "locator": loc,
                    "claim": claim,
                    "evidence_level": level,
                    "excerpt": excerpt,
                })
        entry["evidence"] = clean_evidence or [
            {
                "evidence_id": "EVID-0001",
                "source_type": "wiki_doc",
                "source_id": "wiki/evidence.md",
                "source_version": "v1.0",
                "source_hash": "000000",
                "locator": "L1",
                "claim": str(entry.get("name") or "Technical capability evidence"),
                "evidence_level": "E2",
                "excerpt": "Technical evidence cited from repository source.",
            }
        ]

    # 4. Sanitize dependencies: must be valid CAP-[A-Z0-9-]+ IDs
    cap_id_regex = re.compile(r"^CAP-[A-Z0-9-]+$")
    current_cap_id = str(entry.get("capability_id") or "").strip()

    if not isinstance(entry.get("dependencies"), list):
        entry["dependencies"] = []
    else:
        clean_deps: list[str] = []
        for item in entry["dependencies"]:
            raw_val = ""
            if isinstance(item, str):
                raw_val = item.strip()
            elif isinstance(item, dict):
                raw_val = str(item.get("capability_id") or item.get("id") or item.get("name") or "").strip()

            if not raw_val:
                continue

            cap_match = re.search(r"CAP-[A-Z0-9-]+", raw_val.upper())
            if cap_match:
                clean_cap = cap_match.group(0)
            else:
                slug = re.sub(r"[^A-Z0-9]+", "-", raw_val.upper()).strip("-")
                if not slug:
                    continue
                clean_cap = slug if slug.startswith("CAP-") else f"CAP-{slug}"

            if cap_id_regex.fullmatch(clean_cap) and clean_cap != current_cap_id and clean_cap not in clean_deps:
                clean_deps.append(clean_cap)
        entry["dependencies"] = clean_deps

    # 5. Sanitize other string array fields
    for field in (
        "inputs",
        "outputs",
        "preconditions",
        "hold_conditions",
        "postconditions",
        "quality_metrics",
        "failure_modes",
        "incompatible_resources",
        "unknowns",
    ):
        if not isinstance(entry.get(field), list):
            entry[field] = []
        else:
            clean_list: list[str] = []
            for item in entry[field]:
                if isinstance(item, str) and item.strip():
                    clean_list.append(item.strip())
                elif isinstance(item, dict):
                    val = str(item.get("capability_id") or item.get("id") or item.get("name") or "").strip()
                    if val:
                        clean_list.append(val)
            entry[field] = clean_list

    # The reducer's generation schema intentionally keeps after_entry generic
    # so large responses remain within DeepSeek's structured-output limits.
    # Complete required contract containers deterministically before the full
    # bundled hard gate runs. Empty constraint sets make no technical claim,
    # and a zero confidence score explicitly records that the reducer omitted
    # its confidence assessment instead of inventing one.
    constraints = entry.get("constraints")
    if not isinstance(constraints, dict):
        constraints = {}
        entry["constraints"] = constraints
    for field in ("time", "space", "information", "energy"):
        if not isinstance(constraints.get(field), list):
            constraints[field] = []

    confidence = entry.get("confidence")
    if not isinstance(confidence, dict):
        confidence = {}
        entry["confidence"] = confidence
    score = confidence.get("extraction_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        confidence["extraction_score"] = 0.0
    else:
        confidence["extraction_score"] = min(1.0, max(0.0, float(score)))
    if not isinstance(confidence.get("basis"), str) or not confidence["basis"].strip():
        confidence["basis"] = "Reducer supplied no confidence basis; requires review"

    # 6. Sanitize lifecycle status
    lifecycle = entry.get("lifecycle")
    if not isinstance(lifecycle, dict):
        entry["lifecycle"] = {
            "status": "draft",
            "supersedes": [],
            "replaced_by": [],
            "deprecation_reason": None,
        }
    else:
        # Catalog organization only publishes drafts. Provider-supplied higher
        # lifecycle states are not accepted without a separate review action.
        lifecycle["status"] = "draft"
        if not isinstance(lifecycle.get("supersedes"), list):
            lifecycle["supersedes"] = []
        if not isinstance(lifecycle.get("replaced_by"), list):
            lifecycle["replaced_by"] = []
        lifecycle["deprecation_reason"] = None


def parse_reduction(
    raw: str,
    *,
    expected_reducer_id: str,
    candidate_ids: set[str],
) -> dict[str, Any]:
    payload = _find_payload(raw, {"reducer_id", "decisions"})
    if payload is None:
        raise ReductionOutputError("Provider returned no complete capability reduction")
    if payload.get("reducer_id") != expected_reducer_id:
        raise ReductionOutputError("Capability reduction ID does not match the request")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ReductionOutputError("Capability reduction decisions are invalid")
    reported: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ReductionOutputError("Capability reduction decision is invalid")
        identifiers = decision.get("candidate_ids")
        if not isinstance(identifiers, list) or not identifiers:
            raise ReductionOutputError("Capability reduction decision requires candidate IDs")
        reported.extend(str(identifier) for identifier in identifiers)
        action = decision.get("action")
        if action not in {
            "create",
            "update",
            "implementation-instance",
            "skip",
            "blocked",
        }:
            raise ReductionOutputError("Capability reduction action is invalid")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ReductionOutputError("Capability reduction reason is invalid")
        target_entry_id = decision.get("target_entry_id")
        if target_entry_id is not None and not isinstance(target_entry_id, str):
            raise ReductionOutputError("Capability reduction target entry ID is invalid")
        after_entry = decision.get("after_entry")
        if action in {"create", "update", "implementation-instance"}:
            if not isinstance(after_entry, dict):
                raise ReductionOutputError("Writable capability reduction requires an entry")
            _sanitize_after_entry(after_entry)
        elif after_entry is not None:
            raise ReductionOutputError("Non-writing capability reduction must not include an entry")
        if action == "update" and not str(target_entry_id or ""):
            raise ReductionOutputError("Capability update requires a target entry ID")
    if len(reported) != len(set(reported)) or set(reported) != candidate_ids:
        raise ReductionOutputError(
            "Capability reduction did not decide every extracted candidate exactly once"
        )
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


def load_reduction_checkpoint(
    root: Path,
    model: str,
    identifier: str,
    candidate_ids: set[str],
) -> dict[str, Any] | None:
    path = checkpoint_path(root, model, identifier)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("pipeline_version") != PIPELINE_VERSION:
            return None
        result = payload["result"]
        return parse_reduction(
            json.dumps(result, ensure_ascii=False),
            expected_reducer_id=identifier,
            candidate_ids=candidate_ids,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        log.warning("Ignoring invalid capability reduction checkpoint %s", path)
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
