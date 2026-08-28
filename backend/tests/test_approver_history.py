from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes import approvals as approval_routes
from app.api.routes import invoices as invoice_routes
from app.config import Settings
from app.integrations.paperless import PaperlessError
from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    ApprovalDecision,
    CostCenter,
    Invoice,
    SourceDocumentStatus,
)
from app.schemas import CurrentUser
from app.services.approver_history import (
    get_approver_history_detail,
    list_approver_history,
    user_can_access_invoice_history,
)
from app.services.workflow import create_invoice


def approver(subject: str) -> CurrentUser:
    return CurrentUser(subject=subject, username=subject, roles=["APPROVER"])


def history_invoice(
    db: Session,
    *,
    paperless_id: int,
    subject: str,
    action: ApprovalAction | None = ApprovalAction.APPROVE,
    center_code: str = "200",
    event_at: datetime | None = None,
) -> tuple[Invoice, ApprovalAssignment, ApprovalDecision | None]:
    event_at = event_at or datetime.now(UTC)
    invoice = create_invoice(db, paperless_id)
    invoice.paperless_title = f"Faktura {paperless_id}"
    invoice.paperless_ocr_text = f"OCR autoritativní fráze {paperless_id}"
    invoice.current_revision.data = {
        "supplier_name": f"Dodavatel {paperless_id}",
        "supplier_ico": str(paperless_id),
        "invoice_number": f"H-{paperless_id}",
        "currency": "CZK",
        "total_amount": "1210.00",
    }
    center = CostCenter(
        code=f"{center_code}-{paperless_id}",
        name=f"Středisko {center_code}",
        pohoda_code=f"{center_code}-{paperless_id}",
    )
    db.add(center)
    db.flush()
    allocation = Allocation(
        invoice_id=invoice.id,
        revision_id=invoice.current_revision.id,
        cost_center_id=center.id,
        amount=Decimal("700.00"),
        created_by="manager",
        active=True,
    )
    db.add(allocation)
    db.flush()
    assignment = ApprovalAssignment(
        invoice_id=invoice.id,
        revision_id=invoice.current_revision.id,
        allocation_id=allocation.id,
        approver_subject=subject,
        status={
            None: ApprovalAssignmentStatus.PENDING,
            ApprovalAction.APPROVE: ApprovalAssignmentStatus.APPROVED,
            ApprovalAction.RETURN: ApprovalAssignmentStatus.RETURNED,
            ApprovalAction.REJECT: ApprovalAssignmentStatus.REJECTED,
        }[action],
        active=True,
        assigned_by="manager",
        assigned_at=event_at - timedelta(minutes=5),
        decided_at=event_at if action else None,
    )
    db.add(assignment)
    db.flush()
    decision = None
    if action:
        decision = ApprovalDecision(
            assignment_id=assignment.id,
            revision_id=invoice.current_revision.id,
            action=action,
            actor_subject=subject,
            comment="Historické rozhodnutí",
            valid=True,
            created_at=event_at,
        )
        db.add(decision)
    db.flush()
    return invoice, assignment, decision


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (ApprovalAction.APPROVE, "APPROVE"),
        (ApprovalAction.RETURN, "RETURN"),
        (ApprovalAction.REJECT, "REJECT"),
        (None, None),
    ],
)
def test_every_historical_assignment_and_decision_is_visible(
    db: Session, action: ApprovalAction | None, expected: str | None
) -> None:
    invoice, _, _ = history_invoice(
        db, paperless_id=9100 + len(db.new), subject="approver-1", action=action
    )
    result = list_approver_history(db, "approver-1", page=1, page_size=25)
    assert result["total"] == 1
    assert result["items"][0]["invoice_id"] == invoice.id
    assert result["items"][0]["latest_assignment"]["decision"] == expected


def test_invalidated_revision_remains_visible_with_immutable_decision(db: Session) -> None:
    invoice, assignment, decision = history_invoice(
        db, paperless_id=9201, subject="approver-1"
    )
    old_revision = invoice.current_revision
    invalidated_at = datetime.now(UTC)
    assignment.active = False
    assignment.status = ApprovalAssignmentStatus.INVALIDATED
    assignment.invalidated_at = invalidated_at
    assignment.invalidation_reason = "Vznikla nová revize faktury"
    assert decision is not None
    decision.valid = False
    decision.invalidated_at = invalidated_at
    decision.invalidation_reason = "Vznikla nová revize faktury"
    invoice.current_revision_number = 2
    from app.models import InvoiceRevision

    db.add(
        InvoiceRevision(
            invoice_id=invoice.id,
            number=2,
            data={**old_revision.data, "invoice_number": "H-9201-NEW"},
            created_by="manager",
        )
    )
    db.flush()

    detail = get_approver_history_detail(db, "approver-1", invoice.id)
    assert detail is not None
    assert detail["current_revision"] == 2
    assert detail["history"][0]["revision"] == 1
    assert detail["history"][0]["decision"] == "APPROVE"
    assert detail["history"][0]["invalidated"] is True
    assert detail["history"][0]["decision_valid"] is False
    assert detail["history"][0]["revision_data"]["invoice_number"] == "H-9201"


