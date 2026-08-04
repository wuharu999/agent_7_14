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
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ecs.app.auth import current_session, require_roles, verify_csrf
from ecs.app.config import WORKER_TIMEOUT
from ecs.app.database import (
    aggregate_capability_gaps,
    create_capability_draft_stub,
    create_scenario_assessment,
    get_allowed_teams,
    get_robot_options,
    get_scenario_assessment,
    list_capability_draft_stubs,
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


def _template(name: str) -> str:
    return (_TEMPLATE_ROOT / name).read_text(encoding="utf-8")


def _public_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in assessment.items()
        if key not in {"user_id", "conversation_id"}
    }


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


def _markdown_report(assessment: dict[str, Any]) -> str:
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
        "# Robot Scenario Feasibility Report",
        "",
        f"- Assessment: `{assessment['assessment_id']}`",
        f"- Model: `{assessment['model_id']}`",
        f"- Scenario: `{scenario.get('scenario_id', '')}` — {scenario.get('title', '')}",
        f"- Created: {assessment['created_at']}",
        "",
        "## Scenario",
        "",
        f"**Business goal:** {scenario.get('business_goal', '')}",
        "",
        f"**Target:** {summary['target']}",
        "",
        f"**Environment:** {summary['environment']}",
        "",
        f"**Payload / facts:** {summary['payload']}",
        "",
        f"**Throughput / operations:** {summary['throughput']}",
        "",
        "## Atomic Requirement Match Matrix",
        "",
    ]
    for match in feasibility.get("matches", []):
        requirement_id = str(match.get("requirement_id") or "")
        requirement = requirements.get(requirement_id, {})
        lines.extend(
            [
                f"### {requirement_id}: {requirement.get('name', '')}",
                "",
                f"- Required layer: `{requirement.get('required_abstraction_level', '')}`",
                f"- Match state: `{match.get('match_state', '')}`",
                f"- Confidence: {match.get('confidence', 0)}",
            ]
        )
        capability_ids = [str(value) for value in match.get("capability_ids", [])]
        if capability_ids:
            lines.append("- Matched capabilities:")
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
                    lines.append(f"    - Evidence: `{evidence}`")
        for gate in match.get("gates", []):
            basis = gate.get("basis") or (
                f"requirement={_display_value(gate.get('requirement_value'))}; "
                f"capability={_display_value(gate.get('capability_value'))}"
            )
            lines.append(
                f"- Gate `{gate.get('name', '')}`: **{gate.get('status', '')}** — "
                f"{basis}"
            )
        for gap in match.get("gaps", []):
            lines.append(f"- Gap: {gap}")
        rd_gap = match.get("rd_gap")
        if isinstance(rd_gap, dict):
            lines.append(
                f"- R&D estimate: **{rd_gap.get('person_weeks', 0)} person-weeks** "
                f"({', '.join(str(value) for value in rd_gap.get('domains', []))})"
            )
        lines.extend(["", f"Next action: {match.get('next_action', '')}", ""])
    effort = feasibility.get("rd_effort", {})
    lines.extend(
        [
            "## Conclusions",
            "",
            f"- Technical: **{feasibility.get('technical_conclusion', '')}**",
            f"- Deployment: **{feasibility.get('deployment_conclusion', '')}**",
            f"- R&D effort: **{effort.get('total_person_weeks', 0)} person-weeks**",
            f"- Domains: {', '.join(str(value) for value in effort.get('domains', [])) or 'None'}",
            "",
            "## Residual Risks",
            "",
        ]
    )
    risks = feasibility.get("residual_risks", []) or ["No residual risks recorded"]
    lines.extend(f"- {risk}" for risk in risks)
    lines.extend(["", "## Next Experiment", "", str(feasibility.get("next_experiment") or "Not specified"), ""])
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
    try:
        worker_result = await gateway.command(
            "analyze_scenario",
            timeout=WORKER_TIMEOUT + 30,
            scenario_text=payload.scenario_text,
            model_id=payload.model_id,
            language=payload.language,
        )
        if worker_result.get("status") != "ok" or not isinstance(worker_result.get("result"), dict):
            return JSONResponse(
                {"error": str(worker_result.get("error") or "Scenario analysis failed")},
                status_code=502,
            )
        result = worker_result["result"]
        scenario_spec = result.get("scenario_spec")
        requirements = result.get("atomic_requirements")
        capabilities = result.get("capabilities")
        feasibility = result.get("feasibility_assessment")
        if not isinstance(scenario_spec, dict) or not isinstance(feasibility, dict):
            raise ValueError("Worker returned an incomplete assessment")
        if not isinstance(requirements, list) or not isinstance(capabilities, list):
            raise ValueError("Worker returned invalid requirement or capability records")
        assessment_id = f"ASM-{uuid.uuid4().hex.upper()}"
        feasibility["assessment_id"] = assessment_id
        scenario_id = str(scenario_spec.get("scenario_id") or "")
        if not scenario_id.startswith("SCN-"):
            scenario_id = f"SCN-{uuid.uuid4().hex[:12].upper()}"
            scenario_spec["scenario_id"] = scenario_id
        feasibility["scenario_id"] = scenario_id
        session = current_session(request)
        await asyncio.to_thread(
            create_scenario_assessment,
            assessment_id=assessment_id,
            user_id=int(session["user_id"]) if session else None,
            conversation_id=payload.conversation_id,
            model_id=payload.model_id,
            scenario_spec=scenario_spec,
            atomic_requirements=requirements,
            capabilities=capabilities,
            feasibility_assessment=feasibility,
        )
        saved = get_scenario_assessment(assessment_id)
        if saved is None:
            raise RuntimeError("Assessment persistence failed")
        return JSONResponse(
            {"status": "ok", "assessment_id": assessment_id, "assessment": _public_assessment(saved)}
        )
    except (ConnectionError, TimeoutError, asyncio.TimeoutError):
        log.exception("Worker unavailable during scenario analysis")
        return JSONResponse({"error": "Scenario analysis is temporarily unavailable"}, status_code=503)
    except (TypeError, ValueError, RuntimeError):
        log.exception("Invalid scenario analysis result")
        return JSONResponse({"error": "Scenario analysis returned an invalid result"}, status_code=502)


@router.get("/api/capability-match/assessments/{assessment_id}")
async def get_capability_match(assessment_id: str) -> JSONResponse:
    assessment = get_scenario_assessment(assessment_id)
    if assessment is None:
        return JSONResponse({"error": "Assessment not found"}, status_code=404)
    return JSONResponse({"status": "ok", "assessment": _public_assessment(assessment)})


@router.get("/api/capability-match/export")
async def export_capability_match(
    assessment_id: str = Query(...),
    format: str = Query(default="markdown", pattern=r"^(markdown|pdf)$"),
) -> Response:
    assessment = get_scenario_assessment(assessment_id)
    if assessment is None:
        return JSONResponse({"error": "Assessment not found"}, status_code=404)
    report = _markdown_report(assessment)
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
    return HTMLResponse(page)


@router.get("/api/admin/capabilities")
async def capability_gap_analytics(request: Request) -> JSONResponse:
    require_roles(request, {"admin"})
    return JSONResponse(
        {
            "status": "ok",
            "gaps": aggregate_capability_gaps(),
            "draft_stubs": list_capability_draft_stubs(),
        }
    )


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
