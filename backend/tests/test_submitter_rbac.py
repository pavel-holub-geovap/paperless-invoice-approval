from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.routes.invoices import _preparer, _viewer, list_invoices
from app.models import UploadOrigin
from app.schemas import CurrentUser
from app.services.workflow import create_invoice


def submitter(subject: str = "submitter-1") -> CurrentUser:
    return CurrentUser(
        subject=subject,
        username=subject,
        roles=["INVOICE_SUBMITTER"],
        csrf_token="csrf",
    )


def test_submitter_sees_only_own_documents_and_not_queue_scope(db: Session) -> None:
    own = create_invoice(db, 91001, "system")
    own.uploaded_by_subject = "submitter-1"
    own.uploaded_by_username = "submitter1"
    own.upload_origin = UploadOrigin.INVOICE_SUBMITTER
    foreign = create_invoice(db, 91002, "system")
    foreign.uploaded_by_subject = "submitter-2"
    foreign.uploaded_by_username = "submitter2"
    foreign.upload_origin = UploadOrigin.INVOICE_SUBMITTER
    db.flush()

    rows = list_invoices(
        status_filter=None,
        supplier=None,
        approver=None,
        cost_center=None,
        view="all",
        sort="source_desc",
        scope="uploaded",
        db=db,
        user=submitter(),
    )
    assert [row.id for row in rows] == [own.id]
    _viewer(db, own, submitter())
    with pytest.raises(HTTPException) as caught:
        _viewer(db, foreign, submitter())
    assert caught.value.status_code == 403


def test_submitter_draft_becomes_read_only_after_queue_submission(db: Session) -> None:
    invoice = create_invoice(db, 91003, "system")
    invoice.uploaded_by_subject = "submitter-1"
    invoice.upload_origin = UploadOrigin.INVOICE_SUBMITTER
    assert _preparer(invoice, submitter())
    invoice.current_revision.submitted_to_queue_by = "submitter-1"
    invoice.current_revision.submitted_to_queue_at = invoice.created_at
    with pytest.raises(HTTPException) as caught:
        _preparer(invoice, submitter())
    assert caught.value.status_code == 403


def test_approver_role_does_not_unlock_submitter_draft_without_submitter_role(
    db: Session,
) -> None:
    invoice = create_invoice(db, 91004, "system")
    invoice.uploaded_by_subject = "same-person"
    invoice.upload_origin = UploadOrigin.INVOICE_SUBMITTER
    approver_only = CurrentUser(
        subject="same-person",
        username="same-person",
        roles=["APPROVER"],
        csrf_token="csrf",
    )
    with pytest.raises(HTTPException):
        _preparer(invoice, approver_only)
