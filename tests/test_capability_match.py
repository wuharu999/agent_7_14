from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from starlette.requests import Request

from ecs.app import auth, config, database
from ecs.app.gateway import gateway
from ecs.app.routes.capability_match import (
    AnalyzeScenarioRequest,
    CreateDraftStubRequest,
    OrganizeCapabilitiesRequest,
    _analysis_tasks,
    admin_capabilities_page,
    analysis_limiter,
    analyze_capability_match,
    capability_gap_analytics,
    capability_match_page,
    capability_catalog_job,
    capability_source_changes,
    create_gap_stub,
    export_capability_match,
    get_capability_match,
    start_capability_catalog_organization,
)
from ecs.app.routes import capability_match as capability_match_routes
from worker.capability_matcher import R_AND_D_CLASSIFICATION, enforce_abstraction_hard_gate
from worker import capability_matcher
from worker.manager import WorkerManager


def _worker_payload(*, capability_level: str = "L0_primitive_driver") -> dict:
    return {
        "scenario_spec": {
            "scenario_id": "SCN-POPCORN",
            "title": "Popcorn booth",
            "business_goal": "Serve popcorn safely",
            "target": "Popcorn cup filling",
            "environment": "Outdoor park",
            "payload": "Less than 1.5 kg",
            "throughput": "15 cups per minute",
            "assumptions": [],
            "unknowns": ["Ambient lighting range"],
        },
        "atomic_requirements": [
            {
                "requirement_id": "REQ-FILL",
                "name": "Fill popcorn cup",
                "required_abstraction_level": "L2_composite_skill",
                "effect": "Fill one cup to a bounded amount",
                "acceptance_criteria": ["Target mass tolerance is met"],
                "constraints": ["Food-safe contact surfaces"],
                "dependencies": [],
            }
        ],
        "capabilities": [
            {
                "capability_id": "CAP-JOINT",
                "name": "Set joint angle",
                "abstraction_level": capability_level,
                "effect": "Command one joint",
                "status": "draft",
                "evidence_level": "E2",
                "evidence_refs": ["wiki/sources/sdk.md#joint-command"],
            }
        ],
        "feasibility_assessment": {
            "assessment_id": "ASM-WORKER",
            "scenario_id": "SCN-POPCORN",
            "capability_catalog_revision": "wiki-current",
            "matches": [
                {
                    "match_id": "MATCH-FILL",
                    "requirement_id": "REQ-FILL",
                    "capability_ids": ["CAP-JOINT"],
                    "gates": [
                        {"name": "Evidence", "status": "pass", "hard": True, "basis": "SDK reference"}
                    ],
                    "match_state": "conditional",
                    "confidence": 0.8,
                    "evidence_level": "E2",
                    "conditions": [],
                    "gaps": [],
                    "next_action": "Bench test",
                    "rd_gap": None,
                }
            ],
            "technical_conclusion": "feasible_with_conditions",
            "deployment_conclusion": "viable_with_conditions",
            "deployment_gates": [
                {"name": "Safety", "status": "unknown", "hard": True, "basis": "No field test"}
            ],
            "rd_effort": {"total_person_weeks": 0, "domains": [], "risk_factors": []},
            "residual_risks": ["Outdoor lighting"],
            "next_experiment": "Bench filling test",
        },
    }


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "agent_jobs.db")
    database.initialize_database()
    analysis_limiter.history.clear()
    _analysis_tasks.clear()
    gateway.websocket = None
    async def immediate_to_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)
    yield
    gateway.websocket = None


def _request(*, token: str = "") -> Request:
    headers = []
    if token:
        headers.append(
            (b"cookie", f"{config.SESSION_COOKIE_NAME}={token}".encode("ascii"))
        )
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def _admin_request() -> tuple[Request, str]:
    suffix = uuid.uuid4().hex[:8]
    user_id = database.create_user_record(
        username=f"cap_admin_{suffix}",
        email=f"cap_admin_{suffix}@example.com",
        password_hash="hash",
        password_salt="salt",
        role="admin",
        teams="",
    )
    token, csrf = auth.create_login_session(user_id)
    return _request(token=token), csrf


def test_l0_only_match_is_deterministically_blocked() -> None:
    result = enforce_abstraction_hard_gate(_worker_payload())
    assessment = result["feasibility_assessment"]
    match = assessment["matches"][0]

    assert match["match_state"] == "not_satisfied"
    assert R_AND_D_CLASSIFICATION in match["gaps"]
    assert match["rd_gap"]["classification"] == R_AND_D_CLASSIFICATION
    assert any(gate["name"] == "Abstraction layering hard gate" for gate in match["gates"])
    assert assessment["technical_conclusion"] == "prototype_required"
    assert assessment["deployment_conclusion"] == "business_case_incomplete"
    assert assessment["rd_effort"]["total_person_weeks"] == 2.0


