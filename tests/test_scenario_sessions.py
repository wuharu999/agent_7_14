from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import Request

from ecs.app import auth, config, database
from ecs.app.gateway import gateway
from ecs.app.routes import scenario_sessions as routes
from shared.capability_types import migrate_legacy_capability
from shared.scenario_state import (
    apply_answer,
    apply_state_patch,
    attach_next_question,
    default_candidate_questions,
    initial_state,
    select_question,
)
from worker.analysis_progress import progress_event, sanitize_summary
from worker.manager import WorkerManager


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "agent_jobs.db")
    database.initialize_database()
    gateway.websocket = None
    routes.mutation_limiter.history.clear()

    async def immediate_to_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)
    yield
    gateway.websocket = None
    routes._analysis_tasks.clear()


def _request(*, method: str = "POST", token: str = "") -> Request:
    headers = []
    if token:
        headers.append((b"cookie", f"{config.SESSION_COOKIE_NAME}={token}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def _answer_current(state: dict, answer: str) -> dict:
    question = state["current_question"]
    updated = apply_answer(
        state,
        question_id=question["question_id"],
        answer=answer,
        answer_mode="option",
    )
    return attach_next_question(updated, default_candidate_questions(updated), "en")


def test_one_question_flow_minimum_gate_and_semantic_deduplication() -> None:
    state = attach_next_question(initial_state("SCNSESSION-TEST", "Retrieve parcels"))
    seen: set[str] = set()
    for answer in (
        "Run a limited pilot",
        "System API signal",
        "Navigate -> identify -> pick -> deliver",
        "Object reaches the destination",
    ):
        question = state["current_question"]
        assert question["semantic_key"] not in seen
        assert len(question["options"]) <= 3
        seen.add(question["semantic_key"])
        state = _answer_current(state, answer)

    assert state["minimum_gate"] == {"passed": True, "missing": []}
    assert state["candidate_solution_paths"]
    assert len(state["question_history"]) == 4
    assert state["stability"]["stable"] is True
    assert state["status"] == "stability_countdown"
    assert state["current_question"] is None


def test_unknown_gets_explicit_assumption_or_owner_resolution_path() -> None:
    state = attach_next_question(initial_state("SCNSESSION-UNKNOWN", "Move parcels"))
    first = state["current_question"]
    state = apply_answer(
        state,
        question_id=first["question_id"],
        answer="",
        answer_mode="unknown",
    )
    state = attach_next_question(state, default_candidate_questions(state), "en")
    assert state["question_history"][0]["answer_mode"] == "unknown"
    assert state["question_history"][0]["resolution"] == "unresolved"
    assert state["unresolved_issues"][0]["knowledge_state"] == "unknown"
    assert state["current_question"]["semantic_key"] == first["semantic_key"]
    assert state["current_question"]["refines_question_id"] == first["question_id"]
    assert state["current_question"]["unknown_resolution"] is True
    state = apply_answer(
        state,
        question_id=state["current_question"]["question_id"],
        answer="Assign verification to the vendor",
        answer_mode="option",
    )
    assert state["unresolved_issues"][0]["owner"] == "vendor"
    assert state["unresolved_issues"][0]["can_change_conclusion"] is False


def test_question_selector_rejects_wrong_owner_and_resolved_key() -> None:
    state = attach_next_question(initial_state("SCNSESSION-OWNER", "Move parcels"))
    resolved_key = state["current_question"]["semantic_key"]
    state = _answer_current(state, "Run a limited pilot")
    candidates = [
        {
            "question_id": "Q-VENDOR",
            "semantic_key": "robot.navigation.repeatability",
            "question": "What is navigation repeatability?",
            "reason_for_asking": "Technical boundary",
            "decision_impact": ["feasibility"],
            "can_change_conclusion": True,
            "blocking": True,
            "target_owner": "vendor",
            "answer_type": "single_select_or_custom",
            "options": ["1 mm"],
            "prerequisite_keys": [],
            "refines_question_id": None,
        },
        {
            "question_id": "Q-REPEAT",
            "semantic_key": resolved_key,
            "question": "Repeat the goal?",
            "reason_for_asking": "Repeat",
            "decision_impact": ["feasibility"],
            "can_change_conclusion": True,
            "blocking": True,
            "target_owner": "customer",
            "answer_type": "single_select_or_custom",
            "options": ["Again"],
            "prerequisite_keys": [],
            "refines_question_id": None,
        },
    ]
    selected = select_question(state, candidates)
    assert selected is not None
    assert selected["semantic_key"] not in {"robot.navigation.repeatability", resolved_key}


def test_anonymous_session_persists_and_requires_resume_token() -> None:
    async def exercise():
        created = await routes.create_session_route(
            routes.CreateSessionRequest(
                initial_intent="Retrieve parcels from a locker",
                model_id=database.get_allowed_teams()[0],
                language="en",
            ),
            _request(),
        )
        data = json.loads(created.body)
        session = data["session"]
        resume_token = data["resume_token"]
        denied = await routes.get_session_route(
            session["session_id"], _request(method="GET"), x_scenario_resume_token="wrong"
        )
        loaded = await routes.get_session_route(
            session["session_id"], _request(method="GET"), x_scenario_resume_token=resume_token
        )
        question = session["current_state"]["current_question"]
        answered = await routes.answer_route(
            session["session_id"],
            routes.AnswerRequest(
                expected_state_version=1,
                question_id=question["question_id"],
                answer_mode="custom",
                answer="Run a supervised parcel retrieval pilot",
            ),
            _request(),
            x_scenario_resume_token=resume_token,
        )
        return created, denied, loaded, answered

    created, denied, loaded, answered = asyncio.run(exercise())
    assert created.status_code == 201
    assert denied.status_code == 403
    assert loaded.status_code == 200
    answer_data = json.loads(answered.body)
    assert answer_data["session"]["current_state_version"] == 2
    assert answer_data["session"]["current_state"]["goal"]["confirmation"] == "confirmed"


def test_state_version_write_is_optimistic_and_migration_is_additive() -> None:
    session_id = "SCNSESSION-OPTIMISTIC"
    state = attach_next_question(initial_state(session_id, "Move parcels"))
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    updated = _answer_current(state, "Run a limited pilot")
    database.save_scenario_state_version(
        session_id=session_id,
        expected_version=1,
        state=updated,
        change_source="test",
        actor_user_id=None,
    )
    with pytest.raises(RuntimeError, match="changed"):
        database.save_scenario_state_version(
            session_id=session_id,
            expected_version=1,
            state=updated,
            change_source="stale",
            actor_user_id=None,
        )

    database.initialize_database()
    with sqlite3.connect(database.DATABASE_PATH) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "scenario_sessions",
        "scenario_state_versions",
        "scenario_events",
        "scenario_analysis_jobs",
        "scenario_report_revisions",
        "scenario_share_links",
    } <= tables


def test_claim_requires_token_then_disables_anonymous_resume() -> None:
    suffix = uuid.uuid4().hex[:8]
    user_id = database.create_user_record(
        username=f"owner_{suffix}",
        email=f"owner_{suffix}@example.com",
        password_hash="hash",
        password_salt="salt",
        role="editor",
        teams="",
    )
    login_token, csrf = auth.create_login_session(user_id)

    async def exercise():
        created = await routes.create_session_route(
            routes.CreateSessionRequest(
                initial_intent="Retrieve parcels",
                model_id=database.get_allowed_teams()[0],
                language="en",
            ),
            _request(),
        )
        data = json.loads(created.body)
        claimed = await routes.claim_route(
            data["session"]["session_id"],
            _request(token=login_token),
            x_csrf_token=csrf,
            x_scenario_resume_token=data["resume_token"],
        )
        denied = await routes.get_session_route(
            data["session"]["session_id"],
            _request(method="GET"),
            x_scenario_resume_token=data["resume_token"],
        )
        owner = await routes.get_session_route(
            data["session"]["session_id"],
            _request(method="GET", token=login_token),
        )
        return claimed, denied, owner

    claimed, denied, owner = asyncio.run(exercise())
    assert claimed.status_code == 200
    assert denied.status_code == 403
    assert owner.status_code == 200


def test_worker_clarification_queue_is_separate_and_bounded() -> None:
    manager = WorkerManager()

    async def queue_one():
        await manager.route_message(
            {
                "type": "grill_scenario",
                "id": "clarify-1",
                "scenario_text": "Move parcels",
                "model_id": "walker_s2",
            }
        )

    asyncio.run(queue_one())
    assert manager.clarification_queue.qsize() == 1
    assert manager.capability_match_queue.qsize() == 0


def test_capability_migration_and_progress_sanitization() -> None:
    l1 = migrate_legacy_capability(
        {"capability_id": "CAP-L1", "abstraction_level": "L1_atomic_skill", "effect": "move"}
    )
    l3 = migrate_legacy_capability(
        {"capability_id": "CAP-L3", "abstraction_level": "L3_scenario_module"}
    )
    missing = migrate_legacy_capability({"capability_id": "CAP-MISSING"})
    assert "ambiguous_legacy_l1_review_required" in l1["migration_warnings"]
    assert l3["record_type"] == "solution_artifact"
    assert missing["capability_type"] == "unclassified"
    assert sanitize_summary("Traceback from /root/app/.env SECRET=abc", fallback_stage="gap_evaluation") == "Evaluating gaps and risks"
    migrated_evidence = migrate_legacy_capability(
        {
            "capability_id": "CAP-LEGACY-EVIDENCE",
            "capability_type": "operational_behavior",
            "evidence_level": "E4",
            "evidence_refs": ["wiki/locker-validation.md"],
        }
    )
    assert migrated_evidence["verification_profiles"][0]["support_state"] == "conditional"
    event = progress_event(
        session_id="SCN-PROGRESS",
        analysis_job_id="JOB-PROGRESS",
        state_version=1,
        stage="gap_evaluation",
        status="completed",
        approved_facts={"gaps": 2, "secret": "do not expose", "matches": "not-an-int"},
    )
    assert event["message"] == "Evaluating gaps and risks: 2 gaps requiring action"
    assert event["approved_facts"] == {"gaps": 2}


def test_workbench_uses_safe_dom_and_accessible_report_drawer() -> None:
    page = Path("ecs/app/templates/capability_match.html").read_text(encoding="utf-8")
    assert "I want something else" in page
    assert "I don't know yet" in page
    assert "textContent" in page
    assert "innerHTML" not in page
    assert 'role="separator"' in page
    assert 'aria-label="Scenario report"' in page
    assert "50vh" in page and "88vh" in page
    assert "onclick=" not in page
    assert "confirm(" not in page
    assert "confirmation-card" in page
    assert "eventReconnectTimer=setTimeout(connectEvents,2000)" in page


def _minimum_ready_state(session_id: str) -> dict:
    state = attach_next_question(initial_state(session_id, "Retrieve parcels from a Full Time Locker"))
    for answer in (
        "Run a limited pilot",
        "System or API signal",
        "Navigate -> identify -> pick -> deliver",
        "Object reaches the destination",
    ):
        state = _answer_current(state, answer)
    return state


def _analysis_result() -> dict:
    return {
        "scenario_spec": {
            "scenario_id": "SCN-LOCKER",
            "title": "Locker parcel retrieval",
            "business_goal": "Retrieve parcels",
        },
        "atomic_requirements": [
            {
                "requirement_id": "REQ-RETRIEVE",
                "name": "Retrieve parcel",
                "required_capability_type": "operational_behavior",
            }
        ],
        "capabilities": [
            {
                "capability_id": "CAP-LOCKER-RETRIEVE",
                "name": "Locker parcel retrieval",
                "capability_type": "operational_behavior",
                "support_state": "conditional",
                "evidence_refs": ["/root/private/wiki/locker.md"],
            }
        ],
        "feasibility_assessment": {
            "technical_conclusion": "feasible_with_conditions",
            "deployment_conclusion": "viable_with_conditions",
            "matches": [
                {
                    "conditions": [
                        "Package width remains inside the validated grasp envelope",
                        "Navigation heading repeatability is verified in a pilot",
                    ]
                }
            ],
            "residual_risks": ["Collision and balance validation remain open"],
            "next_experiment": "Bench-test representative packages, then run a supervised locker pilot",
        },
    }


def test_locker_analysis_creates_immutable_report_export_and_private_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-LOCKER"
    resume_token = "resume-locker-token"
    state = _minimum_ready_state(session_id)
    scenario = database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash=routes._token_hash(resume_token),
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    gateway.websocket = object()

    async def fake_command(message_type: str, **payload):
        assert message_type == "analyze_scenario"
        assert payload["scenario_session_id"] == session_id
        assert "Full Time Locker" in payload["scenario_text"]
        return {"status": "ok", "result": _analysis_result()}

    monkeypatch.setattr(gateway, "command", fake_command)

    async def exercise():
        job, created = await routes._queue_analysis(scenario, trigger="user_requested_early")
        assert created
        await asyncio.gather(*list(routes._analysis_tasks.values()))
        reports = database.list_scenario_report_revisions(session_id)
        revision = reports[0]
        exported_md = await routes.report_export_route(
            session_id,
            revision["report_revision_id"],
            _request(method="GET"),
            format="markdown",
            x_scenario_resume_token=resume_token,
        )
        exported_pdf = await routes.report_export_route(
            session_id,
            revision["report_revision_id"],
            _request(method="GET"),
            format="pdf",
            x_scenario_resume_token=resume_token,
        )
        shared = await routes.share_route(
            session_id,
            routes.ShareRequest(
                report_revision_id=revision["report_revision_id"],
                bind_current=False,
                expires_in_hours=24,
            ),
            _request(),
            x_scenario_resume_token=resume_token,
        )
        share_token = json.loads(shared.body)["url"].rsplit("/", 1)[-1]
        page = await routes.shared_report_page(share_token)
        return job, revision, exported_md, exported_pdf, shared, page

    job, revision, exported_md, exported_pdf, shared, page = asyncio.run(exercise())
    assert database.get_scenario_analysis_job(job["job_id"])["status"] == "completed"
    assert revision["is_current"] is True
    assert revision["report"]["conclusion"] == "fit_with_conditions"
    assert revision["report"]["status"] == "current"
    completed_session = database.get_scenario_session(session_id)
    assert completed_session["status"] == "report_ready"
    assert completed_session["current_state"]["status"] == "report_ready"
    assert b"Package width" in exported_md.body
    assert exported_pdf.body.startswith(b"%PDF-")
    assert shared.status_code == 201
    shared_html = page.body.decode()
    assert "fit_with_conditions" in shared_html
    assert "/root/private" not in shared_html
    assert "question_history" not in shared_html


def test_superseded_report_is_preserved_while_new_revision_becomes_current() -> None:
    session_id = "SCNSESSION-REVISIONS"
    state_v1 = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state_v1,
    )
    job1, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-REVISION1",
        session_id=session_id,
        state_version=state_v1["state_version"],
        catalog_revision="cat-1",
        evidence_revision="wiki-1",
        pipeline_version="v2",
        language="en",
        idempotency_key="revision-1",
    )
    old = database.create_scenario_report_revision(
        report_revision_id="REPORT-REVISION1",
        session_id=session_id,
        state_version=state_v1["state_version"],
        analysis_job_id=job1["job_id"],
        status="superseded",
        report={"conclusion": "insufficient_evidence"},
    )
    state_v2 = dict(state_v1)
    state_v2["state_version"] += 1
    state_v2["requirements"] = [
        {
            "original_text": "Low-cost environment modifications are allowed",
            "normalized_value": True,
            "knowledge_state": "known",
            "owner": "customer",
            "can_change_conclusion": True,
            "last_changed_version": state_v2["state_version"],
        }
    ]
    database.save_scenario_state_version(
        session_id=session_id,
        expected_version=state_v1["state_version"],
        state=state_v2,
        change_source="confirmed_requirement_change",
        actor_user_id=None,
    )
    job2, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-REVISION2",
        session_id=session_id,
        state_version=state_v2["state_version"],
        catalog_revision="cat-1",
        evidence_revision="wiki-1",
        pipeline_version="v2",
        language="en",
        idempotency_key="revision-2",
    )
    current = database.create_scenario_report_revision(
        report_revision_id="REPORT-REVISION2",
        session_id=session_id,
        state_version=state_v2["state_version"],
        analysis_job_id=job2["job_id"],
        status="current",
        report={"conclusion": "fit_with_conditions"},
        diff_summary="Environment modification permission was confirmed",
    )
    reports = database.list_scenario_report_revisions(session_id)
    assert old["is_current"] is False
    assert current["is_current"] is True
    assert [item["ordinal"] for item in reports] == [2, 1]
    assert reports[1]["status"] == "superseded"


