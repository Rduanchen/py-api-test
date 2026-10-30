"""Test Scheduler and State Machine coordinating distributed load tests."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from master.account_manager import AccountManager
from master.database import Database
from master.registry import WorkerRegistry
from master.statistics import StatisticsAggregator
from shared.models import (
    MetricsUpdatePayload,
    PrepareTestPayload,
    StudentAccount,
    TestStartRequest,
    TestSummaryReport,
    WorkerInfo,
)
from shared.protocol import TestMode, TestState, WorkerStatus, WsMessageType

logger = logging.getLogger("master.scheduler")


class Scheduler:
    def __init__(self, registry: WorkerRegistry, account_manager: AccountManager, db: Database):
        self.registry = registry
        self.account_manager = account_manager
        self.db = db

        self.current_state: TestState = TestState.IDLE
        self.current_test_id: Optional[str] = None
        self.current_request: Optional[TestStartRequest] = None
        self.active_workers: List[WorkerInfo] = []
        self.ready_workers: set = set()
        self.aggregator: Optional[StatisticsAggregator] = None

        self._tick_task: Optional[asyncio.Task] = None
        self._timer_task: Optional[asyncio.Task] = None
        self._state_lock = asyncio.Lock()

    async def broadcast_state(self):
        await self.registry.broadcast_to_dashboard({
            "type": WsMessageType.TEST_STATE_CHANGED.value,
            "state": self.current_state.value,
            "test_id": self.current_test_id,
            "ready_count": len(self.ready_workers),
            "total_active_workers": len(self.active_workers),
        })

    async def handle_worker_ready(self, worker_id: str, test_id: str):
        if self.current_state == TestState.PREPARING and test_id == self.current_test_id:
            self.ready_workers.add(worker_id)
            logger.info(f"Worker {worker_id} is READY for test {test_id} ({len(self.ready_workers)}/{len(self.active_workers)})")
            await self.broadcast_state()

    async def handle_worker_metrics(self, payload: MetricsUpdatePayload):
        if self.current_state == TestState.RUNNING and payload.test_id == self.current_test_id:
            if self.aggregator:
                self.aggregator.record_worker_update(payload)

    async def handle_worker_completed(self, worker_id: str, test_id: str):
        logger.info(f"Worker {worker_id} finished test {test_id}")

    async def start_test(self, req: TestStartRequest) -> Dict[str, Any]:
        async with self._state_lock:
            if self.current_state in [TestState.PREPARING, TestState.RUNNING, TestState.STOPPING]:
                raise ValueError(f"Cannot start test: another test is currently {self.current_state.value}")

            # 1. Select online workers
            online = self.registry.get_online_workers(req.worker_ids)
            if not online:
                raise ValueError("No online workers available to execute test.")

            self.active_workers = online
            self.ready_workers.clear()
            self.current_request = req
            self.current_test_id = f"test-{time.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:4]}"
            self.current_state = TestState.PREPARING

            logger.info(f"Starting test {self.current_test_id} with {len(self.active_workers)} workers in {req.mode.value} mode")

            # 2. Get exam details
            exam_info = self.account_manager.get_exam_details(req.exam_id)

            # 3. Calculate allocation per worker
            num_workers = len(self.active_workers)
            base_users_per_worker = req.total_users // num_workers
            remainder_users = req.total_users % num_workers
            spawn_rate_per_worker = max(0.1, round(req.spawn_rate / num_workers, 2))

            # Account distribution if actual mode
            account_distribution = {}
            if req.mode == TestMode.ACTUAL:
                account_distribution = self.account_manager.distribute_accounts(
                    req.exam_id, self.active_workers, accounts_per_worker=max(1, base_users_per_worker)
                )

            # 4. Prepare each worker
            cmd_id = f"cmd-prep-{uuid4().hex[:6]}"
            for idx, worker in enumerate(self.active_workers):
                worker.status = WorkerStatus.RUNNING
                worker.current_test_id = self.current_test_id

                allocated_users = base_users_per_worker + (1 if idx < remainder_users else 0)
                assigned_accounts = account_distribution.get(worker.worker_id, [])

                prepare_payload = PrepareTestPayload(
                    test_id=self.current_test_id,
                    command_id=cmd_id,
                    target_url=req.target_url,
                    mode=req.mode,
                    exam_id=req.exam_id,
                    allocated_users=allocated_users,
                    spawn_rate=spawn_rate_per_worker,
                    duration=req.duration,
                    assigned_accounts=assigned_accounts,
                    exam_info=exam_info,
                )

                await self.registry.send_to_worker(worker.worker_id, {
                    "type": WsMessageType.PREPARE_TEST.value,
                    "payload": prepare_payload.model_dump(),
                })

            # Record in DB
            self.db.save_test_record(
                test_id=self.current_test_id,
                target_url=req.target_url,
                mode=req.mode.value,
                exam_id=req.exam_id,
                total_users=req.total_users,
                duration=req.duration,
                state=self.current_state.value,
                start_time=time.strftime("%Y-%m-%d %H:%M:%S"),
            )

            await self.broadcast_state()
            await self.registry.broadcast_worker_update()

            # 5. Launch async coordination task
            asyncio.create_task(self._wait_ready_and_run(req.duration))
            return {"status": "ok", "test_id": self.current_test_id, "workers": len(self.active_workers)}

    async def _wait_ready_and_run(self, duration: int):
        # Wait up to 10 seconds for workers to be ready
        timeout = 10.0
        start_wait = time.time()
        while len(self.ready_workers) < len(self.active_workers) and (time.time() - start_wait) < timeout:
            await asyncio.sleep(0.3)

        if len(self.ready_workers) == 0:
            logger.error("No workers reported READY. Test aborted.")
            self.current_state = TestState.FAILED
            await self.broadcast_state()
            return

        logger.info(f"{len(self.ready_workers)}/{len(self.active_workers)} workers ready. Sending START_TEST command.")

        # Coordinated start timestamp (1 second from now)
        start_at = time.time() + 1.0
        cmd_id = f"cmd-start-{uuid4().hex[:6]}"

        for worker in self.active_workers:
            if worker.worker_id in self.ready_workers:
                await self.registry.send_to_worker(worker.worker_id, {
                    "type": WsMessageType.START_TEST.value,
                    "payload": {
                        "test_id": self.current_test_id,
                        "command_id": cmd_id,
                        "start_at": start_at,
                    }
                })

        # Wait until start_at
        delay = max(0.0, start_at - time.time())
        await asyncio.sleep(delay)

        self.current_state = TestState.RUNNING
        self.aggregator = StatisticsAggregator(
            test_id=self.current_test_id,
            target_url=self.current_request.target_url if self.current_request else "",
            mode=self.current_request.mode.value if self.current_request else "",
            exam_id=self.current_request.exam_id if self.current_request else "",
        )

        await self.broadcast_state()

        # Start ticker task
        self._tick_task = asyncio.create_task(self._metrics_ticker_loop())
        # Start duration timer
        self._timer_task = asyncio.create_task(self._test_duration_timer(duration))

    async def _metrics_ticker_loop(self):
        """Every 1s, aggregate latest worker metrics and broadcast to UI."""
        while self.current_state == TestState.RUNNING:
            try:
                await asyncio.sleep(1.0)
                if self.aggregator and self.current_state == TestState.RUNNING:
                    tick_data = self.aggregator.tick()
                    await self.registry.broadcast_to_dashboard({
                        "type": WsMessageType.METRICS_TICK.value,
                        "test_id": self.current_test_id,
                        "data": tick_data.model_dump(),
                    })
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics ticker loop: {e}", exc_info=True)

    async def _test_duration_timer(self, duration: int):
        try:
            await asyncio.sleep(duration)
            if self.current_state == TestState.RUNNING:
                logger.info(f"Test duration {duration}s reached. Stopping test...")
                await self.stop_test()
        except asyncio.CancelledError:
            pass

    async def stop_test(self) -> Dict[str, Any]:
        async with self._state_lock:
            if self.current_state not in [TestState.PREPARING, TestState.RUNNING]:
                return {"status": "ok", "message": "Test is not running"}

            self.current_state = TestState.STOPPING
            await self.broadcast_state()

            # Cancel duration timer
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()

            # Send stop to all active workers
            cmd_id = f"cmd-stop-{uuid4().hex[:6]}"
            for worker in self.active_workers:
                await self.registry.send_to_worker(worker.worker_id, {
                    "type": WsMessageType.STOP_TEST.value,
                    "payload": {
                        "test_id": self.current_test_id,
                        "command_id": cmd_id,
                    }
                })

            # Wait a moment for workers to gracefully wind down
            await asyncio.sleep(1.5)

            # Cancel metrics ticker
            if self._tick_task and not self._tick_task.done():
                self._tick_task.cancel()

            # Finalize statistics
            summary: Optional[TestSummaryReport] = None
            if self.aggregator:
                summary = self.aggregator.finalize()

            # Reset workers status
            for worker in self.active_workers:
                worker.status = WorkerStatus.ONLINE
                worker.current_test_id = None

            self.current_state = TestState.COMPLETED
            end_time_str = time.strftime("%Y-%m-%d %H:%M:%S")

            if summary:
                self.db.complete_test_record(
                    test_id=self.current_test_id,
                    state=self.current_state.value,
                    end_time=end_time_str,
                    summary=summary.model_dump(),
                )

            await self.broadcast_state()
            await self.registry.broadcast_worker_update()

            logger.info(f"Test {self.current_test_id} completed successfully.")
            return {
                "status": "ok",
                "test_id": self.current_test_id,
                "summary": summary.model_dump() if summary else None,
            }
