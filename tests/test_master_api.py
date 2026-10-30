"""Integration tests for Master REST API and WebSocket."""

import pytest
from fastapi.testclient import TestClient
from master.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_api_exams_list(client):
    res = client.get("/api/exams")
    assert res.status_code == 200
    data = res.json()
    assert "exams" in data
    assert len(data["exams"]) >= 1
    exam = data["exams"][0]
    assert "exam_id" in exam
    assert "title" in exam
    assert "total_users" in exam


def test_api_workers_registration_and_list(client):
    # Register worker 1
    reg_payload = {
        "worker_id": "test-worker-01",
        "hostname": "PC-TEST-01",
        "ip": "192.168.1.55",
        "cpu_cores": 4,
        "version": "1.0.0",
    }
    res = client.post("/api/workers/register", json=reg_payload)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["worker_id"] == "test-worker-01"

    # Fetch workers
    res = client.get("/api/workers")
    assert res.status_code == 200
    data = res.json()
    assert data["counts"]["total"] >= 1
    worker_ids = [w["worker_id"] for w in data["workers"]]
    assert "test-worker-01" in worker_ids


def test_api_test_status_and_history(client):
    res = client.get("/api/test/status")
    assert res.status_code == 200
    assert "state" in res.json()

    res = client.get("/api/test/history")
    assert res.status_code == 200
    assert "tests" in res.json()
