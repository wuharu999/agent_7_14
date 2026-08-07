from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from shared.team_names import normalize_team_name
from shared.capability_types import (
    CAPABILITY_TYPES,
    migrate_legacy_capability,
    required_capability_type,
)
from worker.config import get_team_config
from worker.claude_process import run_claude_process

log = logging.getLogger("worker.capability_matcher")

R_AND_D_CLASSIFICATION = "Operational behavior evidence required"

_BILINGUAL_EVIDENCE_CONCEPTS: dict[str, tuple[str, ...]] = {
    "locker": ("locker", "parcel locker", "快递柜", "储物柜", "柜门"),
    "parcel": ("parcel", "package", "包裹", "快递", "货物"),
    "retrieve": ("retrieve", "pick", "取件", "取出", "抓取"),
    "navigation": ("navigation", "navigate", "导航", "定位", "行走"),
    "lighting": ("lighting", "illumination", "light", "光照", "照明", "亮度"),
    "payload": ("payload", "weight", "load", "负载", "重量", "载荷"),
    "dimensions": ("dimensions", "width", "height", "depth", "尺寸", "宽度", "高度", "深度"),
    "grasp": ("grasp", "gripper", "manipulation", "抓取", "夹爪", "操作"),
    "collision": ("collision", "clearance", "碰撞", "避障", "间隙"),
    "balance": ("balance", "stability", "平衡", "稳定性"),
}

_STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
_GATE_SCHEMA = {
    "type": "object",
    "required": ["name", "status", "hard", "basis"],
    "properties": {
        "name": {"type": "string"},
        "status": {"enum": ["pass", "fail", "unknown", "not_applicable"]},
        "hard": {"type": "boolean"},
        "basis": {"type": "string"},
    },
    "additionalProperties": False,
}
_RD_GAP_SCHEMA = {
    "type": ["object", "null"],
    "required": ["classification", "domains", "person_weeks", "risk_factors"],
    "properties": {
        "classification": {"const": R_AND_D_CLASSIFICATION},
        "domains": _STRING_ARRAY,
        "person_weeks": {"type": "number", "minimum": 0},
        "risk_factors": _STRING_ARRAY,
    },
    "additionalProperties": False,
}

