"""Locust Load Runner coordinator running inside Slave Worker."""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from locust import events
from locust.env import Environment
from locust.stats import StatsEntry

from shared.models import MetricsUpdatePayload, PrepareTestPayload, StudentAccount
from shared.protocol import TestMode
from slave.tasks.actual_user import ActualStudentUser
from slave.tasks.pure_user import PureSimulationUser

logger = logging.getLogger("slave.runner")


class LoadRunner:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.env: Optional[Environment] = None
        self.runner = None
        self.is_running = False

        self.current_test_id: Optional[str] = None
        self.target_url: str = ""
        self.mode: TestMode = TestMode.PURE
        self.assigned_accounts: List[StudentAccount] = []
        self.exam_info: Optional[Dict[str, Any]] = None

        # Metrics collection
        self._lock = threading.Lock()
        self.recent_latencies: List[float] = []
        self.total_requests = 0
        self.failed_requests = 0
        self.errors_count: Dict[str, int] = {}

        self._last_metrics_time = time.time()
        self._last_total_reqs = 0

    def prepare(self, payload: PrepareTestPayload):
        self.current_test_id = payload.test_id
        self.target_url = payload.target_url
        self.mode = payload.mode
        self.allocated_users = payload.allocated_users
        self.spawn_rate = payload.spawn_rate
        self.duration = payload.duration
        self.assigned_accounts = payload.assigned_accounts
        self.exam_info = payload.exam_info

        # Select user class based on mode
        if self.mode == TestMode.ACTUAL:
            user_class = ActualStudentUser
        else:
            user_class = PureSimulationUser

        user_class.host = self.target_url
        user_class.target_host = self.target_url
        user_class.exam_info = self.exam_info

        # Distribute accounts if in actual mode
        if self.assigned_accounts and self.mode == TestMode.ACTUAL:
            user_class.assigned_account = self.assigned_accounts[0]

        self.env = Environment(user_classes=[user_class], events=events)

        # Hook into Locust request events
        @self.env.events.request.add_listener
        def on_request(request_type, name, response_time, response_length, exception, context, **kwargs):
            with self._lock:
                self.total_requests += 1
                self.recent_latencies.append(float(response_time))
                if exception:
                    self.failed_requests += 1
                    err_msg = str(exception)
                    self.errors_count[err_msg] = self.errors_count.get(err_msg, 0) + 1

        self.runner = self.env.create_local_runner()
        logger.info(f"Runner prepared for test {self.current_test_id} ({self.allocated_users} users @ {self.spawn_rate}/s)")

    def start(self):
        if not self.runner:
            raise RuntimeError("Runner not prepared")
        self.is_running = True
        self._last_metrics_time = time.time()
        self._last_total_reqs = 0

        # Start Locust runner in background thread
        def run_locust():
            try:
                self.runner.start(self.allocated_users, spawn_rate=self.spawn_rate)
                self.runner.greenlet.join()
            except Exception as e:
                logger.error(f"Locust runner exception: {e}")

        t = threading.Thread(target=run_locust, daemon=True)
        t.start()
        logger.info("Locust load generation started")

    def stop(self):
        self.is_running = False
        if self.runner:
            try:
                self.runner.stop()
            except Exception as e:
                logger.error(f"Error stopping Locust runner: {e}")
        logger.info("Locust load generation stopped")

    def collect_metrics(self) -> MetricsUpdatePayload:
        with self._lock:
            now = time.time()
            interval = max(0.001, now - self._last_metrics_time)
            delta_reqs = self.total_requests - self._last_total_reqs
            rps = round(delta_reqs / interval, 2)

            latencies = list(self.recent_latencies)
            self.recent_latencies = []

            self._last_metrics_time = now
            self._last_total_reqs = self.total_requests

            active_vu = self.runner.user_count if self.runner else 0

            return MetricsUpdatePayload(
                worker_id=self.worker_id,
                test_id=self.current_test_id or "",
                timestamp=now,
                total_requests=self.total_requests,
                failed_requests=self.failed_requests,
                current_rps=rps,
                latencies_ms=latencies,
                active_vu=active_vu,
                socket_connected=active_vu,  # Each active VU maintains a Socket.IO connection
                socket_disconnected=0,
                errors=dict(self.errors_count),
            )
