from __future__ import annotations

import asyncio
import logging

from worker.manager import WorkerManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def main() -> None:
    try:
        asyncio.run(WorkerManager().run())
    except KeyboardInterrupt:
        logging.getLogger("worker").info("Worker stopped")


if __name__ == "__main__":
    main()