def test_allowlisted_state_patch_updates_dynamic_spec_and_rejects_runtime_fields() -> None:
    state = initial_state("SCNSESSION-PATCH", "Inspect parcels")
    patched = apply_state_patch(
        state,
        [
            {"op": "set", "path": "environment.lighting", "value": "300-500 lux"},
            {
                "op": "upsert",
                "path": "facts",
                "value": {
                    "semantic_key": "locker.opening.width",
                    "normalized_value": "450 mm",
                    "knowledge_state": "known",
                    "owner": "customer",
                },
            },
        ],
    )
    assert patched["environment"]["lighting"] == "300-500 lux"
    assert patched["facts"][0]["semantic_key"] == "locker.opening.width"
    state["current_question"] = {
        "question_id": "Q-DYNAMIC",
        "semantic_key": "locker.internal.depth",
        "question": "What is the locker depth?",
        "options": ["400 mm"],
    }
    answered = apply_answer(
        state,
        question_id="Q-DYNAMIC",
        answer="520 mm",
        answer_mode="custom",
    )
    assert answered["facts"][0]["semantic_key"] == "locker.internal.depth"
    assert answered["facts"][0]["normalized_value"] == "520 mm"
    with pytest.raises(ValueError, match="not allowed"):
        apply_state_patch(
            state,
            [{"op": "set", "path": "status.value", "value": "report_ready"}],
        )


