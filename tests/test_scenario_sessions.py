from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import re
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
    evaluate_state,
    initial_state,
    select_question,
)
from worker.analysis_progress import progress_event, sanitize_summary
from worker.manager import WorkerManager
from worker import scenario_clarification


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


def test_analysis_failed_status_survives_state_evaluation() -> None:
    state = _minimum_ready_state("SCNSESSION-FAILED-STATE")
    state["status"] = "analysis_failed"
    evaluated = evaluate_state(state)
    assert evaluated["status"] == "analysis_failed"


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
        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scenario_sessions)")
        }
        job_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(scenario_analysis_jobs)")
        }
    assert {
        "scenario_sessions",
        "scenario_state_versions",
        "scenario_events",
        "scenario_analysis_jobs",
        "scenario_report_revisions",
        "scenario_share_links",
    } <= tables
    assert "pending_reanalysis_state_version" in session_columns
    assert {"attempt_count", "trigger"} <= job_columns


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
        await manager.route_message(
            {
                "type": "answer_scenario_report_question",
                "id": "report-answer-1",
                "model_id": "walker_s2",
                "language": "en",
                "user_question": "What is the payload limit?",
                "approved_report": {"conditions": ["Payload below 2 kg"]},
            }
        )

    asyncio.run(queue_one())
    assert manager.clarification_queue.qsize() == 2
    assert manager.capability_match_queue.qsize() == 0
    assert manager.clarification_queue.get_nowait()["type"] == "grill_scenario"
    assert (
        manager.clarification_queue.get_nowait()["type"]
        == "answer_scenario_report_question"
    )


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
    assert 'id="process-indicator"' in page
    assert "Paused · Worker offline" in page
    assert "Working · analysis in progress" in page
    assert "Complete · report ready" in page
    assert "Auto-select from scenario" in page
    assert "renderConversationHistory" in page
    assert "dataset.scenarioHistory" in page
    assert "waiting-pulse" in page
    assert "pendingOperation" in page
    assert "Analysis failed · retry available" in page
    assert "!scenario.current_report_revision_id" in page


def test_workbench_registers_every_referenced_element() -> None:
    page = Path("ecs/app/templates/capability_match.html").read_text(encoding="utf-8")
    registry_match = re.search(
        r"const els=Object\.fromEntries\(\[(.*?)\]\.map",
        page,
        flags=re.DOTALL,
    )
    assert registry_match is not None
    registered = set(re.findall(r"'([^']+)'", registry_match.group(1)))
    dot_references = set(re.findall(r"\bels\.([A-Za-z][A-Za-z0-9_-]*)", page))
    bracket_references = set(re.findall(r"\bels\['([^']+)'\]", page))

    assert dot_references | bracket_references <= registered
    assert "thread" in registered


def test_robot_auto_selection_prefers_customer_choice_name_and_scenario_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes,
        "get_allowed_teams",
        lambda: ["tian_gong", "walker_s2", "walker_c1"],
    )
    monkeypatch.setattr(
        routes,
        "get_robot_options",
        lambda **_kwargs: [
            {"name": "tian_gong", "english_name": "Tiangong", "chinese_name": "天工", "description": "Outdoor locomotion"},
            {"name": "walker_s2", "english_name": "Walker S2", "chinese_name": "优必选 S2", "description": "Customer service"},
            {"name": "walker_c1", "english_name": "Walker C1", "chinese_name": "优必选 C1", "description": "Factory warehouse logistics sorting 仓库物流分拣"},
        ],
    )
    monkeypatch.setattr(routes, "ALLOWED_TEAMS", ["tian_gong", "walker_s2", "walker_c1"])

    selected = routes._select_scenario_model("Use a robot in warehouse sorting", "auto")
    assert selected["model_id"] == "walker_c1"
    assert selected["mode"] == "scenario_fit"

    selected_zh = routes._select_scenario_model("在仓库里完成物流分拣", "auto")
    assert selected_zh["model_id"] == "walker_c1"
    assert selected_zh["mode"] == "scenario_fit"

    named = routes._select_scenario_model("Use Walker S2 for warehouse sorting", "auto")
    assert named["model_id"] == "walker_s2"
    assert named["mode"] == "named_in_scenario"

    customer = routes._select_scenario_model("Reception task", "walker_c1")
    assert customer["model_id"] == "walker_c1"
    assert customer["mode"] == "customer_selected"

    fallback = routes._select_scenario_model("Do a new task", "auto")
    assert fallback["model_id"] == "tian_gong"
    assert fallback["mode"] == "configured_default"


