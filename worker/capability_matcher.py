from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from shared.team_names import normalize_team_name
from worker.config import get_team_config
from worker.claude_process import run_claude_process

log = logging.getLogger("worker.capability_matcher")

ABSTRACTION_LEVELS = (
    "L0_primitive_driver",
    "L1_atomic_skill",
    "L2_composite_skill",
    "L3_scenario_module",
)
R_AND_D_CLASSIFICATION = "R&D Gap (Composite Skill Missing)"

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
                    "required_abstraction_level",
                    "effect",
                    "acceptance_criteria",
                    "constraints",
                    "dependencies",
                ],
                "properties": {
                    "requirement_id": {"type": "string", "pattern": "^REQ-[A-Z0-9-]+$"},
                    "name": {"type": "string"},
                    "required_abstraction_level": {
                        "enum": list(ABSTRACTION_LEVELS[1:])
                    },
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
                    "abstraction_level",
                    "effect",
                    "status",
                    "evidence_level",
                    "evidence_refs",
                ],
                "properties": {
                    "capability_id": {"type": "string", "pattern": "^CAP-[A-Z0-9-]+$"},
                    "name": {"type": "string"},
                    "abstraction_level": {"enum": list(ABSTRACTION_LEVELS)},
                    "effect": {"type": "string"},
                    "status": {"enum": ["draft", "reviewed", "verified", "deprecated"]},
                    "evidence_level": {"enum": ["E1", "E2", "E3", "E4", "E5"]},
                    "evidence_refs": _STRING_ARRAY,
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


def enforce_abstraction_hard_gate(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the non-negotiable L0 gate after the model-generated assessment."""
    result = deepcopy(payload)
    requirements = {
        str(item.get("requirement_id") or ""): item
        for item in result.get("atomic_requirements", [])
        if isinstance(item, dict)
    }
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
        required_level = str(requirement.get("required_abstraction_level") or "L1_atomic_skill")
        selected = [
            capabilities[capability_id]
            for capability_id in match.get("capability_ids", [])
            if capability_id in capabilities
        ]
        only_l0 = bool(selected) and all(
            item.get("abstraction_level") == "L0_primitive_driver" for item in selected
        )
        if required_level == "L0_primitive_driver" or not only_l0:
            continue

        has_hard_gap = True
        gate = {
            "name": "Abstraction layering hard gate",
            "category": "composition",
            "status": "fail",
            "hard": True,
            "requirement_value": required_level,
            "capability_value": [
                str(item.get("abstraction_level") or "unknown") for item in selected
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
            or existing.get("name") != "Abstraction layering hard gate"
        ]
        gates.append(gate)
        match["gates"] = gates
        match["match_state"] = "not_satisfied"
        match["confidence"] = min(_nonnegative_number(match.get("confidence")), 0.25)
        gap_text = R_AND_D_CLASSIFICATION
        gaps = [str(value) for value in match.setdefault("gaps", [])]
        if gap_text not in gaps:
            gaps.append(gap_text)
        match["gaps"] = gaps
        match["next_action"] = "Engineer and validate an L1/L2 composite skill before deployment."
        if not isinstance(match.get("rd_gap"), dict):
            match["rd_gap"] = {
                "classification": R_AND_D_CLASSIFICATION,
                "domains": ["Composite Skill Engineering"],
                "person_weeks": 2.0,
                "risk_factors": ["No evidence-backed composite skill currently exists"],
            }

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
                            entries.append(data)
                except Exception:
                    continue
    return entries


async def analyze_scenario(
    scenario_text: str,
    *,
    model_id: str,
    language: str = "en",
) -> dict[str, Any]:
    model = normalize_team_name(model_id, allow_reserved=False)
    if not scenario_text.strip():
        raise ValueError("Scenario text cannot be empty")

    catalog = load_model_capability_catalog(model)
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
        "Respect pre-assigned abstraction levels L0-L3. L0 driver/API primitives NEVER satisfy L1/L2/L3 requirements. "
        "If only L0 support exists, classify as 'R&D Gap (Composite Skill Missing)'. "
        "For each R&D gap, estimate person-weeks using technical domains such as Vision AI, "
        "Precision Force Control, and Bi-manual Coordination, and state concrete risks. Return only "
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
    return enforce_abstraction_hard_gate(payload)


_GRILL_SCENARIO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["questions"],
    "properties": {
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
        }
    },
    "additionalProperties": False,
}


async def grill_scenario(
    scenario_text: str,
    *,
    model_id: str = "tian_gong",
    language: str = "en",
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    lang_instruction = (
        "Output all questions and options in Simplified Chinese (zh-CN)."
        if language in ("zh-CN", "zh", "cn")
        else "Output all questions and options in English."
    )
    prompt = f"""
You are a senior robotics systems architect conducting a "Grill Me" requirements-clarification interview for a customer scenario.

Customer Scenario Description:
\"\"\"{scenario_text}\"\"\"

Target Robot Model ID: {model_id}

Identify the 4 to 6 most critical missing technical parameters or boundary conditions (e.g. payload weight, speed, terrain/slopes, environmental conditions, ROS2 topic interface requirements, human safety distance, battery runtime).

For each question:
1. Provide a concise, probing question.
2. Provide 3 to 4 realistic, selectable options (or concrete parameter choices).

{lang_instruction}
Return ONLY valid JSON matching this schema:
{json.dumps(_GRILL_SCENARIO_SCHEMA, ensure_ascii=False)}
"""
    try:
        raw_output = await run_claude_process(
            prompt=prompt,
            timeout=60.0,
            extra_args=["--model", "haiku"],
            base_dir=base_dir,
        )
        data = json.loads(raw_output)
        if isinstance(data, dict) and isinstance(data.get("questions"), list) and data["questions"]:
            return {"status": "ok", "questions": data["questions"]}
    except Exception as exc:
        log.exception("Grill scenario failed: %s", exc)

    is_zh = language in ("zh-CN", "zh", "cn")
    fallback_questions = [
        {
            "id": "q1",
            "question": "目标负载质量与尺寸要求？" if is_zh else "What is the maximum payload mass and dimension requirement?",
            "options": ["< 5 kg", "5 kg - 15 kg", "15 kg - 30 kg", "自定义 / > 30 kg"] if is_zh else ["< 5 kg", "5 kg - 15 kg", "15 kg - 30 kg", "Custom / > 30 kg"],
        },
        {
            "id": "q2",
            "question": "作业地面与地形环境？" if is_zh else "What is the operating terrain and environment?",
            "options": ["平整室内混凝土地面" if is_zh else "Flat indoor concrete", "包含斜坡/坡道" if is_zh else "Includes ramps/slopes", "室外/不平整地面" if is_zh else "Outdoor/uneven terrain"],
        },
        {
            "id": "q3",
            "question": "接口与控制协议要求？" if is_zh else "What interface and control protocol is required?",
            "options": ["ROS2 话题接口 (/cmd_vel, /tf)" if is_zh else "ROS2 Topics (/cmd_vel, /tf)", "C++ / Python SDK API", "REST API / WebSockets"],
        },
        {
            "id": "q4",
            "question": "人员共存与安全防护要求？" if is_zh else "What safety and human-coexistence requirements apply?",
            "options": ["无人员共存（封闭区域）" if is_zh else "No humans (enclosed zone)", "人机共存（急停与LiDAR减速）" if is_zh else "Human coexistence (LiDAR & E-stop)", "最高等级 ISO 13849 工业安全" if is_zh else "ISO 13849 Industrial safety"],
        },
    ]
    return {"status": "ok", "questions": fallback_questions}
