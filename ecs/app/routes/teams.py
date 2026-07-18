from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ecs.app.auth import require_user, verify_csrf
from ecs.app.config import ALLOWED_TEAMS

router = APIRouter()


@router.get("/teams")
async def get_teams_page(request: Request):
    session = require_user(request)
    error = request.query_params.get("error")
    msg = request.query_params.get("msg")
    
    from ecs.app.database import _DB_LOCK, _connect
    
    captain_teams = []
    
    with _DB_LOCK, _connect() as connection:
        # If admin, fetch all teams as captain, else fetch teams where user is captain
        if session.get("role") == "admin":
            teams = connection.execute("SELECT DISTINCT team_name FROM team_members").fetchall()
        else:
            teams = connection.execute(
                "SELECT team_name FROM team_members WHERE user_id = ? AND role = 'captain'",
                (session["user_id"],)
            ).fetchall()
            
        for t in teams:
            team_name = t["team_name"]
            
            # Fetch requests
            requests = connection.execute(
                "SELECT id, user_id, requested_at FROM team_requests WHERE team_name = ? AND status = 'pending'",
                (team_name,)
            ).fetchall()
            
            # Fetch members
            members = connection.execute(
                """
                SELECT m.user_id, u.username, u.email, m.role, m.joined_at 
                FROM team_members m
                JOIN users u ON m.user_id = u.id
                WHERE m.team_name = ?
                """, (team_name,)
            ).fetchall()
            
            captain_teams.append({
                "name": team_name,
                "requests": [dict(r) for r in requests],
                "members": [dict(m) for m in members]
            })
            
    from ecs.app.pages import _render
    return HTMLResponse(_render(request, "teams.html", {
        "error": error,
        "msg": msg,
        "captain_teams": captain_teams,
        "user_id": session["user_id"]
    }))


@router.post("/teams/join")
async def join_team(
    request: Request,
    team: str = Form(...),
    csrf_token: str = Form(...),
):
    session = require_user(request)
    verify_csrf(session, csrf_token)
    user_id = session["user_id"]

    if team not in ALLOWED_TEAMS:
        return RedirectResponse(f"/dashboard?error={quote('Invalid team')}", status_code=303)

    from ecs.app.database import _DB_LOCK, _connect
    with _DB_LOCK, _connect() as connection:
        # Check if already a member
        member = connection.execute(
            "SELECT id FROM team_members WHERE team_name = ? AND user_id = ?",
            (team, user_id)
        ).fetchone()
        if member:
            return RedirectResponse(f"/dashboard?error={quote('Already a member')}", status_code=303)
        
        # Check if already requested
        existing = connection.execute(
            "SELECT id FROM team_requests WHERE team_name = ? AND user_id = ? AND status = 'pending'",
            (team, user_id)
        ).fetchone()
        if not existing:
            from ecs.app.database import utc_now
            connection.execute(
                "INSERT INTO team_requests (team_name, user_id, status, requested_at) VALUES (?, ?, 'pending', ?)",
                (team, user_id, utc_now())
            )
            
    return RedirectResponse("/dashboard?msg=Join+request+sent", status_code=303)


