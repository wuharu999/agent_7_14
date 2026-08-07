from __future__ import annotations

import json
import logging
from typing import Any

from shared.scenario_state import default_candidate_questions, select_question, validate_state
from worker.claude_process import run_claude_process


log = logging.getLogger("worker.scenario_clarification")

CLARIFICATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "state_patch",
        "candidate_questions",
        "candidate_issues",
        "intent",
        "model_readiness_opinion",
    ],
    "properties": {
        "state_patch": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "required": ["op", "path", "value"],
                "properties": {
                    "op": {"enum": ["set", "append", "upsert"]},
                    "path": {
                        "type": "string",
                        "pattern": "^(goal|workflow|environment|operating_profile|allowed_modifications|human_intervention)(\\.[A-Za-z0-9_]+){1,3}$|^(actors|objects|acceptance_criteria|facts|assumptions|requirements|unresolved_issues|candidate_solution_paths)$",
                    },
                    "value": {},
                },
                "additionalProperties": False,
            },
        },
        "candidate_questions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": [
                    "question_id",
                    "semantic_key",
                    "question",
                    "reason_for_asking",
                    "decision_impact",
                    "can_change_conclusion",
                    "blocking",
                    "target_owner",
                    "answer_type",
                    "options",
                    "prerequisite_keys",
                    "refines_question_id",
                ],
                "properties": {
                    "question_id": {"type": "string", "maxLength": 100},
                    "semantic_key": {"type": "string", "maxLength": 160},
                    "question": {"type": "string", "maxLength": 1200},
                    "reason_for_asking": {"type": "string", "maxLength": 800},
                    "decision_impact": {
                        "type": "array",
                        "items": {
                            "enum": ["safety", "feasibility", "architecture", "cost", "acceptance"]
                        },
                    },
                    "can_change_conclusion": {"type": "boolean"},
                    "blocking": {"type": "boolean"},
                    "target_owner": {
                        "enum": [
                            "customer",
                            "wiki",
                            "vendor",
                            "calculation",
                            "simulation",
                            "bench",
                            "pilot",
                            "field",
                        ]
                    },
                    "answer_type": {"const": "single_select_or_custom"},
                    "options": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {"type": "string", "maxLength": 400},
                    },
                    "prerequisite_keys": {"type": "array", "items": {"type": "string"}},
                    "refines_question_id": {"type": ["string", "null"]},
                    "previous_answer": {"type": ["string", "null"]},
                    "missing_precision": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
        },
        "candidate_issues": {"type": "array", "maxItems": 32},
        "intent": {
            "enum": [
                "requirement_answer",
                "report_question",
                "requirement_change",
                "new_scenario",
                "unclear",
            ]
        },
        "model_readiness_opinion": {
            "type": "object",
            "required": ["stable", "reason"],
            "properties": {"stable": {"type": "boolean"}, "reason": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

FOLLOWUP_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "reason", "proposed_change"],
    "properties": {
        "intent": {
            "enum": ["report_question", "requirement_change", "new_scenario", "unclear"]
        },
        "reason": {"type": "string", "maxLength": 400},
        "proposed_change": {"type": ["string", "null"], "maxLength": 5000},
    },
    "additionalProperties": False,
}


