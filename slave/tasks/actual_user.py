"""Locust User task set for Actual Student Test mode (Full Crypto + Socket.IO)."""

import logging
import random
import time
from locust import HttpUser, task, between
from slave.crypto_helper import CryptoClient
from slave.socketio_worker import UserSocketClient

logger = logging.getLogger("slave.tasks.actual")


class ActualStudentUser(HttpUser):
    # Simulated think time between user actions
    wait_time = between(1.0, 3.0)

    # Injected by runner
    target_host = ""
    assigned_account = None  # StudentAccount
    exam_info = None

    def on_start(self):
        self.crypto = CryptoClient()
        self.device_uuid = self.crypto.device_uuid
        self.test_id = self.assigned_account.test_id if self.assigned_account else f"stu_{random.randint(1000, 9999)}"
        self.socket_client = None
        self.logged_in = False

        self._perform_crypto_login()

    def _perform_crypto_login(self):
        try:
            # 1. Get RSA Public Key
            res = self.client.get("/user/auth/rsa-public-key", name="Auth: Get RSA Key")
            if res.status_code != 200:
                logger.error(f"Failed to get RSA key: {res.status_code} {res.text}")
                return

            rsa_pem = res.json().get("publicKey")
            if not rsa_pem:
                return

            # 2. Register Device with Encrypted AES Key
            encrypted_aes = self.crypto.encrypt_aes_key_with_rsa(rsa_pem)
            reg_payload = {
                "device_uuid": self.device_uuid,
                "encrypted_aes_key": encrypted_aes,
                "resubmit": True,
            }
            res = self.client.post("/user/auth/register-device", json=reg_payload, name="Auth: Register Device")
            if res.status_code != 200:
                logger.error(f"Failed to register device: {res.status_code} {res.text}")
                return

            # 3. Login Student
            login_plain = {"testId": self.test_id}
            login_enc = self.crypto.encrypt_payload(login_plain)
            headers = {"x-test-fake-ip": self.assigned_account.ip if self.assigned_account and self.assigned_account.ip else "192.168.1.50"}

            res = self.client.post("/user/auth/login", json=login_enc, headers=headers, name="Auth: Student Login")
            if res.status_code == 200:
                token = res.json().get("session_token")
                self.crypto.session_token = token
                self.logged_in = True
                logger.info(f"Student {self.test_id} successfully logged in!")

                # 4. Connect Socket.IO in background
                self.socket_client = UserSocketClient(self.target_host, self.device_uuid, token)
                self.socket_client.start()
            else:
                logger.error(f"Student {self.test_id} login failed: {res.status_code} {res.text}")

        except Exception as e:
            logger.error(f"Error during crypto login for {self.test_id}: {e}", exc_info=True)

    def on_stop(self):
        if self.socket_client:
            self.socket_client.disconnect()

    @task(3)
    def submit_code_task(self):
        if not self.logged_in:
            return

        solutions = self.exam_info.get("solutions", {}) if self.exam_info else {}
        if solutions:
            sol_name = random.choice(list(solutions.keys()))
            code = solutions[sol_name]
        else:
            sol_name = "q101"
            code = "print('Hello world from distributed test!')"

        payload = self.crypto.encrypt_payload({
            "questionId": sol_name.upper(),
            "language": "Python",
            "codeContent": code,
        })
        self.client.post("/user/submissions/code", json=payload, name="Exam: Submit Code")

    @task(2)
    def submit_score_task(self):
        if not self.logged_in:
            return

        score = random.choice([0, 25, 50, 75, 100])
        payload = self.crypto.encrypt_payload({"score": score})
        self.client.post("/user/submissions/score", json=payload, name="Exam: Submit Score")

    @task(2)
    def get_exam_config_task(self):
        if not self.logged_in:
            return
        payload = self.crypto.encrypt_payload({})
        self.client.post("/user/exam/config", json=payload, name="Exam: Get Config")

    @task(1)
    def check_messages_task(self):
        if not self.logged_in:
            return
        payload = self.crypto.encrypt_payload({"lastMessageId": 0})
        self.client.post("/user/exam/messages", json=payload, name="Exam: Check Messages")

    @task(2)
    def action_log_task(self):
        if not self.logged_in:
            return
        payload = self.crypto.encrypt_payload({"actionType": "coding_snapshot"})
        self.client.post("/user/log", json=payload, name="Exam: Action Log")
