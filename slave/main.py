"""Slave Worker process entrypoint."""

import argparse
import asyncio
import logging
import os
import signal
import sys

from slave.client import SlaveClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("slave.main")


def parse_args():
    parser = argparse.ArgumentParser(description="Distributed Load Testing Slave Worker")
    parser.add_argument(
        "--master",
        type=str,
        default=os.getenv("MASTER_URL", "http://127.0.0.1:8000"),
        help="Master Server URL (e.g., http://192.168.1.10:8000)",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=os.getenv("WORKER_ID", None),
        help="Explicit Worker ID (defaults to persistent auto-generated ID)",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    logger.info(f"Starting Slave Worker targeting Master at {args.master} ...")

    client = SlaveClient(master_url=args.master, worker_id=args.worker_id)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: client.stop())
        except NotImplementedError:
            pass  # Windows compatibility

    try:
        await client.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Worker shutting down...")
    finally:
        client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
