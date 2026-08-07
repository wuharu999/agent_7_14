from __future__ import annotations

import re
from typing import Any

from shared.scenario_state import new_identifier, utc_now


STAGE_LABELS = {
    "workflow_understanding": "Understanding the workflow",
    "requirement_extraction": "Extracting testable requirements",
    "evidence_retrieval": "Retrieving capability evidence",
    "envelope_comparison": "Comparing operating envelopes",
    "gap_evaluation": "Evaluating gaps and risks",
    "recommendation_building": "Building recommendations",
    "report_generation": "Generating the report revision",
}
_SENSITIVE = re.compile(
    r"(?:/home/|/root/|\\.env|system prompt|tool[_ -]?arguments?|stack trace|traceback|api[_ -]?key|secret)",
    re.IGNORECASE,
)


def progress_event(
    *,
    session_id: str,
    analysis_job_id: str,
    state_version: int,
    stage: str,
    status: str,
    approved_facts: dict[str, int | str] | None = None,
) -> dict[str, Any]:
    if stage not in STAGE_LABELS:
        raise ValueError("Unknown analysis stage")
    if status not in {"queued", "running", "completed", "failed"}:
        raise ValueError("Invalid progress status")
    allowed_keys = {
        "requirements_identified",
        "documents_checked",
        "matches",
        "gaps",
        "elapsed_seconds",
    }
    facts = {
        key: value
        for key, value in (approved_facts or {}).items()
        if key in allowed_keys and isinstance(value, int) and not isinstance(value, bool)
    }
    summaries = []
    for key, label in (
        ("documents_checked", "capability records checked"),
        ("requirements_identified", "requirements identified"),
        ("matches", "candidate matches"),
        ("gaps", "gaps requiring action"),
        ("elapsed_seconds", "seconds elapsed"),
    ):
        if key in facts:
            summaries.append(f"{facts[key]} {label}")
    message = STAGE_LABELS[stage]
    if summaries:
        message = f"{message}: {', '.join(summaries)}"
    return {
        "event_id": new_identifier("EVT"),
        "session_id": session_id,
        "analysis_job_id": analysis_job_id,
        "scenario_state_version": state_version,
        "type": "stage_summary",
        "stage": stage,
        "status": status,
        "message": message,
        "approved_facts": facts,
        "created_at": utc_now(),
    }


def sanitize_summary(value: str, *, fallback_stage: str) -> str:
    clean = " ".join(str(value).split())[:240]
    if not clean or _SENSITIVE.search(clean):
        return STAGE_LABELS.get(fallback_stage, "Analysis is progressing")
    return clean