def _safe_response(payload: Any, state: dict[str, Any], language: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Clarification response must be an object")
    candidates = payload.get("candidate_questions", [])
    selected = select_question(state, candidates if isinstance(candidates, list) else [], language)
    return {
        "status": "ok",
        "state_patch": payload.get("state_patch", []) if isinstance(payload.get("state_patch"), list) else [],
        "candidate_questions": [selected] if selected else [],
        "candidate_issues": payload.get("candidate_issues", []) if isinstance(payload.get("candidate_issues"), list) else [],
        "intent": str(payload.get("intent") or "requirement_answer"),
        "model_readiness_opinion": payload.get("model_readiness_opinion")
        if isinstance(payload.get("model_readiness_opinion"), dict)
        else {"stable": False, "reason": "No model opinion was available."},
    }


async def clarify_scenario(
    scenario_state: dict[str, Any],
    *,
    model_id: str,
    language: str = "en",
    user_message: str = "",
    evidence_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Propose one bounded clarification turn; state mutation remains deterministic upstream."""
    validate_state(scenario_state)
    asked = [
        str(item.get("semantic_key") or "")
        for item in scenario_state.get("question_history", [])
        if isinstance(item, dict)
    ]
    prompt = (
        "Propose high-value scenario clarification questions. Ask only the customer-owned facts that can "
        "change safety, categorical feasibility, architecture, acceptance, or material cost. Do not ask the "
        "customer for robot specifications, SDK scope, repeatability, collision results, or other evidence "
        "owned by the Wiki, vendor, calculation, simulation, bench, pilot, or field test. Propose at most "
        "three answer options per question; the UI adds Other and I don't know. Do not repeat a resolved "
        "semantic key. State patches are advisory and must never replace the whole state.\n\n"
        f"Robot model: {model_id}\nLanguage: {language}\n"
        f"New user message: {user_message[:4000]}\n"
        f"Resolved or asked semantic keys: {json.dumps(asked, ensure_ascii=False)}\n"
        f"Scenario state: {json.dumps(scenario_state, ensure_ascii=False)[:30000]}\n"
        f"Approved evidence context: {json.dumps(evidence_context or [], ensure_ascii=False)[:12000]}"
    )
    try:
        raw = await run_claude_process(
            prompt,
            team=model_id,
            json_schema=CLARIFICATION_RESPONSE_SCHEMA,
            timeout=75,
            extra_args=["--model", "haiku"],
        )
        payload = json.loads(raw)
        return _safe_response(payload, scenario_state, language)
    except Exception:
        log.exception("Structured scenario clarification failed")
        selected = select_question(
            scenario_state,
            default_candidate_questions(scenario_state, language),
            language,
        )
        return {
            "status": "retryable",
            "state_patch": [],
            "candidate_questions": [selected] if selected else [],
            "candidate_issues": [],
            "intent": "requirement_answer",
            "model_readiness_opinion": {
                "stable": False,
                "reason": "The clarification model was unavailable; a deterministic question was selected.",
            },
        }


async def classify_followup_intent(
    scenario_state: dict[str, Any],
    *,
    model_id: str,
    language: str,
    user_message: str,
    report_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a post-report/in-analysis message without exposing model reasoning."""
    validate_state(scenario_state)
    prompt = (
        "Classify the user's message into exactly one product intent. A report_question asks about the "
        "existing analysis. A requirement_change changes a scenario fact, constraint, acceptance target, "
        "workflow, or assumption. A new_scenario is a materially separate use case. Use unclear when the "
        "message does not safely determine one class. Return a short user-safe reason, not chain of thought. "
        "For requirement_change, copy only the requested change into proposed_change; otherwise return null.\n\n"
        f"Robot model: {model_id}\nLanguage: {language}\n"
        f"Scenario state: {json.dumps(scenario_state, ensure_ascii=False)[:24000]}\n"
        f"Current report summary: {json.dumps(report_summary or {}, ensure_ascii=False)[:6000]}\n"
        f"User message: {user_message[:5000]}"
    )
    try:
        raw = await run_claude_process(
            prompt,
            team=model_id,
            json_schema=FOLLOWUP_INTENT_SCHEMA,
            timeout=45,
            extra_args=["--model", "haiku"],
        )
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("intent") not in {
            "report_question",
            "requirement_change",
            "new_scenario",
            "unclear",
        }:
            raise ValueError("Invalid follow-up classification")
        return {
            "status": "ok",
            "intent": str(payload["intent"]),
            "reason": str(payload.get("reason") or "")[:400],
            "proposed_change": (
                str(payload.get("proposed_change") or "")[:5000]
                if payload["intent"] == "requirement_change"
                else None
            ),
        }
    except Exception:
        log.exception("Structured follow-up intent classification failed")
        return {
            "status": "retryable",
            "intent": "unclear",
            "reason": "The message needs clarification before it can change the scenario.",
            "proposed_change": None,
        }