def test_create_session_returns_the_automatically_selected_robot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "get_allowed_teams", lambda: ["walker_s2", "walker_c1"])
    monkeypatch.setattr(
        routes,
        "get_robot_options",
        lambda **_kwargs: [
            {"name": "walker_s2", "english_name": "Walker S2", "chinese_name": "S2", "description": "Customer service"},
            {"name": "walker_c1", "english_name": "Walker C1", "chinese_name": "C1", "description": "Factory warehouse logistics sorting"},
        ],
    )

    response = asyncio.run(
        routes.create_session_route(
            routes.CreateSessionRequest(
                initial_intent="Automate warehouse sorting",
                language="en",
            ),
            _request(),
        )
    )
    data = json.loads(response.body)

    assert response.status_code == 201
    assert data["model_selection"]["model_id"] == "walker_c1"
    assert data["model_selection"]["mode"] == "scenario_fit"
    assert data["session"]["model_id"] == "walker_c1"
    saved = database.get_scenario_session(data["session"]["session_id"])
    assert saved is not None
    assert saved["model_id"] == "walker_c1"


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


def _register_parked_analysis_attempt(job: dict) -> bool:
    async def parked() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(parked())
    routes._analysis_tasks[str(job["job_id"])] = routes._AnalysisTaskRegistration(
        task=task,
        attempt_count=int(job.get("attempt_count") or 1),
    )
    return True


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
        await asyncio.gather(
            *(registration.task for registration in routes._analysis_tasks.values())
        )
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
    actor_patched = apply_state_patch(
        state,
        [{"op": "upsert", "path": "actors", "value": {"actor_id": "operator", "name": "Operator"}}],
    )
    actor_patched = apply_state_patch(
        actor_patched,
        [{"op": "upsert", "path": "actors", "value": {"actor_id": "operator", "name": "Supervisor"}}],
    )
    assert actor_patched["actors"] == [{"actor_id": "operator", "name": "Supervisor"}]
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
    invalid_values = [
        {"op": "set", "path": "workflow.steps", "value": "navigate -> grasp"},
        {"op": "set", "path": "goal.confirmation", "value": True},
        {
            "op": "set",
            "path": "operating_profile.payload_kg",
            "value": {"unexpected": "object"},
        },
        {
            "op": "upsert",
            "path": "candidate_solution_paths",
            "value": {"semantic_key": "model-derived-path"},
        },
        {
            "op": "upsert",
            "path": "facts",
            "value": {
                "semantic_key": "payload.boundary",
                "normalized_value": {"unexpected": "object"},
            },
        },
    ]
    for invalid in invalid_values:
        with pytest.raises(ValueError):
            apply_state_patch(state, [invalid])


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
                },
                {
                    "op": "set",
                    "path": "workflow.steps",
                    "value": "navigate -> grasp",
                },
                {"op": "set", "path": "goal.confirmation", "value": True},
                {
                    "op": "set",
                    "path": "operating_profile.payload_kg",
                    "value": {"unexpected": "object"},
                },
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
    assert isinstance(updated["workflow"]["steps"], list)
    assert updated["goal"]["confirmation"] == "confirmed"
    assert "payload_kg" not in updated["operating_profile"]


