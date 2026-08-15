from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ecs.app.auth import current_session, require_user, verify_csrf
from ecs.app.config import ALLOWED_TEAMS, SCENARIO_REANALYSIS_POLL_SECONDS
from ecs.app.database import (
    append_scenario_event,
    clear_pending_scenario_reanalysis,
    claim_scenario_session,
    create_scenario_analysis_job_record,
    create_scenario_session,
    create_scenario_share_link,
    finalize_scenario_analysis_report,
    get_allowed_teams,
    get_robot_options,
    get_active_scenario_analysis_job,
    get_pending_scenario_reanalysis_version,
    get_scenario_analysis_job,
    get_scenario_report_revision,
    get_scenario_session,
    get_scenario_share_link_by_hash,
    get_scenario_state_version,
    has_current_scenario_report_for_version,
    list_pending_scenario_reanalyses,
    list_user_scenario_sessions,
    list_scenario_events,
    list_scenario_report_revisions,
    mark_scenario_analysis_failed,
    mark_scenario_reanalysis_pending,
    revoke_scenario_share_link,
    save_scenario_state_version,
    supersede_current_scenario_report,
    touch_scenario_share_link,
    update_scenario_analysis_job_record,
    update_scenario_session_status,
)
from ecs.app.gateway import gateway
from ecs.app.languages import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from ecs.app.web_paths import rooted_path
from shared.scenario_state import (
    apply_answer,
    apply_state_patch,
    attach_next_question,
    default_candidate_questions,
    evaluate_state,
    initial_state,
    new_identifier,
    scenario_narrative,
    utc_now,
    validate_state,
)
from worker.analysis_progress import progress_event


router = APIRouter()
log = logging.getLogger("ecs.scenario_sessions")
PIPELINE_VERSION = "scenario-v2.0"


@dataclass(frozen=True)
class _AnalysisTaskRegistration:
    task: asyncio.Task[None]
    attempt_count: int


_analysis_tasks: dict[str, _AnalysisTaskRegistration] = {}
_reanalysis_dispatch_lock = asyncio.Lock()


class _ScenarioMutationLimiter:
    def __init__(self) -> None:
        self.history: dict[str, list[float]] = defaultdict(list)

    def allowed(self, request: Request, action: str) -> bool:
        client = request.client.host if request.client else "unknown"
        key = f"{client}:{action}"
        now = time.monotonic()
        values = [value for value in self.history[key] if now - value < 3600]
        minute_limit = 10 if action == "create" else 60
        hour_limit = 50 if action == "create" else 300
        if len(values) >= hour_limit or sum(1 for value in values if now - value < 60) >= minute_limit:
            self.history[key] = values
            return False
        values.append(now)
        self.history[key] = values
        return True


mutation_limiter = _ScenarioMutationLimiter()


def _rate_limited(request: Request, action: str) -> JSONResponse | None:
    if mutation_limiter.allowed(request, action):
        return None
    return JSONResponse({"error": "Scenario request rate limit exceeded"}, status_code=429)


class CreateSessionRequest(BaseModel):
    initial_intent: str = Field(min_length=3, max_length=20_000)
    model_id: str = Field(default="auto", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    language: str = Field(default=DEFAULT_LANGUAGE, max_length=16)
    conversation_id: str = Field(default="", max_length=200)


class AnswerRequest(BaseModel):
    expected_state_version: int = Field(ge=1)
    question_id: str = Field(min_length=3, max_length=100)
    answer_mode: Literal["option", "custom", "unknown"]
    answer: str = Field(default="", max_length=5000)


class AnalyzeNowRequest(BaseModel):
    expected_state_version: int = Field(ge=1)
    trigger: Literal["user_requested_early", "automatic_stability"] = "user_requested_early"


class MessageRequest(BaseModel):
    expected_state_version: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=5000)


class ConfirmChangeRequest(BaseModel):
    expected_state_version: int = Field(ge=1)
    confirmed: bool
    proposed_change: str = Field(min_length=1, max_length=5000)


class ShareRequest(BaseModel):
    report_revision_id: str | None = Field(default=None, max_length=100)
    bind_current: bool = False
    expires_in_hours: int | None = Field(default=168, ge=1, le=24 * 365)


def _model_aliases(robot: dict[str, str]) -> set[str]:
    aliases: set[str] = set()
    for field in ("name", "english_name", "chinese_name"):
        value = str(robot.get(field) or "").strip().casefold()
        if not value:
            continue
        aliases.add(value)
        aliases.add(value.replace("_", " ").replace("-", " "))
    return {alias for alias in aliases if len(alias) >= 2}


def _contains_alias(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9 _-]+", alias):
        pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return alias in text


def _selection_terms(text: str) -> set[str]:
    normalized = text.casefold()
    terms = set(re.findall(r"[a-z0-9]{3,}", normalized))
    for sequence in re.findall(r"[\u3400-\u9fff]+", normalized):
        for width in (2, 3, 4):
            terms.update(
                sequence[index : index + width]
                for index in range(max(0, len(sequence) - width + 1))
            )
    return terms


def _select_scenario_model(initial_intent: str, requested_model: str) -> dict[str, str]:
    allowed = get_allowed_teams()
    if not allowed:
        raise ValueError("No robot models are available")
    if requested_model != "auto":
        if requested_model not in allowed:
            raise ValueError("Unknown robot model")
        return {
            "model_id": requested_model,
            "mode": "customer_selected",
            "reason": "The customer selected this robot.",
        }

    options = [
        robot
        for robot in get_robot_options(include_description=True)
        if robot.get("name") in allowed
    ]
    by_name = {str(robot["name"]): robot for robot in options}
    text = initial_intent.casefold()
    explicit_matches: list[tuple[int, str]] = []
    for model_id in allowed:
        robot = by_name.get(model_id, {"name": model_id})
        matching = [alias for alias in _model_aliases(robot) if _contains_alias(text, alias)]
        if matching:
            explicit_matches.append((max(len(alias) for alias in matching), model_id))
    if explicit_matches:
        explicit_matches.sort(key=lambda item: (-item[0], allowed.index(item[1])))
        return {
            "model_id": explicit_matches[0][1],
            "mode": "named_in_scenario",
            "reason": "The scenario explicitly names this robot.",
        }

    scenario_terms = _selection_terms(text)
    scores: list[tuple[int, int, str]] = []
    for position, model_id in enumerate(allowed):
        robot = by_name.get(model_id, {"name": model_id, "description": ""})
        description_terms = _selection_terms(str(robot.get("description") or ""))
        score = len(scenario_terms & description_terms)
        scores.append((score, -position, model_id))
    best = max(scores)
    if best[0] > 0:
        return {
            "model_id": best[2],
            "mode": "scenario_fit",
            "reason": "The robot was selected from the scenario and available robot metadata.",
        }

    configured_default = next((model for model in ALLOWED_TEAMS if model in allowed), allowed[0])
    return {
        "model_id": configured_default,
        "mode": "configured_default",
        "reason": "No robot was named, so the configured default robot was selected.",
    }