def test_ecs_requests_worker_evidence_and_returns_validated_state_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _minimum_ready_state("SCNSESSION-EVIDENCE")
    gateway.websocket = object()

    async def fake_command(message_type: str, **payload):
        assert message_type == "grill_scenario"
        assert payload["retrieve_evidence_context"] is True
        assert payload["scenario_state"]["minimum_gate"]["passed"] is True
        return {
            "status": "ok",
            "candidate_questions": [],
            "candidate_issues": [],
            "state_patch": [
                {"op": "set", "path": "environment.lighting", "value": "controlled"}
            ],
        }

    monkeypatch.setattr(gateway, "command", fake_command)
    candidates, issues, patches = asyncio.run(
        routes._candidate_questions(
            state,
            model_id=database.get_allowed_teams()[0],
            language="en",
            user_message="Lighting is controlled",
        )
    )
    assert candidates
    assert issues == []
    assert apply_state_patch(state, patches)["environment"]["lighting"] == "controlled"


def test_answer_route_applies_worker_patch_to_authoritative_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-ROUTE-PATCH"
    token = "route-patch-token"
    state = _minimum_ready_state(session_id)
    state["status"] = "refining"
    state["countdown_suppressed_at_version"] = state["state_version"]
    state["current_question"] = {
        "question_id": "Q-LIGHTING",
        "semantic_key": "environment.lighting_range",
        "question": "What lighting range is available?",
        "reason_for_asking": "Lighting changes perception evidence applicability.",
        "decision_impact": ["feasibility"],
        "can_change_conclusion": True,
        "blocking": False,
        "target_owner": "customer",
        "answer_type": "single_select_or_custom",
        "options": ["300-500 lux"],
        "prerequisite_keys": [],
        "refines_question_id": None,
    }
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash=routes._token_hash(token),
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    gateway.websocket = object()

    async def fake_command(message_type: str, **payload):
        return {
            "status": "ok",
            "candidate_questions": [],
            "candidate_issues": [],
            "state_patch": [
                {
                    "op": "set",
                    "path": "environment.lighting",
                    "value": "300-500 lux",
                }
            ],
        }

    monkeypatch.setattr(gateway, "command", fake_command)
    response = asyncio.run(
        routes.answer_route(
            session_id,
            routes.AnswerRequest(
                expected_state_version=state["state_version"],
                question_id="Q-LIGHTING",
                answer_mode="option",
                answer="300-500 lux",
            ),
            _request(),
            x_scenario_resume_token=token,
        )
    )
    updated = json.loads(response.body)["session"]["current_state"]
    assert response.status_code == 200
    assert updated["environment"]["lighting"] == "300-500 lux"
    assert any(
        item.get("semantic_key") == "environment.lighting_range"
        for item in updated["facts"]
    )


