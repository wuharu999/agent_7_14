from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from worker.config import DOWNLOAD_TIMEOUT, MAX_UPLOAD_BYTES, WORKER_SHARED_SECRET

ProgressCallback = Callable[[int | None, str], Awaitable[None]]


async def download_file(
    *,
    url: str,
    destination: Path,
    on_progress: ProgressCallback,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_suffix(destination.suffix + ".part")
    timeout = httpx.Timeout(DOWNLOAD_TIMEOUT, connect=30)
    downloaded = 0
    last_report = 0.0
    headers = {
        "X-Worker-Secret": WORKER_SHARED_SECRET,
        "User-Agent": "agent-7-14-worker/1.0",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header and total_header.isdigit() else None
                if total is not None and total > MAX_UPLOAD_BYTES:
                    raise ValueError(f"Upload exceeds MAX_UPLOAD_BYTES ({MAX_UPLOAD_BYTES})")

                with part_path.open("wb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > MAX_UPLOAD_BYTES:
                            raise ValueError(f"Upload exceeds MAX_UPLOAD_BYTES ({MAX_UPLOAD_BYTES})")
                        output.write(chunk)
                        now = time.monotonic()
                        if now - last_report >= 0.75:
                            percent = int(downloaded * 100 / total) if total else None
                            await on_progress(percent, f"Downloaded {downloaded} byte(s)")
                            last_report = now
        part_path.replace(destination)
        await on_progress(100, f"Download complete: {downloaded} byte(s)")
        return destination
    except Exception:
        part_path.unlink(missing_ok=True)
        raise