_COMPACT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "scenario_spec",
        "atomic_requirements",
        "capabilities",
        "feasibility_assessment",
    ],
    "properties": {
        "scenario_spec": {
            "type": "object",
            "required": [
                "scenario_id",
                "title",
                "business_goal",
                "target",
                "environment",
                "payload",
                "throughput",
                "assumptions",
                "unknowns",
            ],
            "properties": {
                "scenario_id": {"type": "string", "pattern": "^SCN-[A-Z0-9-]+$"},
                "title": {"type": "string"},
                "business_goal": {"type": "string"},
                "target": {"type": "string"},
                "environment": {"type": "string"},
                "payload": {"type": "string"},
                "throughput": {"type": "string"},
                "assumptions": _STRING_ARRAY,
                "unknowns": _STRING_ARRAY,
            },
            "additionalProperties": False,
        },
        "atomic_requirements": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "requirement_id",
                    "name",
                    "required_capability_type",
                    "effect",
                    "acceptance_criteria",
                    "constraints",
                    "dependencies",
                ],
                "properties": {
                    "requirement_id": {"type": "string", "pattern": "^REQ-[A-Z0-9-]+$"},
                    "name": {"type": "string"},
                    "required_capability_type": {"enum": sorted(CAPABILITY_TYPES)},
                    "effect": {"type": "string"},
                    "acceptance_criteria": _STRING_ARRAY,
                    "constraints": _STRING_ARRAY,
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "^REQ-[A-Z0-9-]+$"},
                    },
                },
                "additionalProperties": False,
            },
        },
        "capabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "capability_id",
                    "name",
                    "capability_type",
                    "effect",
                    "status",
                    "evidence_level",
                    "evidence_refs",
                    "verification_profiles",
                    "migration_warnings",
                ],
                "properties": {
                    "capability_id": {"type": "string", "pattern": "^CAP-[A-Z0-9-]+$"},
                    "name": {"type": "string"},
                    "capability_type": {"enum": sorted(CAPABILITY_TYPES)},
                    "effect": {"type": "string"},
                    "status": {"enum": ["draft", "reviewed", "verified", "deprecated"]},
                    "evidence_level": {"enum": ["E1", "E2", "E3", "E4", "E5"]},
                    "evidence_refs": _STRING_ARRAY,
                    "verification_profiles": {"type": "array", "items": {"type": "object"}},
                    "migration_warnings": _STRING_ARRAY,
                },
                "additionalProperties": False,
            },
        },
        "feasibility_assessment": {
            "type": "object",
            "required": [
                "assessment_id",
                "scenario_id",
                "capability_catalog_revision",
                "matches",
                "technical_conclusion",
                "deployment_conclusion",
                "deployment_gates",
                "rd_effort",
                "residual_risks",
                "next_experiment",
            ],
            "properties": {
                "assessment_id": {"type": "string", "pattern": "^ASM-[A-Z0-9-]+$"},
                "scenario_id": {"type": "string", "pattern": "^SCN-[A-Z0-9-]+$"},
                "capability_catalog_revision": {"type": "string"},
                "matches": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "match_id",
                            "requirement_id",
                            "capability_ids",
                            "gates",
                            "match_state",
                            "confidence",
                            "evidence_level",
                            "conditions",
                            "gaps",
                            "next_action",
                            "rd_gap",
                        ],
                        "properties": {
                            "match_id": {"type": "string", "pattern": "^MATCH-[A-Z0-9-]+$"},
                            "requirement_id": {"type": "string", "pattern": "^REQ-[A-Z0-9-]+$"},
                            "capability_ids": {
                                "type": "array",
                                "items": {"type": "string", "pattern": "^CAP-[A-Z0-9-]+$"},
                            },
                            "gates": {"type": "array", "minItems": 1, "items": _GATE_SCHEMA},
                            "match_state": {
                                "enum": [
                                    "verified_satisfied",
                                    "conditional",
                                    "partial",
                                    "composite",
                                    "unproven",
                                    "not_satisfied",
                                    "requirement_incomplete",
                                ]
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence_level": {"enum": ["E1", "E2", "E3", "E4", "E5"]},
                            "conditions": _STRING_ARRAY,
                            "gaps": _STRING_ARRAY,
                            "next_action": {"type": "string"},
                            "rd_gap": _RD_GAP_SCHEMA,
                        },
                        "additionalProperties": False,
                    },
                },
                "technical_conclusion": {
                    "enum": [
                        "feasible",
                        "feasible_with_conditions",
                        "prototype_required",
                        "currently_unproven",
                        "infeasible",
                    ]
                },
                "deployment_conclusion": {
                    "enum": [
                        "viable",
                        "viable_with_conditions",
                        "business_case_incomplete",
                        "not_viable",
                    ]
                },
                "deployment_gates": {"type": "array", "minItems": 1, "items": _GATE_SCHEMA},
                "rd_effort": {
                    "type": "object",
                    "required": ["total_person_weeks", "domains", "risk_factors"],
                    "properties": {
                        "total_person_weeks": {"type": "number", "minimum": 0},
                        "domains": _STRING_ARRAY,
                        "risk_factors": _STRING_ARRAY,
                    },
                    "additionalProperties": False,
                },
                "residual_risks": _STRING_ARRAY,
                "next_experiment": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def _rewrite_schema_refs(value: Any, prefix: str) -> Any:
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                rewritten[key] = f"#/$defs/{prefix}_{item.rsplit('/', 1)[-1]}"
            else:
                rewritten[key] = _rewrite_schema_refs(item, prefix)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_schema_refs(item, prefix) for item in value]
    return value