@router.get("/api/scenario-sessions")
async def list_sessions_route(request: Request) -> JSONResponse:
    account = require_user(request)
    sessions = await asyncio.to_thread(
        list_user_scenario_sessions, int(account["user_id"]), 50
    )
    return JSONResponse(
        {"status": "ok", "sessions": [_public_session(item) for item in sessions]}
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in session.items()
        if key not in {"owner_user_id", "anonymous_token_hash", "deleted_at"}
    }


def _resume_token(
    header_token: str, query_token: str = ""
) -> str:
    return header_token or query_token


def _authorize(
    request: Request,
    session_id: str,
    resume_token: str,
    *,
    csrf_token: str = "",
    mutation: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    scenario = get_scenario_session(session_id)
    if scenario is None:
        raise LookupError("Scenario session not found")
    account = current_session(request)
    owner_id = scenario.get("owner_user_id")
    if owner_id is not None:
        if account is None or int(account["user_id"]) != int(owner_id):
            raise PermissionError("Scenario session belongs to another account")
        if mutation:
            verify_csrf(account, csrf_token)
        return scenario, account
    expected_hash = str(scenario.get("anonymous_token_hash") or "")
    supplied_hash = _token_hash(resume_token) if resume_token else ""
    if not expected_hash or not hmac.compare_digest(expected_hash, supplied_hash):
        raise PermissionError("A valid anonymous resume token is required")
    return scenario, account


def _authorization_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, LookupError):
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse({"error": str(exc)}, status_code=403)


def _append_event(
    session_id: str, event_type: str, payload: dict[str, Any], *, public: bool = False
) -> int:
    return append_scenario_event(
        event_id=new_identifier("EVT"),
        session_id=session_id,
        event_type=event_type,
        payload=payload,
        public=public,
    )


def _normalize_worker_questions(result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw in result.get("candidate_questions", result.get("questions", [])):
        if not isinstance(raw, dict):
            continue
        semantic_key = str(raw.get("semantic_key") or raw.get("id") or "").strip()
        question_text = str(raw.get("question") or "").strip()
        options = [str(value) for value in raw.get("options", []) if str(value).strip()][:3]
        if not semantic_key or not question_text or not options:
            continue
        candidates.append(
            {
                "question_id": str(raw.get("question_id") or new_identifier("Q")),
                "semantic_key": semantic_key,
                "question": question_text,
                "reason_for_asking": str(
                    raw.get("reason_for_asking")
                    or "This answer can change feasibility, architecture, or validation."
                ),
                "decision_impact": raw.get("decision_impact") or ["feasibility"],
                "can_change_conclusion": bool(raw.get("can_change_conclusion", True)),
                "blocking": bool(raw.get("blocking", False)),
                "target_owner": str(raw.get("target_owner") or "customer"),
                "answer_type": "single_select_or_custom",
                "options": options,
                "prerequisite_keys": raw.get("prerequisite_keys") or [],
                "refines_question_id": raw.get("refines_question_id"),
                "previous_answer": raw.get("previous_answer"),
                "missing_precision": raw.get("missing_precision"),
            }
        )
    return candidates


def _apply_advisory_state_patches(
    state: dict[str, Any], patches: list[dict[str, Any]]
) -> dict[str, Any]:
    updated = state
    for patch in patches[:32]:
        try:
            updated = apply_state_patch(updated, [patch])
        except ValueError as exc:
            log.warning(
                "Discarded invalid advisory scenario patch for path %s: %s",
                str(patch.get("path") or "")[:120],
                exc,
            )
    return updated


async def _candidate_questions(
    state: dict[str, Any], *, model_id: str, language: str, user_message: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    deterministic = default_candidate_questions(state, language)
    if not gateway.online:
        return deterministic, [], [], False
    try:
        result = await gateway.command(
            "grill_scenario",
            timeout=60,
            scenario_text=str(state.get("initial_intent") or ""),
            model_id=model_id,
            language=language,
            history=state.get("question_history", []),
            accumulated_specs={
                "goal": str(state.get("goal", {}).get("normalized_value") or ""),
                "workflow": scenario_narrative(state),
            },
            scenario_state=state,
            user_message=user_message,
            retrieve_evidence_context=True,
        )
        candidates = _normalize_worker_questions(result)
        issues = [
            issue
            for issue in result.get("candidate_issues", [])
            if isinstance(issue, dict)
            and issue.get("owner") in {
                "wiki", "vendor", "calculation", "simulation", "bench", "pilot", "field"
            }
        ]
        patches = [
            patch for patch in result.get("state_patch", []) if isinstance(patch, dict)
        ][:32]
        return candidates + deterministic, issues, patches, result.get("status") == "ok"
    except Exception:
        log.exception("Worker clarification failed; using deterministic candidates")
        return deterministic, [], [], False


def _conclusion_from_legacy(result: dict[str, Any]) -> str:
    feasibility = result.get("feasibility_assessment", {})
    technical = str(feasibility.get("technical_conclusion") or "").lower()
    deployment = str(feasibility.get("deployment_conclusion") or "").lower()
    if technical in {"not_feasible", "not_a_fit"}:
        return "not_a_fit"
    if technical == "prototype_required":
        return "prototype_required"
    if technical == "feasible" and deployment == "viable":
        return "fit"
    if technical in {"feasible", "feasible_with_conditions"}:
        return "fit_with_conditions"
    return "insufficient_evidence"


_PRIVATE_REPORT_TEXT_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/|\.{1,2}/)?(?:[^\s\[\]()<>]+[\\/])*[^\s\[\]()<>]+\.md",
    re.IGNORECASE,
)
_ABSOLUTE_REPORT_PATH_RE = re.compile(
    r"(?:(?:/home|/root|/tmp|/Users)/[^\s\]\[()<>]+|[A-Za-z]:[\\/][^\s\]\[()<>]+)",
    re.IGNORECASE,
)


