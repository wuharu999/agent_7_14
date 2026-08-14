from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ecs.app.config import APP_NAME, APP_VERSION, ensure_directories
from ecs.app.database import delete_expired_sessions, initialize_database
from ecs.app.routes import (
    admin_users,
    ask,
    auth,
    capability_match,
    manage,
    pages,
    scenario_sessions,
    status,
    uploads,
    wecom,
    worker_socket,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_directories()
    initialize_database()
    delete_expired_sessions()
    reanalysis_task = asyncio.create_task(
        scenario_sessions.scenario_reanalysis_dispatcher(),
        name="scenario-reanalysis-dispatcher",
    )
    try:
        yield
    finally:
        reanalysis_task.cancel()
        with suppress(asyncio.CancelledError):
            await reanalysis_task


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(ask.router)
app.include_router(uploads.router)
app.include_router(manage.router)
app.include_router(status.router)
app.include_router(worker_socket.router)
app.include_router(wecom.router)
app.include_router(admin_users.router)
app.include_router(capability_match.router)
app.include_router(scenario_sessions.router)
