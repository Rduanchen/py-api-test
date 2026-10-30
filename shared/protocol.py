"""Protocol constants and message types for Master-Slave communication."""

from enum import Enum


class WorkerStatus(str, Enum):
    ONLINE = "Online"
    OFFLINE = "Offline"
    RUNNING = "Running"
    ERROR = "Error"


class TestState(str, Enum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TestMode(str, Enum):
    ACTUAL = "actual"  # Real student login with RSA+AES encryption and assigned accounts
    PURE = "pure"      # Skip registration / high-concurrency simulation


class WsMessageType(str, Enum):
    # Slave -> Master
    HEARTBEAT = "HEARTBEAT"
    TEST_READY = "TEST_READY"
    METRICS_UPDATE = "METRICS_UPDATE"
    TEST_COMPLETED = "TEST_COMPLETED"
    TEST_ERROR = "TEST_ERROR"

    # Master -> Slave
    HEARTBEAT_ACK = "HEARTBEAT_ACK"
    ASSIGN_ACCOUNTS = "ASSIGN_ACCOUNTS"
    PREPARE_TEST = "PREPARE_TEST"
    START_TEST = "START_TEST"
    STOP_TEST = "STOP_TEST"

    # Master -> Web UI Dashboard
    DASHBOARD_INIT = "DASHBOARD_INIT"
    WORKER_STATUS_CHANGED = "WORKER_STATUS_CHANGED"
    TEST_STATE_CHANGED = "TEST_STATE_CHANGED"
    METRICS_TICK = "METRICS_TICK"