def test_report_finalization_closes_state_change_race_and_queues_latest_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-FINALIZE-RACE"
    state_v1 = _minimum_ready_state(session_id)
    scenario = database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state_v1,
    )
    job, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-FINALIZE-RACE",
        session_id=session_id,
        state_version=state_v1["state_version"],
        catalog_revision="catalog-current",
        evidence_revision="wiki-current",
        pipeline_version=routes.PIPELINE_VERSION,
        language="en",
        idempotency_key="finalize-race-v1",
    )
    gateway.websocket = object()

    async def fake_command(message_type: str, **payload):
        return {"status": "ok", "result": _analysis_result()}

    monkeypatch.setattr(gateway, "command", fake_command)
    real_finalize = database.finalize_scenario_analysis_report
    inserted_change = False

    def finalize_after_state_change(**kwargs):
        nonlocal inserted_change
        if not inserted_change:
            inserted_change = True
            state_v2 = json.loads(json.dumps(state_v1))
            state_v2["state_version"] += 1
            state_v2["status"] = "analyzing"
            state_v2["requirements"].append(
                {
                    "requirement_id": "REQ-RACE-CHANGE",
                    "semantic_key": "locker.width.minimum",
                    "original_text": "Locker opening is at least 450 mm",
                    "normalized_value": "450 mm",
                    "knowledge_state": "known",
                    "owner": "customer",
                    "last_changed_version": state_v2["state_version"],
                }
            )
            database.save_scenario_state_version(
                session_id=session_id,
                expected_version=state_v1["state_version"],
                state=state_v2,
                change_source="race_test_change",
                actor_user_id=None,
            )
        return real_finalize(**kwargs)

    queued_versions: list[int] = []

    async def fake_queue(latest: dict, *, trigger: str):
        assert trigger == "coalesced_reanalysis"
        latest_version = int(latest["current_state_version"])
        queued_versions.append(latest_version)
        queued_job, _ = database.create_scenario_analysis_job_record(
            job_id="JOB-FINALIZE-RACE-V2",
            session_id=session_id,
            state_version=latest_version,
            catalog_revision="catalog-current",
            evidence_revision="wiki-current",
            pipeline_version=routes.PIPELINE_VERSION,
            language="en",
            idempotency_key="finalize-race-v2",
        )
        database.update_scenario_session_status(session_id, status="analyzing")
        _register_parked_analysis_attempt(queued_job)
        return queued_job, True

    monkeypatch.setattr(routes, "finalize_scenario_analysis_report", finalize_after_state_change)
    monkeypatch.setattr(routes, "_queue_analysis", fake_queue)
    asyncio.run(routes._run_analysis(job, state_v1, partial=False))

    loaded = database.get_scenario_session(session_id)
    reports = database.list_scenario_report_revisions(session_id)
    completed_job = database.get_scenario_analysis_job(job["job_id"])
    assert inserted_change is True
    assert loaded["current_state_version"] == state_v1["state_version"] + 1
    assert reports[0]["scenario_state_version"] == state_v1["state_version"]
    assert reports[0]["status"] == "superseded"
    assert reports[0]["is_current"] is False
    assert completed_job["status"] == "completed"
    assert completed_job["superseded"] == 1
    assert queued_versions == [state_v1["state_version"] + 1]
    assert database.get_active_scenario_analysis_job(session_id)["job_id"] == "JOB-FINALIZE-RACE-V2"
    assert database.get_pending_scenario_reanalysis_version(session_id) is None
    assert loaded["status"] == loaded["current_state"]["status"] == "analyzing"


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


def test_report_question_is_answered_by_ai_from_approved_report_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-REPORT-ANSWER"
    token = "report-answer-token"
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
    with sqlite3.connect(database.DATABASE_PATH) as connection:
        report = database.get_scenario_report_revision(session_id, report_id)["report"]
        report["conditions"] = ["Payload must remain below 2 kg"]
        report["technical_details"] = {"private_path": "/root/private/wiki.md"}
        connection.execute(
            "UPDATE scenario_report_revisions SET report_json = ? WHERE report_revision_id = ?",
            (json.dumps(report), report_id),
        )
    gateway.websocket = object()
    calls: list[str] = []

    async def fake_command(message_type: str, **payload):
        calls.append(message_type)
        if message_type == "classify_scenario_message":
            return {"status": "ok", "intent": "report_question", "proposed_change": None}
        assert message_type == "answer_scenario_report_question"
        assert payload["user_question"] == "What is the payload limit?"
        assert payload["approved_report"]["conditions"] == ["Payload must remain below 2 kg"]
        assert "technical_details" not in payload["approved_report"]
        assert "/root/private" not in json.dumps(payload["approved_report"])
        return {
            "status": "ok",
            "answer": "The report limits payload to below 2 kg.",
            "citations": [{"section": "conditions", "index": 0}],
        }

    monkeypatch.setattr(gateway, "command", fake_command)
    response = asyncio.run(
        routes.message_route(
            session_id,
            routes.MessageRequest(
                expected_state_version=state["state_version"],
                message="What is the payload limit?",
            ),
            _request(),
            x_scenario_resume_token=token,
        )
    )
    body = json.loads(response.body)
    assert calls == ["classify_scenario_message", "answer_scenario_report_question"]
    assert body["response"] == "The report limits payload to below 2 kg."
    assert body["report_citations"] == [{"section": "conditions", "index": 0}]