def _full_response_schema() -> dict[str, Any]:
    schema_root = Path(__file__).resolve().parents[1] / "shared" / "schemas"
    root_defs: dict[str, Any] = {}

    def install(filename: str, prefix: str) -> str:
        source = json.loads((schema_root / filename).read_text(encoding="utf-8"))
        local_defs = source.pop("$defs", {})
        source.pop("$schema", None)
        source.pop("$id", None)
        record_name = f"{prefix}_record"
        root_defs[record_name] = _rewrite_schema_refs(source, prefix)
        for name, definition in local_defs.items():
            root_defs[f"{prefix}_{name}"] = _rewrite_schema_refs(definition, prefix)
        return f"#/$defs/{record_name}"

    scenario_ref = install("scenario-spec.schema.json", "scenario")
    requirement_ref = install("atomic-requirement.schema.json", "requirement")
    capability_ref = install("atomic-capability.schema.json", "capability")
    feasibility_ref = install("feasibility-assessment.schema.json", "feasibility")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "scenario_spec",
            "atomic_requirements",
            "capabilities",
            "feasibility_assessment",
        ],
        "properties": {
            "scenario_spec": {"$ref": scenario_ref},
            "atomic_requirements": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": requirement_ref},
            },
            "capabilities": {
                "type": "array",
                "items": {"$ref": capability_ref},
            },
            "feasibility_assessment": {"$ref": feasibility_ref},
        },
        "$defs": root_defs,
        "additionalProperties": False,
    }


MATCHER_RESPONSE_SCHEMA = _full_response_schema()


def _skill_text(name: str) -> str:
    path = Path(__file__).resolve().parent / "skills" / name / "SKILL.md"
    return path.read_text(encoding="utf-8")


_MATCHER_REQUIRED_KEYS = {
    "scenario_spec",
    "atomic_requirements",
    "capabilities",
    "feasibility_assessment",
}


def _find_structured_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if _MATCHER_REQUIRED_KEYS <= value.keys():
            return value
        for key in ("structured_output", "result", "content"):
            found = _find_structured_payload(value.get(key))
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_structured_payload(item)
            if found is not None:
                return found
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith("```json") and candidate.endswith("```"):
            candidate = candidate[7:-3].strip()
        elif candidate.startswith("```") and candidate.endswith("```"):
            candidate = candidate[3:-3].strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return _find_structured_payload(parsed)
    return None


