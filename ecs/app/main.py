from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from ecs.app.config import APP_NAME, APP_VERSION, ROOT_PATH, ensure_directories
from ecs.app.database import delete_expired_sessions, initialize_database
from ecs.app.routes import (
    admin_users,
    ask,
    auth,
    manage,
    pages,
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
    yield


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
    root_path=ROOT_PATH,
)


@app.middleware("http")
async def preserve_unprefixed_static_assets(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Serve absolute /static URLs even when the application uses a root path."""
    request_path = request.scope.get("path", "")
    if ROOT_PATH and (
        request_path == "/static" or request_path.startswith("/static/")
    ):
        request.scope["root_path"] = ""
    return await call_next(request)


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
