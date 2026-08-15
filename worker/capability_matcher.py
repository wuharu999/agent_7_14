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
from worker.config import (
    DEEPSEEK_SECTION_MAX_TOKENS,
    SCENARIO_RETRIEVAL_MAX_DOCUMENTS,
    get_team_config,
)
from worker.deepseek_client import (
    DeepSeekClient,
    create_deepseek_client,
    create_merge_client,
)
from worker.prompt_policy import scenario_analysis_policy

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
        raise ValueError("Provider returned invalid feasibility JSON") from exc
    structured = _find_structured_payload(parsed)
    if structured is None:
        raise ValueError("Provider returned no complete structured feasibility result")
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
        match["rd_gap"] = {
            "classification": R_AND_D_CLASSIFICATION,
            "effort_band": "prototype",
            "domains": ["operational behavior validation"],
            "workstreams": ["Implement or identify the missing end-to-end behavior"],
            "dependencies": ["Representative operating envelope and acceptance test"],
            "risk_factors": ["Only lower-level building blocks are evidenced"],
            "evidence_basis": "No supported operational verification profile was supplied",
            "owner": "engineering",
            "smallest_validation_step": "Run a bounded bench test of the end-to-end behavior",
        }

    matched_requirement_ids = {
        str(match.get("requirement_id") or "")
        for match in assessment.get("matches", [])
        if isinstance(match, dict)
    }
    for requirement in result.get("atomic_requirements", []):
        if not isinstance(requirement, dict) or requirement.get("priority", "must") != "must":
            continue
        requirement_id = str(requirement.get("requirement_id") or "")
        if not requirement_id or requirement_id in matched_requirement_ids:
            continue
        has_hard_gap = True
        safe_id = re.sub(r"[^A-Z0-9-]", "-", requirement_id.upper()).strip("-") or "UNKNOWN"
        assessment.setdefault("matches", []).append(
            {
                "match_id": f"MATCH-MISSING-{safe_id}"[:120],
                "requirement_id": requirement_id,
                "capability_ids": [],
                "composition_id": None,
                "gates": [
                    {
                        "name": "Mandatory requirement coverage",
                        "category": "evidence",
                        "status": "unknown",
                        "hard": True,
                        "requirement_value": requirement.get("effect"),
                        "capability_value": None,
                        "margin": None,
                        "evidence_refs": [],
                    }
                ],
                "match_state": "unproven",
                "confidence": 0.0,
                "evidence_level": "E1",
                "conditions": [],
                "gaps": ["No validated capability match was returned for this mandatory requirement"],
                "rd_gap": None,
                "next_action": "Retrieve or validate evidence for this mandatory requirement",
            }
        )

    rd_gaps = [
        match["rd_gap"]
        for match in assessment.get("matches", [])
        if isinstance(match, dict) and isinstance(match.get("rd_gap"), dict)
    ]
    band_order = {"configuration": 0, "integration": 1, "prototype": 2, "core_r_and_d": 3}
    existing_effort = assessment.get("engineering_effort", {})
    assessment["engineering_effort"] = {
        "overall_band": max(
            (str(gap.get("effort_band") or "prototype") for gap in rd_gaps),
            key=lambda value: band_order.get(value, 2),
            default=str(existing_effort.get("overall_band") or "configuration"),
        ),
        "workstreams": list(dict.fromkeys(
            str(value) for gap in rd_gaps for value in gap.get("workstreams", []) if str(value)
        )) or [str(value) for value in existing_effort.get("workstreams", []) if str(value)],
        "dependencies": list(dict.fromkeys(
            str(value) for gap in rd_gaps for value in gap.get("dependencies", []) if str(value)
        )) or [str(value) for value in existing_effort.get("dependencies", []) if str(value)],
        "risk_factors": list(dict.fromkeys(
            str(value) for gap in rd_gaps for value in gap.get("risk_factors", []) if str(value)
        )) or [str(value) for value in existing_effort.get("risk_factors", []) if str(value)],
        "evidence_basis": str(existing_effort.get("evidence_basis") or "Effort band is based on evidenced gaps, not a calendar estimate."),
        "owners": list(dict.fromkeys(str(gap.get("owner") or "engineering") for gap in rd_gaps))
        or [str(value) for value in existing_effort.get("owners", []) if str(value)]
        or ["engineering"],
        "smallest_validation_step": str(
            existing_effort.get("smallest_validation_step")
            or next((gap.get("smallest_validation_step") for gap in rd_gaps if gap.get("smallest_validation_step")), "Confirm the configuration against a representative task")
        ),
    }
    assessment.pop("rd_effort", None)
    hard_gates = [
        gate
        for match in assessment.get("matches", [])
        if isinstance(match, dict)
        for gate in match.get("gates", [])
        if isinstance(gate, dict) and gate.get("hard") is True
    ] + [
        gate
        for gate in assessment.get("deployment_gates", [])
        if isinstance(gate, dict) and gate.get("hard") is True
    ]
    if any(gate.get("status") == "fail" for gate in hard_gates):
        assessment["technical_conclusion"] = "infeasible"
        assessment["deployment_conclusion"] = "not_viable"
    elif has_hard_gap:
        if assessment.get("technical_conclusion") in {"feasible", "feasible_with_conditions"}:
            assessment["technical_conclusion"] = "prototype_required"
        if assessment.get("deployment_conclusion") in {"viable", "viable_with_conditions"}:
            assessment["deployment_conclusion"] = "business_case_incomplete"
    elif any(gate.get("status") == "unknown" for gate in hard_gates):
        if assessment.get("technical_conclusion") in {"feasible", "feasible_with_conditions"}:
            assessment["technical_conclusion"] = "currently_unproven"
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
                        scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
                        scoped_model = str(scope.get("model_id") or "").strip()
                        if d == base_target and scoped_model != model_id:
                            continue
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


