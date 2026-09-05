from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import (
    approvals,
    auth,
    cost_centers,
    exports,
    invoices,
    section_permissions,
    uploads,
    users,
)
from app.db import SessionLocal
from app.models import SystemHeartbeat
from app.request_context import reset_correlation_id, set_correlation_id
from app.schemas import Health

app = FastAPI(
    title="Paperless Invoice Approval API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.include_router(auth.router, prefix="/api")
app.include_router(invoices.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(approvals.router, prefix="/api")
app.include_router(cost_centers.router, prefix="/api")
app.include_router(section_permissions.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(users.router, prefix="/api")


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Request-ID") or str(uuid4())
    token = set_correlation_id(correlation_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = correlation_id
        return response
    finally:
        reset_correlation_id(token)


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
