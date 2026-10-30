"""Tests for WorkerRegistry, AccountManager, and StatisticsAggregator."""

import time
import pytest
from master.account_manager import AccountManager
from master.database import Database
from master.registry import WorkerRegistry
from master.statistics import StatisticsAggregator, calculate_percentiles
from shared.models import (
    HeartbeatPayload,
    MetricsUpdatePayload,
    StudentAccount,
    WorkerInfo,
    WorkerRegisterRequest,
)
from shared.protocol import WorkerStatus


def test_percentile_calculation():
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    pcts = calculate_percentiles(latencies, [50, 95, 99])
    assert pcts["p50"] == 55.0
    assert pcts["p95"] > 90.0
    assert pcts["p99"] > 95.0


def test_worker_registration_and_heartbeat(tmp_path):
    db = Database(tmp_path / "test.db")
    registry = WorkerRegistry(db)

    req = WorkerRegisterRequest(
        worker_id="worker-01",
        hostname="PC-01",
        ip="192.168.1.101",
        cpu_cores=8,
        version="1.0.0",
    )
    resp = registry.register_worker(req, remote_ip="192.168.1.101")
    assert resp.status == "ok"
    assert "worker-01" in registry.workers

    w = registry.workers["worker-01"]
    assert w.status == WorkerStatus.ONLINE
    assert w.cpu_cores == 8

    # Heartbeat
    hb = HeartbeatPayload(
        worker_id="worker-01",
        timestamp=time.time(),
        cpu_percent=25.5,
        memory_percent=45.0,
        active_vu=10,
        socket_connected=10,
    )
    registry.handle_heartbeat(hb)
    assert registry.workers["worker-01"].metrics.cpu_percent == 25.5


def test_account_manager_distribution(tmp_path):
    mgr = AccountManager()
    exams = mgr.list_available_exams()
    assert len(exams) >= 1  # Should find exam1, exam2, exam3, exam4

    exam_ids = [e["exam_id"] for e in exams]
    assert "exam1" in exam_ids

    workers = [
        WorkerInfo(
            worker_id=f"worker-{i}",
            hostname=f"PC-{i}",
            ip=f"192.168.1.{i}",
            remote_ip=f"192.168.1.{i}",
            cpu_cores=4,
        )
        for i in range(1, 4)
    ]

    dist = mgr.distribute_accounts("exam1", workers, accounts_per_worker=2)
    assert len(dist) == 3
    assert len(dist["worker-1"]) == 2
    assert len(dist["worker-2"]) == 2
    assert len(dist["worker-3"]) == 2
    # Check that test_id is present
    assert dist["worker-1"][0].test_id != ""


def test_statistics_aggregator(tmp_path):
    agg = StatisticsAggregator(
        test_id="test-unit-01",
        target_url="http://127.0.0.1:3001",
        mode="pure",
        exam_id="exam1",
    )

    # Worker 1 metrics
    agg.record_worker_update(
        MetricsUpdatePayload(
            worker_id="w1",
            test_id="test-unit-01",
            timestamp=time.time(),
            total_requests=100,
            failed_requests=2,
            current_rps=20.0,
            latencies_ms=[15.0, 20.0, 25.0, 18.0],
            active_vu=5,
            socket_connected=5,
        )
    )

    # Worker 2 metrics
    agg.record_worker_update(
        MetricsUpdatePayload(
            worker_id="w2",
            test_id="test-unit-01",
            timestamp=time.time(),
            total_requests=150,
            failed_requests=3,
            current_rps=30.0,
            latencies_ms=[30.0, 35.0, 40.0],
            active_vu=5,
            socket_connected=5,
        )
    )

    tick_data = agg.tick()
    assert tick_data.total_requests == 250
    assert tick_data.failed_requests == 5
    assert tick_data.active_workers == 2
    assert tick_data.active_virtual_users == 10
    assert tick_data.socket_connections == 10

    report = agg.finalize()
    assert report.total_requests == 250
    assert report.failed_requests == 5
    assert report.failure_rate == 2.0
