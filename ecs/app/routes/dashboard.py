from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from ecs.app.auth import require_roles, require_user, verify_csrf
from ecs.app.gateway import gateway

router = APIRouter()

@router.get("/dashboard")
async def dashboard_view(request: Request):
    session = require_user(request)
    error = request.query_params.get("error")
    msg = request.query_params.get("msg")
    
    from ecs.app.database import _DB_LOCK, _connect
    
    visitor_count = 0
    wiki_metrics = []
    team_settings = []
    
    with _DB_LOCK, _connect() as connection:
        # Visitor count
        visitor_count = connection.execute("SELECT COUNT(DISTINCT ip_address) as count FROM qa_visitors").fetchone()["count"]
        
        # Wiki metrics
        wiki_metrics = connection.execute("SELECT date, new_entries FROM wiki_metrics ORDER BY date DESC LIMIT 30").fetchall()
        wiki_metrics = [dict(r) for r in wiki_metrics]
        
        # Team settings based on role
        if session.get("role") == "admin":
            settings = connection.execute("SELECT * FROM team_settings").fetchall()
        else:
            settings = connection.execute(
                """
                SELECT ts.* FROM team_settings ts
                JOIN team_members m ON ts.team_name = m.team_name
                WHERE m.user_id = ? AND m.role = 'captain'
                """, (session["user_id"],)
            ).fetchall()
        
        team_settings = [dict(s) for s in settings]
        
    from ecs.app.pages import _render
    return HTMLResponse(_render(request, "dashboard.html", {
        "error": error,
        "msg": msg,
        "visitor_count": visitor_count,
        "wiki_metrics": wiki_metrics,
        "team_settings": team_settings
    }))

@router.post("/dashboard/trigger_review")
async def trigger_review(
    request: Request,
    team: str = Form(...),
    csrf_token: str = Form(...),
):
    session = require_user(request)
    verify_csrf(session, csrf_token)
    
    # Check if admin or captain
    is_admin = session.get("role") == "admin"
    from ecs.app.database import _DB_LOCK, _connect, utc_now
    with _DB_LOCK, _connect() as connection:
        if not is_admin:
            is_captain = connection.execute(
                "SELECT id FROM team_members WHERE team_name = ? AND user_id = ? AND role = 'captain'",
                (team, session["user_id"])
            ).fetchone()
            if not is_captain:
                return RedirectResponse(f"/dashboard?error={quote('Unauthorized')}", status_code=303)
                
        # Send WS command to worker
        if not gateway.online:
            return RedirectResponse(f"/dashboard?error={quote('Worker is offline')}", status_code=303)
            
        import uuid
        task_id = f"review-{uuid.uuid4().hex[:8]}"
        
        message = {
            "type": "trigger_review",
            "id": task_id,
            "team": team,
            "initiated_by": session["username"]
        }
        
        try:
            await gateway.send(message)
        except Exception:
            return RedirectResponse(f"/dashboard?error={quote('Failed to send request')}", status_code=303)
            
        # Update last review
        connection.execute(
            """
            INSERT INTO team_settings (team_name, last_review_at) 
            VALUES (?, ?) 
            ON CONFLICT(team_name) DO UPDATE SET 
            prev_review_at = last_review_at, 
            last_review_at = excluded.last_review_at
            """, (team, utc_now())
        )
        
    return RedirectResponse("/dashboard?msg=Review+triggered", status_code=303)

@router.post("/dashboard/toggle_auto_review")
async def toggle_auto_review(
    request: Request,
    team: str = Form(...),
    enabled: int = Form(...),
    csrf_token: str = Form(...),
):
    session = require_user(request)
    verify_csrf(session, csrf_token)
    
    # Check if admin or captain
    is_admin = session.get("role") == "admin"
    from ecs.app.database import _DB_LOCK, _connect
    with _DB_LOCK, _connect() as connection:
        if not is_admin:
            is_captain = connection.execute(
                "SELECT id FROM team_members WHERE team_name = ? AND user_id = ? AND role = 'captain'",
                (team, session["user_id"])
            ).fetchone()
            if not is_captain:
                return RedirectResponse(f"/dashboard?error={quote('Unauthorized')}", status_code=303)
                
        connection.execute(
            """
            INSERT INTO team_settings (team_name, auto_review_enabled) 
            VALUES (?, ?) 
            ON CONFLICT(team_name) DO UPDATE SET 
            auto_review_enabled = excluded.auto_review_enabled
            """, (team, enabled)
        )
        
    return RedirectResponse(f"/dashboard?msg=Auto+review+{'enabled' if enabled else 'disabled'}", status_code=303)