def _structured_payload(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Claude returned invalid feasibility JSON") from exc
    structured = _find_structured_payload(parsed)
    if structured is None:
        raise ValueError("Claude returned no complete structured feasibility result")
    return structured


def _nonnegative_number(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def enforce_evidence_contract_gate(payload: dict[str, Any]) -> dict[str, Any]:
    """Require end-to-end behavior evidence without inventing fixed R&D effort."""
    result = deepcopy(payload)
    requirements = {
        str(item.get("requirement_id") or ""): item
        for item in result.get("atomic_requirements", [])
        if isinstance(item, dict)
    }
    migrated_capabilities = [
        migrate_legacy_capability(item)
        for item in result.get("capabilities", [])
        if isinstance(item, dict)
    ]
    result["capabilities"] = [
        item for item in migrated_capabilities if item.get("record_type") != "solution_artifact"
    ]
    capabilities = {
        str(item.get("capability_id") or ""): item
        for item in result.get("capabilities", [])
        if isinstance(item, dict)
    }
    assessment = result.setdefault("feasibility_assessment", {})
    has_hard_gap = False

    for match in assessment.get("matches", []):
        if not isinstance(match, dict):
            continue
        requirement = requirements.get(str(match.get("requirement_id") or ""), {})
        required_type = required_capability_type(requirement)
        requirement["required_capability_type"] = required_type
        legacy_required = requirement.pop("required_abstraction_level", None)
        if legacy_required:
            requirement["legacy_required_abstraction_level"] = legacy_required
        selected = [
            capabilities[capability_id]
            for capability_id in match.get("capability_ids", [])
            if capability_id in capabilities
        ]
        only_building_blocks = bool(selected) and all(
            item.get("capability_type") == "building_block" for item in selected
        )
        operational_profiles = [
            profile
            for item in selected
            if item.get("capability_type") == "operational_behavior"
            for profile in item.get("verification_profiles", [])
            if isinstance(profile, dict)
            and profile.get("support_state") in {"supported", "conditional"}
        ]
        missing_operational_evidence = (
            required_type == "operational_behavior"
            and (only_building_blocks or not operational_profiles)
        )
        if not missing_operational_evidence:
            continue

        has_hard_gap = True
        gate = {
            "name": "Operational evidence and contract gate",
            "category": "evidence",
            "status": "unknown",
            "hard": True,
            "requirement_value": required_type,
            "capability_value": [
                str(item.get("capability_type") or "unclassified") for item in selected
            ],
            "margin": None,
            "evidence_refs": [
                str(reference)
                for item in selected
                for reference in item.get("evidence_refs", item.get("evidence", []))
                if isinstance(reference, str)
            ],
        }
        gates = [
            existing
            for existing in match.setdefault("gates", [])
            if not isinstance(existing, dict)
            or existing.get("name") != "Operational evidence and contract gate"
        ]
        gates.append(gate)
        match["gates"] = gates
        match["match_state"] = "unproven"
        match["confidence"] = min(_nonnegative_number(match.get("confidence")), 0.4)
        gap_text = R_AND_D_CLASSIFICATION
        gaps = [str(value) for value in match.setdefault("gaps", [])]
        if gap_text not in gaps:
            gaps.append(gap_text)
        match["gaps"] = gaps
        match["next_action"] = "Build or identify an operational behavior and validate it inside the required operating envelope."
        match["rd_gap"] = None

    rd_gaps = [
        match["rd_gap"]
        for match in assessment.get("matches", [])
        if isinstance(match, dict) and isinstance(match.get("rd_gap"), dict)
    ]
    assessment["rd_effort"] = {
        "total_person_weeks": round(
            sum(_nonnegative_number(gap.get("person_weeks")) for gap in rd_gaps),
            2,
        ),
        "domains": sorted(
            {str(domain) for gap in rd_gaps for domain in gap.get("domains", []) if str(domain)}
        ),
        "risk_factors": list(
            dict.fromkeys(
                str(risk)
                for gap in rd_gaps
                for risk in gap.get("risk_factors", [])
                if str(risk)
            )
        ),
    }
    if has_hard_gap:
        if assessment.get("technical_conclusion") in {"feasible", "feasible_with_conditions"}:
            assessment["technical_conclusion"] = "prototype_required"
        if assessment.get("deployment_conclusion") in {"viable", "viable_with_conditions"}:
            assessment["deployment_conclusion"] = "business_case_incomplete"
    return result


def enforce_abstraction_hard_gate(payload: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible name for callers during rolling upgrades."""
    return enforce_evidence_contract_gate(payload)


def load_model_capability_catalog(model_id: str) -> list[dict[str, Any]]:
    tc = get_team_config(model_id)
    base_target = tc.base_dir / "wiki" / "capabilities"
    model_dir = base_target / model_id
    search_dirs = [model_dir, base_target]
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for d in search_dirs:
        if d.is_dir() and not d.is_symlink():
            for path in sorted(d.glob("CAP-*.json")):
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        cap_id = str(data.get("capability_id") or path.stem)
                        if cap_id not in seen_ids:
                            seen_ids.add(cap_id)
                            migrated = migrate_legacy_capability(data)
                            if migrated.get("record_type") != "solution_artifact":
                                entries.append(migrated)
                except Exception:
                    continue
    return entries


def _scenario_evidence_query(scenario_state: dict[str, Any]) -> str:
    values: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            clean = value.strip()
            if clean:
                values.append(clean)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(str(value))
        elif isinstance(value, list):
            for item in value[:100]:
                collect(item)
        elif isinstance(value, dict):
            for item in list(value.values())[:100]:
                collect(item)

    for key in (
        "initial_intent",
        "goal",
        "workflow",
        "actors",
        "objects",
        "environment",
        "operating_profile",
        "allowed_modifications",
        "human_intervention",
        "acceptance_criteria",
        "facts",
        "assumptions",
        "requirements",
        "unresolved_issues",
    ):
        collect(scenario_state.get(key))
    return " ".join(values).casefold()[:50_000]


def retrieve_relevant_capability_evidence(
    scenario_state: dict[str, Any], model_id: str, *, limit: int = 12
) -> list[dict[str, Any]]:
    """Return bounded catalog evidence whose public fields overlap the scenario."""
    catalog = load_model_capability_catalog(normalize_team_name(model_id, allow_reserved=False))
    query = _scenario_evidence_query(scenario_state)
    terms = {
        token
        for token in re.findall(r"[\w-]+", query)
        if len(token) >= 4 and not token.isdigit()
    }
    query_concepts = {
        concept
        for concept, aliases in _BILINGUAL_EVIDENCE_CONCEPTS.items()
        if any(alias.casefold() in query for alias in aliases)
    }
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in catalog:
        profiles = [
            profile
            for profile in item.get("verification_profiles", [])
            if isinstance(profile, dict)
        ]
        searchable = " ".join(
            [
                str(item.get(key) or "")
                for key in (
                    "capability_id", "name", "effect", "description", "unknowns",
                    "aliases", "semantic_tags", "bilingual_tags",
                )
            ]
            + [json.dumps(profile, ensure_ascii=False) for profile in profiles]
        ).casefold()
        item_concepts = {
            concept
            for concept, aliases in _BILINGUAL_EVIDENCE_CONCEPTS.items()
            if any(alias.casefold() in searchable for alias in aliases)
        }
        direct_score = sum(1 for term in terms if term in searchable)
        concept_score = len(query_concepts & item_concepts)
        score = direct_score + (concept_score * 4)
        summary = {
            "capability_id": str(item.get("capability_id") or ""),
            "name": str(item.get("name") or ""),
            "capability_type": str(item.get("capability_type") or "unclassified"),
            "effect": str(item.get("effect") or ""),
            "status": str(item.get("status") or "draft"),
            "evidence_level": str(item.get("evidence_level") or ""),
            "verification_profiles": profiles[:4],
            "unknowns": [str(value) for value in item.get("unknowns", [])][:12],
            "migration_warnings": [
                str(value) for value in item.get("migration_warnings", [])
            ][:12],
            "bilingual_tags": sorted(item_concepts),
        }
        ranked.append((score, summary["capability_id"], summary))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    positively_ranked = [row[2] for row in ranked if row[0] > 0]
    return positively_ranked[: max(1, min(limit, 24))]


async def analyze_scenario(
    scenario_text: str,
    *,
    model_id: str,
    language: str = "en",
    evidence_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model = normalize_team_name(model_id, allow_reserved=False)
    if not scenario_text.strip():
        raise ValueError("Scenario text cannot be empty")

    catalog = evidence_context or load_model_capability_catalog(model)
    catalog_summary = (
        json.dumps(catalog, ensure_ascii=False, indent=2)
        if catalog
        else "No published capabilities in catalog yet."
    )

    system_prompt = (
        "You are the Robot Scenario Feasibility Compiler. Follow a strict TWO-STAGE evaluation strategy:\n"
        "STAGE 1 (Catalog Match): First, extract ScenarioSpec and atomic requirements, and check the pre-classified "
        "published atomic capability catalog provided below in the prompt.\n"
        "STAGE 2 (Wiki Fallback): If any required capability is missing, partial, or unverified in the catalog, "
        "use filesystem tools (Read/Grep/Glob) to search deep wiki evidence files ('wiki/') for uncataloged "
        "evidence. If verified in raw wiki files, cite the evidence; if missing after checking both catalog and wiki, "
        "classify as 'R&D Gap (Composite Skill Missing)'.\n\n"
        "Use exactly two capability types: building_block for a callable engineering primitive and "
        "operational_behavior for an independently testable end-to-end behavior. A building block may satisfy "
        "an interface requirement but cannot prove a customer behavior. Operational behavior support is scoped "
        "to an evidenced operating envelope. Missing operational evidence means prototype_required or "
        "currently_unproven; never invent a fixed effort estimate. Return only "
        "structured output in the requested schema.\n\n"
        "ENGINEER SCENARIO REQUIREMENTS SKILL:\n"
        f"{_skill_text('engineer-scenario-requirements')}\n\n"
        "ASSESS SCENARIO FEASIBILITY SKILL:\n"
        f"{_skill_text('assess-scenario-feasibility')}"
    )
    prompt = (
        f"Requested model_id: {model}\n"
        f"Requested report language: {language}\n\n"
        f"PRE-CLASSIFIED PUBLISHED CAPABILITY CATALOG FOR MODEL '{model}':\n"
        f"\"\"\"\n{catalog_summary}\n\"\"\"\n\n"
        "Customer scenario conversation:\n"
        f"{scenario_text.strip()}"
    )
    raw = await run_claude_process(
        prompt,
        team=model,
        system_prompt=system_prompt,
        json_schema=MATCHER_RESPONSE_SCHEMA,
        timeout=450,
    )
    payload = _structured_payload(raw)
    return enforce_evidence_contract_gate(payload)


_MULTI_TURN_GRILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["is_complete", "questions", "summary_if_complete"],
    "properties": {
        "is_complete": {"type": "boolean"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "question", "options"],
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "summary_if_complete": {"type": "string"},
    },
    "additionalProperties": False,
}


async def grill_scenario(
    scenario_text: str,
    *,
    model_id: str = "tian_gong",
    language: str = "en",
    history: list[dict[str, Any]] | None = None,
    accumulated_specs: dict[str, str] | None = None,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    # Rolling-upgrade adapter for an older ECS that still sends the legacy
    # command. New ECS sessions include a validated ScenarioState and are
    # handled directly by WorkerManager. Keep the old prompt below as dormant
    # rollback code until the real split ECS/Worker pair is validated.
    from shared.scenario_state import initial_state
    from worker.scenario_clarification import clarify_scenario

    temporary_id = "SCNSESSION-LEGACYADAPTER"
    temporary_state = initial_state(temporary_id, scenario_text)
    for item in history or []:
        if not isinstance(item, dict):
            continue
        semantic_key = str(item.get("semantic_key") or item.get("id") or "").strip()
        if not semantic_key:
            continue
        temporary_state["question_history"].append(
            {
                "question_id": str(item.get("question_id") or item.get("id") or semantic_key),
                "semantic_key": semantic_key,
                "question": str(item.get("question") or ""),
                "answer": str(item.get("answer") or item.get("value") or ""),
                "answer_mode": "custom",
                "resolution": "resolved",
                "state_version": 1,
            }
        )
    result = await clarify_scenario(
        temporary_state,
        model_id=model_id,
        language=language,
        user_message=json.dumps(accumulated_specs or {}, ensure_ascii=False),
    )
    questions = [
        {
            "id": str(item.get("semantic_key") or item.get("question_id") or ""),
            "question": str(item.get("question") or ""),
            "options": [str(value) for value in item.get("options", [])][:3],
        }
        for item in result.get("candidate_questions", [])
        if isinstance(item, dict)
    ]
    return {
        "status": result.get("status", "ok"),
        "questions": questions,
        "is_complete": not questions,
        "summary_if_complete": scenario_text if not questions else "",
    }

    # Dormant pre-V2 implementation retained only for a short rollback window.
    lang_instruction = (
        "Output all questions, options, and summaries in Simplified Chinese (zh-CN)."
        if language in ("zh-CN", "zh", "cn")
        else "Output all questions, options, and summaries in English."
    )

    specs_json = json.dumps(accumulated_specs or {}, indent=2, ensure_ascii=False)
    history_json = json.dumps(history or [], indent=2, ensure_ascii=False)

    prompt = f"""
You are an expert senior robotics systems architect conducting an ongoing, multi-turn "Grill Me" technical interview to turn a vague customer request into a detailed, rock-solid engineering specification.

Initial Customer Scenario Intent:
\"\"\"{scenario_text}\"\"\"

Accumulated Specifications & Parameters Clarified So Far:
{specs_json}

Previous Interview Q&A History:
{history_json}

Your Task:
Critically evaluate whether the technical scenario is fully specified and detailed enough to perform a precise feasibility match against repository capabilities.
You must systematically check ALL of the following technical dimensions for completeness. Do NOT mark as complete until the vast majority are clarified:

Environment & Workspace:
1. Specific environment type (e.g. automotive assembly line, semiconductor cleanroom, logistics warehouse, food processing plant, commercial restaurant, outdoor construction site, hospital, retail store, public exhibition)
2. Floor surface & terrain (e.g. polished concrete, epoxy-coated, steel grating, carpet, gravel, grass, wet/oily surfaces, anti-static flooring)
3. Workspace layout & obstacles (e.g. narrow aisles, conveyor belts, shelving racks, human foot traffic paths, doorways/thresholds, cable runs on floor)
4. Ambient conditions (e.g. temperature range, humidity, dust/particle level, lighting conditions, noise level, explosive atmosphere classification ATEX)

Mobility & Locomotion:
5. Movement requirements (e.g. stationary workstation, wheeled AGV on flat floor, biped walking, stair climbing, slope traversal, step-over obstacles, outdoor uneven terrain)
6. Navigation & mapping (e.g. pre-mapped fixed routes, SLAM dynamic navigation, follow-the-leader, GPS waypoints, visual landmark navigation)
7. Speed & cycle time requirements (e.g. maximum walking speed, task cycle time, throughput per hour, response time to events)

Manipulation & Payload:
8. Object types & properties (e.g. rigid/deformable, fragile/robust, transparent, reflective, wet/slippery, hot/cold, hazardous materials)
9. Payload weight & dimensions (e.g. max single-object mass, max volume, multi-object batch handling)
10. Gripper & end-effector requirements (e.g. parallel jaw, vacuum suction, soft adaptive gripper, tool changer, force-torque sensing precision)
11. Manipulation precision (e.g. placement accuracy ±mm, insertion tolerance, assembly force control, visual servoing alignment)

Task Workflow & Process (CRITICAL — ask MULTIPLE questions about this):
12. Exact task description step-by-step (e.g. "pick glass from conveyor → inspect for defects → place into shipping box" — break down every sub-step)
13. Source & destination of objects (e.g. where do items arrive from? conveyor belt, pallet, shelf, human handoff, random bin? Where do they go? shipping box, tray, rack, another conveyor?)
14. Sorting / grouping criteria (e.g. sort by size, color, SKU label, weight, defect status, destination address, product type)
15. Quality inspection requirements (e.g. visual defect detection, dimensional measurement, weight verification, barcode/QR scanning, label verification)
16. Packaging & shipping preparation (e.g. wrapping, cushioning/padding, box assembly, lid closing, labeling, palletizing, shrink-wrapping)
17. Task throughput & speed (e.g. items per minute, boxes per hour, orders per shift, peak vs. steady-state rate)
18. Error handling & exceptions (e.g. what happens when an object is dropped, broken, missing, wrong size? reject bin? human escalation? retry?)
19. Multi-step sequencing & tool changes (e.g. does the robot need to switch between tasks? pick-and-place then inspection? assembly then packaging?)
20. Object variety & SKU count (e.g. how many different object types? 1 uniform item, 5-10 variants, 100+ SKUs with different shapes/sizes?)
21. Upstream & downstream process dependencies (e.g. does the robot wait for a conveyor signal? is there a human handing items? does a downstream machine need synchronization?)

Connectivity & Control:
22. Network infrastructure (e.g. offline standalone, local Wi-Fi, industrial Ethernet, 5G private network, mesh network, cloud connectivity requirements)
23. Control interface & protocol (e.g. ROS2 topics, proprietary SDK API, REST/WebSocket, PLC integration, EtherCAT, OPC-UA)
24. Integration with existing systems (e.g. MES/ERP integration, conveyor synchronization, AGV fleet coordination, vision system handoff)

Safety & Compliance:
25. Human coexistence level (e.g. no humans in workspace, occasional human entry with lockout, continuous human-robot collaboration, child/public-accessible area)
26. Safety standards & certifications required (e.g. ISO 13849 PL-d, ISO 10218, ISO/TS 15066 collaborative, CE marking, UL certification)
27. Emergency stop & protective measures (e.g. E-stop button placement, LiDAR safety zones, light curtains, pressure-sensitive skin, speed/force limiting)

Power & Durability:
28. Power source & battery requirements (e.g. continuous AC tethered, battery runtime hours, hot-swap battery, charging dock location, solar/hybrid)
29. Operating schedule & duty cycle (e.g. single shift 8h, 24/7 continuous, intermittent on-demand, seasonal peaks)
30. Environmental durability (e.g. IP rating for water/dust, operating temperature range, vibration resistance, corrosion resistance)

Business & Deployment:
31. Quantity & scale (e.g. single prototype, pilot fleet of 3-5, production fleet of 50+, multi-site deployment)
32. Timeline & budget constraints (e.g. proof-of-concept timeline, production deployment deadline, budget range)
33. Maintenance & support requirements (e.g. on-site technician availability, remote monitoring, predictive maintenance, spare parts logistics)

Rules for your Decision:
- ALWAYS prioritize drilling into the Task Workflow & Process dimensions FIRST. If the user says "sort glasses" or "ship packages", you must ask detailed follow-ups about exact step-by-step workflow, source/destination, sorting criteria, throughput, error handling, and inspection BEFORE moving to other categories.
- If key boundary parameters remain vague or unclarified (e.g., user just said "factory" or "sort items" or only answered a few dimensions):
  1. Set `is_complete: false`
  2. Formulate 3 to 5 sharp, highly probing follow-up questions targeting the NEXT most critical unclarified dimensions.
  3. Provide 3 to 4 concrete, realistic selectable option choices for each question.
  4. Do NOT repeat questions already answered in the history.
- If the technical specification is already comprehensive and detailed across at least 20 of the above dimensions (or if 6+ turns of detailed parameters have been provided covering task workflow, environment, mobility, manipulation, safety, connectivity, and power):
  1. Set `is_complete: true`
  2. Set `summary_if_complete` to a comprehensive, professional technical summary of the fully refined scenario specification covering all clarified dimensions.

{lang_instruction}
Return ONLY valid JSON matching this schema:
{json.dumps(_MULTI_TURN_GRILL_SCHEMA, ensure_ascii=False)}
"""
    try:
        raw_output = await run_claude_process(
            prompt=prompt,
            timeout=60.0,
            extra_args=["--model", "haiku"],
            base_dir=base_dir,
        )
        data = json.loads(raw_output)
        if isinstance(data, dict):
            questions = data.get("questions") if isinstance(data.get("questions"), list) else []
            is_complete = bool(data.get("is_complete", False))
            summary_if_complete = str(data.get("summary_if_complete") or "")
            return {
                "status": "ok",
                "questions": questions,
                "is_complete": is_complete,
                "summary_if_complete": summary_if_complete,
            }
    except Exception as exc:
        log.exception("Grill scenario failed: %s", exc)

    is_zh = language in ("zh-CN", "zh", "cn")
    fallback_questions = [
        {
            "id": "q_factory_env",
            "question": "作业场地与工厂环境类型？" if is_zh else "What specific factory or environment type is this?",
            "options": ["汽车/重工车间" if is_zh else "Automotive/Heavy Machinery", "电子/半导体洁净室" if is_zh else "Electronics Cleanroom", "电商物流仓库" if is_zh else "Logistics Warehouse", "商业餐饮/公共场所" if is_zh else "Restaurant / Public Area"],
        },
        {
            "id": "q_mobility",
            "question": "机器人移动与地形履约要求？" if is_zh else "Does the robot need to walk or navigate around obstacles?",
            "options": ["固定工位/台面作业" if is_zh else "Static Station / Bench Top", "轮式平整地面移动" if is_zh else "Wheeled Flat Floor AGV", "双足跨越斜坡/障碍" if is_zh else "Biped Walking Over Slopes/Obstacles", "双足上下楼梯" if is_zh else "Biped Stair Climbing"],
        },
    ]
    return {"status": "ok", "questions": fallback_questions, "is_complete": False, "summary_if_complete": ""}