def test_l1_or_higher_match_is_not_reclassified_by_l0_gate() -> None:
    payload = _worker_payload(capability_level="L1_atomic_skill")
    result = enforce_abstraction_hard_gate(payload)
    match = result["feasibility_assessment"]["matches"][0]

    assert match["match_state"] == "conditional"
    assert match["gaps"] == []
    assert match["rd_gap"] is None


def test_worker_analysis_embeds_skills_and_reapplies_hard_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_process(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return json.dumps(_worker_payload())

    monkeypatch.setattr(capability_matcher, "run_claude_process", fake_process)
    result = asyncio.run(
        capability_matcher.analyze_scenario(
            "Serve popcorn outdoors",
            model_id="walker_s2",
            language="en",
        )
    )

    assert captured["team"] == "walker_s2"
    assert "ENGINEER SCENARIO REQUIREMENTS SKILL" in captured["system_prompt"]
    assert "ASSESS SCENARIO FEASIBILITY SKILL" in captured["system_prompt"]
    assert captured["json_schema"] is capability_matcher.MATCHER_RESPONSE_SCHEMA
    assert result["feasibility_assessment"]["matches"][0]["match_state"] == "not_satisfied"


def test_worker_unwraps_nested_structured_result_and_rejects_incomplete_envelope() -> None:
    payload = _worker_payload()
    nested = capability_matcher._structured_payload(json.dumps({"result": payload}))
    fenced = capability_matcher._structured_payload(
        json.dumps({"result": f"```json\n{json.dumps(payload)}\n```"})
    )

    assert nested == payload
    assert fenced == payload
    with pytest.raises(ValueError, match="no complete structured feasibility result"):
        capability_matcher._structured_payload(
            json.dumps({"type": "result", "result": "Analysis was incomplete"})
        )


def test_worker_capability_analysis_queue_is_bounded() -> None:
    manager = WorkerManager()

    async def fill_queue() -> dict:
        for index in range(manager.capability_match_queue.maxsize):
            await manager.route_message(
                {
                    "type": "analyze_scenario",
                    "id": f"analysis-{index}",
                    "scenario_text": "Scenario",
                    "model_id": "walker_s2",
                }
            )
        await manager.route_message(
            {
                "type": "analyze_scenario",
                "id": "analysis-overflow",
                "scenario_text": "Scenario",
                "model_id": "walker_s2",
            }
        )
        return manager.outgoing.get_nowait()

    rejected = asyncio.run(fill_queue())
    assert rejected["id"] == "analysis-overflow"
    assert rejected["status"] == "failed"


def test_schema_bundle_contains_eight_schemas_and_explicit_layers() -> None:
    schema_root = Path(__file__).resolve().parents[1] / "shared" / "schemas"
    schemas = sorted(schema_root.glob("*.schema.json"))
    assert len(schemas) == 8
    capability_schema = json.loads((schema_root / "atomic-capability.schema.json").read_text())
    requirement_schema = json.loads((schema_root / "atomic-requirement.schema.json").read_text())
    assert "abstraction_level" in capability_schema["required"]
    assert capability_schema["properties"]["abstraction_level"]["enum"][0] == "L0_primitive_driver"
    assert "required_abstraction_level" in requirement_schema["required"]
    runtime = capability_matcher.MATCHER_RESPONSE_SCHEMA
    scenario_record = runtime["$defs"]["scenario_record"]
    requirement_record = runtime["$defs"]["requirement_record"]
    assert {"boundary", "environment_profile", "operations_profile"} <= set(
        scenario_record["required"]
    )
    assert {"acceptance_criteria", "constraints", "required_abstraction_level"} <= set(
        requirement_record["required"]
    )


def test_assessment_migration_is_additive_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(old_path)
    connection.execute("CREATE TABLE legacy_state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO legacy_state (value) VALUES ('preserved')")
    connection.execute(
        """
        CREATE TABLE scenario_assessments (
            assessment_id TEXT PRIMARY KEY,
            user_id INTEGER,
            model_id TEXT NOT NULL,
            scenario_spec TEXT NOT NULL,
            feasibility_assessment TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO scenario_assessments (
            assessment_id, user_id, model_id, scenario_spec,
            feasibility_assessment, created_at
        ) VALUES ('ASM-LEGACY', NULL, 'walker_s2', '{}', '{}', '2026-01-01T00:00:00+00:00')
        """
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(database, "DATABASE_PATH", old_path)
    database.initialize_database()
    database.initialize_database()
    connection = sqlite3.connect(old_path)
    try:
        assert connection.execute("SELECT value FROM legacy_state").fetchone()[0] == "preserved"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert "scenario_assessments" in tables
    assert "capability_draft_stubs" in tables
    assert "capability_catalog_jobs" in tables
    assert "capability_catalog_source_state" in tables
    connection = sqlite3.connect(old_path)
    try:
        assessment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scenario_assessments)")
        }
        legacy_status = connection.execute(
            "SELECT analysis_status, updated_at FROM scenario_assessments "
            "WHERE assessment_id = 'ASM-LEGACY'"
        ).fetchone()
    finally:
        connection.close()
    assert {
        "conversation_id",
        "atomic_requirements",
        "capabilities",
        "scenario_text",
        "language",
        "analysis_status",
        "analysis_error",
        "updated_at",
    } <= assessment_columns
    assert legacy_status == ("completed", "2026-01-01T00:00:00+00:00")


