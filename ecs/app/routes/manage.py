from __future__ import annotations

import json

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ecs.app.auth import check_robot_access, require_roles, verify_csrf
from ecs.app.database import (
    assign_robot_editor,
    create_robot,
    delete_robot,
    get_all_robots,
    get_all_upload_timestamps,
    get_robot_by_id,
    get_robot_by_name,
    get_robot_editors,
    get_user_by_id,
    list_audit_log,
    list_active_editors,
    mark_sources_deleted,
    reconcile_robots_with_source_tree,
    remove_robot_editor,
    write_audit,
)
from ecs.app.gateway import gateway
from shared.team_names import normalize_team_name

router = APIRouter()


class DeleteSourceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


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
    def enrich_tree(node, current_ts=None):
        if not isinstance(node, dict):
            return
        name = node.get("name")
        node_ts = current_ts
        if name and name in timestamps:
            node_ts = timestamps[name]
        if node_ts:
            node["created_at"] = node_ts
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                enrich_tree(child, node_ts)

    enrich_tree(tree)

    return {
        "worker_online": gateway.online,
        "user": {"username": session["username"], "role": session["role"]},
        "tree": tree,
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
    description: str = Field(default="", max_length=500)
    storage_path: str = Field(default="")

class AssignEditorRequest(BaseModel):
    user_id: int

@router.get("/api/manage/robots")
async def list_robots(request: Request):
    session = require_roles(request, {"admin"})
    return {"robots": get_all_robots()}


@router.get("/api/manage/editors")
async def list_editor_pool(request: Request):
    require_roles(request, {"admin"})
    return {"editors": list_active_editors()}


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
        robot_id = create_robot(name, payload.description, name)
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


@router.post("/api/manage/generate_report")
async def generate_report_endpoint(
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin", "editor"})
    verify_csrf(session, x_csrf_token)

    from ecs.app.database import _connect, _DB_LOCK
    from datetime import datetime
    import urllib.request
    import asyncio

    with _DB_LOCK, _connect() as connection:
        visitors = connection.execute(
            "SELECT ip_address, visited_at FROM qa_visitors ORDER BY visited_at DESC"
        ).fetchall()
        audit_logs = connection.execute(
            "SELECT username, action, source_path, result, created_at FROM file_audit_log ORDER BY created_at DESC"
        ).fetchall()

    unique_ips = set()
    ip_counts = {}
    for v in visitors:
        ip = v["ip_address"]
        unique_ips.add(ip)
        ip_counts[ip] = ip_counts.get(ip, 0) + 1

    geolocations = {}

    def resolve_geo(ip: str):
        if ip in ("127.0.0.1", "localhost", "unknown"):
            return "本地 / 内网"
        try:
            with urllib.request.urlopen(f"http://ip-api.com/json/{ip}", timeout=3) as res:
                data = json.loads(res.read().decode())
                if data.get("status") == "success":
                    country = data.get("country", "未知")
                    region = data.get("regionName", "未知")
                    city = data.get("city", "未知")
                    org = data.get("org", "未知")
                    return f"{country}（{region}，{city}）- {org}"
                else:
                    return "地理位置解析失败"
        except Exception:
            return "地理定位出错"

    for ip in unique_ips:
        geolocations[ip] = await asyncio.to_thread(resolve_geo, ip)

    lines = []
    lines.append("# 用户活动与地理位置分析报告")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## 问答访客统计")
    lines.append(f"- **已记录的问答访问总数**：{len(visitors)}")
    lines.append("")
    lines.append("| IP 地址 | 访问次数 | 解析位置 / 运营商 |")
    lines.append("| --- | --- | --- |")
    for ip, count in sorted(ip_counts.items(), key=lambda x: x[1], reverse=True):
        geo = geolocations.get(ip, "未知")
        lines.append(f"| `{ip}` | {count} | {geo} |")
    lines.append("")

    lines.append("## 近期管理操作与文件审计日志")
    lines.append(f"- **操作日志总数**：{len(audit_logs)}")
    lines.append("")
    lines.append("| 时间 | 用户 | 操作 | 目标 | 结果 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for log in audit_logs[:50]:
        lines.append(
            f"| {log['created_at']} | {log['username']} | `{log['action']}` | `{log['source_path']}` | {log['result']} |"
        )

    report_markdown = "\n".join(lines)
    return {"report": report_markdown}


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