def _create_current_report(session_id: str, state: dict) -> str:
    job, _ = database.create_scenario_analysis_job_record(
        job_id=f"JOB-{session_id}",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog",
        evidence_revision="evidence",
        pipeline_version="v2",
        language="en",
        idempotency_key=f"idempotency-{session_id}",
    )
    database.update_scenario_analysis_job_record(job["job_id"], status="completed")
    revision = database.create_scenario_report_revision(
        report_revision_id=f"REPORT-{session_id}",
        session_id=session_id,
        state_version=state["state_version"],
        analysis_job_id=job["job_id"],
        status="current",
        report={"conclusion": "fit_with_conditions", "conditions": []},
    )
    return str(revision["report_revision_id"])


def test_structured_intent_classification_overrides_question_mark_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-INTENT"
    token = "intent-token"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash=routes._token_hash(token),
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    _create_current_report(session_id, state)
    gateway.websocket = object()

    async def fake_command(message_type: str, **payload):
        assert message_type == "classify_scenario_message"
        return {
            "status": "ok",
            "intent": "requirement_change",
            "proposed_change": "Reduce the maximum payload to 2 kg?",
        }

    monkeypatch.setattr(gateway, "command", fake_command)
    response = asyncio.run(
        routes.message_route(
            session_id,
            routes.MessageRequest(
                expected_state_version=state["state_version"],
                message="Reduce the maximum payload to 2 kg?",
            ),
            _request(),
            x_scenario_resume_token=token,
        )
    )
    body = json.loads(response.body)
    assert body["intent"] == "requirement_change"
    assert body["confirmation_required"] is True


