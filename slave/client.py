"""Slave Client connecting to Master Server with auto-registration and heartbeat."""

import asyncio
import json
import logging
import os
import platform
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import psutil
import websockets

from shared.models import (
    HeartbeatPayload,
    PrepareTestPayload,
    WorkerRegisterRequest,
)
from shared.protocol import WsMessageType
from slave.runner import LoadRunner

logger = logging.getLogger("slave.client")

ID_FILE = Path(__file__).resolve().parent / ".worker_id"


def get_persistent_worker_id() -> str:
    """Read or create a unique, persistent Worker ID."""
    if ID_FILE.exists():
        try:
            return ID_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    hostname = socket.gethostname()
    clean_host = "".join(c for c in hostname if c.isalnum() or c in "-_")
    new_id = f"worker-{clean_host}-{uuid.uuid4().hex[:6]}"
    try:
        ID_FILE.write_text(new_id, encoding="utf-8")
    except Exception:
        pass
    return new_id


def get_local_ip() -> str:
    """Detect local LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually send data
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class SlaveClient:
    def __init__(self, master_url: str, worker_id: Optional[str] = None):
        self.master_url = master_url.rstrip("/")
        self.worker_id = worker_id or get_persistent_worker_id()
        self.hostname = socket.gethostname()
        self.ip = get_local_ip()
        self.cpu_cores = os.cpu_count() or 4
        self.version = "1.0.0"

        self.runner = LoadRunner(self.worker_id)
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = True

        self._heartbeat_task: Optional[asyncio.Task] = None
        self._metrics_task: Optional[asyncio.Task] = None

    async def register(self) -> bool:
        """Register to Master HTTP API with retry backoff."""
        reg_url = f"{self.master_url}/api/workers/register"
        payload = WorkerRegisterRequest(
            worker_id=self.worker_id,
            hostname=self.hostname,
            ip=self.ip,
            cpu_cores=self.cpu_cores,
            version=self.version,
        )

        backoff = 2
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    res = await client.post(reg_url, json=payload.model_dump())
                    if res.status_code == 200:
                        logger.info(f"Successfully registered to Master ({self.master_url}) as {self.worker_id}")
                        return True
                    else:
                        logger.warning(f"Registration rejected with status {res.status_code}: {res.text}")
            except Exception as e:
                logger.warning(f"Master not reachable at {self.master_url} ({e}). Retrying in {backoff}s...")

            await asyncio.sleep(backoff)
            backoff = min(15, backoff * 1.5)

        return False

    async def run(self):
        """Main lifecycle loop with auto-reconnect."""
        while self._running:
            registered = await self.register()
            if not registered:
                break

            ws_url = self.master_url.replace("http://", "ws://").replace("https://", "wss://")
            ws_endpoint = f"{ws_url}/ws/worker/{self.worker_id}"

            try:
                logger.info(f"Connecting to Master WebSocket at {ws_endpoint} ...")
                async with websockets.connect(ws_endpoint) as websocket:
                    self.ws = websocket
                    logger.info("Master WebSocket connected. Starting heartbeat...")

                    # Start heartbeat task
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    # Listen for Master commands
                    async for message in websocket:
                        try:
                            data = json.loads(message)
                            await self._handle_command(data)
                        except Exception as e:
                            logger.error(f"Error handling master message: {e}", exc_info=True)

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                logger.warning(f"Master connection lost ({e}). Reconnecting in 3s...")
            except Exception as e:
                logger.error(f"Unexpected WebSocket error: {e}", exc_info=True)
            finally:
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                if self._metrics_task and not self._metrics_task.done():
                    self._metrics_task.cancel()
                self.ws = None

            await asyncio.sleep(3.0)

    async def _heartbeat_loop(self):
        """Sends heartbeat every 5 seconds to Master."""
        while self.ws and self._running:
            try:
                cpu = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory().percent
                active_vu = self.runner.runner.user_count if (self.runner and self.runner.runner) else 0

                payload = HeartbeatPayload(
                    worker_id=self.worker_id,
                    timestamp=time.time(),
                    cpu_percent=cpu,
                    memory_percent=mem,
                    active_vu=active_vu,
                    socket_connected=active_vu,
                )

                await self.ws.send(json.dumps({
                    "type": WsMessageType.HEARTBEAT.value,
                    "payload": payload.model_dump(),
                }))

                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat send failed: {e}")
                break

    async def _metrics_report_loop(self, test_id: str):
        """Every 1s, send metrics to Master while running."""
        while self.runner.is_running and self.ws and self._running:
            try:
                await asyncio.sleep(1.0)
                metrics = self.runner.collect_metrics()
                if self.ws:
                    await self.ws.send(json.dumps({
                        "type": WsMessageType.METRICS_UPDATE.value,
                        "payload": metrics.model_dump(),
                    }))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Metrics report send failed: {e}")

    async def _handle_command(self, data: Dict[str, Any]):
        msg_type = data.get("type")
        payload = data.get("payload", {})

        if msg_type == WsMessageType.HEARTBEAT_ACK.value:
            pass  # Heartbeat ACK received

        elif msg_type == WsMessageType.PREPARE_TEST.value:
            logger.info(f"Received PREPARE_TEST command for {payload.get('test_id')}")
            prep_data = PrepareTestPayload(**payload)
            self.runner.prepare(prep_data)

            # Reply TEST_READY
            if self.ws:
                await self.ws.send(json.dumps({
                    "type": WsMessageType.TEST_READY.value,
                    "test_id": prep_data.test_id,
                    "command_id": prep_data.command_id,
                }))

        elif msg_type == WsMessageType.START_TEST.value:
            test_id = payload.get("test_id")
            start_at = payload.get("start_at", time.time())
            delay = max(0.0, start_at - time.time())
            logger.info(f"Received START_TEST for {test_id}, starting in {delay:.2f}s...")
            await asyncio.sleep(delay)

            self.runner.start()
            # Start metrics reporting
            self._metrics_task = asyncio.create_task(self._metrics_report_loop(test_id))

        elif msg_type == WsMessageType.STOP_TEST.value:
            test_id = payload.get("test_id")
            logger.info(f"Received STOP_TEST for {test_id}")
            self.runner.stop()
            if self._metrics_task and not self._metrics_task.done():
                self._metrics_task.cancel()

            # Final metrics
            metrics = self.runner.collect_metrics()
            if self.ws:
                await self.ws.send(json.dumps({
                    "type": WsMessageType.METRICS_UPDATE.value,
                    "payload": metrics.model_dump(),
                }))
                await self.ws.send(json.dumps({
                    "type": WsMessageType.TEST_COMPLETED.value,
                    "test_id": test_id,
                }))

    def stop(self):
        self._running = False
        self.runner.stop()
