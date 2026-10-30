"""Socket.IO client for connecting to the /user namespace and maintaining active user session."""

import logging
import threading
import time
from typing import Optional

import socketio

logger = logging.getLogger("slave.socketio")


class UserSocketClient:
    def __init__(self, target_host: str, device_uuid: str, session_token: Optional[str] = None):
        self.target_host = target_host.rstrip("/")
        self.device_uuid = device_uuid
        self.session_token = session_token

        self.sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,  # Infinite
            reconnection_delay=2,
            logger=False,
            engineio_logger=False,
        )

        self.is_connected = False
        self.disconnect_count = 0
        self._setup_handlers()

    def _setup_handlers(self):
        @self.sio.on("connect", namespace="/user")
        def on_connect():
            self.is_connected = True
            logger.info(f"Socket.IO connected to /user for device {self.device_uuid}")
            # Register socket binding with device_uuid and session_token
            register_payload = {"device_uuid": self.device_uuid}
            if self.session_token:
                register_payload["session_token"] = self.session_token
            self.sio.emit("register", register_payload, namespace="/user")

        @self.sio.on("disconnect", namespace="/user")
        def on_disconnect():
            self.is_connected = False
            self.disconnect_count += 1
            logger.warning(f"Socket.IO disconnected for device {self.device_uuid}")

        @self.sio.on("exam-message", namespace="/user")
        def on_exam_message(data):
            logger.debug(f"Received exam-message for {self.device_uuid}: {data}")

        @self.sio.on("exam-reset", namespace="/user")
        def on_exam_reset(data):
            logger.warning(f"Received exam-reset for {self.device_uuid}: {data}")

    def start(self):
        try:
            url = self.target_host
            logger.info(f"Connecting to Socket.IO at {url}/user ...")
            self.sio.connect(
                url,
                namespaces=["/user"],
                transports=["websocket"],
                socketio_path="/socket.io",
                wait=True,
                wait_timeout=5,
            )
        except Exception as e:
            logger.error(f"Failed to connect Socket.IO for {self.device_uuid}: {e}")

    def disconnect(self):
        if self.sio.connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
            self.is_connected = False