def test_database_restart_marks_active_analysis_as_interrupted() -> None:
    database.create_scenario_analysis_job(
        assessment_id="ASM-RESTARTED",
        user_id=None,
        conversation_id="web:restart",
        model_id=database.get_allowed_teams()[0],
        scenario_text="Move boxes",
        language="en",
    )

    database.initialize_database()

    recovered = database.get_scenario_assessment("ASM-RESTARTED")
    assert recovered is not None
    assert recovered["analysis_status"] == "failed"
    assert "server restart" in recovered["analysis_error"]


def test_public_analysis_persists_and_exports_markdown_and_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    model_id = database.get_allowed_teams()[0]
    gateway.websocket = object()

    async def fake_command(message_type: str, **payload):
        assert message_type == "analyze_scenario"
        assert payload["model_id"] == model_id
        assert payload["language"] == "zh-CN"
        return {"status": "ok", "result": enforce_abstraction_hard_gate(_worker_payload())}

    monkeypatch.setattr(gateway, "command", fake_command)
    async def run_analysis() -> tuple[object, dict, object, dict]:
        response = await analyze_capability_match(
            AnalyzeScenarioRequest(
                scenario_text="Serve popcorn outdoors at 15 cups per minute",
                model_id=model_id,
                conversation_id="web:test",
                language="zh-CN",
            ),
            _request(),
        )
        response_data = json.loads(response.body)
        assessment_id = response_data["assessment_id"]
        active_response = await get_capability_match(assessment_id)
        active_data = json.loads(active_response.body)
        active_export = await export_capability_match(
            assessment_id=assessment_id,
            format="markdown",
            language="en",
        )
        await asyncio.gather(*list(_analysis_tasks.values()))
        completed_response = await get_capability_match(assessment_id)
        return response, active_data, active_export, json.loads(completed_response.body)

    response, active_data, active_export, completed_data = asyncio.run(
        run_analysis()
    )
    response_data = json.loads(response.body)
    assert response.status_code == 202, response_data
    assert active_data["analysis_status"] in {"queued", "processing"}
    assert active_export.status_code == 409
    assessment_id = response_data["assessment_id"]
    assert completed_data["analysis_status"] == "completed"
    assert completed_data["assessment"]["assessment_id"] == assessment_id
    saved = database.get_scenario_assessment(assessment_id)
    assert saved is not None
    assert saved["model_id"] == model_id
    assert saved["analysis_status"] == "completed"

    workbench = asyncio.run(capability_match_page(assessment_id))
    assert workbench.status_code == 200
    workbench_html = workbench.body.decode()
    assert "Atomic Skill Matchup Matrix" in workbench_html
    assert '<option value="zh-CN">简体中文</option>' in workbench_html
    assert "机器人场景可行性与能力匹配工作台" in workbench_html
    assert "language:uiLanguage.value" in workbench_html
    assert 'id="analysisProgress"' in workbench_html
    assert "monitorAssessment(data.assessment_id)" in workbench_html
    assert "可以安全刷新或离开此页面" in workbench_html

    ask_html = (
        Path(__file__).resolve().parents[1] / "ecs" / "app" / "templates" / "ask.html"
    ).read_text(encoding="utf-8")
    assert "monitorDemandAnalysis(latest.assessment_id)" in ask_html
    assert "Analysis continues in the background" in ask_html

    markdown = asyncio.run(
        export_capability_match(
            assessment_id=assessment_id,
            format="markdown",
            language="zh-CN",
        )
    )
    assert markdown.status_code == 200
    markdown_text = markdown.body.decode()
    assert "# 机器人场景可行性报告" in markdown_text
    assert "## 原子需求匹配矩阵" in markdown_text
    assert "R&D Gap (Composite Skill Missing)" in markdown_text
    assert markdown.headers["content-disposition"].endswith('"feasibility_report.md"')

    pdf = asyncio.run(
        export_capability_match(
            assessment_id=assessment_id,
            format="pdf",
            language="zh-CN",
        )
    )
    assert pdf.status_code == 200
    assert pdf.body.startswith(b"%PDF-")
    assert pdf.headers["content-type"] == "application/pdf"