def test_confirmed_refinement_keeps_report_visible_and_queues_reanalysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-REFINE"
    token = "refine-token"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash=routes._token_hash(token),
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    report_id = _create_current_report(session_id, state)
    gateway.websocket = object()

    async def fake_queue(scenario: dict, *, trigger: str):
        assert trigger == "coalesced_reanalysis"
        database.update_scenario_session_status(session_id, status="analyzing")
        return {"job_id": "JOB-COALESCED"}, True

    monkeypatch.setattr(routes, "_queue_analysis", fake_queue)
    response = asyncio.run(
        routes.confirm_change_route(
            session_id,
            routes.ConfirmChangeRequest(
                expected_state_version=state["state_version"],
                confirmed=True,
                proposed_change="Locker opening width is at least 450 mm",
            ),
            _request(),
            x_scenario_resume_token=token,
        )
    )
    body = json.loads(response.body)
    loaded = database.get_scenario_session(session_id)
    assert response.status_code == 202
    assert body["analysis_job_id"] == "JOB-COALESCED"
    assert loaded["current_report_revision_id"] == report_id
    assert loaded["status"] == loaded["current_state"]["status"] == "analyzing"
    assert database.get_scenario_report_revision(session_id, report_id)["status"] == "superseded"


def test_change_during_analysis_reuses_running_job_and_defers_one_reanalysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-INFLIGHT"
    token = "inflight-token"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash=routes._token_hash(token),
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    report_id = _create_current_report(session_id, state)
    active, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-INFLIGHT",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog-new",
        evidence_revision="evidence-new",
        pipeline_version="v2-new",
        language="en",
        idempotency_key="inflight-analysis",
    )
    database.update_scenario_analysis_job_record(active["job_id"], status="processing")
    gateway.websocket = object()

    async def forbidden_queue(*args, **kwargs):
        raise AssertionError("A second analysis must not be queued while one is running")

    monkeypatch.setattr(routes, "_queue_analysis", forbidden_queue)
    response = asyncio.run(
        routes.confirm_change_route(
            session_id,
            routes.ConfirmChangeRequest(
                expected_state_version=state["state_version"],
                confirmed=True,
                proposed_change="Allow low-cost fiducial markers",
            ),
            _request(),
            x_scenario_resume_token=token,
        )
    )
    body = json.loads(response.body)
    loaded = database.get_scenario_session(session_id)
    assert response.status_code == 202
    assert body["analysis_job_id"] == "JOB-INFLIGHT"
    assert loaded["current_state_version"] == state["state_version"] + 1
    assert loaded["current_report_revision_id"] == report_id
    assert loaded["status"] == loaded["current_state"]["status"] == "analyzing"


