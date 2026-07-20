from __future__ import annotations

import json

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ecs.app.auth import require_roles, verify_csrf, check_robot_access
from ecs.app.database import get_all_upload_timestamps, mark_sources_deleted, write_audit, list_audit_log, get_all_robots, create_robot, get_robot_editors, assign_robot_editor, remove_robot_editor, get_user_by_id
from ecs.app.gateway import gateway

router = APIRouter()


class DeleteSourceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


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
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="")
    storage_path: str = Field(default="")

class AssignEditorRequest(BaseModel):
    user_id: int

@router.get("/api/manage/robots")
async def list_robots(request: Request):
    session = require_roles(request, {"admin"})
    return {"robots": get_all_robots()}

@router.post("/api/manage/robots")
async def create_robot_endpoint(
    payload: CreateRobotRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)
    try:
        rid = create_robot(payload.name, payload.description, payload.storage_path)
        return {"status": "ok", "robot_id": rid}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

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
    assign_robot_editor(robot_id, payload.user_id)
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


