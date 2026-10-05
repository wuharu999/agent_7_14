from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ecs.app.auth import check_robot_access, require_roles, verify_csrf
from ecs.app.database import (
    assign_robot_editor,
    create_robot,
    create_chat_robot_option,
    delete_chat_robot_option,
    delete_robot,
    get_all_robots,
    get_all_upload_timestamps,
    get_chat_robot_option_by_id,
    get_chat_robot_option_by_name,
    get_robot_by_id,
    get_robot_by_name,
    get_robot_editors,
    get_user_by_id,
    list_all_chat_robot_options,
    list_audit_log,
    list_recent_qa_question_records,
    list_active_editors,
    mark_sources_deleted,
    reconcile_robots_with_source_tree,
    remove_robot_editor,
    set_chat_robot_options_order,
    set_robot_display_order,
    update_chat_robot_option,
    update_robot_display_names,
    write_audit,
)
from ecs.app.gateway import gateway
from shared.team_names import normalize_team_name

router = APIRouter()


class DeleteSourceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


def _markdown_cell(value: object) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>").replace("|", "\\|")


def _question_report_markdown(records: list[dict[str, object]]) -> str:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[(str(record["conversation_id"]), str(record["ip_address"]))].append(record)

    lines = [
        "# 最近 14 天问答记录报告",
        f"生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- **问题总数**：{len(records)}",
        f"- **会话数**：{len({str(record['conversation_id']) for record in records})}",
        f"- **IP 地址数**：{len({str(record['ip_address']) for record in records})}",
    ]
    for (conversation_id, ip_address), questions in grouped.items():
        lines.extend(
            [
                "",
                f"## 会话 `{_markdown_cell(conversation_id)}` · IP `{_markdown_cell(ip_address)}`",
                f"- **问题数**：{len(questions)}",
                "",
                "| 时间 | 机器人 / 主题 | 语言 | 问题 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for question in questions:
            lines.append(
                "| {asked_at} | {topic} | {language} | {text} |".format(
                    asked_at=_markdown_cell(question["asked_at"]),
                    topic=_markdown_cell(question["topic_label"]),
                    language=_markdown_cell(question["language"]),
                    text=_markdown_cell(question["question"]),
                )
            )
    return "\n".join(lines)


def _robot_names_from_source_tree(tree: object) -> list[str]:
    if not isinstance(tree, dict):
        raise ValueError("Worker returned an invalid source tree")
    children = tree.get("children", [])
    if not isinstance(children, list):
        raise ValueError("Worker returned invalid source-tree children")

    names: list[str] = []
    for child in children:
        if not isinstance(child, dict):
            raise ValueError("Worker returned an invalid source-tree entry")
        if child.get("type") != "directory":
            continue
        name = normalize_team_name(
            str(child.get("name") or ""), allow_reserved=False
        )
        if str(child.get("path") or "") != name:
            raise ValueError("Worker returned an invalid robot source path")
        names.append(name)
    return names


@router.get("/api/manage/sources")
async def list_sources(request: Request):
    session = require_roles(request, {"editor", "admin"})
    try:
        result = await gateway.command("list_sources")
    except ConnectionError:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)
    except TimeoutError:
        return JSONResponse({"error": "Worker did not return the source tree in time"}, status_code=504)

    if result.get("status") != "ok":
        return JSONResponse(
            {"error": str(result.get("error") or "Unable to list sources")},
            status_code=500,
        )

    tree = result.get("tree") or {"root": "raw/sources", "children": []}
    try:
        robot_sync = reconcile_robots_with_source_tree(
            _robot_names_from_source_tree(tree)
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    for removed_name in robot_sync["removed"]:
        mark_sources_deleted(removed_name)
    if robot_sync["added"] or robot_sync["removed"]:
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="sync_robots_from_source_tree",
            source_path="raw/sources",
            result="ok",
            details=json.dumps(
                {
                    "added": robot_sync["added"],
                    "removed": robot_sync["removed"],
                },
                ensure_ascii=False,
            ),
        )

    if session.get("role") != "admin":
        tree["children"] = [
            child for child in tree.get("children", [])
            if check_robot_access(session, child.get("name"))
        ]

    timestamps = get_all_upload_timestamps()
    all_robots = get_all_robots()
    robots_by_name = {str(r["name"]): r for r in all_robots}

    def enrich_tree(node, current_ts=None):
        if not isinstance(node, dict):
            return
        name = node.get("name")
        node_ts = current_ts
        if name and name in timestamps:
            node_ts = timestamps[name]
        if node_ts:
            node["created_at"] = node_ts
        if node.get("type") == "directory" and name in robots_by_name:
            r = robots_by_name[name]
            node["display_name_zh"] = str(r.get("display_name_zh") or name)
            node["display_name_en"] = str(r.get("display_name_en") or name)
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                enrich_tree(child, node_ts)

    enrich_tree(tree)

    folder_names_zh: list[str] = []
    for child in tree.get("children", []):
        if child.get("type") == "directory":
            c_name = str(child.get("name") or "")
            r = robots_by_name.get(c_name)
            zh_name = str(r.get("display_name_zh") or c_name) if r else c_name
            folder_names_zh.append(zh_name)

    return {
        "worker_online": gateway.online,
        "user": {"username": session["username"], "role": session["role"]},
        "tree": tree,
        "folder_names_zh": folder_names_zh,
    }


@router.post("/api/manage/sources/delete")
async def delete_source(
    payload: DeleteSourceRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    source_path = payload.path.strip()

    parts = source_path.split("/")
    if parts:
        team = parts[0]
        if not check_robot_access(session, team):
            return JSONResponse({"error": "You do not have permission to manage this robot"}, status_code=403)

    try:
        result = await gateway.command("delete_source", path=source_path)
    except ConnectionError:
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="worker_offline",
        )
        return JSONResponse({"error": "Worker is offline"}, status_code=503)
    except TimeoutError:
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="timeout",
        )
        return JSONResponse({"error": "Worker did not finish the removal in time"}, status_code=504)

    status = str(result.get("status") or "failed")
    if status == "busy":
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="blocked_processing",
            details=str(result.get("error") or ""),
        )
        return JSONResponse({"error": result.get("error")}, status_code=409)
    if status != "ok":
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="failed",
            details=str(result.get("error") or ""),
        )
        return JSONResponse(
            {"error": str(result.get("error") or "Unable to remove source")},
            status_code=400,
        )

    deleted_path = str(result.get("path") or source_path)
    mark_sources_deleted(deleted_path)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="delete_source",
        source_path=deleted_path,
        result="ok",
        details=json.dumps(
            {
                "trash_path": result.get("trash_path"),
                "deleted_files": result.get("deleted_files"),
                "deleted_type": result.get("deleted_type"),
            },
            ensure_ascii=False,
        ),
    )
    return {
        "status": "ok",
        "path": deleted_path,
        "trash_path": result.get("trash_path"),
        "deleted_files": result.get("deleted_files", 0),
        "message": "Source moved to Worker trash. LLM Wiki Source Watch will process the removal.",
    }


class CreateRobotRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    chinese_name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    storage_path: str = Field(default="")


class AssignEditorRequest(BaseModel):
    user_id: int


class UpdateRobotDisplayNamesRequest(BaseModel):
    english_name: str = Field(min_length=1, max_length=64)
    chinese_name: str = Field(min_length=1, max_length=64)


class UpdateRobotOrderRequest(BaseModel):
    robot_ids: list[int] = Field(min_length=1, max_length=10_000)


@router.get("/api/manage/robots")
async def list_robots(request: Request):
    session = require_roles(request, {"admin"})
    return {"robots": get_all_robots()}


@router.get("/api/manage/editors")
async def list_editor_pool(request: Request):
    require_roles(request, {"admin"})
    return {"editors": list_active_editors()}


@router.put("/api/manage/robots/order")
async def update_robot_order(
    payload: UpdateRobotOrderRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    try:
        robots = set_robot_display_order(payload.robot_ids)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="update_robot_order",
        source_path="robots",
        result="ok",
        details=json.dumps(
            {"robot_names": [str(robot["name"]) for robot in robots]},
            ensure_ascii=False,
        ),
    )
    return {"status": "ok", "robots": robots}


@router.post("/api/manage/robots")
async def create_robot_endpoint(
    payload: CreateRobotRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)

    try:
        name = normalize_team_name(payload.name, allow_reserved=False)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    chinese_name = payload.chinese_name.strip()
    if not chinese_name:
        return JSONResponse({"error": "Chinese robot name is required"}, status_code=400)

    if get_robot_by_name(name) is not None:
        return JSONResponse({"error": f"Robot '{name}' already exists"}, status_code=409)
    if not gateway.online:
        return JSONResponse(
            {"error": "Worker is offline; robot was not created"}, status_code=503
        )

    try:
        result = await gateway.command("create_robot_folder", team=name)
    except ConnectionError:
        return JSONResponse(
            {"error": "Worker disconnected; robot was not created"}, status_code=503
        )
    except TimeoutError:
        return JSONResponse(
            {"error": "Worker did not create the robot folder in time"},
            status_code=504,
        )

    if result.get("status") != "ok":
        return JSONResponse(
            {"error": str(result.get("error") or "Worker could not create the robot folder")},
            status_code=500,
        )

    try:
        robot_id = create_robot(
            name,
            payload.description,
            name,
            display_name_en=name,
            display_name_zh=chinese_name,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return {"status": "ok", "robot_id": robot_id, "name": name}


@router.delete("/api/manage/robots/{robot_id}")
async def remove_robot(
    robot_id: int,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    robot = get_robot_by_id(robot_id)
    if robot is None:
        return JSONResponse({"error": "Robot not found"}, status_code=404)

    robot_name = str(robot["name"])
    try:
        result = await gateway.command("delete_robot_folder", team=robot_name)
    except ConnectionError:
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_robot",
            source_path=robot_name,
            result="worker_offline",
        )
        return JSONResponse(
            {"error": "Worker is offline; robot was not removed"}, status_code=503
        )
    except TimeoutError:
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_robot",
            source_path=robot_name,
            result="timeout",
        )
        return JSONResponse(
            {"error": "Worker did not remove the robot folder in time"},
            status_code=504,
        )

    status = str(result.get("status") or "failed")
    if status == "busy":
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_robot",
            source_path=robot_name,
            result="blocked_processing",
            details=str(result.get("error") or ""),
        )
        return JSONResponse({"error": result.get("error")}, status_code=409)
    if status != "ok":
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_robot",
            source_path=robot_name,
            result="failed",
            details=str(result.get("error") or ""),
        )
        return JSONResponse(
            {"error": str(result.get("error") or "Unable to remove robot")},
            status_code=400,
        )

    mark_sources_deleted(robot_name)
    delete_robot(robot_id)
    details = {
        "trash_path": result.get("trash_path"),
        "deleted_files": result.get("deleted_files", 0),
        "folder_existed": result.get("removed", True),
    }
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="delete_robot",
        source_path=robot_name,
        result="ok",
        details=json.dumps(details, ensure_ascii=False),
    )
    return {
        "status": "ok",
        "name": robot_name,
        **details,
        "message": "Robot source folder moved to Worker trash and metadata removed.",
    }