def test_admin_gap_analytics_and_stub_generator_require_admin_and_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = database.get_allowed_teams()[0]
    assessment_id = f"ASM-{uuid.uuid4().hex.upper()}"
    payload = enforce_abstraction_hard_gate(_worker_payload())
    database.create_scenario_assessment(
        assessment_id=assessment_id,
        user_id=None,
        conversation_id="web:test",
        model_id=model_id,
        scenario_spec=payload["scenario_spec"],
        atomic_requirements=payload["atomic_requirements"],
        capabilities=payload["capabilities"],
        feasibility_assessment={**payload["feasibility_assessment"], "assessment_id": assessment_id},
    )
    request, csrf = _admin_request()
    page = asyncio.run(admin_capabilities_page(request))
    assert page.status_code == 200
    analytics = asyncio.run(capability_gap_analytics(request))
    assert analytics.status_code == 200
    assert json.loads(analytics.body)["gaps"][0]["occurrence_count"] == 1

    with pytest.raises(Exception) as missing_csrf:
        asyncio.run(
            create_gap_stub(
                CreateDraftStubRequest(
                    assessment_id=assessment_id, requirement_id="REQ-FILL"
                ),
                request,
                x_csrf_token="",
            )
        )
    assert getattr(missing_csrf.value, "status_code", None) == 403

    created = asyncio.run(
        create_gap_stub(
            CreateDraftStubRequest(
                assessment_id=assessment_id, requirement_id="REQ-FILL"
            ),
            request,
            x_csrf_token=csrf,
        )
    )
    created_data = json.loads(created.body)
    assert created.status_code == 200, created_data
    assert created_data["stub"]["status"] == "draft"
    assert created_data["stub"]["details"]["evidence_status"] == "acquisition_required"
    assert database.list_audit_log(limit=1)[0]["action"] == "create_capability_draft_stub"

    duplicate = asyncio.run(
        create_gap_stub(
            CreateDraftStubRequest(
                assessment_id=assessment_id, requirement_id="REQ-FILL"
            ),
            request,
            x_csrf_token=csrf,
        )
    )
    assert duplicate.status_code == 200
    assert json.loads(duplicate.body)["stub"]["stub_id"] == created_data["stub"]["stub_id"]


def test_admin_catalog_start_and_source_change_snapshot_are_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = database.get_robot_options()[0]["name"]
    request, csrf = _admin_request()
    gateway.websocket = object()  # The API only needs the persistent Worker online signal here.
    started: list[tuple[str, str, str]] = []

    async def fake_command(message_type: str, **payload):
        assert message_type == "inspect_capability_source_changes"
        assert payload["model_id"] == model_id
        return {
            "status": "ok",
            "result": {
                "model_id": model_id,
                "current_source_files": 2,
                "last_organized_manifest_files": 1,
                "changes": {
                    "baseline_exists": True,
                    "added": ["upload/new.md"],
                    "modified": [],
                    "deleted": [],
                    "counts": {"added": 1, "modified": 0, "deleted": 0, "total": 1},
                },
            },
        }

    monkeypatch.setattr(gateway, "command", fake_command)
    monkeypatch.setattr(
        capability_match_routes,
        "_start_capability_catalog_job",
        lambda job_id, selected_model, snapshot_id: started.append(
            (job_id, selected_model, snapshot_id)
        ),
    )

    changes_response = asyncio.run(capability_source_changes(request, model_id=model_id))
    changes = json.loads(changes_response.body)
    assert changes_response.status_code == 200
    assert changes["changes"]["added"] == ["upload/new.md"]
    assert database.get_capability_catalog_source_state(model_id)["current_source_files"] == 2

    start_response = asyncio.run(
        start_capability_catalog_organization(
            OrganizeCapabilitiesRequest(model_id=model_id),
            request,
            x_csrf_token=csrf,
        )
    )
    body = json.loads(start_response.body)
    assert start_response.status_code == 202
    assert body["job"]["status"] == "queued"
    assert started[0][0] == body["job"]["job_id"]

    shared_response = asyncio.run(capability_gap_analytics(request))
    shared = json.loads(shared_response.body)
    assert shared["catalog_jobs"][0]["job_id"] == body["job"]["job_id"]
    job_response = asyncio.run(capability_catalog_job(body["job"]["job_id"], request))
    assert json.loads(job_response.body)["job"]["model_id"] == model_id
    assert database.list_audit_log(limit=1)[0]["action"] == "organize_atomic_capabilities"
