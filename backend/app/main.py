from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import approvals, auth, cost_centers, exports, invoices, users
from app.db import SessionLocal
from app.models import SystemHeartbeat
from app.schemas import Health

app = FastAPI(
    title="Paperless Invoice Approval API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.include_router(auth.router, prefix="/api")
app.include_router(invoices.router, prefix="/api")
app.include_router(approvals.router, prefix="/api")
app.include_router(cost_centers.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(users.router, prefix="/api")


@app.get("/api/health", response_model=Health, tags=["system"])
def health() -> Health:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return Health(status="ok", component="backend")
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/health/worker", response_model=Health, tags=["system"])
def worker_health() -> Health:
    try:
        with SessionLocal() as db:
            heartbeat = db.get(SystemHeartbeat, "worker")
        healthy = bool(
            heartbeat and heartbeat.updated_at >= datetime.now(UTC) - timedelta(seconds=60)
        )
        if not healthy:
            raise HTTPException(status_code=503, detail="Worker heartbeat is stale")
        return Health(status="ok", component="worker")
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Worker health unavailable") from exc