@pytest.mark.asyncio
async def test_paperless_fulltext_is_intersected_with_historical_access(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed, _, _ = history_invoice(db, paperless_id=9301, subject="approver-1")
    denied, _, _ = history_invoice(db, paperless_id=9302, subject="approver-2")

    class FakePaperless:
        def __init__(self, _settings: Settings):
            pass

        async def search_document_ids(self, query: str) -> set[int]:
            assert query == "autoritativní fráze"
            return {allowed.paperless_document_id, denied.paperless_document_id}

        async def close(self) -> None:
            pass

    monkeypatch.setattr(approval_routes, "PaperlessClient", FakePaperless)
    result = await approval_routes.my_history(
        q="autoritativní fráze",
        decision=None,
        date_from=None,
        cost_center=None,
        page=1,
        page_size=25,
        db=db,
        user=approver("approver-1"),
        settings=Settings(),
    )
    assert result["total"] == 1
    assert result["items"][0]["invoice_id"] == allowed.id
    assert "9301" in result["items"][0]["ocr_snippet"]
    serialized = str(result)
    assert denied.id not in serialized
    assert "9302" not in serialized


def test_foreign_history_detail_and_pdf_are_forbidden(db: Session) -> None:
    invoice, _, _ = history_invoice(db, paperless_id=9401, subject="approver-1")
    assert user_can_access_invoice_history(db, "approver-1", invoice.id)
    assert not user_can_access_invoice_history(db, "approver-2", invoice.id)
    with pytest.raises(HTTPException) as detail_error:
        approval_routes.my_history_detail(invoice.id, db, approver("approver-2"))
    assert detail_error.value.status_code == 403
    with pytest.raises(HTTPException) as pdf_error:
        invoice_routes._pdf_viewer(db, invoice, approver("approver-2"))
    assert pdf_error.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_source_remains_in_history_and_pdf_is_unavailable(db: Session) -> None:
    invoice, _, _ = history_invoice(db, paperless_id=9501, subject="approver-1")
    invoice.source_status = SourceDocumentStatus.MISSING
    detail = get_approver_history_detail(db, "approver-1", invoice.id)
    assert detail is not None
    assert detail["pdf_available"] is False
    with pytest.raises(HTTPException) as pdf_error:
        await invoice_routes.proxy_pdf(
            invoice.id, db, approver("approver-1"), Settings()
        )
    assert pdf_error.value.status_code == 409


@pytest.mark.asyncio
async def test_paperless_timeout_does_not_mark_source_missing(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice, _, _ = history_invoice(db, paperless_id=9502, subject="approver-1")

    class FailingPaperless:
        def __init__(self, _settings: Settings):
            pass

        async def download_pdf(self, _document_id: int) -> bytes:
            raise PaperlessError("timeout")

        async def close(self) -> None:
            pass

    monkeypatch.setattr(invoice_routes, "PaperlessClient", FailingPaperless)
    with pytest.raises(HTTPException) as pdf_error:
        await invoice_routes.proxy_pdf(
            invoice.id, db, approver("approver-1"), Settings()
        )
    assert pdf_error.value.status_code == 502
    assert invoice.source_status == SourceDocumentStatus.AVAILABLE


def test_history_pagination_and_combined_filters(db: Session) -> None:
    recent = datetime.now(UTC)
    matching, _, _ = history_invoice(
        db,
        paperless_id=9601,
        subject="approver-1",
        action=ApprovalAction.APPROVE,
        center_code="200",
        event_at=recent,
    )
    history_invoice(
        db,
        paperless_id=9602,
        subject="approver-1",
        action=ApprovalAction.RETURN,
        center_code="300",
        event_at=recent - timedelta(days=500),
    )
    filtered = list_approver_history(
        db,
        "approver-1",
        page=1,
        page_size=1,
        query="Dodavatel 9601",
        paperless_document_ids=set(),
        decision="APPROVE",
        date_from=date.today() - timedelta(days=365),
        cost_center="200-9601",
    )
    assert filtered["total"] == 1
    assert filtered["page_size"] == 1
    assert filtered["items"][0]["invoice_id"] == matching.id


def test_history_access_lookup_uses_composite_index(db: Session) -> None:
    history_invoice(db, paperless_id=9701, subject="approver-1")
    plan = db.execute(
        text(
            "EXPLAIN QUERY PLAN SELECT id FROM approval_assignments "
            "WHERE approver_subject = :subject AND invoice_id = :invoice_id"
        ),
        {"subject": "approver-1", "invoice_id": "irrelevant"},
    ).all()
    assert "ix_approval_assignment_approver_invoice" in " ".join(
        str(value) for row in plan for value in row
    )