@router.post("/teams/approve")
async def approve_request(
    request: Request,
    request_id: int = Form(...),
    csrf_token: str = Form(...),
):
    session = require_user(request)
    verify_csrf(session, csrf_token)
    
    from ecs.app.database import _DB_LOCK, _connect, utc_now
    
    with _DB_LOCK, _connect() as connection:
        req = connection.execute("SELECT team_name, user_id, status FROM team_requests WHERE id = ?", (request_id,)).fetchone()
        if not req or req["status"] != "pending":
            return RedirectResponse(f"/dashboard?error={quote('Invalid request')}", status_code=303)
        
        team_name = req["team_name"]
        
        # Verify current user is a captain or admin
        is_admin = session.get("role") == "admin"
        if not is_admin:
            is_captain = connection.execute(
                "SELECT id FROM team_members WHERE team_name = ? AND user_id = ? AND role = 'captain'",
                (team_name, session["user_id"])
            ).fetchone()
            if not is_captain:
                return RedirectResponse(f"/dashboard?error={quote('Unauthorized')}", status_code=303)
        
        # Check max 15 members
        member_count = connection.execute(
            "SELECT COUNT(*) as count FROM team_members WHERE team_name = ?", (team_name,)
        ).fetchone()["count"]
        
        if member_count >= 15:
            return RedirectResponse(f"/dashboard?error={quote('Team is full (max 15)')}", status_code=303)
        
        connection.execute("UPDATE team_requests SET status = 'approved' WHERE id = ?", (request_id,))
        connection.execute(
            "INSERT INTO team_members (team_name, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (team_name, req["user_id"], utc_now())
        )
        # Update user's teams column
        user_row = connection.execute("SELECT teams FROM users WHERE id = ?", (req["user_id"],)).fetchone()
        current_teams = [t.strip() for t in str(user_row["teams"]).split(",") if t.strip()]
        if team_name not in current_teams:
            current_teams.append(team_name)
            connection.execute("UPDATE users SET teams = ? WHERE id = ?", (",".join(current_teams), req["user_id"]))
            
    return RedirectResponse("/dashboard?msg=Request+approved", status_code=303)


@router.post("/teams/kick")
async def kick_member(
    request: Request,
    team: str = Form(...),
    member_id: int = Form(...),
    csrf_token: str = Form(...),
):
    session = require_user(request)
    verify_csrf(session, csrf_token)
    
    from ecs.app.database import _DB_LOCK, _connect
    
    with _DB_LOCK, _connect() as connection:
        # Verify current user is a captain or admin
        is_admin = session.get("role") == "admin"
        if not is_admin:
            is_captain = connection.execute(
                "SELECT id FROM team_members WHERE team_name = ? AND user_id = ? AND role = 'captain'",
                (team, session["user_id"])
            ).fetchone()
            if not is_captain:
                return RedirectResponse(f"/dashboard?error={quote('Unauthorized')}", status_code=303)
        
        # Cannot kick yourself
        if int(session["user_id"]) == int(member_id):
            return RedirectResponse(f"/dashboard?error={quote('Cannot kick yourself')}", status_code=303)

        connection.execute("DELETE FROM team_members WHERE team_name = ? AND user_id = ?", (team, member_id))
        
        # Remove from user's teams column
        user_row = connection.execute("SELECT teams FROM users WHERE id = ?", (member_id,)).fetchone()
        if user_row:
            current_teams = [t.strip() for t in str(user_row["teams"]).split(",") if t.strip()]
            if team in current_teams:
                current_teams.remove(team)
                connection.execute("UPDATE users SET teams = ? WHERE id = ?", (",".join(current_teams), member_id))
                
    return RedirectResponse("/dashboard?msg=Member+kicked", status_code=303)


@router.post("/teams/transfer_captain")
async def transfer_captain(
    request: Request,
    team: str = Form(...),
    member_id: int = Form(...),
    csrf_token: str = Form(...),
):
    session = require_user(request)
    verify_csrf(session, csrf_token)
    
    from ecs.app.database import _DB_LOCK, _connect
    
    with _DB_LOCK, _connect() as connection:
        # Verify current user is a captain
        is_captain = connection.execute(
            "SELECT id FROM team_members WHERE team_name = ? AND user_id = ? AND role = 'captain'",
            (team, session["user_id"])
        ).fetchone()
        
        is_admin = session.get("role") == "admin"
        
        if not is_captain and not is_admin:
            return RedirectResponse(f"/dashboard?error={quote('Unauthorized')}", status_code=303)
            
        # Target must be a member
        target = connection.execute(
            "SELECT id FROM team_members WHERE team_name = ? AND user_id = ?",
            (team, member_id)
        ).fetchone()
        if not target:
            return RedirectResponse(f"/dashboard?error={quote('Target is not a member')}", status_code=303)
        
        # Perform transfer
        if not is_admin:
            connection.execute("UPDATE team_members SET role = 'member' WHERE team_name = ? AND user_id = ?", (team, session["user_id"]))
        connection.execute("UPDATE team_members SET role = 'captain' WHERE team_name = ? AND user_id = ?", (team, member_id))
        
    return RedirectResponse("/dashboard?msg=Captain+role+transferred", status_code=303)