def test_structured_clarification_uses_supported_no_tools_process_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = initial_state("SCNSESSION-CLAUDE-CONTRACT", "Retrieve parcels")
    captured: dict = {}

    async def fake_process(prompt: str, **kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "state_patch": [],
                "candidate_questions": [],
                "candidate_issues": [],
                "intent": "requirement_answer",
                "model_readiness_opinion": {"stable": False, "reason": "More detail needed"},
            }
        )

    monkeypatch.setattr(scenario_clarification, "run_claude_process", fake_process)
    result = asyncio.run(
        scenario_clarification.clarify_scenario(
            state,
            model_id=database.get_allowed_teams()[0],
            language="en",
        )
    )
    assert result["status"] == "ok"
    assert captured["tools"] == ()
    assert captured["system_prompt"]
    assert "extra_args" not in captured
    patch_variants = scenario_clarification.CLARIFICATION_RESPONSE_SCHEMA[
        "properties"
    ]["state_patch"]["items"]["oneOf"]
    facts_variant = next(
        variant
        for variant in patch_variants
        if variant["properties"]["path"].get("const") == "facts"
    )
    assert "semantic_key" in facts_variant["properties"]["value"]["properties"]
    assert "actor_id" not in facts_variant["properties"]["value"]["properties"]


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


def test_confirm_change_rechecks_job_if_analysis_finishes_during_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-CONFIRM-RACE"
    token = "confirm-race-token"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash=routes._token_hash(token),
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    active, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-CONFIRM-RACE-V1",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog-v1",
        evidence_revision="evidence-v1",
        pipeline_version="pipeline-v1",
        language="en",
        idempotency_key="confirm-race-v1",
    )
    database.update_scenario_analysis_job_record(active["job_id"], status="processing")
    gateway.websocket = object()
    real_get_active = database.get_active_scenario_analysis_job
    active_reads = 0

    def finish_after_first_active_read(requested_session_id: str):
        nonlocal active_reads
        active_reads += 1
        if active_reads == 1:
            observed = real_get_active(requested_session_id)
            database.update_scenario_analysis_job_record(
                active["job_id"], status="completed"
            )
            return observed
        return real_get_active(requested_session_id)

    queued_versions: list[int] = []

    async def fake_queue(latest: dict, *, trigger: str):
        queued_versions.append(int(latest["current_state_version"]))
        database.update_scenario_session_status(session_id, status="analyzing")
        return {"job_id": "JOB-CONFIRM-RACE-V2"}, True

    monkeypatch.setattr(
        routes, "get_active_scenario_analysis_job", finish_after_first_active_read
    )
    monkeypatch.setattr(routes, "_queue_analysis", fake_queue)
    response = asyncio.run(
        routes.confirm_change_route(
            session_id,
            routes.ConfirmChangeRequest(
                expected_state_version=state["state_version"],
                confirmed=True,
                proposed_change="Require a 2 kg payload limit",
            ),
            _request(),
            x_scenario_resume_token=token,
        )
    )
    body = json.loads(response.body)
    assert active_reads == 2
    assert queued_versions == [state["state_version"] + 1]
    assert body["analysis_job_id"] == "JOB-CONFIRM-RACE-V2"
    assert response.status_code == 202


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
    assert loaded["status"] == loaded["current_state"]["status"] == "analysis_failed"


def test_restart_repairs_legacy_silently_failed_analysis_state() -> None:
    session_id = "SCNSESSION-LEGACY-SILENT-FAILURE"
    state = _minimum_ready_state(session_id)
    state["status"] = "minimum_ready"
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    job, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-LEGACY-SILENT-FAILURE",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog",
        evidence_revision="evidence",
        pipeline_version="v2",
        language="en",
        idempotency_key="legacy-silent-failure",
    )
    database.update_scenario_analysis_job_record(job["job_id"], status="failed")

    database.initialize_database()
    database.initialize_database()

    loaded = database.get_scenario_session(session_id)
    assert loaded["status"] == loaded["current_state"]["status"] == "analysis_failed"
    assert database.get_scenario_analysis_job(job["job_id"])["status"] == "failed"


