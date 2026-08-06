from __future__ import annotations

import asyncio
import html
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from textwrap import wrap
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ecs.app.auth import current_session, require_roles, verify_csrf
from ecs.app.config import CAPABILITY_CATALOG_TIMEOUT, FILE_COMMAND_TIMEOUT, WORKER_TIMEOUT
from ecs.app.database import (
    aggregate_capability_gaps,
    complete_scenario_analysis_job,
    create_capability_catalog_job,
    create_capability_draft_stub,
    create_scenario_analysis_job,
    get_active_capability_catalog_job,
    get_allowed_teams,
    get_capability_catalog_job,
    get_capability_catalog_source_state,
    get_robot_options,
    get_scenario_assessment,
    list_capability_catalog_jobs,
    list_capability_draft_stubs,
    update_capability_catalog_job,
    update_scenario_analysis_status,
    upsert_capability_catalog_source_state,
    write_audit,
)
from ecs.app.gateway import gateway
from ecs.app.languages import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

router = APIRouter()
log = logging.getLogger("ecs.capability_match")
_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"
_PDF_FONT_LOCK = threading.Lock()


class AnalyzeScenarioRequest(BaseModel):
    scenario_text: str = Field(min_length=3, max_length=20_000)
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    conversation_id: str = Field(default="", max_length=128)
    language: str = Field(default=DEFAULT_LANGUAGE, max_length=16)


class CreateDraftStubRequest(BaseModel):
    assessment_id: str = Field(min_length=8, max_length=80, pattern=r"^ASM-[A-Z0-9-]+$")
    requirement_id: str = Field(min_length=5, max_length=80, pattern=r"^REQ-[A-Z0-9-]+$")


class OrganizeCapabilitiesRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    scan_mode: Literal["incremental", "full", "full_fresh"] = "incremental"


class _AnalysisRateLimiter:
    def __init__(self) -> None:
        self.history: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        now = time.monotonic()
        timestamps = [value for value in self.history[client_id] if now - value < 3600]
        if len(timestamps) >= 20:
            self.history[client_id] = timestamps
            return False
        if sum(1 for value in timestamps if now - value < 60) >= 5:
            self.history[client_id] = timestamps
            return False
        timestamps.append(now)
        self.history[client_id] = timestamps
        return True


analysis_limiter = _AnalysisRateLimiter()
_analysis_tasks: dict[str, asyncio.Task[None]] = {}
_catalog_tasks: dict[str, asyncio.Task[None]] = {}


def _template(name: str) -> str:
    return (_TEMPLATE_ROOT / name).read_text(encoding="utf-8")


def _public_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in assessment.items()
        if key not in {"user_id", "conversation_id", "scenario_text", "analysis_error"}
    }


async def _mark_analysis_failed(assessment_id: str, error: str) -> None:
    try:
        await asyncio.to_thread(
            update_scenario_analysis_status,
            assessment_id,
            status="failed",
            error=error,
        )
    except Exception:
        log.exception("Could not persist failed state for scenario analysis %s", assessment_id)


async def _run_scenario_analysis(
    assessment_id: str,
    payload: AnalyzeScenarioRequest,
) -> None:
    try:
        await asyncio.to_thread(
            update_scenario_analysis_status,
            assessment_id,
            status="processing",
        )
        worker_result = await gateway.command(
            "analyze_scenario",
            timeout=WORKER_TIMEOUT + 30,
            scenario_text=payload.scenario_text,
            model_id=payload.model_id,
            language=payload.language,
        )
        if worker_result.get("status") != "ok" or not isinstance(worker_result.get("result"), dict):
            error = str(worker_result.get("error") or "Scenario analysis failed")
            log.warning("Scenario analysis %s failed on Worker: %s", assessment_id, error)
            await _mark_analysis_failed(assessment_id, error)
            return

        result = worker_result["result"]
        scenario_spec = result.get("scenario_spec")
        requirements = result.get("atomic_requirements")
        capabilities = result.get("capabilities")
        feasibility = result.get("feasibility_assessment")
        if not isinstance(scenario_spec, dict) or not isinstance(feasibility, dict):
            raise ValueError("Worker returned an incomplete assessment")
        if not isinstance(requirements, list) or not isinstance(capabilities, list):
            raise ValueError("Worker returned invalid requirement or capability records")

        feasibility["assessment_id"] = assessment_id
        scenario_id = str(scenario_spec.get("scenario_id") or "")
        if not scenario_id.startswith("SCN-"):
            scenario_id = f"SCN-{uuid.uuid4().hex[:12].upper()}"
            scenario_spec["scenario_id"] = scenario_id
        feasibility["scenario_id"] = scenario_id
        await asyncio.to_thread(
            complete_scenario_analysis_job,
            assessment_id=assessment_id,
            scenario_spec=scenario_spec,
            atomic_requirements=requirements,
            capabilities=capabilities,
            feasibility_assessment=feasibility,
        )
        log.info("Scenario analysis %s completed", assessment_id)
    except asyncio.CancelledError:
        await _mark_analysis_failed(
            assessment_id,
            "Scenario analysis was interrupted. Please start it again.",
        )
        raise
    except (ConnectionError, TimeoutError, asyncio.TimeoutError):
        log.exception("Worker unavailable during scenario analysis %s", assessment_id)
        await _mark_analysis_failed(
            assessment_id,
            "Scenario analysis is temporarily unavailable. Please try again.",
        )
    except (TypeError, ValueError, RuntimeError):
        log.exception("Invalid result for scenario analysis %s", assessment_id)
        await _mark_analysis_failed(
            assessment_id,
            "Scenario analysis returned an invalid result. Please try again.",
        )
    except Exception:
        log.exception("Unexpected failure during scenario analysis %s", assessment_id)
        await _mark_analysis_failed(
            assessment_id,
            "Scenario analysis failed. Please try again.",
        )