@router.patch("/api/manage/robots/{robot_id}")
async def update_robot_display_names_endpoint(
    robot_id: int,
    payload: UpdateRobotDisplayNamesRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    robot = get_robot_by_id(robot_id)
    if robot is None:
        return JSONResponse({"error": "Robot not found"}, status_code=404)
    try:
        updated = update_robot_display_names(
            robot_id,
            display_name_en=payload.english_name,
            display_name_zh=payload.chinese_name,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="update_robot_display_names",
        source_path=str(robot["name"]),
        result="ok",
        details=json.dumps(
            {
                "english_name": updated["display_name_en"],
                "chinese_name": updated["display_name_zh"],
            },
            ensure_ascii=False,
        ),
    )
    return {
        "status": "ok",
        "robot": updated,
    }

@router.get("/api/manage/robots/{robot_id}/editors")
async def list_robot_editors(robot_id: int, request: Request):
    session = require_roles(request, {"admin"})
    return {"editors": get_robot_editors(robot_id)}

@router.post("/api/manage/robots/{robot_id}/editors")
async def assign_editor(
    robot_id: int,
    payload: AssignEditorRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    user = get_user_by_id(payload.user_id)
    if user is None:
        return JSONResponse({"error": f"用户 ID {payload.user_id} 不存在"}, status_code=404)
    if get_robot_by_id(robot_id) is None:
        return JSONResponse({"error": "Robot not found"}, status_code=404)
    try:
        assign_robot_editor(robot_id, payload.user_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"status": "ok", "username": user["username"]}

@router.delete("/api/manage/robots/{robot_id}/editors/{user_id}")
async def remove_editor(
    robot_id: int,
    user_id: int,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    if get_robot_by_id(robot_id) is None:
        return JSONResponse({"error": "Robot not found"}, status_code=404)
    if get_user_by_id(user_id) is None:
        return JSONResponse({"error": "User not found"}, status_code=404)
    remove_robot_editor(robot_id, user_id)
    return {"status": "ok"}

@router.get("/api/manage/audit_log")
async def get_audit_log(request: Request):
    session = require_roles(request, {"admin", "editor"})
    return {"audit_log": list_audit_log()}


@router.post("/api/manage/question_report")
async def question_report_endpoint(
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    records = list_recent_qa_question_records()
    return {"report": _question_report_markdown(records)}


@router.get("/api/manage/contradictions")
async def get_contradictions(request: Request):
    session = require_roles(request, {"editor", "admin"})
    from ecs.app.database import get_recent_wiki_contradictions
    from ecs.app.auth import check_robot_access

    all_contradictions = get_recent_wiki_contradictions(days=7)
    filtered = [
        c for c in all_contradictions
        if check_robot_access(session, c["team"])
    ]
    return {"contradictions": filtered}


class CreateChatRobotRequest(BaseModel):
    name: str = Field(default="", max_length=64)
    display_name_zh: str = Field(min_length=1, max_length=64)
    display_name_en: str = Field(min_length=1, max_length=64)
    folder_name: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=256)


class UpdateChatRobotRequest(BaseModel):
    display_name_zh: str | None = Field(default=None, min_length=1, max_length=64)
    display_name_en: str | None = Field(default=None, min_length=1, max_length=64)
    folder_name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=256)
    is_enabled: int | None = None


class UpdateChatRobotOrderRequest(BaseModel):
    option_ids: list[int] = Field(min_length=1, max_length=10_000)


@router.get("/api/manage/chat_robots")
async def list_chat_robots_endpoint(request: Request):
    require_roles(request, {"editor", "admin"})
    return {
        "chat_robots": list_all_chat_robot_options(),
        "folders": [str(r["name"]) for r in get_all_robots()],
    }


@router.post("/api/manage/chat_robots")
async def create_chat_robot_endpoint(
    payload: CreateChatRobotRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    opt_name = payload.name.strip()
    if not opt_name:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", payload.display_name_en.strip()).strip("_").lower()
        if not slug:
            slug = f"bot_{uuid.uuid4().hex[:8]}"
        base_name = slug[:50]
        opt_name = base_name
        while get_chat_robot_option_by_name(opt_name):
            opt_name = f"{base_name[:48]}_{uuid.uuid4().hex[:4]}"
    try:
        opt_id = create_chat_robot_option(
            opt_name,
            display_name_zh=payload.display_name_zh,
            display_name_en=payload.display_name_en,
            folder_name=payload.folder_name,
            description=payload.description,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="create_chat_robot_option",
        source_path=opt_name,
        result="ok",
        details=json.dumps(
            {
                "name": opt_name,
                "display_name_zh": payload.display_name_zh,
                "display_name_en": payload.display_name_en,
                "folder_name": payload.folder_name,
            },
            ensure_ascii=False,
        ),
    )
    return {"status": "ok", "id": opt_id, "name": opt_name}


@router.patch("/api/manage/chat_robots/{option_id}")
async def update_chat_robot_endpoint(
    option_id: int,
    payload: UpdateChatRobotRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    try:
        updated = update_chat_robot_option(
            option_id,
            display_name_zh=payload.display_name_zh,
            display_name_en=payload.display_name_en,
            folder_name=payload.folder_name,
            description=payload.description,
            is_enabled=payload.is_enabled,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="update_chat_robot_option",
        source_path=str(updated.get("name") or option_id),
        result="ok",
        details=json.dumps(updated, ensure_ascii=False),
    )
    return {"status": "ok", "chat_robot": updated}


@router.put("/api/manage/chat_robots/order")
async def update_chat_robot_order_endpoint(
    payload: UpdateChatRobotOrderRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    try:
        ordered = set_chat_robot_options_order(payload.option_ids)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="update_chat_robot_order",
        source_path="chat_robot_options",
        result="ok",
        details=json.dumps({"option_ids": payload.option_ids}, ensure_ascii=False),
    )
    return {"status": "ok", "chat_robots": ordered}


@router.delete("/api/manage/chat_robots/{option_id}")
async def delete_chat_robot_endpoint(
    option_id: int,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    deleted = delete_chat_robot_option(option_id)
    if not deleted:
        return JSONResponse({"error": "Option not found"}, status_code=404)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="delete_chat_robot_option",
        source_path=str(deleted.get("name") or option_id),
        result="ok",
        details=json.dumps(deleted, ensure_ascii=False),
    )
    return {"status": "ok", "deleted": deleted}
