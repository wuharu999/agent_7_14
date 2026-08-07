from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from typing import Any, Iterable
import uuid


KNOWLEDGE_STATES = {"known", "assumed", "unknown", "conflicted"}
CONFIRMATION_STATES = {"unknown", "proposed", "confirmed", "conflicted"}
SOURCE_OWNERS = {
    "customer",
    "wiki",
    "vendor",
    "calculation",
    "simulation",
    "bench",
    "pilot",
    "field",
}
CONCLUSIONS = {
    "fit",
    "fit_with_conditions",
    "prototype_required",
    "insufficient_evidence",
    "not_a_fit",
}
PATCHABLE_OBJECT_ROOTS = {
    "goal",
    "workflow",
    "environment",
    "operating_profile",
    "allowed_modifications",
    "human_intervention",
}
PATCHABLE_COLLECTION_ROOTS = {
    "actors",
    "objects",
    "acceptance_criteria",
    "facts",
    "assumptions",
    "requirements",
    "unresolved_issues",
}
PATCH_OPERATIONS = {"set", "append", "upsert"}
_TEXT_PATCH_PATHS = {
    ("goal", "original_text"),
    ("goal", "normalized_value"),
    ("workflow", "trigger"),
    ("workflow", "end_outcome"),
}
_CONFIRMATION_PATCH_PATHS = {
    ("goal", "confirmation"),
    ("workflow", "confirmation"),
    ("allowed_modifications", "confirmation"),
    ("human_intervention", "confirmation"),
}
_DYNAMIC_PATCH_ROOTS = {
    "environment",
    "operating_profile",
    "allowed_modifications",
    "human_intervention",
}
_COLLECTION_PATCH_FIELDS = {
    "actors": {
        "semantic_key", "actor_id", "name", "role", "original_text",
        "normalized_value", "knowledge_state", "owner", "evidence_locator",
        "last_changed_version",
    },
    "objects": {
        "semantic_key", "object_id", "name", "original_text", "normalized_value",
        "knowledge_state", "owner", "evidence_locator", "last_changed_version",
    },
    "acceptance_criteria": {
        "semantic_key", "criterion_id", "name", "original_text", "normalized_value",
        "knowledge_state", "owner", "evidence_locator", "last_changed_version",
    },
    "facts": {
        "semantic_key", "original_text", "normalized_value", "knowledge_state",
        "owner", "evidence_locator", "affected_decision", "can_change_conclusion",
        "last_changed_version",
    },
    "assumptions": {
        "semantic_key", "original_text", "normalized_value", "knowledge_state",
        "owner", "evidence_locator", "affected_decision", "can_change_conclusion",
        "last_changed_version",
    },
    "requirements": {
        "semantic_key", "requirement_id", "original_text", "normalized_value",
        "knowledge_state", "owner", "evidence_locator", "affected_decision",
        "can_change_conclusion", "last_changed_version",
    },
    "unresolved_issues": {
        "semantic_key", "issue_id", "original_text", "normalized_value",
        "knowledge_state", "owner", "evidence_locator", "affected_decision",
        "can_change_conclusion", "next_action", "status", "last_changed_version",
        "resolution_options",
    },
}
_COLLECTION_IDENTITY_FIELDS = {
    "actors": ("semantic_key", "actor_id"),
    "objects": ("semantic_key", "object_id"),
    "acceptance_criteria": ("semantic_key", "criterion_id"),
    "facts": ("semantic_key",),
    "assumptions": ("semantic_key",),
    "requirements": ("semantic_key", "requirement_id"),
    "unresolved_issues": ("semantic_key", "issue_id"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


def initial_state(session_id: str, initial_intent: str) -> dict[str, Any]:
    intent = initial_intent.strip()
    state: dict[str, Any] = {
        "session_id": session_id,
        "state_version": 1,
        "status": "clarifying",
        "initial_intent": intent,
        "goal": {
            "original_text": intent,
            "normalized_value": "",
            "confirmation": "unknown",
        },
        "workflow": {
            "trigger": "",
            "steps": [],
            "end_outcome": "",
            "confirmation": "unknown",
        },
        "actors": [],
        "objects": [],
        "environment": {},
        "operating_profile": {},
        "allowed_modifications": {},
        "human_intervention": {},
        "acceptance_criteria": [],
        "facts": [],
        "assumptions": [],
        "requirements": [],
        "unresolved_issues": [],
        "question_history": [],
        "candidate_solution_paths": [],
        "minimum_gate": {"passed": False, "missing": ["goal", "workflow"]},
        "stability": {
            "stable": False,
            "reason": "The customer goal and workflow still need confirmation.",
            "remaining_user_decisions": ["goal", "workflow"],
        },
        "current_question": None,
        "analysis_trigger": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    return evaluate_state(state)


def _confirmed(value: Any) -> bool:
    return str(value or "") == "confirmed"


def _customer_issue_keys(state: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for issue in state.get("unresolved_issues", []):
        if not isinstance(issue, dict):
            continue
        if (
            issue.get("owner") == "customer"
            and bool(issue.get("can_change_conclusion"))
            and issue.get("status", "open") == "open"
        ):
            keys.append(str(issue.get("semantic_key") or issue.get("issue_id") or "unknown"))
    return keys


def evaluate_state(state: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(state)
    goal = result.get("goal") if isinstance(result.get("goal"), dict) else {}
    workflow = result.get("workflow") if isinstance(result.get("workflow"), dict) else {}
    missing: list[str] = []
    if not (_confirmed(goal.get("confirmation")) and str(goal.get("normalized_value") or "").strip()):
        missing.append("goal")
    workflow_complete = (
        _confirmed(workflow.get("confirmation"))
        and bool(str(workflow.get("trigger") or "").strip())
        and bool([step for step in workflow.get("steps", []) if str(step).strip()])
        and bool(str(workflow.get("end_outcome") or "").strip())
    )
    if not workflow_complete:
        missing.append("workflow")
    minimum_passed = not missing
    result["minimum_gate"] = {"passed": minimum_passed, "missing": missing}

    customer_issues = _customer_issue_keys(result)
    has_path = bool(result.get("candidate_solution_paths"))
    stable = minimum_passed and has_path and not customer_issues
    if stable:
        reason = "The goal and workflow are confirmed, and no remaining customer decision is expected to change the conclusion."
    elif not minimum_passed:
        reason = "The minimum goal and workflow gate has not passed."
    elif not has_path:
        reason = "A preliminary fit or hard-failure path has not yet been established."
    else:
        reason = "High-impact customer decisions remain unresolved."
    result["stability"] = {
        "stable": stable,
        "reason": reason,
        "remaining_user_decisions": customer_issues,
    }
    if result.get("status") not in {
        "analyzing",
        "analysis_failed",
        "report_ready",
        "refining",
    }:
        result["status"] = "minimum_ready" if minimum_passed else "clarifying"
    result["updated_at"] = utc_now()
    return result


def _clean_patch_path(value: Any) -> tuple[str, ...]:
    path = str(value or "").strip().strip(".")
    parts = tuple(part for part in path.split(".") if part)
    if not parts or len(parts) > 4:
        raise ValueError("State patch path is invalid")
    if any(not part.replace("_", "").isalnum() for part in parts):
        raise ValueError("State patch path contains an invalid segment")
    return parts


def _bounded_text(value: Any, *, field: str, maximum: int = 5000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} patch value must be text")
    clean = value.strip()
    if not clean or len(clean) > maximum:
        raise ValueError(f"{field} patch text is empty or too long")
    return clean


def _finite_number(value: Any, *, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} patch value must be a finite number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field} patch number must be finite")
    return value


def _validated_dynamic_value(path: tuple[str, ...], value: Any) -> Any:
    field = ".".join(path)
    if isinstance(value, str):
        return _bounded_text(value, field=field)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _finite_number(value, field=field)
    if not isinstance(value, dict):
        raise ValueError(f"{field} patch value has an unsupported type")
    allowed = {"value", "min", "max", "unit"}
    if not value or set(value) - allowed:
        raise ValueError(f"{field} patch envelope has unsupported fields")
    unit = _bounded_text(value.get("unit"), field=f"{field}.unit", maximum=40)
    normalized: dict[str, Any] = {"unit": unit}
    for key in ("value", "min", "max"):
        if key in value:
            normalized[key] = _finite_number(value[key], field=f"{field}.{key}")
    if not any(key in normalized for key in ("value", "min", "max")):
        raise ValueError(f"{field} patch envelope needs value, min, or max")
    if "min" in normalized and "max" in normalized and normalized["min"] > normalized["max"]:
        raise ValueError(f"{field} patch envelope min exceeds max")
    return normalized


def _validated_record_value(root: str, value: Any) -> Any:
    field = f"{root}.normalized_value"
    if isinstance(value, str):
        if len(value) > 5000:
            raise ValueError(f"{field} patch text is too long")
        return value.strip()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _finite_number(value, field=field)
    if isinstance(value, dict):
        return _validated_dynamic_value((root, "normalized_value"), value)
    if isinstance(value, list) and len(value) <= 64:
        return [
            _bounded_text(item, field=field, maximum=500)
            for item in value
        ]
    raise ValueError(f"{field} patch value has an unsupported type")


def _validated_collection_value(root: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{root} patch value must be an object")
    allowed = _COLLECTION_PATCH_FIELDS[root]
    unexpected = set(value) - allowed
    if unexpected:
        raise ValueError(f"{root} patch contains unsupported fields")
    normalized = deepcopy(value)
    if not any(
        str(normalized.get(field) or "").strip()
        for field in _COLLECTION_IDENTITY_FIELDS[root]
    ):
        raise ValueError(f"{root} patch requires a stable semantic identifier")
    for key in (
        "semantic_key", "requirement_id", "issue_id", "actor_id", "object_id",
        "criterion_id", "name", "role", "original_text", "affected_decision",
        "next_action", "status",
    ):
        if key in normalized:
            normalized[key] = _bounded_text(normalized[key], field=f"{root}.{key}")
    if "knowledge_state" in normalized and normalized["knowledge_state"] not in KNOWLEDGE_STATES:
        raise ValueError(f"{root} patch has an invalid knowledge state")
    if "owner" in normalized and normalized["owner"] not in SOURCE_OWNERS:
        raise ValueError(f"{root} patch has an invalid owner")
    if "normalized_value" in normalized:
        normalized["normalized_value"] = _validated_record_value(
            root, normalized["normalized_value"]
        )
    if "evidence_locator" in normalized:
        locator = normalized["evidence_locator"]
        if locator is not None:
            normalized["evidence_locator"] = _bounded_text(
                locator, field=f"{root}.evidence_locator", maximum=1000
            )
    if "can_change_conclusion" in normalized and not isinstance(
        normalized["can_change_conclusion"], bool
    ):
        raise ValueError(f"{root} patch conclusion flag must be boolean")
    if "last_changed_version" in normalized and (
        isinstance(normalized["last_changed_version"], bool)
        or not isinstance(normalized["last_changed_version"], int)
        or normalized["last_changed_version"] < 1
    ):
        raise ValueError(f"{root} patch version must be a positive integer")
    if "resolution_options" in normalized:
        options = normalized["resolution_options"]
        if not isinstance(options, list) or len(options) > 8:
            raise ValueError(f"{root} patch resolution options must be a bounded list")
        normalized["resolution_options"] = [
            _bounded_text(item, field=f"{root}.resolution_options", maximum=500)
            for item in options
        ]
    return normalized


def _collection_identity(root: str, value: dict[str, Any]) -> tuple[str, str]:
    for field in _COLLECTION_IDENTITY_FIELDS[root]:
        identity = str(value.get(field) or "").strip()
        if identity:
            return field, identity
    raise ValueError(f"{root} patch requires a stable semantic identifier")


def apply_state_patch(
    state: dict[str, Any], patches: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Apply only bounded semantic updates; runtime/version fields are never patchable."""
    validate_state(state)
    result = deepcopy(state)
    for raw in list(patches)[:32]:
        if not isinstance(raw, dict):
            raise ValueError("State patch entries must be objects")
        operation = str(raw.get("op") or "")
        if operation not in PATCH_OPERATIONS:
            raise ValueError("State patch operation is not allowed")
        parts = _clean_patch_path(raw.get("path"))
        root = parts[0]
        value = deepcopy(raw.get("value"))
        if len(str(value)) > 20_000:
            raise ValueError("State patch value exceeds the size limit")

        if operation == "set":
            if root not in PATCHABLE_OBJECT_ROOTS or len(parts) < 2:
                raise ValueError("State patch set path is not allowed")
            if parts == ("workflow", "steps"):
                if not isinstance(value, list) or not 1 <= len(value) <= 64:
                    raise ValueError("workflow.steps patch value must be a bounded list")
                value = [
                    _bounded_text(item, field="workflow.steps", maximum=500)
                    for item in value
                ]
            elif parts in _CONFIRMATION_PATCH_PATHS:
                if value not in CONFIRMATION_STATES:
                    raise ValueError("Confirmation patch value is invalid")
            elif parts in _TEXT_PATCH_PATHS:
                value = _bounded_text(value, field=".".join(parts))
            elif root in _DYNAMIC_PATCH_ROOTS and len(parts) == 2:
                value = _validated_dynamic_value(parts, value)
            else:
                raise ValueError("State patch set path has no value contract")
            target = result.setdefault(root, {})
            if not isinstance(target, dict):
                raise ValueError("State patch target must be an object")
            for segment in parts[1:-1]:
                child = target.setdefault(segment, {})
                if not isinstance(child, dict):
                    raise ValueError("State patch nested target must be an object")
                target = child
            target[parts[-1]] = value
            continue

        if root not in PATCHABLE_COLLECTION_ROOTS or len(parts) != 1:
            raise ValueError("State patch collection path is not allowed")
        collection = result.setdefault(root, [])
        if not isinstance(collection, list):
            raise ValueError("State patch collection target must be an array")
        if len(collection) >= 1000:
            raise ValueError("State patch collection exceeds the record limit")
        value = _validated_collection_value(root, value)
        if operation == "append":
            collection.append(value)
            continue
        identity_field, identity = _collection_identity(root, value)
        result[root] = [
            item
            for item in collection
            if not isinstance(item, dict)
            or str(item.get(identity_field) or "").strip() != identity
        ] + [value]
    validate_state(result)
    return evaluate_state(result)


def validate_state(state: dict[str, Any], *, session_id: str | None = None) -> None:
    if not isinstance(state, dict):
        raise ValueError("Scenario state must be an object")
    required = {
        "session_id",
        "state_version",
        "status",
        "initial_intent",
        "goal",
        "workflow",
        "facts",
        "assumptions",
        "requirements",
        "unresolved_issues",
        "question_history",
        "minimum_gate",
        "stability",
    }
    missing = required - set(state)
    if missing:
        raise ValueError(f"Scenario state is missing fields: {', '.join(sorted(missing))}")
    if session_id is not None and state.get("session_id") != session_id:
        raise ValueError("Scenario state belongs to another session")
    if not isinstance(state.get("state_version"), int) or state["state_version"] < 1:
        raise ValueError("Scenario state version must be a positive integer")
    if len(str(state)) > 500_000:
        raise ValueError("Scenario state exceeds the size limit")
    for collection_name in ("facts", "assumptions", "requirements", "unresolved_issues"):
        collection = state.get(collection_name)
        if not isinstance(collection, list):
            raise ValueError(f"{collection_name} must be an array")
        for item in collection:
            if not isinstance(item, dict):
                raise ValueError(f"{collection_name} entries must be objects")
            knowledge_state = item.get("knowledge_state")
            if knowledge_state is not None and knowledge_state not in KNOWLEDGE_STATES:
                raise ValueError(f"Invalid knowledge state: {knowledge_state}")
            owner = item.get("owner")
            if owner is not None and owner not in SOURCE_OWNERS:
                raise ValueError(f"Invalid source owner: {owner}")


def question(
    semantic_key: str,
    text: str,
    reason: str,
    options: Iterable[str],
    *,
    impact: Iterable[str],
    blocking: bool = True,
    refines_question_id: str | None = None,
    previous_answer: str | None = None,
    missing_precision: str | None = None,
) -> dict[str, Any]:
    return {
        "question_id": new_identifier("Q"),
        "semantic_key": semantic_key,
        "question": text,
        "reason_for_asking": reason,
        "decision_impact": list(impact),
        "can_change_conclusion": True,
        "blocking": blocking,
        "target_owner": "customer",
        "answer_type": "single_select_or_custom",
        "options": [str(value) for value in options][:3],
        "prerequisite_keys": [],
        "refines_question_id": refines_question_id,
        "previous_answer": previous_answer,
        "missing_precision": missing_precision,
    }


def _asked_keys(state: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for record in state.get("question_history", []):
        if isinstance(record, dict):
            keys.add(str(record.get("semantic_key") or ""))
    return keys


def default_candidate_questions(state: dict[str, Any], language: str = "en") -> list[dict[str, Any]]:
    zh = language.lower().startswith("zh")
    resolved = _asked_keys(state)
    candidates = [
        question(
            "goal.customer_outcome",
            "这项部署必须为客户实现什么可观察结果？" if zh else "What observable customer outcome must this deployment achieve?",
            "目标决定后续需求和成功判定。" if zh else "The outcome anchors the requirements and success decision.",
            ["验证技术可行性", "运行有限试点", "无人值守生产部署"] if zh else ["Prove technical feasibility", "Run a limited pilot", "Operate unattended in production"],
            impact=["feasibility", "acceptance"],
        ),
        question(
            "workflow.trigger",
            "什么事件会触发机器人开始一次任务？" if zh else "What event triggers the robot to start one task run?",
            "触发条件定义工作流的起点。" if zh else "The trigger defines the start boundary of the workflow.",
            ["操作员指令", "系统/API 信号", "传感器检测到对象"] if zh else ["Operator command", "System or API signal", "Sensor detects an object"],
            impact=["architecture"],
        ),
        question(
            "workflow.major_steps",
            "请确认从触发到完成的主要步骤。" if zh else "Which major steps happen from the trigger to completion?",
            "端到端步骤是允许分析的最低条件之一。" if zh else "The end-to-end steps are part of the minimum analysis gate.",
            ["导航→识别→抓取→交付", "识别→操作→验证", "接收→处理→移交"] if zh else ["Navigate → identify → pick → deliver", "Identify → manipulate → verify", "Receive → process → hand off"],
            impact=["feasibility", "architecture"],
        ),
        question(
            "workflow.end_outcome",
            "一次任务完成时，必须能观察到什么结果？" if zh else "What observable result marks one task run as complete?",
            "结束状态防止把局部接口误当作完整任务能力。" if zh else "The end state prevents treating a partial interface as an end-to-end behavior.",
            ["物体到达指定位置", "系统确认处理成功", "人员确认并接管"] if zh else ["Object reaches the destination", "System records successful completion", "A person confirms and takes over"],
            impact=["acceptance"],
        ),
        question(
            "deployment.stage",
            "本次目标是监督式 PoC、有限试点，还是无人值守生产？" if zh else "Is the target a supervised PoC, a limited pilot, or unattended production?",
            "部署阶段会改变安全、证据和可靠性门槛。" if zh else "Deployment stage changes the safety, evidence, and reliability bar.",
            ["监督式 PoC", "有限试点", "无人值守生产"] if zh else ["Supervised PoC", "Limited pilot", "Unattended production"],
            impact=["safety", "feasibility"],
        ),
        question(
            "environment.allowed_modifications",
            "允许对环境做多大改造？" if zh else "How much may the environment be modified?",
            "标记、照明、托盘或机械导向等改造可能改变结论。" if zh else "Markers, lighting, trays, or mechanical guides can change the conclusion.",
            ["不允许改造", "允许低成本改造", "允许结构性改造"] if zh else ["No modifications", "Low-cost modifications", "Structural modifications allowed"],
            impact=["feasibility", "cost"],
        ),
        question(
            "object.operating_envelope",
            "机器人必须处理的对象尺寸和重量范围是什么？" if zh else "What object size and weight envelope must the robot handle?",
            "负载和尺寸边界直接影响抓取与可达性。" if zh else "Payload and dimensions directly affect grasp and reach feasibility.",
            ["小型轻量且规格固定", "尺寸重量在已知范围内变化", "目前范围未知"] if zh else ["Small, light, fixed items", "Known bounded variation", "Envelope is not known yet"],
            impact=["feasibility", "architecture"],
        ),
        question(
            "acceptance.observable_success",
            "验收一次成功运行时要测量什么？" if zh else "What should be measured to accept one successful run?",
            "可测量的验收标准决定验证方案。" if zh else "A measurable acceptance criterion determines the validation plan.",
            ["任务完成且无人工干预", "规定时间内完成", "成功率达到约定阈值"] if zh else ["Completion without intervention", "Completion within a time limit", "Success rate meets a threshold"],
            impact=["acceptance"],
        ),
    ]
    return [item for item in candidates if item["semantic_key"] not in resolved]


def select_question(
    state: dict[str, Any], candidates: Iterable[dict[str, Any]], language: str = "en"
) -> dict[str, Any] | None:
    resolved = _asked_keys(state)
    allowed_impacts = {"safety", "feasibility", "architecture", "cost", "acceptance"}
    priority = {"safety": 0, "feasibility": 1, "architecture": 2, "acceptance": 3, "cost": 4}
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = str(candidate.get("semantic_key") or "").strip()
        is_explicit_refinement = bool(candidate.get("refines_question_id"))
        if (
            not key
            or (key in resolved and not is_explicit_refinement)
            or candidate.get("target_owner", "customer") != "customer"
        ):
            continue
        impacts = [value for value in candidate.get("decision_impact", []) if value in allowed_impacts]
        if not impacts:
            continue
        item = deepcopy(candidate)
        item["options"] = [str(value) for value in item.get("options", []) if str(value).strip()][:3]
        if not item["options"]:
            continue
        filtered.append(item)
    if not filtered:
        filtered = default_candidate_questions(state, language)
    if not filtered:
        return None
    minimum_priority = {
        "goal.customer_outcome": 0,
        "workflow.trigger": 1,
        "workflow.major_steps": 2,
        "workflow.end_outcome": 3,
    }
    minimum_missing = not bool(state.get("minimum_gate", {}).get("passed"))
    filtered.sort(
        key=lambda item: (
            minimum_priority.get(str(item.get("semantic_key")), 9) if minimum_missing else 0,
            min(priority.get(value, 9) for value in item.get("decision_impact", [])),
            0 if bool(item.get("can_change_conclusion")) else 1,
            0 if bool(item.get("blocking")) else 1,
        )
    )
    return filtered[0]


def _upsert_issue(
    state: dict[str, Any], semantic_key: str, *, answer: str, unknown: bool
) -> None:
    issues = [
        item
        for item in state.get("unresolved_issues", [])
        if not isinstance(item, dict) or item.get("semantic_key") != semantic_key
    ]
    if unknown:
        issues.append(
            {
                "issue_id": new_identifier("ISSUE"),
                "semantic_key": semantic_key,
                "original_text": answer,
                "normalized_value": "",
                "knowledge_state": "unknown",
                "owner": "customer",
                "evidence_locator": None,
                "affected_decision": "feasibility",
                "can_change_conclusion": True,
                "next_action": "Customer confirmation",
                "resolution_options": [
                    "Provide the missing value",
                    "Use a conservative assumption",
                    "Assign verification to the vendor or pilot owner",
                ],
                "status": "open",
                "last_changed_version": state["state_version"] + 1,
            }
        )
    state["unresolved_issues"] = issues


def apply_answer(
    state: dict[str, Any],
    *,
    question_id: str,
    answer: str,
    answer_mode: str,
) -> dict[str, Any]:
    validate_state(state)
    result = deepcopy(state)
    current = result.get("current_question")
    if not isinstance(current, dict) or current.get("question_id") != question_id:
        raise ValueError("This question is no longer current")
    if answer_mode not in {"option", "custom", "unknown"}:
        raise ValueError("Invalid answer mode")
    clean_answer = answer.strip()
    if answer_mode != "unknown" and not clean_answer:
        raise ValueError("Answer cannot be empty")
    semantic_key = str(current.get("semantic_key") or "")
    next_version = int(result["state_version"]) + 1
    unknown = answer_mode == "unknown"
    record = {
        "question_id": question_id,
        "semantic_key": semantic_key,
        "question": str(current.get("question") or ""),
        "answer": clean_answer if not unknown else "I don't know yet",
        "answer_mode": answer_mode,
        "resolution": "unresolved" if unknown else "resolved",
        "refines_question_id": current.get("refines_question_id"),
        "previous_answer": current.get("previous_answer"),
        "missing_precision": current.get("missing_precision"),
        "state_version": next_version,
        "answered_at": utc_now(),
    }
    result.setdefault("question_history", []).append(record)
    result["state_version"] = next_version
    result["current_question"] = None
    is_unknown_resolution = bool(
        current.get("refines_question_id") and current.get("unknown_resolution")
    )
    if not is_unknown_resolution:
        _upsert_issue(result, semantic_key, answer=record["answer"], unknown=unknown)

    if not unknown:
        if semantic_key == "goal.customer_outcome":
            result["goal"] = {
                "original_text": clean_answer,
                "normalized_value": clean_answer,
                "confirmation": "confirmed",
            }
        elif semantic_key == "workflow.trigger":
            result["workflow"]["trigger"] = clean_answer
        elif semantic_key == "workflow.major_steps":
            separators = ["→", "->", ";", "\n"]
            steps = [clean_answer]
            for separator in separators:
                if separator in clean_answer:
                    steps = [part.strip() for part in clean_answer.split(separator) if part.strip()]
                    break
            result["workflow"]["steps"] = steps
        elif semantic_key == "workflow.end_outcome":
            result["workflow"]["end_outcome"] = clean_answer
        elif semantic_key == "environment.allowed_modifications":
            result["allowed_modifications"] = {
                "original_text": clean_answer,
                "normalized_value": clean_answer,
                "confirmation": "confirmed",
            }
        elif semantic_key == "object.operating_envelope":
            result.setdefault("objects", []).append(
                {"original_text": clean_answer, "normalized_value": clean_answer}
            )
        elif semantic_key == "deployment.stage":
            result.setdefault("operating_profile", {})["deployment_stage"] = clean_answer
        elif semantic_key == "acceptance.observable_success":
            result.setdefault("acceptance_criteria", []).append(
                {"original_text": clean_answer, "normalized_value": clean_answer}
            )
        else:
            result.setdefault("facts", []).append(
                {
                    "semantic_key": semantic_key,
                    "original_text": clean_answer,
                    "normalized_value": clean_answer,
                    "knowledge_state": "known",
                    "owner": "customer",
                    "evidence_locator": None,
                    "last_changed_version": next_version,
                }
            )

        if is_unknown_resolution:
            resolution = str(clean_answer).casefold()
            for issue in result.get("unresolved_issues", []):
                if not isinstance(issue, dict) or issue.get("semantic_key") != semantic_key:
                    continue
                if "assumption" in resolution:
                    issue["knowledge_state"] = "assumed"
                    issue["status"] = "resolved"
                    issue["can_change_conclusion"] = False
                    result.setdefault("assumptions", []).append(
                        {
                            "semantic_key": semantic_key,
                            "original_text": clean_answer,
                            "normalized_value": "Conservative assumption pending validation",
                            "knowledge_state": "assumed",
                            "owner": "customer",
                            "evidence_locator": None,
                            "last_changed_version": next_version,
                        }
                    )
                elif "vendor" in resolution or "pilot" in resolution:
                    issue["owner"] = "vendor" if "vendor" in resolution else "pilot"
                    issue["next_action"] = "Validate the missing boundary before deployment"
                    issue["can_change_conclusion"] = False
                else:
                    issue["knowledge_state"] = "known"
                    issue["normalized_value"] = clean_answer
                    issue["status"] = "resolved"
                    issue["can_change_conclusion"] = False

    workflow = result["workflow"]
    if workflow.get("trigger") and workflow.get("steps") and workflow.get("end_outcome"):
        workflow["confirmation"] = "confirmed"
    if result.get("minimum_gate", {}).get("passed") or (
        result["goal"].get("confirmation") == "confirmed"
        and workflow.get("confirmation") == "confirmed"
    ):
        result["candidate_solution_paths"] = [
            {
                "path_id": "PATH-PRELIMINARY",
                "description": "Evidence-backed robot workflow to be validated",
                "status": "preliminary",
            }
        ]
    return evaluate_state(result)


def attach_next_question(
    state: dict[str, Any], candidates: Iterable[dict[str, Any]] | None = None, language: str = "en"
) -> dict[str, Any]:
    result = evaluate_state(state)
    suppressed = int(result.get("countdown_suppressed_at_version") or 0) == int(
        result["state_version"]
    )
    if (
        result["stability"]["stable"]
        and not suppressed
        and result.get("status") not in {"analyzing", "report_ready"}
    ):
        result["current_question"] = None
        result["status"] = "stability_countdown"
        return result

    customer_issues = [
        item
        for item in result.get("unresolved_issues", [])
        if isinstance(item, dict)
        and item.get("owner") == "customer"
        and item.get("status", "open") == "open"
    ]
    if customer_issues:
        issue = customer_issues[0]
        previous = next(
            (
                item
                for item in reversed(result.get("question_history", []))
                if isinstance(item, dict) and item.get("semantic_key") == issue.get("semantic_key")
            ),
            {},
        )
        selected = question(
            str(issue.get("semantic_key") or "unknown.customer_decision"),
            f"How should we resolve this unknown: {previous.get('question') or issue.get('original_text')}?",
            "Choose an assumption or validation owner so the scenario does not remain permanently blocked.",
            [
                "Use a conservative assumption",
                "Assign verification to the vendor",
                "Assign verification to the pilot owner",
            ],
            impact=[str(issue.get("affected_decision") or "feasibility")],
            blocking=True,
            refines_question_id=str(previous.get("question_id") or "unknown"),
            previous_answer=str(previous.get("answer") or "I don't know yet"),
            missing_precision="Unknown value needs an assumption or named validation owner",
        )
        selected["unknown_resolution"] = True
        result["current_question"] = selected
        return result

    selected = select_question(result, candidates or [], language)
    result["current_question"] = selected
    return result


def scenario_narrative(state: dict[str, Any]) -> str:
    goal = state.get("goal", {})
    workflow = state.get("workflow", {})
    lines = [
        f"Initial intent: {state.get('initial_intent', '')}",
        f"Confirmed goal: {goal.get('normalized_value') or 'unknown'}",
        f"Trigger: {workflow.get('trigger') or 'unknown'}",
        "Major steps: " + (" -> ".join(str(value) for value in workflow.get("steps", [])) or "unknown"),
        f"End outcome: {workflow.get('end_outcome') or 'unknown'}",
    ]
    for record in state.get("question_history", []):
        if isinstance(record, dict):
            lines.append(f"{record.get('semantic_key')}: {record.get('answer')}")
    if state.get("unresolved_issues"):
        lines.append("Unresolved conditions:")
        for issue in state["unresolved_issues"]:
            if isinstance(issue, dict):
                lines.append(f"- {issue.get('semantic_key')}: {issue.get('original_text')}")
    return "\n".join(lines)