def _save_newer_scenario_state(
    session_id: str, previous: dict, *, suffix: str
) -> dict:
    updated = deepcopy(previous)
    updated["state_version"] = int(previous["state_version"]) + 1
    updated["status"] = "analyzing"
    updated["requirements"].append(
        {
            "requirement_id": f"REQ-{suffix}",
            "semantic_key": f"recovery.{suffix.casefold()}",
            "original_text": f"Recovery change {suffix}",
            "normalized_value": f"Recovery change {suffix}",
            "knowledge_state": "known",
            "owner": "customer",
            "last_changed_version": updated["state_version"],
        }
    )
    database.save_scenario_state_version(
        session_id=session_id,
        expected_version=int(previous["state_version"]),
        state=updated,
        change_source="recovery_test",
        actor_user_id=None,
    )
    return updated


def test_stale_analysis_failure_cannot_overwrite_newer_state() -> None:
    session_id = "SCNSESSION-STALE-FAILURE"
    state_v1 = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state_v1,
    )
    state_v2 = _save_newer_scenario_state(session_id, state_v1, suffix="V2")

    changed = database.mark_scenario_analysis_failed(
        session_id,
        state_version=state_v1["state_version"],
    )

    loaded = database.get_scenario_session(session_id)
    assert changed is False
    assert loaded["current_state_version"] == state_v2["state_version"]
    assert loaded["status"] == loaded["current_state"]["status"] == "analyzing"


def test_restart_dispatches_marker_left_after_report_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-RECOVER-FINALIZED"
    state_v1 = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state_v1,
    )
    old_job, _ = database.create_scenario_analysis_job_record(
        job_id="JOB-RECOVER-FINALIZED-V1",
        session_id=session_id,
        state_version=state_v1["state_version"],
        catalog_revision="catalog-current",
        evidence_revision="wiki-current",
        pipeline_version=routes.PIPELINE_VERSION,
        language="en",
        idempotency_key="recover-finalized-v1",
    )
    state_v2 = _save_newer_scenario_state(session_id, state_v1, suffix="V2")
    database.finalize_scenario_analysis_report(
        report_revision_id="REPORT-RECOVER-FINALIZED-V1",
        session_id=session_id,
        state_version=state_v1["state_version"],
        analysis_job_id=old_job["job_id"],
        partial=False,
        report={"conclusion": "fit_with_conditions"},
    )
    assert database.get_pending_scenario_reanalysis_version(session_id) == state_v2["state_version"]

    database.initialize_database()
    assert database.get_active_scenario_analysis_job(session_id) is None
    assert database.get_pending_scenario_reanalysis_version(session_id) == state_v2["state_version"]

    launched: list[tuple[str, int]] = []
    def fake_launch(job: dict, state: dict, *, trigger: str) -> bool:
        launched.append((str(job["job_id"]), int(state["state_version"])))
        return _register_parked_analysis_attempt(job)

    monkeypatch.setattr(routes, "_launch_analysis_task", fake_launch)
    gateway.websocket = object()
    stats = asyncio.run(routes.reconcile_pending_scenario_reanalyses())
    active = database.get_active_scenario_analysis_job(session_id)
    assert stats["reconciled"] == 1
    assert active["scenario_state_version"] == state_v2["state_version"]
    assert active["trigger"] == "coalesced_reanalysis"
    assert launched == [(active["job_id"], state_v2["state_version"])]
    assert database.get_pending_scenario_reanalysis_version(session_id) is None