def _sanitize_report_value(value: Any, *, key: str = "") -> Any:
    if key in {
        "evidence_refs",
        "evidence_locator",
        "relative_path",
        "source_path",
        "wiki_entry",
        "locator",
        "document_id",
        "digest",
        "confidence",
    }:
        return [] if isinstance(value, list) else None
    if isinstance(value, dict):
        return {name: _sanitize_report_value(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_report_value(item, key=key) for item in value]
    if isinstance(value, str):
        value = _PRIVATE_REPORT_TEXT_RE.sub("[internal reference removed]", value)
        return _ABSOLUTE_REPORT_PATH_RE.sub("[internal reference removed]", value)
    return value


def _criterion_text(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)
    metric = str(value.get("metric") or "Acceptance criterion")
    operator = str(value.get("operator") or "")
    expected = value.get("value")
    unit = str(value.get("unit") or "")
    test_method = str(value.get("test_method") or "")
    threshold = " ".join(part for part in (operator, str(expected), unit) if part)
    return f"{metric}: {threshold}" + (f"; test: {test_method}" if test_method else "")


def _report_from_worker(
    *,
    session_id: str,
    report_revision_id: str,
    state: dict[str, Any],
    job: dict[str, Any],
    result: dict[str, Any],
    partial: bool,
) -> dict[str, Any]:
    result = _sanitize_report_value(result)
    feasibility = result.get("feasibility_assessment", {})
    composition = result.get("report_composition", {})
    matches = feasibility.get("matches", []) if isinstance(feasibility, dict) else []
    conditions = [
        str(condition)
        for match in matches
        if isinstance(match, dict)
        for condition in match.get("conditions", [])
        if str(condition)
    ]
    capabilities = result.get("capabilities", [])
    evidence = [
        {
            "capability_id": str(item.get("capability_id") or ""),
            "name": str(item.get("name") or "Capability evidence"),
            "support_state": str(item.get("support_state") or item.get("status") or "unproven"),
            "capability_type": str(item.get("capability_type") or "unclassified"),
        }
        for item in capabilities
        if isinstance(item, dict)
    ]
    unknowns = [
        {
            "name": str(item.get("semantic_key") or item.get("issue_id") or "Unresolved condition"),
            "owner": str(item.get("owner") or "customer"),
            "next_action": str(item.get("next_action") or "Confirm or validate"),
            "can_change_conclusion": bool(item.get("can_change_conclusion")),
        }
        for item in state.get("unresolved_issues", [])
        if isinstance(item, dict) and item.get("status", "open") == "open"
    ]
    next_actions = [
        {
            "owner": item["owner"],
            "action": item["next_action"],
        }
        for item in unknowns
    ]
    next_experiment = str(feasibility.get("next_experiment") or "Run a bounded bench or pilot validation")
    if not next_actions:
        next_actions.append({"owner": "bench", "action": next_experiment})
    return {
        "report_revision_id": report_revision_id,
        "session_id": session_id,
        "scenario_state_version": int(state["state_version"]),
        "status": "partial" if partial else "current",
        "conclusion": _conclusion_from_legacy(result),
        "executive_summary": str(composition.get("executive_summary") or ""),
        "conditions": list(dict.fromkeys(conditions)),
        "main_evidence": evidence,
        "high_impact_unknowns": unknowns,
        "next_actions": next_actions,
        "next_experiment": next_experiment,
        "confirmed_scenario": result.get("scenario_spec", {}),
        "atomic_requirements": result.get("atomic_requirements", []),
        "capability_evaluation": feasibility.get("matches", []),
        "engineering_effort": feasibility.get("engineering_effort", composition.get("engineering_effort", {})),
        "tool_support": composition.get("tool_support", []),
        "preconditions_and_risks": {
            "deployment_gates": feasibility.get("deployment_gates", []),
            "residual_risks": feasibility.get("residual_risks", []),
        },
        "poc_plan": composition.get("poc_plan", {}),
        "acceptance_tests": list(
            dict.fromkeys(
                [
                    _criterion_text(criterion)
                    for requirement in result.get("atomic_requirements", [])
                    if isinstance(requirement, dict)
                    for criterion in requirement.get("acceptance_criteria", [])
                    if str(criterion)
                ]
                + [
                    str(criterion)
                    for criterion in composition.get("poc_plan", {}).get("acceptance_tests", [])
                    if str(criterion)
                ]
            )
        ),
        "sections": {
            "validation_assumptions": state.get("assumptions", []),
            "customer_environment": state.get("environment", {}),
            "requirements": result.get("atomic_requirements", []),
            "implementation_workflow": state.get("workflow", {}),
            "environment_modifications": state.get("allowed_modifications", {}),
            "risks": feasibility.get("residual_risks", []),
        },
        "technical_details": result,
        "metadata": {
            "analysis_job_id": job["job_id"],
            "catalog_revision": job["catalog_revision"],
            "evidence_revision": str(result.get("evidence_revision") or job["evidence_revision"]),
            "pipeline_version": job["pipeline_version"],
            "language": job["language"],
            "created_at": utc_now(),
        },
    }


def _approved_report_question_context(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "conclusion": str(content.get("conclusion") or "insufficient_evidence")[:120],
        "executive_summary": str(content.get("executive_summary") or "")[:6000],
        "conditions": [str(value)[:1000] for value in content.get("conditions", [])[:20]],
        "main_evidence": [
            {
                "name": str(item.get("name") or "Evidence")[:500],
                "support_state": str(item.get("support_state") or "unproven")[:80],
                "capability_type": str(item.get("capability_type") or "unclassified")[:80],
            }
            for item in content.get("main_evidence", [])[:30]
            if isinstance(item, dict)
        ],
        "high_impact_unknowns": [
            {
                "name": str(item.get("name") or "Unknown")[:500],
                "owner": str(item.get("owner") or "unassigned")[:80],
                "next_action": str(item.get("next_action") or "Confirm or validate")[:1000],
            }
            for item in content.get("high_impact_unknowns", [])[:30]
            if isinstance(item, dict)
        ],
        "next_actions": [
            {
                "owner": str(item.get("owner") or "owner")[:80],
                "action": str(item.get("action") or "")[:1000],
            }
            for item in content.get("next_actions", [])[:30]
            if isinstance(item, dict)
        ],
        "engineering_effort": content.get("engineering_effort", {}),
        "tool_support": content.get("tool_support", [])[:30],
        "poc_plan": content.get("poc_plan", {}),
        "acceptance_tests": [
            str(value)[:1000] for value in content.get("acceptance_tests", [])[:40]
        ],
    }


async def _emit_progress(
    session_id: str,
    job_id: str,
    state_version: int,
    stage: str,
    status: str,
    approved_facts: dict[str, int | str] | None = None,
) -> None:
    event = progress_event(
        session_id=session_id,
        analysis_job_id=job_id,
        state_version=state_version,
        stage=stage,
        status=status,
        approved_facts=approved_facts,
    )
    await asyncio.to_thread(
        append_scenario_event,
        event_id=event["event_id"],
        session_id=session_id,
        event_type="analysis_progress",
        payload=event,
        public=True,
    )


async def _run_analysis(job: dict[str, Any], state: dict[str, Any], *, partial: bool) -> None:
    session_id = str(job["session_id"])
    job_id = str(job["job_id"])
    state_version = int(job["scenario_state_version"])
    try:
        await asyncio.to_thread(update_scenario_analysis_job_record, job_id, status="processing")
        await _emit_progress(session_id, job_id, state_version, "workflow_understanding", "completed")
        await _emit_progress(session_id, job_id, state_version, "requirement_extraction", "running")
        async def worker_progress(event: dict[str, Any]) -> None:
            stage = str(event.get("stage") or "")
            status = str(event.get("status") or "")
            facts = event.get("approved_facts")
            if stage not in {
                "workflow_understanding",
                "requirement_extraction",
                "evidence_retrieval",
                "envelope_comparison",
                "gap_evaluation",
                "recommendation_building",
                "report_generation",
            } or status not in {"queued", "running", "completed", "failed"}:
                return
            await _emit_progress(
                session_id,
                job_id,
                state_version,
                stage,
                status,
                facts if isinstance(facts, dict) else None,
            )

        result = await gateway.command(
            "analyze_scenario",
            timeout=480,
            on_progress=worker_progress,
            scenario_text=scenario_narrative(state),
            model_id=str(get_scenario_session(session_id)["model_id"]),
            language=str(job["language"]),
            scenario_session_id=session_id,
            scenario_state_version=state_version,
            pipeline_version=PIPELINE_VERSION,
            scenario_state=state,
        )
        if result.get("status") != "ok" or not isinstance(result.get("result"), dict):
            raise RuntimeError(str(result.get("error") or "Worker analysis failed"))
        worker_result = result["result"]
        requirements = worker_result.get("atomic_requirements", [])
        await _emit_progress(
            session_id,
            job_id,
            state_version,
            "requirement_extraction",
            "completed",
            {"requirements_identified": len(requirements) if isinstance(requirements, list) else 0},
        )
        await _emit_progress(session_id, job_id, state_version, "evidence_retrieval", "completed")
        await _emit_progress(session_id, job_id, state_version, "envelope_comparison", "completed")
        await _emit_progress(session_id, job_id, state_version, "gap_evaluation", "completed")
        await _emit_progress(session_id, job_id, state_version, "recommendation_building", "completed")
        await _emit_progress(session_id, job_id, state_version, "report_generation", "running")

        report_id = new_identifier("REPORT")
        report = _report_from_worker(
            session_id=session_id,
            report_revision_id=report_id,
            state=state,
            job=job,
            result=worker_result,
            partial=partial,
        )
        finalized = await asyncio.to_thread(
            finalize_scenario_analysis_report,
            report_revision_id=report_id,
            session_id=session_id,
            state_version=state_version,
            analysis_job_id=job_id,
            partial=partial,
            report=report,
            diff_summary="Initial report" if not list_scenario_report_revisions(session_id) else "Scenario requirements changed",
        )
        try:
            await _emit_progress(
                session_id, job_id, state_version, "report_generation", "completed"
            )
        except Exception:
            log.exception(
                "Scenario report %s finalized but its completion event could not be persisted",
                report_id,
            )
        if finalized["reanalysis_state_version"] is not None:
            try:
                await _queue_pending_reanalysis(session_id)
            except Exception:
                log.exception(
                    "Pending scenario reanalysis could not be queued for %s; marker retained",
                    session_id,
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        correlation_id = f"ERR-{uuid.uuid4().hex[:12].upper()}"
        log.exception("Scenario analysis failed (%s)", correlation_id)
        await asyncio.to_thread(
            update_scenario_analysis_job_record,
            job_id,
            status="failed",
            error_category="analysis_failed",
            internal_error_correlation_id=correlation_id,
        )
        if job.get("trigger") == "coalesced_reanalysis":
            await asyncio.to_thread(
                mark_scenario_reanalysis_pending, session_id, state_version
            )
        await asyncio.to_thread(
            mark_scenario_analysis_failed,
            session_id,
            state_version=state_version,
        )
        try:
            await _emit_progress(session_id, job_id, state_version, "report_generation", "failed")
        except Exception:
            log.exception("Could not persist failed progress event")
    finally:
        current_task = asyncio.current_task()
        registration = _analysis_tasks.get(job_id)
        if (
            registration is not None
            and registration.task is current_task
            and registration.attempt_count == int(job.get("attempt_count") or 1)
        ):
            _analysis_tasks.pop(job_id, None)


def _launch_analysis_task(
    job: dict[str, Any], state: dict[str, Any], *, trigger: str
) -> bool:
    job_id = str(job["job_id"])
    attempt_count = int(job.get("attempt_count") or 1)
    registration = _analysis_tasks.get(job_id)
    if registration is not None and not registration.task.done():
        if registration.attempt_count == attempt_count:
            return True
        if registration.attempt_count > attempt_count:
            return False
    task = asyncio.create_task(
        _run_analysis(
            job,
            state,
            partial=(
                trigger == "user_requested_early"
                and (
                    bool(state["unresolved_issues"])
                    or state.get("current_question") is not None
                    or not bool(state.get("stability", {}).get("stable"))
                )
            ),
        ),
        name=f"scenario-analysis-{job_id}",
    )
    _analysis_tasks[job_id] = _AnalysisTaskRegistration(
        task=task,
        attempt_count=attempt_count,
    )
    return True


def _analysis_attempt_is_running(job: dict[str, Any]) -> bool:
    registration = _analysis_tasks.get(str(job["job_id"]))
    return bool(
        registration is not None
        and registration.attempt_count == int(job.get("attempt_count") or 1)
        and not registration.task.done()
    )


async def _queue_analysis(
    scenario: dict[str, Any], *, trigger: str
) -> tuple[dict[str, Any], bool]:
    state = scenario["current_state"]
    state_version = int(state["state_version"])
    catalog_revision = "catalog-current"
    evidence_revision = "wiki-current"
    language = str(scenario["language"])
    idempotency_key = hashlib.sha256(
        f"{scenario['session_id']}|{state_version}|{catalog_revision}|{evidence_revision}|{PIPELINE_VERSION}|{language}".encode()
    ).hexdigest()
    job, created = await asyncio.to_thread(
        create_scenario_analysis_job_record,
        job_id=new_identifier("JOB"),
        session_id=str(scenario["session_id"]),
        state_version=state_version,
        catalog_revision=catalog_revision,
        evidence_revision=evidence_revision,
        pipeline_version=PIPELINE_VERSION,
        language=language,
        idempotency_key=idempotency_key,
        trigger=trigger,
    )
    if created:
        await asyncio.to_thread(update_scenario_session_status, str(scenario["session_id"]), status="analyzing")
        try:
            await _emit_progress(str(scenario["session_id"]), str(job["job_id"]), state_version, "workflow_understanding", "queued")
        except Exception:
            log.exception(
                "Scenario job %s queued but its initial progress event could not be persisted",
                job["job_id"],
            )
        _launch_analysis_task(job, state, trigger=trigger)
    return job, created


async def _queue_pending_reanalysis(
    session_id: str,
) -> tuple[dict[str, Any], bool] | None:
    if not gateway.online:
        return None
    pending_version = await asyncio.to_thread(
        get_pending_scenario_reanalysis_version, session_id
    )
    if pending_version is None:
        return None
    scenario = await asyncio.to_thread(get_scenario_session, session_id)
    if scenario is None:
        return None
    current_version = int(scenario["current_state_version"])
    if current_version < pending_version:
        raise RuntimeError("Pending reanalysis version is newer than the current scenario")
    if await asyncio.to_thread(
        has_current_scenario_report_for_version, session_id, current_version
    ):
        await asyncio.to_thread(
            clear_pending_scenario_reanalysis,
            session_id,
            through_state_version=current_version,
        )
        return None
    active = await asyncio.to_thread(get_active_scenario_analysis_job, session_id)
    if active is not None:
        if int(active["scenario_state_version"]) != current_version:
            return None
        if active["status"] == "queued":
            launched = _launch_analysis_task(
                active,
                scenario["current_state"],
                trigger=str(active.get("trigger") or "coalesced_reanalysis"),
            )
            if not launched:
                return active, False
        await asyncio.to_thread(
            update_scenario_session_status, session_id, status="analyzing"
        )
        confirmed = await asyncio.to_thread(
            get_scenario_analysis_job, str(active["job_id"])
        )
        if (
            confirmed is None
            or int(confirmed["scenario_state_version"]) != current_version
            or confirmed["status"] not in {"queued", "processing"}
            or not _analysis_attempt_is_running(confirmed)
        ):
            return active, False
        await asyncio.to_thread(
            clear_pending_scenario_reanalysis,
            session_id,
            through_state_version=current_version,
        )
        return active, False
    job, created = await _queue_analysis(scenario, trigger="coalesced_reanalysis")
    confirmed = await asyncio.to_thread(get_scenario_analysis_job, str(job["job_id"]))
    if (
        confirmed is None
        or int(confirmed["scenario_state_version"]) != current_version
        or confirmed["status"] not in {"queued", "processing"}
        or not _analysis_attempt_is_running(confirmed)
    ):
        return job, created
    await asyncio.to_thread(
        clear_pending_scenario_reanalysis,
        session_id,
        through_state_version=current_version,
    )
    return job, created


async def reconcile_pending_scenario_reanalyses() -> dict[str, int]:
    stats = {"pending": 0, "reconciled": 0, "deferred": 0, "failed": 0}
    async with _reanalysis_dispatch_lock:
        pending = await asyncio.to_thread(list_pending_scenario_reanalyses)
        stats["pending"] = len(pending)
        if not gateway.online:
            stats["deferred"] = len(pending)
            return stats
        for item in pending:
            if not gateway.online:
                stats["deferred"] += 1
                continue
            session_id = str(item["session_id"])
            try:
                before = await asyncio.to_thread(
                    get_pending_scenario_reanalysis_version, session_id
                )
                await _queue_pending_reanalysis(session_id)
                after = await asyncio.to_thread(
                    get_pending_scenario_reanalysis_version, session_id
                )
                if before is not None and after is None:
                    stats["reconciled"] += 1
                else:
                    stats["deferred"] += 1
            except Exception:
                stats["failed"] += 1
                log.exception(
                    "Pending scenario reanalysis reconciliation failed for %s",
                    session_id,
                )
    return stats


async def scenario_reanalysis_dispatcher() -> None:
    while True:
        try:
            await reconcile_pending_scenario_reanalyses()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scenario reanalysis dispatcher iteration failed")
        await asyncio.sleep(SCENARIO_REANALYSIS_POLL_SECONDS)


@router.post("/api/scenario-sessions")
async def create_session_route(
    payload: CreateSessionRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "create")
    if limited is not None:
        return limited
    try:
        model_selection = _select_scenario_model(payload.initial_intent, payload.model_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if payload.language not in SUPPORTED_LANGUAGES:
        return JSONResponse({"error": "Unsupported language"}, status_code=400)
    account = current_session(request)
    if account is not None:
        verify_csrf(account, x_csrf_token)
    session_id = new_identifier("SCNSESSION")
    raw_resume_token = "" if account else secrets.token_urlsafe(48)
    state = attach_next_question(initial_state(session_id, payload.initial_intent), language=payload.language)
    scenario = await asyncio.to_thread(
        create_scenario_session,
        session_id=session_id,
        owner_user_id=int(account["user_id"]) if account else None,
        anonymous_token_hash=_token_hash(raw_resume_token) if raw_resume_token else None,
        language=payload.language,
        model_id=model_selection["model_id"],
        state=state,
        conversation_id=payload.conversation_id,
    )
    await asyncio.to_thread(
        _append_event,
        session_id,
        "session_created",
        {
            "state_version": state["state_version"],
            "model_id": model_selection["model_id"],
            "model_selection_mode": model_selection["mode"],
        },
    )
    response = {
        "status": "ok",
        "session": _public_session(scenario),
        "model_selection": model_selection,
    }
    if raw_resume_token:
        response["resume_token"] = raw_resume_token
    return JSONResponse(response, status_code=201)


@router.get("/api/scenario-sessions/{session_id}")
async def get_session_route(
    session_id: str,
    request: Request,
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    try:
        scenario, _ = await asyncio.to_thread(
            _authorize, request, session_id, x_scenario_resume_token
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    result = _public_session(scenario)
    result["reports"] = await asyncio.to_thread(list_scenario_report_revisions, session_id)
    return JSONResponse({"status": "ok", "session": result})


@router.post("/api/scenario-sessions/{session_id}/answers")
async def answer_route(
    session_id: str,
    payload: AnswerRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "answer")
    if limited is not None:
        return limited
    try:
        scenario, account = await asyncio.to_thread(
            _authorize,
            request,
            session_id,
            x_scenario_resume_token,
            csrf_token=x_csrf_token,
            mutation=True,
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    state = scenario["current_state"]
    if int(state["state_version"]) != payload.expected_state_version:
        return JSONResponse({"error": "Scenario state changed", "session": _public_session(scenario)}, status_code=409)
    try:
        accepted = apply_answer(
            state,
            question_id=payload.question_id,
            answer=payload.answer,
            answer_mode=payload.answer_mode,
        )
        accepted = attach_next_question(
            accepted,
            default_candidate_questions(accepted, str(scenario["language"])),
            str(scenario["language"]),
        )
        validate_state(accepted, session_id=session_id)
        saved = await asyncio.to_thread(
            save_scenario_state_version,
            session_id=session_id,
            expected_version=payload.expected_state_version,
            state=accepted,
            change_source="clarification_answer",
            actor_user_id=int(account["user_id"]) if account else None,
        )
        await asyncio.to_thread(
            _append_event,
            session_id,
            "answer_recorded",
            {
                "question_id": payload.question_id,
                "answer_mode": payload.answer_mode,
                "state_version": accepted["state_version"],
            },
        )

        candidates, technical_issues, state_patches, model_enriched = await _candidate_questions(
            saved["current_state"],
            model_id=str(scenario["model_id"]),
            language=str(scenario["language"]),
            user_message=payload.answer,
        )
        if not model_enriched:
            return JSONResponse(
                {
                    "status": "ok",
                    "session": _public_session(saved),
                    "clarification_enrichment": "retryable",
                }
            )

        updated = deepcopy(saved["current_state"])
        updated["state_version"] = int(updated["state_version"]) + 1
        updated["current_question"] = None
        if state_patches:
            updated = _apply_advisory_state_patches(updated, state_patches)
        if technical_issues:
            existing_keys = {
                str(item.get("semantic_key") or item.get("issue_id") or "")
                for item in updated.get("unresolved_issues", [])
                if isinstance(item, dict)
            }
            for issue in technical_issues:
                key = str(issue.get("semantic_key") or issue.get("issue_id") or "")
                if not key or key in existing_keys:
                    continue
                updated.setdefault("unresolved_issues", []).append(
                    {
                        "issue_id": str(issue.get("issue_id") or new_identifier("ISSUE")),
                        "semantic_key": key,
                        "original_text": str(issue.get("original_text") or issue.get("name") or key),
                        "normalized_value": issue.get("normalized_value", ""),
                        "knowledge_state": "unknown",
                        "owner": str(issue["owner"]),
                        "evidence_locator": issue.get("evidence_locator"),
                        "affected_decision": str(issue.get("affected_decision") or "feasibility"),
                        "can_change_conclusion": bool(issue.get("can_change_conclusion", True)),
                        "next_action": str(issue.get("next_action") or "Validate with the assigned owner"),
                        "status": "open",
                        "last_changed_version": updated["state_version"],
                    }
                )
                existing_keys.add(key)
        updated = attach_next_question(updated, candidates, str(scenario["language"]))
        validate_state(updated, session_id=session_id)
        saved = await asyncio.to_thread(
            save_scenario_state_version,
            session_id=session_id,
            expected_version=accepted["state_version"],
            state=updated,
            change_source="clarification_enrichment",
            actor_user_id=int(account["user_id"]) if account else None,
        )
        await asyncio.to_thread(
            _append_event,
            session_id,
            "clarification_enriched",
            {
                "state_version": updated["state_version"],
            },
        )
        return JSONResponse({"status": "ok", "session": _public_session(saved)})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)


@router.post("/api/scenario-sessions/{session_id}/analyze-now")
async def analyze_now_route(
    session_id: str,
    payload: AnalyzeNowRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "analyze")
    if limited is not None:
        return limited
    try:
        scenario, account = await asyncio.to_thread(
            _authorize,
            request,
            session_id,
            x_scenario_resume_token,
            csrf_token=x_csrf_token,
            mutation=True,
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    state = scenario["current_state"]
    if int(state["state_version"]) != payload.expected_state_version:
        return JSONResponse({"error": "Scenario state changed"}, status_code=409)
    if not state.get("minimum_gate", {}).get("passed"):
        return JSONResponse(
            {
                "error": "Confirm the customer goal and complete workflow before analysis",
                "missing": state.get("minimum_gate", {}).get("missing", []),
                "current_question": state.get("current_question"),
                "draft_saved": True,
            },
            status_code=409,
        )
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline; the draft remains saved"}, status_code=503)
    updated = dict(state)
    updated["state_version"] = int(state["state_version"]) + 1
    updated["analysis_trigger"] = payload.trigger
    updated["status"] = "analyzing"
    updated["updated_at"] = utc_now()
    saved = await asyncio.to_thread(
        save_scenario_state_version,
        session_id=session_id,
        expected_version=payload.expected_state_version,
        state=updated,
        change_source="analysis_requested",
        actor_user_id=int(account["user_id"]) if account else None,
    )
    job, created = await _queue_analysis(saved, trigger=payload.trigger)
    await asyncio.to_thread(
        _append_event,
        session_id,
        "analysis_requested",
        {"job_id": job["job_id"], "trigger": payload.trigger, "created": created},
    )
    return JSONResponse(
        {"status": "accepted", "job_id": job["job_id"], "session": _public_session(saved)},
        status_code=202,
    )


@router.post("/api/scenario-sessions/{session_id}/keep-asking")
async def keep_asking_route(
    session_id: str,
    payload: AnalyzeNowRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "keep_asking")
    if limited is not None:
        return limited
    try:
        scenario, account = await asyncio.to_thread(
            _authorize, request, session_id, x_scenario_resume_token,
            csrf_token=x_csrf_token, mutation=True,
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    state = scenario["current_state"]
    if int(state["state_version"]) != payload.expected_state_version:
        return JSONResponse({"error": "Scenario state changed"}, status_code=409)
    updated = deepcopy(state)
    updated["state_version"] = int(state["state_version"]) + 1
    updated["status"] = "refining"
    updated["countdown_suppressed_at_version"] = updated["state_version"]
    candidates, technical_issues, patches, _ = await _candidate_questions(
        updated,
        model_id=str(scenario["model_id"]),
        language=str(scenario["language"]),
        user_message="Keep asking before analysis",
    )
    if patches:
        updated = _apply_advisory_state_patches(updated, patches)
    if technical_issues:
        updated.setdefault("unresolved_issues", []).extend(technical_issues)
    updated = attach_next_question(updated, candidates, str(scenario["language"]))
    updated["status"] = "refining"
    saved = await asyncio.to_thread(
        save_scenario_state_version,
        session_id=session_id,
        expected_version=payload.expected_state_version,
        state=updated,
        change_source="keep_asking",
        actor_user_id=int(account["user_id"]) if account else None,
    )
    await asyncio.to_thread(_append_event, session_id, "countdown_cancelled", {"state_version": updated["state_version"]})
    return JSONResponse({"status": "ok", "session": _public_session(saved)})


@router.post("/api/scenario-sessions/{session_id}/messages")
async def message_route(
    session_id: str,
    payload: MessageRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "message")
    if limited is not None:
        return limited
    try:
        scenario, _ = await asyncio.to_thread(
            _authorize, request, session_id, x_scenario_resume_token,
            csrf_token=x_csrf_token, mutation=True,
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    if int(scenario["current_state_version"]) != payload.expected_state_version:
        return JSONResponse({"error": "Scenario state changed"}, status_code=409)
    text = payload.message.strip()
    current_report_id = scenario.get("current_report_revision_id")
    report = (
        get_scenario_report_revision(session_id, str(current_report_id))
        if current_report_id
        else None
    )
    content = report.get("report", {}) if report else {}
    if gateway.online:
        try:
            classified = await gateway.command(
                "classify_scenario_message",
                timeout=60,
                scenario_state=scenario["current_state"],
                model_id=str(scenario["model_id"]),
                language=str(scenario["language"]),
                user_message=text,
                report_summary={
                    "conclusion": content.get("conclusion"),
                    "conditions": content.get("conditions", [])[:5],
                    "high_impact_unknowns": content.get("high_impact_unknowns", [])[:5],
                },
            )
            intent = str(classified.get("intent") or "unclear")
            proposed_change = classified.get("proposed_change")
        except Exception:
            log.exception("Follow-up intent classification failed")
            intent = "unclear"
            proposed_change = None
    else:
        intent = "unclear"
        proposed_change = None
    report_citations: list[dict[str, Any]] = []

    if intent == "new_scenario":
        response = "Start a new scenario session to keep this report and its evidence snapshot intact."
    elif intent == "report_question":
        approved_report = _approved_report_question_context(content)
        try:
            answered = await gateway.command(
                "answer_scenario_report_question",
                timeout=75,
                model_id=str(scenario["model_id"]),
                language=str(scenario["language"]),
                user_question=text,
                approved_report=approved_report,
            )
            response = str(answered.get("answer") or "").strip()
            if not response:
                raise ValueError("Worker returned an empty report answer")
            report_citations = [
                item
                for item in answered.get("citations", [])
                if isinstance(item, dict)
            ][:8]
        except Exception:
            log.exception("Report question answering failed")
            response = (
                "The report could not answer that question right now. "
                f"Its recorded conclusion is {approved_report['conclusion']}."
            )
    elif intent == "requirement_change" and proposed_change:
        response = "Please confirm the proposed requirement change in this conversation before it updates the scenario."
    else:
        intent = "unclear"
        proposed_change = None
        response = "Is this a question about the report, a requirement change, or a new scenario?"
    await asyncio.to_thread(_append_event, session_id, "post_report_intent", {"intent": intent, "message": text})
    return JSONResponse(
        {
            "status": "ok",
            "intent": intent,
            "response": response,
            "proposed_change": proposed_change,
            "confirmation_required": intent == "requirement_change",
            "report_citations": report_citations,
        }
    )


@router.post("/api/scenario-sessions/{session_id}/confirm-change")
async def confirm_change_route(
    session_id: str,
    payload: ConfirmChangeRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "confirm_change")
    if limited is not None:
        return limited
    try:
        scenario, account = await asyncio.to_thread(
            _authorize, request, session_id, x_scenario_resume_token,
            csrf_token=x_csrf_token, mutation=True,
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    state = scenario["current_state"]
    if int(state["state_version"]) != payload.expected_state_version:
        return JSONResponse({"error": "Scenario state changed"}, status_code=409)
    if not payload.confirmed:
        await asyncio.to_thread(_append_event, session_id, "requirement_change_rejected", {"proposed_change": payload.proposed_change})
        return JSONResponse({"status": "ok", "session": _public_session(scenario)})
    updated = deepcopy(state)
    updated["state_version"] = int(state["state_version"]) + 1
    active_job = await asyncio.to_thread(get_active_scenario_analysis_job, session_id)
    should_analyze = bool(updated.get("minimum_gate", {}).get("passed")) and gateway.online
    updated["status"] = "analyzing" if active_job or should_analyze else "refining"
    requirements = list(state.get("requirements", []))
    requirements.append(
        {
            "requirement_id": new_identifier("REQ"),
            "original_text": payload.proposed_change,
            "normalized_value": payload.proposed_change,
            "knowledge_state": "known",
            "owner": "customer",
            "evidence_locator": None,
            "affected_decision": "feasibility",
            "can_change_conclusion": True,
            "last_changed_version": updated["state_version"],
        }
    )
    updated["requirements"] = requirements
    if not active_job and not should_analyze:
        updated["countdown_suppressed_at_version"] = updated["state_version"]
        candidates, _, patches, _ = await _candidate_questions(
            updated,
            model_id=str(scenario["model_id"]),
            language=str(scenario["language"]),
            user_message=payload.proposed_change,
        )
        if patches:
            updated = _apply_advisory_state_patches(updated, patches)
        updated = attach_next_question(updated, candidates, str(scenario["language"]))
        updated["status"] = "refining"
    saved = await asyncio.to_thread(
        save_scenario_state_version,
        session_id=session_id,
        expected_version=payload.expected_state_version,
        state=evaluate_state(updated),
        change_source="confirmed_requirement_change",
        actor_user_id=int(account["user_id"]) if account else None,
    )
    await asyncio.to_thread(supersede_current_scenario_report, session_id)
    saved = await asyncio.to_thread(get_scenario_session, session_id)
    queued_job: dict[str, Any] | None = None
    active_after_save = await asyncio.to_thread(
        get_active_scenario_analysis_job, session_id
    )
    if active_after_save:
        await asyncio.to_thread(update_scenario_session_status, session_id, status="analyzing")
        saved = await asyncio.to_thread(get_scenario_session, session_id)
    elif should_analyze and saved is not None:
        queued_job, _ = await _queue_analysis(saved, trigger="coalesced_reanalysis")
        saved = await asyncio.to_thread(get_scenario_session, session_id)
    await asyncio.to_thread(_append_event, session_id, "requirement_change_confirmed", {"state_version": updated["state_version"]})
    return JSONResponse(
        {
            "status": "accepted" if queued_job or active_after_save else "ok",
            "session": _public_session(saved),
            "analysis_job_id": (
                str(queued_job["job_id"])
                if queued_job
                else (
                    str(active_after_save["job_id"])
                    if active_after_save
                    else None
                )
            ),
        },
        status_code=202 if queued_job or active_after_save else 200,
    )


@router.post("/api/scenario-sessions/{session_id}/claim")
async def claim_route(
    session_id: str,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "claim")
    if limited is not None:
        return limited
    account = require_user(request)
    verify_csrf(account, x_csrf_token)
    try:
        scenario, _ = await asyncio.to_thread(_authorize, request, session_id, x_scenario_resume_token)
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    if scenario.get("owner_user_id") is not None:
        return JSONResponse({"error": "Scenario session is already claimed"}, status_code=409)
    claimed = await asyncio.to_thread(claim_scenario_session, session_id, user_id=int(account["user_id"]))
    if not claimed:
        return JSONResponse({"error": "Scenario session could not be claimed"}, status_code=409)
    await asyncio.to_thread(_append_event, session_id, "session_claimed", {"user_id": int(account["user_id"])})
    return JSONResponse({"status": "ok", "session": _public_session(get_scenario_session(session_id))})


@router.get("/api/scenario-sessions/{session_id}/events", response_model=None)
async def events_route(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    resume_token: str = Query(default="", max_length=200),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
) -> Any:
    try:
        await asyncio.to_thread(
            _authorize,
            request,
            session_id,
            _resume_token(x_scenario_resume_token, resume_token),
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    cursor = after
    if last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))

    async def stream():
        current = cursor
        heartbeat_at = time.monotonic()
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            events = await asyncio.to_thread(
                list_scenario_events, session_id, after_sequence=current, public_only=True
            )
            if events:
                for item in events:
                    current = int(item["sequence"])
                    payload = json.dumps(item["payload"], ensure_ascii=False)
                    yield f"id: {current}\nevent: {item['event_type']}\ndata: {payload}\n\n"
            elif time.monotonic() - heartbeat_at >= 15:
                yield ": keep-alive\n\n"
                heartbeat_at = time.monotonic()
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/scenario-sessions/{session_id}/reports")
async def reports_route(
    session_id: str,
    request: Request,
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    try:
        await asyncio.to_thread(_authorize, request, session_id, x_scenario_resume_token)
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    return JSONResponse({"status": "ok", "reports": await asyncio.to_thread(list_scenario_report_revisions, session_id)})


@router.get("/api/scenario-sessions/{session_id}/reports/{revision_id}")
async def report_route(
    session_id: str,
    revision_id: str,
    request: Request,
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    try:
        await asyncio.to_thread(_authorize, request, session_id, x_scenario_resume_token)
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    report = await asyncio.to_thread(get_scenario_report_revision, session_id, revision_id)
    return JSONResponse({"status": "ok", "report": report}) if report else JSONResponse({"error": "Report not found"}, status_code=404)


@router.post("/api/scenario-sessions/{session_id}/share")
async def share_route(
    session_id: str,
    payload: ShareRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "share")
    if limited is not None:
        return limited
    try:
        scenario, account = await asyncio.to_thread(
            _authorize, request, session_id, x_scenario_resume_token,
            csrf_token=x_csrf_token, mutation=True,
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    revision_id = payload.report_revision_id or scenario.get("current_report_revision_id")
    if not payload.bind_current and (
        not revision_id or get_scenario_report_revision(session_id, str(revision_id)) is None
    ):
        return JSONResponse({"error": "A valid report revision is required"}, status_code=400)
    raw_token = secrets.token_urlsafe(48)
    expires_at = None
    if payload.expires_in_hours is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)).isoformat()
    share_id = new_identifier("SHARE")
    await asyncio.to_thread(
        create_scenario_share_link,
        share_id=share_id,
        session_id=session_id,
        report_revision_id=None if payload.bind_current else str(revision_id),
        token_hash=_token_hash(raw_token),
        bind_current=payload.bind_current,
        expires_at=expires_at,
        creator_user_id=int(account["user_id"]) if account else None,
    )
    return JSONResponse(
        {
            "status": "ok",
            "share_id": share_id,
            "url": rooted_path(f"/s/scenario-report/{raw_token}"),
            "expires_at": expires_at,
        },
        status_code=201,
    )


@router.delete("/api/scenario-sessions/{session_id}/share/{share_id}")
async def revoke_share_route(
    session_id: str,
    share_id: str,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
    x_scenario_resume_token: str = Header(default="", alias="X-Scenario-Resume-Token"),
) -> JSONResponse:
    limited = _rate_limited(request, "revoke_share")
    if limited is not None:
        return limited
    try:
        await asyncio.to_thread(
            _authorize, request, session_id, x_scenario_resume_token,
            csrf_token=x_csrf_token, mutation=True,
        )
    except (LookupError, PermissionError) as exc:
        return _authorization_error(exc)
    revoked = await asyncio.to_thread(revoke_scenario_share_link, share_id, session_id)
    return JSONResponse({"status": "ok", "revoked": revoked})


def _shared_report(report: dict[str, Any]) -> dict[str, Any]:
    content = report.get("report", {})
    return {
        "report_revision_id": report.get("report_revision_id"),
        "ordinal": report.get("ordinal"),
        "status": report.get("status"),
        "conclusion": content.get("conclusion"),
        "executive_summary": content.get("executive_summary", ""),
        "conditions": content.get("conditions", []),
        "main_evidence": [
            {
                "name": item.get("name"),
                "support_state": item.get("support_state"),
                "capability_type": item.get("capability_type"),
            }
            for item in content.get("main_evidence", [])
            if isinstance(item, dict)
        ],
        "high_impact_unknowns": content.get("high_impact_unknowns", []),
        "next_actions": content.get("next_actions", []),
        "next_experiment": content.get("next_experiment"),
        "engineering_effort": content.get("engineering_effort", {}),
        "tool_support": content.get("tool_support", []),
        "preconditions_and_risks": content.get("preconditions_and_risks", {}),
        "poc_plan": content.get("poc_plan", {}),
        "acceptance_tests": content.get("acceptance_tests", []),
    }


@router.get("/s/scenario-report/{share_token}", response_class=HTMLResponse)
async def shared_report_page(share_token: str) -> HTMLResponse:
    supplied_hash = _token_hash(share_token)
    share = await asyncio.to_thread(get_scenario_share_link_by_hash, supplied_hash)
    if share is None or not hmac.compare_digest(str(share["token_hash"]), supplied_hash):
        return HTMLResponse("Report link not found", status_code=404)
    if share.get("revoked_at"):
        return HTMLResponse("Report link has been revoked", status_code=410)
    expires_at = share.get("expires_at")
    if expires_at and datetime.fromisoformat(str(expires_at)) <= datetime.now(timezone.utc):
        return HTMLResponse("Report link has expired", status_code=410)
    session_id = str(share["session_id"])
    revision_id = share.get("report_revision_id")
    if bool(share.get("bind_current")):
        scenario = await asyncio.to_thread(get_scenario_session, session_id)
        revision_id = scenario.get("current_report_revision_id") if scenario else None
    report = await asyncio.to_thread(get_scenario_report_revision, session_id, str(revision_id)) if revision_id else None
    if report is None:
        return HTMLResponse("Report is not available", status_code=404)
    await asyncio.to_thread(touch_scenario_share_link, str(share["share_id"]))
    safe = _shared_report(report)
    payload = json.dumps(safe, ensure_ascii=False).replace("<", "\\u003c")
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>Shared scenario report</title><style>body{font-family:system-ui;max-width:900px;margin:40px auto;padding:0 20px;color:#172033}"
        ".card{border:1px solid #dbe2ee;border-radius:16px;padding:24px}li{margin:.5rem 0}</style></head>"
        "<body><main class='card'><h1>Scenario feasibility report</h1><div id='report'></div></main>"
        f"<script type='application/json' id='report-data'>{payload}</script>"
        "<script>const d=JSON.parse(document.getElementById('report-data').textContent),r=document.getElementById('report');"
        "const h=document.createElement('h2');h.textContent=String(d.conclusion||'insufficient_evidence').replaceAll('_',' ');r.append(h);"
        "for(const [title,items] of [['Conditions',d.conditions],['High-impact unknowns',d.high_impact_unknowns],['Next actions',d.next_actions]]){"
        "const x=document.createElement('h3');x.textContent=title;r.append(x);const ul=document.createElement('ul');"
        "for(const item of (items||[])){const li=document.createElement('li');li.textContent=typeof item==='string'?item:JSON.stringify(item);ul.append(li)}r.append(ul)}"
        "</script></body></html>"
    )
