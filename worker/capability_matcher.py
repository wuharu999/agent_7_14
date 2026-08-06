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