def _start_scenario_analysis(assessment_id: str, payload: AnalyzeScenarioRequest) -> None:
    task = asyncio.create_task(
        _run_scenario_analysis(assessment_id, payload),
        name=f"scenario-analysis-{assessment_id}",
    )
    _analysis_tasks[assessment_id] = task

    def forget(completed: asyncio.Task[None]) -> None:
        if _analysis_tasks.get(assessment_id) is completed:
            _analysis_tasks.pop(assessment_id, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            log.error(
                "Scenario analysis task %s escaped its error boundary",
                assessment_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(forget)


def _model_is_available(model_id: str) -> bool:
    return model_id in {str(item["name"]) for item in get_robot_options()}


def _source_state_from_worker(result: dict[str, Any]) -> dict[str, Any]:
    changes = result.get("source_changes")
    if not isinstance(changes, dict):
        changes = result.get("changes")
    if not isinstance(changes, dict):
        changes = {}
    current_files = result.get("current_source_files")
    if current_files is None:
        current_files = result.get("source_file_count", 0)
    manifest_files = result.get("last_organized_manifest_files")
    if manifest_files is None:
        manifest_files = result.get("manifest_file_count", 0)
    return {
        "changes": changes,
        "current_source_files": int(current_files or 0),
        "last_organized_manifest_files": int(manifest_files or 0),
    }


async def _save_source_state(model_id: str, result: dict[str, Any]) -> None:
    state = _source_state_from_worker(result)
    await asyncio.to_thread(
        upsert_capability_catalog_source_state,
        model_id=model_id,
        **state,
    )


async def _run_capability_catalog_job(
    job_id: str,
    model_id: str,
    snapshot_id: str,
    scan_mode: str,
) -> None:
    force_reextract = scan_mode == "full_fresh"
    worker_scan_mode = "full" if force_reextract else scan_mode
    try:
        await asyncio.to_thread(
            update_capability_catalog_job,
            job_id,
            status="processing",
            stage="dispatching",
            message=(
                "Sending a forced full re-extraction to the Worker without checkpoints."
                if force_reextract
                else "Sending the source snapshot to the Worker with checkpoint resume enabled."
            ),
        )
        worker_result = await gateway.command(
            "organize_capability_catalog",
            timeout=CAPABILITY_CATALOG_TIMEOUT,
            job_id=job_id,
            model_id=model_id,
            snapshot_id=snapshot_id,
            scan_mode=worker_scan_mode,
            reuse_checkpoints=not force_reextract,
        )
        result = worker_result.get("result")
        if worker_result.get("status") != "ok" or not isinstance(result, dict):
            raise RuntimeError(str(worker_result.get("error") or "Worker organization failed"))
        await _save_source_state(model_id, result)
        completion_status = str(result.get("completion_status") or "completed")
        partial = completion_status == "partial"
        await asyncio.to_thread(
            update_capability_catalog_job,
            job_id,
            status="partial" if partial else "completed",
            stage="completed_with_warnings" if partial else "completed",
            message=(
                "Capability organization completed with blocked or unprocessed Wiki evidence; "
                "the successful baseline was not advanced."
                if partial
                else "Atomic capability organization completed."
            ),
            result=result,
        )
        log.info("Capability catalog job %s completed for %s", job_id, model_id)
    except asyncio.CancelledError:
        await asyncio.to_thread(
            update_capability_catalog_job,
            job_id,
            status="failed",
            stage="interrupted",
            message="Capability organization was interrupted.",
            error="Capability organization was interrupted.",
        )
        raise
    except (ConnectionError, TimeoutError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
        log.exception("Capability catalog job %s failed", job_id)
        detail = str(exc).strip() or "Capability organization failed."
        await asyncio.to_thread(
            update_capability_catalog_job,
            job_id,
            status="failed",
            stage="failed",
            message="Capability organization failed.",
            error=detail[:1200],
        )
    except Exception:
        log.exception("Unexpected capability catalog failure for %s", job_id)
        await asyncio.to_thread(
            update_capability_catalog_job,
            job_id,
            status="failed",
            stage="failed",
            message="Capability organization failed. Check the Worker logs.",
            error="Capability organization failed. Check the Worker logs.",
        )


def _start_capability_catalog_job(
    job_id: str, model_id: str, snapshot_id: str, scan_mode: str
) -> None:
    task = asyncio.create_task(
        _run_capability_catalog_job(job_id, model_id, snapshot_id, scan_mode),
        name=f"capability-catalog-{job_id}",
    )
    _catalog_tasks[job_id] = task

    def forget(completed: asyncio.Task[None]) -> None:
        if _catalog_tasks.get(job_id) is completed:
            _catalog_tasks.pop(job_id, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            log.error(
                "Capability catalog task %s escaped its error boundary",
                job_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(forget)


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


def _scenario_summary(scenario: dict[str, Any]) -> dict[str, str]:
    boundary = scenario.get("boundary") if isinstance(scenario.get("boundary"), dict) else {}
    environment = scenario.get("environment_profile")
    operations = scenario.get("operations_profile")
    facts = scenario.get("facts") if isinstance(scenario.get("facts"), list) else []
    fact_text = "; ".join(
        str(item.get("text") or "") for item in facts if isinstance(item, dict) and item.get("text")
    )
    return {
        "target": _display_value(
            scenario.get("target") or boundary.get("end_state") or scenario.get("business_goal")
        ),
        "environment": _display_value(scenario.get("environment") or environment),
        "payload": _display_value(scenario.get("payload") or fact_text or "Unknown"),
        "throughput": _display_value(scenario.get("throughput") or operations),
    }


_REPORT_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Robot Scenario Feasibility Report",
        "assessment": "Assessment",
        "model": "Model",
        "scenario": "Scenario",
        "created": "Created",
        "business_goal": "Business goal",
        "target": "Target",
        "environment": "Environment",
        "payload": "Payload / facts",
        "throughput": "Throughput / operations",
        "matrix": "Atomic Requirement Match Matrix",
        "required_layer": "Required layer",
        "match_state": "Match state",
        "confidence": "Confidence",
        "capabilities": "Matched capabilities",
        "evidence": "Evidence",
        "gate": "Gate",
        "gap": "Gap",
        "rd_estimate": "R&D estimate",
        "person_weeks": "person-weeks",
        "next_action": "Next action",
        "conclusions": "Conclusions",
        "technical": "Technical",
        "deployment": "Deployment",
        "rd_effort": "R&D effort",
        "domains": "Domains",
        "none": "None",
        "residual_risks": "Residual Risks",
        "no_risks": "No residual risks recorded",
        "next_experiment": "Next Experiment",
        "not_specified": "Not specified",
    },
    "zh-CN": {
        "title": "机器人场景可行性报告",
        "assessment": "评估编号",
        "model": "机器人型号",
        "scenario": "场景",
        "created": "创建时间",
        "business_goal": "业务目标",
        "target": "目标",
        "environment": "环境",
        "payload": "负载 / 已知事实",
        "throughput": "吞吐量 / 作业情况",
        "matrix": "原子需求匹配矩阵",
        "required_layer": "所需能力层级",
        "match_state": "匹配状态",
        "confidence": "置信度",
        "capabilities": "已匹配能力",
        "evidence": "证据",
        "gate": "门槛",
        "gap": "能力缺口",
        "rd_estimate": "研发投入估算",
        "person_weeks": "人周",
        "next_action": "下一步行动",
        "conclusions": "结论",
        "technical": "技术结论",
        "deployment": "部署结论",
        "rd_effort": "研发投入",
        "domains": "研发领域",
        "none": "无",
        "residual_risks": "剩余风险",
        "no_risks": "未记录剩余风险",
        "next_experiment": "下一项实验",
        "not_specified": "未指定",
    },
}


def _markdown_report(assessment: dict[str, Any], *, language: str = "en") -> str:
    labels = _REPORT_LABELS.get(language, _REPORT_LABELS["en"])
    scenario = assessment["scenario_spec"]
    requirements = {
        str(item.get("requirement_id") or ""): item
        for item in assessment["atomic_requirements"]
        if isinstance(item, dict)
    }
    capabilities = {
        str(item.get("capability_id") or ""): item
        for item in assessment["capabilities"]
        if isinstance(item, dict)
    }
    feasibility = assessment["feasibility_assessment"]
    summary = _scenario_summary(scenario)
    lines = [
        f"# {labels['title']}",
        "",
        f"- {labels['assessment']}: `{assessment['assessment_id']}`",
        f"- {labels['model']}: `{assessment['model_id']}`",
        f"- {labels['scenario']}: `{scenario.get('scenario_id', '')}` — {scenario.get('title', '')}",
        f"- {labels['created']}: {assessment['created_at']}",
        "",
        f"## {labels['scenario']}",
        "",
        f"**{labels['business_goal']}:** {scenario.get('business_goal', '')}",
        "",
        f"**{labels['target']}:** {summary['target']}",
        "",
        f"**{labels['environment']}:** {summary['environment']}",
        "",
        f"**{labels['payload']}:** {summary['payload']}",
        "",
        f"**{labels['throughput']}:** {summary['throughput']}",
        "",
        f"## {labels['matrix']}",
        "",
    ]
    for match in feasibility.get("matches", []):
        requirement_id = str(match.get("requirement_id") or "")
        requirement = requirements.get(requirement_id, {})
        lines.extend(
            [
                f"### {requirement_id}: {requirement.get('name', '')}",
                "",
                f"- {labels['required_layer']}: `{requirement.get('required_abstraction_level', '')}`",
                f"- {labels['match_state']}: `{match.get('match_state', '')}`",
                f"- {labels['confidence']}: {match.get('confidence', 0)}",
            ]
        )
        capability_ids = [str(value) for value in match.get("capability_ids", [])]
        if capability_ids:
            lines.append(f"- {labels['capabilities']}:")
            for capability_id in capability_ids:
                capability = capabilities.get(capability_id, {})
                lines.append(
                    f"  - `{capability_id}` {capability.get('name', '')} "
                    f"({capability.get('abstraction_level', '')}, "
                    f"{capability.get('status', '')}, {capability.get('evidence_level', '')})"
                )
                evidence_items = capability.get("evidence_refs", capability.get("evidence", []))
                for evidence in evidence_items:
                    if isinstance(evidence, dict):
                        evidence = f"{evidence.get('wiki_entry', '')}#{evidence.get('locator', '')}"
                    lines.append(f"    - {labels['evidence']}: `{evidence}`")
        for gate in match.get("gates", []):
            basis = gate.get("basis") or (
                f"requirement={_display_value(gate.get('requirement_value'))}; "
                f"capability={_display_value(gate.get('capability_value'))}"
            )
            lines.append(
                f"- {labels['gate']} `{gate.get('name', '')}`: **{gate.get('status', '')}** — "
                f"{basis}"
            )
        for gap in match.get("gaps", []):
            lines.append(f"- {labels['gap']}: {gap}")
        rd_gap = match.get("rd_gap")
        if isinstance(rd_gap, dict):
            lines.append(
                f"- {labels['rd_estimate']}: **{rd_gap.get('person_weeks', 0)} {labels['person_weeks']}** "
                f"({', '.join(str(value) for value in rd_gap.get('domains', []))})"
            )
        lines.extend(["", f"{labels['next_action']}: {match.get('next_action', '')}", ""])
    effort = feasibility.get("rd_effort", {})
    lines.extend(
        [
            f"## {labels['conclusions']}",
            "",
            f"- {labels['technical']}: **{feasibility.get('technical_conclusion', '')}**",
            f"- {labels['deployment']}: **{feasibility.get('deployment_conclusion', '')}**",
            f"- {labels['rd_effort']}: **{effort.get('total_person_weeks', 0)} {labels['person_weeks']}**",
            f"- {labels['domains']}: {', '.join(str(value) for value in effort.get('domains', [])) or labels['none']}",
            "",
            f"## {labels['residual_risks']}",
            "",
        ]
    )
    risks = feasibility.get("residual_risks", []) or [labels["no_risks"]]
    lines.extend(f"- {risk}" for risk in risks)
    lines.extend(
        [
            "",
            f"## {labels['next_experiment']}",
            "",
            str(feasibility.get("next_experiment") or labels["not_specified"]),
            "",
        ]
    )
    return "\n".join(lines)


def _basic_pdf_bytes(report: str) -> bytes:
    """Create a dependency-free, print-ready PDF summary."""
    ascii_lines: list[str] = []
    for source_line in report.splitlines():
        line = source_line.replace("`", "").replace("**", "")
        encoded = line.encode("ascii", errors="replace").decode("ascii")
        while len(encoded) > 92:
            ascii_lines.append(encoded[:92])
            encoded = encoded[92:]
        ascii_lines.append(encoded)
    chunks = [ascii_lines[index : index + 54] for index in range(0, len(ascii_lines), 54)] or [[]]
    object_count = 3 + len(chunks) * 2
    objects: list[bytes] = [b""] * object_count
    page_numbers = [4 + index * 2 for index in range(len(chunks))]
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(chunks)} >>".encode("ascii")
    objects[2] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for index, lines in enumerate(chunks):
        page_number = page_numbers[index]
        content_number = page_number + 1
        commands = ["BT", "/F1 9 Tf", "48 790 Td", "13 TL"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.extend([f"({escaped}) Tj", "T*"])
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii")
        objects[page_number - 1] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_number} 0 R >>"
        ).encode("ascii")
        objects[content_number - 1] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(output)


def _pdf_bytes(report: str) -> bytes:
    """Render a Unicode PDF when ReportLab is installed, with a safe fallback."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas

        font_name = "Agent1Unicode"
        with _PDF_FONT_LOCK:
            if font_name not in pdfmetrics.getRegisteredFontNames():
                font_paths = (
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
                )
                font_path = next((path for path in font_paths if path.is_file()), None)
                if font_path is not None:
                    pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
                else:
                    font_name = "STSong-Light"
                    if font_name not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(UnicodeCIDFont(font_name))

        buffer = BytesIO()
        document = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
        width, height = A4
        margin = 42
        y = height - margin
        document.setTitle("Robot Scenario Feasibility Report")
        document.setAuthor("Agent1 Knowledge Base")
        document.setFont(font_name, 9)
        for source_line in report.splitlines():
            lines = wrap(source_line, width=92, replace_whitespace=False, drop_whitespace=False) or [""]
            for line in lines:
                if y < margin:
                    document.showPage()
                    document.setFont(font_name, 9)
                    y = height - margin
                document.drawString(margin, y, line)
                y -= 13
        document.save()
        return buffer.getvalue()
    except Exception:
        log.exception("Unicode PDF rendering failed; using the basic PDF fallback")
        return _basic_pdf_bytes(report)


@router.get("/capability-match", response_class=HTMLResponse)
async def capability_match_page(assessment_id: str = Query(default="")) -> HTMLResponse:
    page = _template("capability_match.html")
    page = page.replace("__ROBOTS__", json.dumps(get_robot_options(), ensure_ascii=False))
    page = page.replace("__ASSESSMENT_ID__", json.dumps(assessment_id))
    return HTMLResponse(page)


@router.post("/api/capability-match/analyze")
async def analyze_capability_match(payload: AnalyzeScenarioRequest, request: Request) -> JSONResponse:
    client_id = request.client.host if request.client else "unknown"
    if not analysis_limiter.is_allowed(client_id):
        return JSONResponse({"error": "Scenario analysis rate limit exceeded"}, status_code=429)
    if payload.language not in SUPPORTED_LANGUAGES:
        return JSONResponse({"error": "Unsupported report language"}, status_code=400)
    if payload.model_id not in get_allowed_teams():
        return JSONResponse({"error": "Unknown robot model"}, status_code=400)
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)
    assessment_id = f"ASM-{uuid.uuid4().hex.upper()}"
    session = current_session(request)
    try:
        await asyncio.to_thread(
            create_scenario_analysis_job,
            assessment_id=assessment_id,
            user_id=int(session["user_id"]) if session else None,
            conversation_id=payload.conversation_id,
            model_id=payload.model_id,
            scenario_text=payload.scenario_text,
            language=payload.language,
        )
        _start_scenario_analysis(assessment_id, payload)
        return JSONResponse(
            {
                "status": "accepted",
                "analysis_status": "queued",
                "assessment_id": assessment_id,
            },
            status_code=202,
        )
    except Exception:
        log.exception("Could not create scenario analysis job")
        return JSONResponse({"error": "Scenario analysis could not be started"}, status_code=500)


class GrillScenarioRequest(BaseModel):
    scenario_text: str
    model_id: str = "tian_gong"
    language: str = "en"


@router.post("/api/capability-match/grill")
async def grill_scenario_route(
    payload: GrillScenarioRequest,
    request: Request,
) -> JSONResponse:
    if not payload.scenario_text.strip():
        return JSONResponse({"error": "Scenario text cannot be empty"}, status_code=400)
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)

    command_id = f"GRILL-{uuid.uuid4().hex[:12].upper()}"
    try:
        worker_result = await gateway.send_command(
            "grill_scenario",
            command_id,
            scenario_text=payload.scenario_text,
            model_id=payload.model_id,
            language=payload.language,
            timeout=45,
        )
        if worker_result.get("status") != "ok":
            return JSONResponse(
                {"error": str(worker_result.get("error") or "Failed to generate Grill Me questions")},
                status_code=500,
            )
        return JSONResponse({"status": "ok", "questions": worker_result.get("questions", [])})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/capability-match/assessments/{assessment_id}")
async def get_capability_match(assessment_id: str) -> JSONResponse:
    assessment = get_scenario_assessment(assessment_id)
    if assessment is None:
        return JSONResponse({"error": "Assessment not found"}, status_code=404)
    analysis_status = str(assessment.get("analysis_status") or "completed")
    response: dict[str, Any] = {
        "status": "ok",
        "analysis_status": analysis_status,
        "assessment_id": assessment_id,
        "created_at": assessment.get("created_at"),
        "updated_at": assessment.get("updated_at") or assessment.get("created_at"),
    }
    if analysis_status == "completed":
        response["assessment"] = _public_assessment(assessment)
    elif analysis_status == "failed":
        response["error"] = str(assessment.get("analysis_error") or "Scenario analysis failed")
    return JSONResponse(response)


@router.get("/api/capability-match/export")
async def export_capability_match(
    assessment_id: str = Query(...),
    format: str = Query(default="markdown", pattern=r"^(markdown|pdf)$"),
    language: str = Query(default="en", pattern=r"^(en|zh-CN)$"),
) -> Response:
    assessment = get_scenario_assessment(assessment_id)
    if assessment is None:
        return JSONResponse({"error": "Assessment not found"}, status_code=404)
    if str(assessment.get("analysis_status") or "completed") != "completed":
        return JSONResponse(
            {"error": "Assessment is not complete yet"},
            status_code=409,
        )
    report = _markdown_report(assessment, language=language)
    if format == "pdf":
        pdf = await asyncio.to_thread(_pdf_bytes, report)
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="feasibility_report.pdf"'},
        )
    return Response(
        report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="feasibility_report.md"'},
    )


@router.get("/admin/capabilities", response_class=HTMLResponse)
async def admin_capabilities_page(request: Request) -> Response:
    session = current_session(request)
    if session is None:
        return RedirectResponse("/login?next=/admin/capabilities", status_code=303)
    if session["role"] != "admin":
        return RedirectResponse("/manage", status_code=303)
    page = _template("admin_capabilities.html")
    page = page.replace("__CSRF_TOKEN__", html.escape(str(session["csrf_token"]), quote=True))
    page = page.replace("__USERNAME__", html.escape(str(session["username"])))
    page = page.replace(
        "__ROBOTS__",
        json.dumps(get_robot_options(), ensure_ascii=False).replace("</", "<\\/"),
    )
    return HTMLResponse(page)


@router.get("/api/admin/capabilities")
async def capability_gap_analytics(request: Request) -> JSONResponse:
    require_roles(request, {"admin"})
    return JSONResponse(
        {
            "status": "ok",
            "gaps": aggregate_capability_gaps(),
            "draft_stubs": list_capability_draft_stubs(),
            "catalog_jobs": list_capability_catalog_jobs(),
        }
    )


@router.get("/api/admin/capabilities/source-changes")
async def capability_source_changes(
    request: Request,
    model_id: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
) -> JSONResponse:
    require_roles(request, {"admin"})
    if not _model_is_available(model_id):
        return JSONResponse({"error": "Robot model not found"}, status_code=404)
    if gateway.online:
        try:
            worker_result = await gateway.command(
                "inspect_capability_source_changes",
                timeout=FILE_COMMAND_TIMEOUT,
                model_id=model_id,
            )
            result = worker_result.get("result")
            if worker_result.get("status") != "ok" or not isinstance(result, dict):
                raise RuntimeError(str(worker_result.get("error") or "Worker inspection failed"))
            await _save_source_state(model_id, result)
            return JSONResponse({"status": "ok", "stale": False, **result})
        except (ConnectionError, TimeoutError, asyncio.TimeoutError, RuntimeError, ValueError):
            log.exception("Could not refresh capability source changes for %s", model_id)
    cached = await asyncio.to_thread(get_capability_catalog_source_state, model_id)
    if cached is not None:
        return JSONResponse({"status": "ok", "stale": True, **cached})
    return JSONResponse(
        {"error": "Worker is offline and no source-change snapshot is available."},
        status_code=503,
    )


@router.get("/api/admin/capabilities/jobs/{job_id}")
async def capability_catalog_job(job_id: str, request: Request) -> JSONResponse:
    require_roles(request, {"admin"})
    job = await asyncio.to_thread(get_capability_catalog_job, job_id)
    if job is None:
        return JSONResponse({"error": "Organization job not found"}, status_code=404)
    return JSONResponse({"status": "ok", "job": job})


@router.post("/api/admin/capabilities/organize")
async def start_capability_catalog_organization(
    payload: OrganizeCapabilitiesRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
) -> JSONResponse:
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    if not _model_is_available(payload.model_id):
        return JSONResponse({"error": "Robot model not found"}, status_code=404)
    active = await asyncio.to_thread(get_active_capability_catalog_job, payload.model_id)
    if active is not None:
        return JSONResponse({"status": "already_running", "job": active}, status_code=409)
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)
    job_id = f"CAT-{uuid.uuid4().hex[:16].upper()}"
    snapshot_id = f"SRCSET-{payload.model_id.upper()}-{uuid.uuid4().hex[:12].upper()}"
    job = await asyncio.to_thread(
        create_capability_catalog_job,
        job_id=job_id,
        created_by=int(session["user_id"]),
        model_id=payload.model_id,
        snapshot_id=snapshot_id,
        scan_mode=payload.scan_mode,
    )
    if job["job_id"] != job_id:
        return JSONResponse({"status": "already_running", "job": job}, status_code=409)
    await asyncio.to_thread(
        write_audit,
        user_id=int(session["user_id"]),
        username=str(session.get("username") or ""),
        action="organize_atomic_capabilities",
        source_path=payload.model_id,
        result="queued",
        details=json.dumps(
            {"job_id": job_id, "snapshot_id": snapshot_id, "scan_mode": payload.scan_mode}
        ),
    )
    _start_capability_catalog_job(
        job_id, payload.model_id, snapshot_id, payload.scan_mode
    )
    return JSONResponse({"status": "queued", "job": job}, status_code=202)


@router.post("/api/admin/capabilities/stubs")
async def create_gap_stub(
    payload: CreateDraftStubRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
) -> JSONResponse:
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    assessment = get_scenario_assessment(payload.assessment_id)
    if assessment is None:
        return JSONResponse({"error": "Assessment not found"}, status_code=404)
    requirement = next(
        (
            item
            for item in assessment["atomic_requirements"]
            if isinstance(item, dict) and item.get("requirement_id") == payload.requirement_id
        ),
        None,
    )
    match = next(
        (
            item
            for item in assessment["feasibility_assessment"].get("matches", [])
            if isinstance(item, dict) and item.get("requirement_id") == payload.requirement_id
        ),
        None,
    )
    if requirement is None or match is None:
        return JSONResponse({"error": "Requirement match not found"}, status_code=404)
    if not match.get("gaps") and not isinstance(match.get("rd_gap"), dict):
        return JSONResponse({"error": "Requirement is not an R&D gap"}, status_code=400)
    stub_id = f"CAP-DRAFT-{uuid.uuid4().hex[:16].upper()}"
    details = {
        "capability_id": stub_id,
        "name": str(requirement.get("name") or payload.requirement_id),
        "model_id": str(assessment["model_id"]),
        "required_abstraction_level": requirement.get("required_abstraction_level"),
        "effect": requirement.get("effect"),
        "acceptance_criteria": requirement.get("acceptance_criteria", []),
        "constraints": requirement.get("constraints", []),
        "gaps": match.get("gaps", []),
        "rd_gap": match.get("rd_gap"),
        "evidence_status": "acquisition_required",
        "lifecycle": "draft",
    }
    stub = await asyncio.to_thread(
        create_capability_draft_stub,
        stub_id=stub_id,
        assessment_id=payload.assessment_id,
        requirement_id=payload.requirement_id,
        model_id=str(assessment["model_id"]),
        name=str(requirement.get("name") or payload.requirement_id),
        details=details,
        created_by=int(session["user_id"]),
    )
    await asyncio.to_thread(
        write_audit,
        user_id=int(session["user_id"]),
        username=str(session.get("username") or ""),
        action="create_capability_draft_stub",
        source_path=f"{assessment['model_id']}/{payload.requirement_id}",
        result="ok",
        details=json.dumps({"stub_id": stub["stub_id"], "assessment_id": payload.assessment_id}),
    )
    return JSONResponse({"status": "ok", "stub": stub})


class UpdateCapabilityStatusRequest(BaseModel):
    model_id: str = "tian_gong"
    capability_id: str
    status: str


@router.post("/api/admin/capabilities/status")
async def update_capability_lifecycle_status(
    payload: UpdateCapabilityStatusRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
) -> JSONResponse:
    session = require_roles(request, {"admin", "editor"})
    verify_csrf(session, x_csrf_token)
    if payload.status not in {"draft", "reviewed", "verified", "deprecated"}:
        return JSONResponse({"error": "Invalid lifecycle status"}, status_code=400)
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)

    command_id = f"CAPSTAT-{uuid.uuid4().hex[:12].upper()}"
    try:
        worker_result = await gateway.send_command(
            "update_capability_status",
            command_id,
            model_id=payload.model_id,
            capability_id=payload.capability_id,
            status=payload.status,
            timeout=30,
        )
        if worker_result.get("status") != "ok":
            return JSONResponse(
                {"error": str(worker_result.get("error") or "Failed to update capability status")},
                status_code=500,
            )
        await asyncio.to_thread(
            write_audit,
            user_id=int(session["user_id"]),
            username=str(session.get("username") or ""),
            action="update_capability_status",
            source_path=f"{payload.model_id}/{payload.capability_id}",
            result="ok",
            details=json.dumps({"capability_id": payload.capability_id, "new_status": payload.status}),
        )
        return JSONResponse({"status": "ok", **worker_result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


class SaveCapabilityRequest(BaseModel):
    model_id: str = "tian_gong"
    entry: dict[str, Any]


class DeleteCapabilityRequest(BaseModel):
    model_id: str = "tian_gong"
    capability_id: str


@router.post("/api/admin/capabilities/save")
async def save_capability(
    payload: SaveCapabilityRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
) -> JSONResponse:
    session = require_roles(request, {"admin", "editor"})
    verify_csrf(session, x_csrf_token)
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)

    capability_id = str(payload.entry.get("capability_id") or "").strip().upper()
    if not capability_id.startswith("CAP-"):
        return JSONResponse({"error": "Capability ID must start with CAP-"}, status_code=400)

    command_id = f"CAPSAVE-{uuid.uuid4().hex[:12].upper()}"
    try:
        worker_result = await gateway.send_command(
            "save_capability",
            command_id,
            model_id=payload.model_id,
            entry=payload.entry,
            timeout=30,
        )
        if worker_result.get("status") != "ok":
            return JSONResponse(
                {"error": str(worker_result.get("error") or "Failed to save capability entry")},
                status_code=500,
            )
        await asyncio.to_thread(
            write_audit,
            user_id=int(session["user_id"]),
            username=str(session.get("username") or ""),
            action="save_capability",
            source_path=f"{payload.model_id}/{capability_id}",
            result="ok",
            details=json.dumps({"capability_id": capability_id, "model_id": payload.model_id}),
        )
        return JSONResponse({"status": "ok", **worker_result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/admin/capabilities/delete")
async def delete_capability(
    payload: DeleteCapabilityRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
) -> JSONResponse:
    session = require_roles(request, {"admin", "editor"})
    verify_csrf(session, x_csrf_token)
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)

    command_id = f"CAPDEL-{uuid.uuid4().hex[:12].upper()}"
    try:
        worker_result = await gateway.send_command(
            "delete_capability",
            command_id,
            model_id=payload.model_id,
            capability_id=payload.capability_id,
            timeout=30,
        )
        if worker_result.get("status") != "ok":
            return JSONResponse(
                {"error": str(worker_result.get("error") or "Failed to delete capability entry")},
                status_code=500,
            )
        await asyncio.to_thread(
            write_audit,
            user_id=int(session["user_id"]),
            username=str(session.get("username") or ""),
            action="delete_capability",
            source_path=f"{payload.model_id}/{payload.capability_id}",
            result="ok",
            details=json.dumps({"capability_id": payload.capability_id, "model_id": payload.model_id}),
        )
        return JSONResponse({"status": "ok", **worker_result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