def test_sse_is_long_lived_and_browser_reconnects_after_normal_eof() -> None:
    route_source = Path("ecs/app/routes/scenario_sessions.py").read_text(encoding="utf-8")
    page = Path("ecs/app/templates/capability_match.html").read_text(encoding="utf-8")
    assert "for _ in range(20)" not in route_source
    assert "while not await request.is_disconnected()" in route_source
    assert "retry: 2000" in route_source
    assert "eventReconnectTimer=setTimeout(connectEvents,2000)" in page


def test_keep_asking_attaches_a_real_question_instead_of_dead_ending() -> None:
    session_id = "SCNSESSION-KEEP-ASKING"
    token = "keep-asking-token"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash=routes._token_hash(token),
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    response = asyncio.run(
        routes.keep_asking_route(
            session_id,
            routes.AnalyzeNowRequest(
                expected_state_version=state["state_version"],
                trigger="automatic_stability",
            ),
            _request(),
            x_scenario_resume_token=token,
        )
    )
    updated = json.loads(response.body)["session"]["current_state"]
    assert response.status_code == 200
    assert updated["status"] == "refining"
    assert updated["current_question"] is not None
    assert updated["current_question"]["semantic_key"] == "deployment.stage"


def test_first_superseded_snapshot_remains_visible_during_reanalysis() -> None:
    session_id = "SCNSESSION-FIRST-SUPERSEDED"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    job, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-FIRST-SUPERSEDED",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog",
        evidence_revision="evidence",
        pipeline_version="v2",
        language="en",
        idempotency_key="first-superseded",
    )
    database.update_scenario_session_status(session_id, status="analyzing")
    revision = database.create_scenario_report_revision(
        report_revision_id="REPORT-FIRST-SUPERSEDED",
        session_id=session_id,
        state_version=state["state_version"],
        analysis_job_id=job["job_id"],
        status="superseded",
        report={"conclusion": "prototype_required"},
    )
    loaded = database.get_scenario_session(session_id)
    assert revision["is_current"] is False
    assert loaded["current_report_revision_id"] == revision["report_revision_id"]
    assert loaded["status"] == loaded["current_state"]["status"] == "analyzing"


def test_restart_recovers_interrupted_analysis_status_canonically() -> None:
    session_id = "SCNSESSION-RESTART"
    state = _minimum_ready_state(session_id)
    state["status"] = "analyzing"
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    job, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-RESTART",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog",
        evidence_revision="evidence",
        pipeline_version="v2",
        language="en",
        idempotency_key="restart-analysis",
    )
    database.update_scenario_analysis_job_record(job["job_id"], status="processing")
    database.initialize_database()
    loaded = database.get_scenario_session(session_id)
    assert database.get_scenario_analysis_job(job["job_id"])["status"] == "failed"
    assert loaded["status"] == loaded["current_state"]["status"] == "minimum_ready"
