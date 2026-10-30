"""Worker Registry for managing slave registrations, heartbeats, and WebSocket connections."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set
from fastapi import WebSocket

from master.database import Database
from shared.models import (
    HeartbeatPayload,
    StudentAccount,
    WorkerInfo,
    WorkerMetrics,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
)
from shared.protocol import WorkerStatus, WsMessageType

logger = logging.getLogger("master.registry")


class WorkerRegistry:
    def __init__(self, db: Database):
        self.db = db
        self.workers: Dict[str, WorkerInfo] = {}
        self.worker_sockets: Dict[str, WebSocket] = {}
        self.dashboard_sockets: Set[WebSocket] = set()
        self._check_task: Optional[asyncio.Task] = None

    def start_heartbeat_checker(self):
        if not self._check_task:
            self._check_task = asyncio.create_task(self._heartbeat_checker_loop())

    async def _heartbeat_checker_loop(self):
        """Checks for timed out workers every 2 seconds. Mark offline if >15s without heartbeat."""
        while True:
            try:
                await asyncio.sleep(2.0)
                now = time.time()
                changed = False
                for worker in list(self.workers.values()):
                    if worker.status in [WorkerStatus.ONLINE, WorkerStatus.RUNNING]:
                        if now - worker.last_heartbeat > 15.0:
                            logger.warning(f"Worker {worker.worker_id} timed out (>15s without heartbeat). Marking OFFLINE.")
                            worker.status = WorkerStatus.OFFLINE
                            self.db.upsert_worker(
                                worker_id=worker.worker_id,
                                hostname=worker.hostname,
                                ip=worker.ip,
                                remote_ip=worker.remote_ip,
                                cpu_cores=worker.cpu_cores,
                                version=worker.version,
                                status=worker.status.value,
                                last_seen=worker.last_heartbeat,
                            )
                            changed = True

                if changed:
                    await self.broadcast_worker_update()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat checker loop: {e}", exc_info=True)

    def register_worker(self, req: WorkerRegisterRequest, remote_ip: str) -> WorkerRegisterResponse:
        now = time.time()
        existing = self.workers.get(req.worker_id)
        status = WorkerStatus.ONLINE

        if existing:
            existing.hostname = req.hostname
            existing.ip = req.ip
            existing.remote_ip = remote_ip
            existing.cpu_cores = req.cpu_cores
            existing.version = req.version
            existing.status = status
            existing.last_heartbeat = now
            worker_info = existing
        else:
            worker_info = WorkerInfo(
                worker_id=req.worker_id,
                hostname=req.hostname,
                ip=req.ip,
                remote_ip=remote_ip,
                cpu_cores=req.cpu_cores,
                version=req.version,
                status=status,
                last_heartbeat=now,
            )
            self.workers[req.worker_id] = worker_info

        self.db.upsert_worker(
            worker_id=worker_info.worker_id,
            hostname=worker_info.hostname,
            ip=worker_info.ip,
            remote_ip=worker_info.remote_ip,
            cpu_cores=worker_info.cpu_cores,
            version=worker_info.version,
            status=worker_info.status.value,
            last_seen=now,
        )

        logger.info(f"Worker registered: {req.worker_id} ({req.hostname} @ {remote_ip})")
        return WorkerRegisterResponse(
            status="ok",
            worker_id=req.worker_id,
            master_time=now,
        )

    def register_worker_ws(self, worker_id: str, ws: WebSocket):
        self.worker_sockets[worker_id] = ws
        if worker_id in self.workers:
            self.workers[worker_id].status = WorkerStatus.ONLINE
            self.workers[worker_id].last_heartbeat = time.time()

    def unregister_worker_ws(self, worker_id: str):
        if worker_id in self.worker_sockets:
            del self.worker_sockets[worker_id]
        if worker_id in self.workers:
            self.workers[worker_id].status = WorkerStatus.OFFLINE

    def handle_heartbeat(self, payload: HeartbeatPayload):
        worker = self.workers.get(payload.worker_id)
        now = time.time()
        if worker:
            worker.last_heartbeat = now
            if worker.status == WorkerStatus.OFFLINE:
                worker.status = WorkerStatus.ONLINE
            worker.metrics = WorkerMetrics(
                cpu_percent=payload.cpu_percent,
                memory_percent=payload.memory_percent,
                active_vu=payload.active_vu,
                socket_connected=payload.socket_connected,
            )

    def register_dashboard_ws(self, ws: WebSocket):
        self.dashboard_sockets.add(ws)

    def unregister_dashboard_ws(self, ws: WebSocket):
        self.dashboard_sockets.discard(ws)

    async def broadcast_to_dashboard(self, message: Dict[str, Any]):
        dead_sockets = set()
        for ws in self.dashboard_sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead_sockets.add(ws)
        for ws in dead_sockets:
            self.dashboard_sockets.discard(ws)

    async def broadcast_worker_update(self):
        workers_list = [w.model_dump() for w in self.workers.values()]
        counts = {
            "total": len(self.workers),
            "online": sum(1 for w in self.workers.values() if w.status == WorkerStatus.ONLINE),
            "running": sum(1 for w in self.workers.values() if w.status == WorkerStatus.RUNNING),
            "offline": sum(1 for w in self.workers.values() if w.status == WorkerStatus.OFFLINE),
        }
        await self.broadcast_to_dashboard({
            "type": WsMessageType.WORKER_STATUS_CHANGED.value,
            "counts": counts,
            "workers": workers_list,
        })

    async def send_to_worker(self, worker_id: str, message: Dict[str, Any]) -> bool:
        ws = self.worker_sockets.get(worker_id)
        if not ws:
            logger.warning(f"No active WebSocket connection for worker {worker_id}")
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception as e:
            logger.error(f"Failed to send message to worker {worker_id}: {e}")
            return False

    def get_online_workers(self, filter_ids: Optional[List[str]] = None) -> List[WorkerInfo]:
        online = [w for w in self.workers.values() if w.status in (WorkerStatus.ONLINE, WorkerStatus.RUNNING)]
        if filter_ids:
            return [w for w in online if w.worker_id in filter_ids]
        return online

    def assign_accounts_to_worker(self, worker_id: str, accounts: List[StudentAccount]):
        if worker_id in self.workers:
            self.workers[worker_id].assigned_accounts = accounts
