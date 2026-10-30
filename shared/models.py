"""Pydantic data models for Master-Slave communication and Web UI."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from shared.protocol import TestMode, TestState, WorkerStatus


class WorkerRegisterRequest(BaseModel):
    worker_id: str
    hostname: str
    ip: str
    cpu_cores: int
    version: str = "1.0.0"


class WorkerRegisterResponse(BaseModel):
    status: str = "ok"
    worker_id: str
    master_time: float


class StudentAccount(BaseModel):
    test_id: str
    name: Optional[str] = None
    ip: Optional[str] = None


class WorkerMetrics(BaseModel):
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    active_vu: int = 0
    socket_connected: int = 0


class WorkerInfo(BaseModel):
    worker_id: str
    hostname: str
    ip: str
    remote_ip: str
    cpu_cores: int
    version: str = "1.0.0"
    status: WorkerStatus = WorkerStatus.ONLINE
    last_heartbeat: float = Field(default_factory=float)
    current_test_id: Optional[str] = None
    assigned_accounts: List[StudentAccount] = Field(default_factory=list)
    metrics: WorkerMetrics = Field(default_factory=WorkerMetrics)


class HeartbeatPayload(BaseModel):
    worker_id: str
    timestamp: float
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    active_vu: int = 0
    socket_connected: int = 0


class TestStartRequest(BaseModel):
    target_url: str = "http://127.0.0.1:3001"
    mode: TestMode = TestMode.PURE
    exam_id: str = "exam1"
    total_users: int = 10
    spawn_rate: float = 2.0
    duration: int = 60
    worker_ids: Optional[List[str]] = None


class PrepareTestPayload(BaseModel):
    test_id: str
    command_id: str
    target_url: str
    mode: TestMode
    exam_id: str
    allocated_users: int
    spawn_rate: float
    duration: int
    assigned_accounts: List[StudentAccount] = Field(default_factory=list)
    exam_info: Optional[Dict[str, Any]] = None


class MetricsUpdatePayload(BaseModel):
    worker_id: str
    test_id: str
    timestamp: float
    total_requests: int
    failed_requests: int
    current_rps: float
    latencies_ms: List[float] = Field(default_factory=list)
    active_vu: int = 0
    socket_connected: int = 0
    socket_disconnected: int = 0
    errors: Dict[str, int] = Field(default_factory=dict)


class AggregatedMetricsTick(BaseModel):
    timestamp: float
    elapsed_seconds: float
    total_requests: int
    requests_per_second: float
    avg_response_time: float
    p50_response_time: float
    p95_response_time: float
    p99_response_time: float
    failed_requests: int
    failure_rate: float
    active_workers: int
    active_virtual_users: int
    socket_connections: int


class TestSummaryReport(BaseModel):
    test_id: str
    target_url: str
    mode: str
    exam_id: str
    start_time: str
    end_time: str
    duration_seconds: float
    total_workers: int
    total_requests: int
    failed_requests: int
    average_rps: float
    peak_rps: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    failure_rate: float
    worker_summaries: Dict[str, Any] = Field(default_factory=dict)
    time_series: List[Dict[str, Any]] = Field(default_factory=list)
