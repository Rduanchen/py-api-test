"""Locust User task set for Pure / High Concurrency Simulation mode (Skip account registration)."""

import logging
import random
import uuid
from locust import HttpUser, task, between
from slave.socketio_worker import UserSocketClient

logger = logging.getLogger("slave.tasks.pure")


class PureSimulationUser(HttpUser):
    # Faster pacing for high concurrency testing
    wait_time = between(0.2, 0.8)

    # Injected by runner
    target_host = ""
    assigned_account = None
    exam_info = None

    def on_start(self):
        self.device_uuid = str(uuid.uuid4())
        self.test_id = f"vu_{uuid.uuid4().hex[:8]}"
        self.fake_ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
        self.token = None
        self.socket_client = None

        headers = {
            "x-test-fake-ip": self.fake_ip,
            "Content-Type": "application/json",
        }

        # 1. Direct Pure Login
        login_res = self.client.post(
            "/user/auth/login",
            json={"device_uuid": self.device_uuid, "testId": self.test_id},
            headers=headers,
            name="Pure: Login",
        )

        if login_res.status_code == 200:
            self.token = login_res.json().get("session_token")
            # Connect Socket.IO
            self.socket_client = UserSocketClient(self.target_host, self.device_uuid, self.token)
            self.socket_client.start()
        else:
            logger.debug(f"Pure login response: {login_res.status_code}")

    def on_stop(self):
        if self.socket_client:
            self.socket_client.disconnect()

    def get_auth_headers(self):
        h = {"x-test-fake-ip": self.fake_ip}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    @task(4)
    def submit_code(self):
        payload = {
            "device_uuid": self.device_uuid,
            "testId": self.test_id,
            "questionId": "Q101",
            "language": "Python",
            "codeContent": "print('Pure load test code submission')",
        }
        self.client.post("/user/submissions/code", json=payload, headers=self.get_auth_headers(), name="Pure: Submit Code")

    @task(3)
    def submit_score(self):
        payload = {
            "device_uuid": self.device_uuid,
            "testId": self.test_id,
            "score": 100,
        }
        self.client.post("/user/submissions/score", json=payload, headers=self.get_auth_headers(), name="Pure: Submit Score")

    @task(2)
    def get_config(self):
        payload = {
            "device_uuid": self.device_uuid,
            "testId": self.test_id,
        }
        self.client.post("/user/exam/config", json=payload, headers=self.get_auth_headers(), name="Pure: Get Config")

    @task(2)
    def get_messages(self):
        payload = {
            "device_uuid": self.device_uuid,
            "testId": self.test_id,
            "lastMessageId": 0,
        }
        self.client.post("/user/exam/messages", json=payload, headers=self.get_auth_headers(), name="Pure: Messages")

    @task(3)
    def log_action(self):
        payload = {
            "device_uuid": self.device_uuid,
            "testId": self.test_id,
            "actionType": "keystroke_batch",
        }
        self.client.post("/user/log", json=payload, headers=self.get_auth_headers(), name="Pure: Log Action")
