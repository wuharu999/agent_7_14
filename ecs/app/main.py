from __future__ import annotations

import logging

from fastapi import FastAPI

from ecs.app.config import APP_NAME, APP_VERSION, ensure_directories
from ecs.app.database import delete_expired_sessions, initialize_database
from ecs.app.routes import auth, ask, authoring, manage, pages, status, uploads, wecom, worker_socket, admin_users

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from pathlib import Path
from fastapi.staticfiles import StaticFiles

app = FastAPI(title=APP_NAME, version=APP_VERSION)

project_root = Path(__file__).resolve().parents[2]
petdex_dir = project_root / "petdex_robot_companion"
app.mount("/petdex", StaticFiles(directory=str(petdex_dir)), name="petdex")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(ask.router)
app.include_router(uploads.router)
app.include_router(authoring.router)
app.include_router(manage.router)
app.include_router(status.router)
app.include_router(worker_socket.router)
app.include_router(wecom.router)
app.include_router(admin_users.router)


@app.on_event("startup")
async def startup() -> None:
    ensure_directories()
    initialize_database()
    delete_expired_sessions()