@pytest.mark.parametrize("clear_before_restart", [False, True])
def test_restart_retries_job_created_around_marker_clear(
    monkeypatch: pytest.MonkeyPatch,
    clear_before_restart: bool,
) -> None:
    session_id = "SCNSESSION-RECOVER-QUEUED"
    state = _minimum_ready_state(session_id)
    scenario = database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    database.mark_scenario_reanalysis_pending(session_id, state["state_version"])
    launches: list[str] = []
    def fake_launch(job: dict, _state: dict, *, trigger: str) -> bool:
        launches.append(str(job["job_id"]))
        return _register_parked_analysis_attempt(job)

    monkeypatch.setattr(routes, "_launch_analysis_task", fake_launch)
    gateway.websocket = object()
    queued, started = asyncio.run(
        routes._queue_analysis(scenario, trigger="coalesced_reanalysis")
    )
    assert started is True
    assert database.get_pending_scenario_reanalysis_version(session_id) == state["state_version"]
    if clear_before_restart:
        database.clear_pending_scenario_reanalysis(
            session_id, through_state_version=state["state_version"]
        )
        assert database.get_pending_scenario_reanalysis_version(session_id) is None

    database.initialize_database()
    interrupted = database.get_scenario_analysis_job(queued["job_id"])
    assert interrupted["status"] == "failed"
    assert interrupted["attempt_count"] == 1
    assert database.get_pending_scenario_reanalysis_version(session_id) == state["state_version"]
    launches.clear()
    stats = asyncio.run(routes.reconcile_pending_scenario_reanalyses())
    retried = database.get_scenario_analysis_job(queued["job_id"])
    assert stats["reconciled"] == 1
    assert retried["status"] == "queued"
    assert retried["attempt_count"] == 2
    assert launches == [queued["job_id"]]
    assert database.get_pending_scenario_reanalysis_version(session_id) is None


def test_offline_marker_waits_for_worker_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-RECOVER-RECONNECT"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    database.mark_scenario_reanalysis_pending(session_id, state["state_version"])
    gateway.websocket = None
    offline = asyncio.run(routes.reconcile_pending_scenario_reanalyses())
    assert offline["reconciled"] == 0
    assert offline["pending"] == offline["deferred"] == 1
    assert database.get_active_scenario_analysis_job(session_id) is None
    assert database.get_pending_scenario_reanalysis_version(session_id) == state["state_version"]

    launched: list[str] = []
    def fake_launch(job: dict, _state: dict, *, trigger: str) -> bool:
        launched.append(str(job["job_id"]))
        return _register_parked_analysis_attempt(job)

    monkeypatch.setattr(routes, "_launch_analysis_task", fake_launch)
    gateway.websocket = object()
    connected = asyncio.run(routes.reconcile_pending_scenario_reanalyses())
    assert connected["reconciled"] == 1
    assert launched
    assert database.get_pending_scenario_reanalysis_version(session_id) is None
    assert "scenario_reanalysis_dispatcher" in Path("ecs/app/main.py").read_text(encoding="utf-8")
    assert "reconcile_pending_scenario_reanalyses" in Path(
        "ecs/app/routes/worker_socket.py"
    ).read_text(encoding="utf-8")


def test_failed_logical_analysis_reopens_one_operational_attempt() -> None:
    session_id = "SCNSESSION-FAILED-ATTEMPT"
    state = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    logical_key = hashlib.sha256(b"failed-logical-analysis").hexdigest()
    original, created = database.create_scenario_analysis_job_record(
        job_id="JOB-FAILED-ATTEMPT-1",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog-current",
        evidence_revision="wiki-current",
        pipeline_version=routes.PIPELINE_VERSION,
        language="en",
        idempotency_key=logical_key,
        trigger="coalesced_reanalysis",
    )
    assert created is True
    database.update_scenario_analysis_job_record(original["job_id"], status="failed")
    retried, should_start = database.create_scenario_analysis_job_record(
        job_id="JOB-FAILED-ATTEMPT-2",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog-current",
        evidence_revision="wiki-current",
        pipeline_version=routes.PIPELINE_VERSION,
        language="en",
        idempotency_key=logical_key,
        trigger="coalesced_reanalysis",
    )
    duplicate, duplicate_start = database.create_scenario_analysis_job_record(
        job_id="JOB-FAILED-ATTEMPT-3",
        session_id=session_id,
        state_version=state["state_version"],
        catalog_revision="catalog-current",
        evidence_revision="wiki-current",
        pipeline_version=routes.PIPELINE_VERSION,
        language="en",
        idempotency_key=logical_key,
        trigger="coalesced_reanalysis",
    )
    assert retried["job_id"] == duplicate["job_id"] == original["job_id"]
    assert should_start is True
    assert duplicate_start is False
    assert retried["attempt_count"] == duplicate["attempt_count"] == 2


