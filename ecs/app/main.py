from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ecs.app.config import APP_NAME, APP_VERSION, ensure_directories
from ecs.app.database import delete_expired_sessions, initialize_database
from ecs.app.routes import auth, ask, authoring, manage, pages, status, uploads, wecom, worker_socket, admin_users

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_directories()
    initialize_database()
    delete_expired_sessions()
    yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

project_root = Path(__file__).resolve().parents[2]
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
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