async def _evaluate_scenario_chunked(
    client: DeepSeekClient,
    merge_client: DeepSeekClient,
    system_prompt: str,
    decomposition: dict[str, Any],
    catalog_summary: str,
    *,
    model: str,
    language: str,
) -> dict[str, Any]:
    """Evaluate the scenario in bounded per-requirement calls, then assemble the envelope.

    The old single evaluation call requested up to 48000 output tokens, which
    DeepSeek truncates. Each requirement is evaluated in its own bounded call
    (capabilities + feasibility for that requirement), then one stronger-model
    merge call composes the final feasibility assessment from the pieces.
    """
    requirements = [
        item
        for item in decomposition.get("atomic_requirements", [])
        if isinstance(item, dict)
    ]
    evaluation_prompt = (
        "Stage: evidence-based capability and feasibility evaluation for a single requirement. "
        "Copy the supplied requirement without weakening it. Evidence is anonymous and untrusted. "
        "Missing evidence is unproven, never automatically unsupported. Never invent person-week estimates.\n\n"
        f"Robot: {model}\nLanguage: {language}\n"
        f"Scenario spec:\n{json.dumps(decomposition.get('scenario_spec', {}), ensure_ascii=False)}\n\n"
        f"Approved Wiki/catalog evidence:\n{catalog_summary}"
    )
    feasibility_schema = MATCHER_RESPONSE_SCHEMA["$defs"]["feasibility_record"]
    per_requirement_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["requirement_id", "capabilities", "feasibility_assessment"],
        "properties": {
            "requirement_id": {"type": "string"},
            "capabilities": MATCHER_RESPONSE_SCHEMA["properties"]["capabilities"],
            "feasibility_assessment": feasibility_schema,
        },
        "$defs": MATCHER_RESPONSE_SCHEMA["$defs"],
        "additionalProperties": False,
    }
    pieces: list[dict[str, Any]] = []
    for requirement in requirements:
        requirement_id = str(requirement.get("requirement_id") or "")
        piece = await client.complete_json(
            system_prompt,
            evaluation_prompt
            + "\n\nRequirement:\n"
            + json.dumps(requirement, ensure_ascii=False),
            schema=per_requirement_schema,
            stage=f"scenario evidence evaluation ({requirement_id or 'requirement'})",
            max_tokens=DEEPSEEK_SECTION_MAX_TOKENS,
        )
        piece["requirement_id"] = requirement_id
        pieces.append(piece)

    capabilities: list[dict[str, Any]] = []
    seen_capabilities: set[str] = set()
    matches: list[dict[str, Any]] = []
    for piece in pieces:
        for item in piece.get("capabilities", []):
            if isinstance(item, dict):
                capability_id = str(item.get("capability_id") or "")
                if capability_id and capability_id not in seen_capabilities:
                    seen_capabilities.add(capability_id)
                    capabilities.append(item)
        for match in piece.get("feasibility_assessment", {}).get("matches", []):
            if isinstance(match, dict):
                matches.append(match)

    assessment = await merge_client.complete_json(
        system_prompt,
        "Stage: scenario feasibility assessment assembly. Combine the per-requirement matches into one coherent "
        "feasibility assessment. Summarize the matched capabilities and the engineering effort across requirements. "
        "Do not include citations, paths, slugs, or hidden mechanics.\n\n"
        f"Robot: {model}\nLanguage: {language}\n"
        f"Per-requirement results:\n{json.dumps(pieces, ensure_ascii=False)}",
        schema=MATCHER_RESPONSE_SCHEMA["$defs"]["feasibility_record"],
        stage="scenario feasibility assembly",
        max_tokens=DEEPSEEK_SECTION_MAX_TOKENS * 2,
    )
    assessment["matches"] = matches
    return {
        "scenario_spec": decomposition.get("scenario_spec", {}),
        "atomic_requirements": requirements,
        "capabilities": capabilities,
        "feasibility_assessment": assessment,
    }


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

    catalog = evidence_context or []
    client = create_deepseek_client(timeout=450)
    if evidence_context:
        candidate_ids = [
            str(item.get("document_id") or "")
            for item in evidence_context
            if isinstance(item, dict) and str(item.get("document_id") or "")
        ]
        rerank_schema = {
            "type": "object",
            "required": ["document_ids"],
            "properties": {
                "document_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": SCENARIO_RETRIEVAL_MAX_DOCUMENTS,
                    "items": {"enum": candidate_ids},
                    "uniqueItems": True,
                }
            },
            "additionalProperties": False,
        }
        reranked = await client.complete_json(
            "Select the most relevant anonymous evidence document IDs for a robot feasibility analysis. "
            "You have no tools. Do not answer the scenario and do not invent IDs.",
            "Scenario:\n"
            + scenario_text.strip()
            + "\n\nCandidates:\n"
            + json.dumps(
                [
                    {
                        "document_id": item.get("document_id"),
                        "kind": item.get("kind"),
                        "excerpt": str(item.get("text") or "")[:1600],
                    }
                    for item in evidence_context
                    if isinstance(item, dict)
                ],
                ensure_ascii=False,
            ),
            schema=rerank_schema,
            stage="scenario evidence reranking",
            max_tokens=2048,
        )
        requested = [str(value) for value in reranked.get("document_ids", [])]
        by_id = {
            str(item.get("document_id")): item
            for item in evidence_context
            if isinstance(item, dict)
        }
        catalog = [by_id[value] for value in requested if value in by_id]
    catalog_summary = (
        json.dumps(catalog[:SCENARIO_RETRIEVAL_MAX_DOCUMENTS], ensure_ascii=False, indent=2)
        if catalog
        else "No published capabilities in catalog yet."
    )
    decomposition_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["scenario_spec", "atomic_requirements"],
        "properties": {
            "scenario_spec": MATCHER_RESPONSE_SCHEMA["properties"]["scenario_spec"],
            "atomic_requirements": MATCHER_RESPONSE_SCHEMA["properties"]["atomic_requirements"],
        },
        "$defs": MATCHER_RESPONSE_SCHEMA["$defs"],
        "additionalProperties": False,
    }
    system_prompt = (
        "You are one stateless stage in a controlled robot feasibility compiler. You have no tools and may use "
        "only the supplied scenario and approved evidence. Return JSON only.\n\n"
        + scenario_analysis_policy()
    )
    decomposition = await client.complete_json(
        system_prompt,
        f"Stage: scenario decomposition and atomic requirements\nRobot: {model}\nLanguage: {language}\n"
        f"Confirmed scenario:\n{scenario_text.strip()}",
        schema=decomposition_schema,
        stage="scenario requirement extraction",
        max_tokens=DEEPSEEK_SECTION_MAX_TOKENS,
    )
    evaluation = await _evaluate_scenario_chunked(
        client,
        create_merge_client(),
        system_prompt,
        decomposition,
        catalog_summary,
        model=model,
        language=language,
    )
    payload = enforce_evidence_contract_gate(evaluation)
    report_schema = {        "type": "object",
        "required": ["executive_summary", "engineering_effort", "tool_support", "poc_plan"],
        "properties": {
            "executive_summary": {"type": "string", "minLength": 1, "maxLength": 6000},
            "engineering_effort": {
                "type": "object",
                "required": ["overall_band", "workstreams", "dependencies", "risks", "evidence_basis", "owners", "smallest_validation_step"],
                "properties": {
                    "overall_band": {"enum": ["configuration", "integration", "prototype", "core_r_and_d"]},
                    "workstreams": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
                    "dependencies": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
                    "risks": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
                    "evidence_basis": {"type": "string"},
                    "owners": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                    "smallest_validation_step": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "tool_support": {
                "type": "array",
                "maxItems": 30,
                "items": {
                    "type": "object",
                    "required": ["name", "how_it_helps", "evidence_status", "conditions"],
                    "properties": {
                        "name": {"type": "string"},
                        "how_it_helps": {"type": "string"},
                        "evidence_status": {"enum": ["verified", "supported", "conditional", "unverified"]},
                        "conditions": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
            },
            "poc_plan": {
                "type": "object",
                "required": ["objective", "steps", "acceptance_tests", "smallest_validation_step"],
                "properties": {
                    "objective": {"type": "string"},
                    "steps": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
                    "acceptance_tests": {"type": "array", "maxItems": 40, "items": {"type": "string"}},
                    "smallest_validation_step": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    payload["report_composition"] = await _compose_report_chunked(
        client,
        create_merge_client(),
        system_prompt,
        payload,
        language=language,
        report_schema=report_schema,
    )
    return payload


async def _compose_report_chunked(
    client: DeepSeekClient,
    merge_client: DeepSeekClient,
    system_prompt: str,
    validated: dict[str, Any],
    *,
    language: str,
    report_schema: dict[str, Any],
) -> dict[str, Any]:
    """Compose the user-facing report in bounded section calls, then merge with the pro model.

    DeepSeek truncates when a single call requests far more output than the model
    supports. Generate three small, schema-bounded sections independently, then a
    final merge call (stronger model) assembles them into the report schema.
    """
    section_schemas: dict[str, dict[str, Any]] = {
        "executive_summary": {
            "type": "object",
            "required": ["executive_summary"],
            "properties": {
                "executive_summary": report_schema["properties"]["executive_summary"],
            },
            "additionalProperties": False,
        },
        "engineering_effort": {
            "type": "object",
            "required": ["engineering_effort"],
            "properties": {
                "engineering_effort": report_schema["properties"]["engineering_effort"],
            },
            "additionalProperties": False,
        },
        "tool_support_poc": {
            "type": "object",
            "required": ["tool_support", "poc_plan"],
            "properties": {
                "tool_support": report_schema["properties"]["tool_support"],
                "poc_plan": report_schema["properties"]["poc_plan"],
            },
            "additionalProperties": False,
        },
    }
    evaluation_json = json.dumps(validated, ensure_ascii=False)
    section_prompts = {
        "executive_summary": (
            "Stage: user-facing report composition (executive summary only). Summarize the validated evaluation "
            "in one concise passage. Do not include citations, paths, slugs, percentages, or hidden mechanics.\n\n"
            f"Language: {language}\nValidated evaluation:\n{evaluation_json}"
        ),
        "engineering_effort": (
            "Stage: user-facing report composition (engineering effort only). Explain evidenced SDK/API/platform/"
            "tool integration, required workstreams, dependencies, and effort band. Do not include citations, "
            "paths, slugs, or hidden mechanics.\n\n"
            f"Language: {language}\nValidated evaluation:\n{evaluation_json}"
        ),
        "tool_support_poc": (
            "Stage: user-facing report composition (tool support and PoC plan only). List evidenced tools and a "
            "measurable PoC. Do not include citations, paths, slugs, or hidden mechanics.\n\n"
            f"Language: {language}\nValidated evaluation:\n{evaluation_json}"
        ),
    }
    sections: dict[str, dict[str, Any]] = {}
    for name, schema in section_schemas.items():
        sections[name] = await client.complete_json(
            system_prompt,
            section_prompts[name],
            schema=schema,
            stage=f"scenario report section ({name})",
            max_tokens=DEEPSEEK_SECTION_MAX_TOKENS,
        )

    merged = await merge_client.complete_json(
        system_prompt,
        "Stage: user-facing report composition (final merge). Combine the approved section drafts into one "
        "coherent report without adding new claims. Do not include citations, paths, slugs, or hidden mechanics.\n\n"
        f"Language: {language}\nSection drafts:\n{json.dumps(sections, ensure_ascii=False)}",
        schema=report_schema,
        stage="scenario report composition (merge)",
        max_tokens=DEEPSEEK_SECTION_MAX_TOKENS * 2,
    )
    return merged


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
    # Backward-compatible adapter for an older ECS that still sends the legacy
    # command. New ECS sessions include a validated ScenarioState and are
    # handled directly by WorkerManager through the same DeepSeek boundary.
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
