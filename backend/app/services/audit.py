from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import AuditEvent, Invoice, UserIdentity
from app.request_context import get_correlation_id


def record_event(
    db: Session,
    event_type: str,
    *,
    actor: str = "system",
    invoice: Invoice | None = None,
    revision_number: int | None = None,
    old_state: str | None = None,
    new_state: str | None = None,
    old_value: Any | None = None,
    new_value: Any | None = None,
    comment: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    enriched = dict(metadata or {})
    enriched.setdefault("action", event_type)
    correlation_id = get_correlation_id()
    if correlation_id:
        enriched.setdefault("correlation_id", correlation_id)
    if actor != "system":
        identity = db.get(UserIdentity, actor)
        if identity is not None:
            enriched.setdefault("actor_username", identity.username)
            enriched.setdefault("actor_roles", list(identity.roles))
    audit = AuditEvent(
        invoice_id=invoice.id if invoice else None,
        revision_number=revision_number or (invoice.current_revision_number if invoice else None),
        event_type=event_type,
        actor_subject=actor,
        old_state=old_state,
        new_state=new_state,
        old_value=old_value,
        new_value=new_value,
        comment=comment,
        metadata_json=enriched,
    )
    db.add(audit)
    return audit


@event.listens_for(AuditEvent, "before_update")
def prevent_audit_update(*_: object) -> None:
    raise ValueError("Audit events are append-only and cannot be updated")


@event.listens_for(AuditEvent, "before_delete")
def prevent_audit_delete(*_: object) -> None:
    raise ValueError("Audit events are append-only and cannot be deleted")
