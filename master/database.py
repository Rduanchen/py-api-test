"""SQLite Database manager for historical tests and worker persistence."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "database.sqlite"


class Database:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    hostname TEXT,
                    ip TEXT,
                    remote_ip TEXT,
                    cpu_cores INTEGER,
                    version TEXT,
                    status TEXT,
                    last_seen REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tests (
                    test_id TEXT PRIMARY KEY,
                    target_url TEXT,
                    mode TEXT,
                    exam_id TEXT,
                    total_users INTEGER,
                    duration INTEGER,
                    state TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    summary_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def upsert_worker(self, worker_id: str, hostname: str, ip: str, remote_ip: str, cpu_cores: int, version: str, status: str, last_seen: float):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO workers (worker_id, hostname, ip, remote_ip, cpu_cores, version, status, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    hostname = excluded.hostname,
                    ip = excluded.ip,
                    remote_ip = excluded.remote_ip,
                    cpu_cores = excluded.cpu_cores,
                    version = excluded.version,
                    status = excluded.status,
                    last_seen = excluded.last_seen
                """,
                (worker_id, hostname, ip, remote_ip, cpu_cores, version, status, last_seen),
            )
            conn.commit()

    def save_test_record(self, test_id: str, target_url: str, mode: str, exam_id: str, total_users: int, duration: int, state: str, start_time: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO tests (test_id, target_url, mode, exam_id, total_users, duration, state, start_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (test_id, target_url, mode, exam_id, total_users, duration, state, start_time),
            )
            conn.commit()

    def complete_test_record(self, test_id: str, state: str, end_time: str, summary: Dict[str, Any]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tests
                SET state = ?, end_time = ?, summary_json = ?
                WHERE test_id = ?
                """,
                (state, end_time, json.dumps(summary, ensure_ascii=False), test_id),
            )
            conn.commit()

    def get_tests(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tests ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                if item.get("summary_json"):
                    try:
                        item["summary"] = json.loads(item["summary_json"])
                    except Exception:
                        item["summary"] = None
                results.append(item)
            return results

    def get_test(self, test_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tests WHERE test_id = ?", (test_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("summary_json"):
                try:
                    item["summary"] = json.loads(item["summary_json"])
                except Exception:
                    item["summary"] = None
            return item
