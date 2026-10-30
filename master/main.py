"""FastAPI Master Server entrypoint."""

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from master.account_manager import AccountManager
from master.database import Database
from master.registry import WorkerRegistry
from master.scheduler import Scheduler
from master.statistics import REPORTS_DIR
from shared.models import (
    HeartbeatPayload,
    MetricsUpdatePayload,
    TestStartRequest,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
)
from shared.protocol import TestState, WorkerStatus, WsMessageType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("master.main")

STATIC_DIR = Path(__file__).resolve().parent / "static"

db = Database()
registry = WorkerRegistry(db)
account_manager = AccountManager()
scheduler = Scheduler(registry, account_manager, db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Master Server background tasks...")
    registry.start_heartbeat_checker()
    yield
    # Shutdown
    logger.info("Shutting down Master Server...")


app = FastAPI(title="Distributed Load Tester Master", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Master Web UI is starting up...</h1>")


# ─── Worker APIs ─────────────────────────────────────────────────────────────

@app.post("/api/workers/register", response_model=WorkerRegisterResponse)
async def register_worker(req: WorkerRegisterRequest, request: Request):
    remote_ip = request.client.host if request.client else "unknown"
    resp = registry.register_worker(req, remote_ip)
    await registry.broadcast_worker_update()
    return resp


@app.get("/api/workers")
async def get_workers():
    workers_list = [w.model_dump() for w in registry.workers.values()]
    counts = {
        "total": len(registry.workers),
        "online": sum(1 for w in registry.workers.values() if w.status == WorkerStatus.ONLINE),
        "running": sum(1 for w in registry.workers.values() if w.status == WorkerStatus.RUNNING),
        "offline": sum(1 for w in registry.workers.values() if w.status == WorkerStatus.OFFLINE),
    }
    return {"counts": counts, "workers": workers_list}


# ─── Exams & Accounts APIs ───────────────────────────────────────────────────

@app.get("/api/exams")
async def get_exams():
    exams = account_manager.list_available_exams()
    return {"exams": exams}


@app.get("/api/exams/{exam_id}")
async def get_exam_details(exam_id: str):
    details = account_manager.get_exam_details(exam_id)
    if not details:
        raise HTTPException(status_code=404, detail="Exam not found")
    return details


# ─── Test Control APIs ────────────────────────────────────────────────────────

@app.post("/api/test/start")
async def start_test(req: TestStartRequest):
    try:
        res = await scheduler.start_test(req)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start test: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/test/stop")
async def stop_test():
    try:
        res = await scheduler.stop_test()
        return res
    except Exception as e:
        logger.error(f"Failed to stop test: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/test/status")
async def get_test_status():
    return {
        "state": scheduler.current_state.value,
        "test_id": scheduler.current_test_id,
        "active_workers": len(scheduler.active_workers),
        "ready_workers": len(scheduler.ready_workers),
    }


@app.get("/api/test/history")
async def get_test_history():
    records = db.get_tests()
    return {"tests": records}


@app.get("/api/reports/{test_id}.html")
async def download_html_report(test_id: str):
    path = REPORTS_DIR / f"{test_id}.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path=str(path), media_type="text/html", filename=f"{test_id}.html")


@app.get("/api/reports/{test_id}.csv")
async def download_csv_report(test_id: str):
    path = REPORTS_DIR / f"{test_id}.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path=str(path), media_type="text/csv", filename=f"{test_id}.csv")


# ─── WebSocket Endpoints ─────────────────────────────────────────────────────

@app.websocket("/ws/worker/{worker_id}")
async def worker_ws(websocket: WebSocket, worker_id: str):
    await websocket.accept()
    registry.register_worker_ws(worker_id, websocket)
    await registry.broadcast_worker_update()
    logger.info(f"Worker {worker_id} established WebSocket control channel")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == WsMessageType.HEARTBEAT.value:
                payload = HeartbeatPayload(**data.get("payload", {}))
                registry.handle_heartbeat(payload)
                await websocket.send_json({
                    "type": WsMessageType.HEARTBEAT_ACK.value,
                    "master_time": time.time(),
                })

            elif msg_type == WsMessageType.TEST_READY.value:
                test_id = data.get("test_id")
                await scheduler.handle_worker_ready(worker_id, test_id)

            elif msg_type == WsMessageType.METRICS_UPDATE.value:
                payload = MetricsUpdatePayload(**data.get("payload", {}))
                await scheduler.handle_worker_metrics(payload)

            elif msg_type == WsMessageType.TEST_COMPLETED.value:
                test_id = data.get("test_id")
                await scheduler.handle_worker_completed(worker_id, test_id)

            elif msg_type == WsMessageType.TEST_ERROR.value:
                err = data.get("error")
                logger.error(f"Worker {worker_id} reported test error: {err}")

    except WebSocketDisconnect:
        logger.warning(f"Worker {worker_id} disconnected WebSocket")
    except Exception as e:
        logger.error(f"WebSocket error for worker {worker_id}: {e}")
    finally:
        registry.unregister_worker_ws(worker_id)
        await registry.broadcast_worker_update()


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    await websocket.accept()
    registry.register_dashboard_ws(websocket)
    logger.info("Web UI dashboard connected to WebSocket")

    # Send initial snapshot
    workers_list = [w.model_dump() for w in registry.workers.values()]
    counts = {
        "total": len(registry.workers),
        "online": sum(1 for w in registry.workers.values() if w.status == WorkerStatus.ONLINE),
        "running": sum(1 for w in registry.workers.values() if w.status == WorkerStatus.RUNNING),
        "offline": sum(1 for w in registry.workers.values() if w.status == WorkerStatus.OFFLINE),
    }

    try:
        await websocket.send_json({
            "type": WsMessageType.DASHBOARD_INIT.value,
            "counts": counts,
            "workers": workers_list,
            "test_state": scheduler.current_state.value,
            "current_test_id": scheduler.current_test_id,
            "exams": account_manager.list_available_exams(),
        })

        while True:
            # Keep dashboard alive, listen for ping or manual events
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Dashboard WS exception: {e}")
    finally:
        registry.unregister_dashboard_ws(websocket)
