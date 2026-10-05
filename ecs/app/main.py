from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from ecs.app.config import APP_NAME, APP_VERSION, ROOT_PATH, ensure_directories
from ecs.app.database import delete_expired_sessions, initialize_database
from ecs.app import email_notifications, email_store
from ecs.app.wiki_mcp import mcp, transport
from ecs.app.chat_history import interrupt_unfinished_turns, prune_expired_conversations
from ecs.app.routes import (
    admin_users,
    ask,
    auth,
    chat_history,
    manage,
    mcp_setup,
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
    interrupt_unfinished_turns()
    delete_expired_sessions()
    prune_expired_conversations(30)
    email_store.initialize()
    email_settings = email_notifications.Settings.from_env()
    email_settings.validate()
    stop = threading.Event()
    email_task = asyncio.create_task(email_notifications.run_service(email_settings, stop)) if email_settings.enabled else None
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        stop.set()
        if email_task is not None:
            await email_task


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
app.include_router(chat_history.router)
app.include_router(uploads.router)
app.include_router(manage.router)
app.include_router(status.router)
app.include_router(worker_socket.router)
app.include_router(wecom.router)
app.include_router(admin_users.router)

app.mount("/mcp", transport)
app.include_router(mcp_setup.router)