def test_retry_launches_new_attempt_before_old_task_finally_and_keeps_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-LIVE-RETRY-RACE"
    state = _minimum_ready_state(session_id)
    scenario = database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state,
    )
    old_failure_paused = asyncio.Event()
    allow_old_finally = asyncio.Event()
    retry_started = asyncio.Event()
    allow_retry_finish = asyncio.Event()
    command_calls = 0

    async def fake_progress(
        _session_id: str,
        _job_id: str,
        _state_version: int,
        _stage: str,
        status: str,
        _approved_facts=None,
    ) -> None:
        if status == "failed":
            old_failure_paused.set()
            await allow_old_finally.wait()

    async def fake_command(message_type: str, **_payload):
        nonlocal command_calls
        assert message_type == "analyze_scenario"
        command_calls += 1
        if command_calls == 1:
            raise RuntimeError("attempt one failed")
        retry_started.set()
        await allow_retry_finish.wait()
        return {"status": "ok", "result": _analysis_result()}

    monkeypatch.setattr(routes, "_emit_progress", fake_progress)
    monkeypatch.setattr(gateway, "command", fake_command)
    gateway.websocket = object()

    async def exercise() -> None:
        job, started = await routes._queue_analysis(
            scenario, trigger="coalesced_reanalysis"
        )
        assert started is True
        first_registration = routes._analysis_tasks[job["job_id"]]
        assert first_registration.attempt_count == 1
        await asyncio.wait_for(old_failure_paused.wait(), timeout=2)
        assert database.get_scenario_analysis_job(job["job_id"])["status"] == "failed"
        assert database.get_pending_scenario_reanalysis_version(session_id) == state["state_version"]

        recovery = await routes.reconcile_pending_scenario_reanalyses()
        retried = database.get_scenario_analysis_job(job["job_id"])
        second_registration = routes._analysis_tasks[job["job_id"]]
        assert recovery["reconciled"] == 1
        assert retried["attempt_count"] == second_registration.attempt_count == 2
        assert second_registration.task is not first_registration.task
        assert routes._analysis_attempt_is_running(retried) is True
        assert database.get_pending_scenario_reanalysis_version(session_id) is None
        await asyncio.wait_for(retry_started.wait(), timeout=2)

        allow_old_finally.set()
        await asyncio.wait_for(first_registration.task, timeout=2)
        assert routes._analysis_tasks[job["job_id"]] is second_registration
        assert second_registration.task.done() is False
        assert command_calls == 2

        no_duplicate = await routes.reconcile_pending_scenario_reanalyses()
        assert no_duplicate["pending"] == 0
        assert command_calls == 2
        allow_retry_finish.set()
        await asyncio.wait_for(second_registration.task, timeout=2)
        assert job["job_id"] not in routes._analysis_tasks
        assert database.get_scenario_analysis_job(job["job_id"])["status"] == "completed"

    asyncio.run(exercise())


def test_pending_changes_coalesce_to_only_latest_state_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "SCNSESSION-RECOVER-COALESCE"
    state_v1 = _minimum_ready_state(session_id)
    database.create_scenario_session(
        session_id=session_id,
        owner_user_id=None,
        anonymous_token_hash="hash",
        language="en",
        model_id=database.get_allowed_teams()[0],
        state=state_v1,
    )
    database.mark_scenario_reanalysis_pending(session_id, state_v1["state_version"])
    state_v2 = _save_newer_scenario_state(session_id, state_v1, suffix="V2")
    state_v3 = _save_newer_scenario_state(session_id, state_v2, suffix="V3")
    launched_versions: list[int] = []
    def fake_launch(job: dict, state: dict, *, trigger: str) -> bool:
        launched_versions.append(int(state["state_version"]))
        return _register_parked_analysis_attempt(job)

    monkeypatch.setattr(routes, "_launch_analysis_task", fake_launch)
    gateway.websocket = object()
    stats = asyncio.run(routes.reconcile_pending_scenario_reanalyses())
    active = database.get_active_scenario_analysis_job(session_id)
    assert stats["pending"] == stats["reconciled"] == 1
    assert launched_versions == [state_v3["state_version"]]
    assert active["scenario_state_version"] == state_v3["state_version"]
    assert active["attempt_count"] == 1
    assert database.get_pending_scenario_reanalysis_version(session_id) is None
