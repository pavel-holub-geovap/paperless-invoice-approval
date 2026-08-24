from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.routes.invoices import _require_current_revision
from app.models import UserIdentity
from app.request_context import reset_correlation_id, set_correlation_id
from app.services.audit import record_event
from app.services.workflow import create_invoice


def test_stale_revision_returns_structured_http_409(db: Session) -> None:
    invoice = create_invoice(db, 9101)

    try:
        _require_current_revision(invoice, invoice.current_revision_number - 1)
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["code"] == "STALE_REVISION"
        assert exc.detail["current_revision"] == invoice.current_revision_number
    else:
        raise AssertionError("A stale revision must be rejected")


def test_audit_enriches_actor_and_correlation_context(db: Session) -> None:
    actor = UserIdentity(
        subject="manager-subject",
        username="queue-manager",
        roles=["QUEUE_MANAGER"],
    )
    db.add(actor)
    db.flush()
    token = set_correlation_id("test-correlation-id")
    try:
        event = record_event(db, "SECURITY_ACTION", actor=actor.subject)
        db.flush()
    finally:
        reset_correlation_id(token)

    assert event.metadata_json["actor_username"] == "queue-manager"
    assert event.metadata_json["actor_roles"] == ["QUEUE_MANAGER"]
    assert event.metadata_json["correlation_id"] == "test-correlation-id"
    assert event.metadata_json["action"] == "SECURITY_ACTION"
