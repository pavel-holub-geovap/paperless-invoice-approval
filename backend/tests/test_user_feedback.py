from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select
from starlette.requests import Request
from test_exports import FakePaperless, approved_invoice
from test_workflow import prepared_invoice

from app.api.routes.admin import system_audit
from app.api.routes.approvals import my_approvals
from app.api.routes.auth import logout
from app.api.routes.invoices import invoice_audit
from app.auth import get_current_user, require_roles, token_cipher
from app.config import Settings
from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    AuditEvent,
    ExtractedField,
    OidcSession,
    UserIdentity,
)
from app.schemas import AllocationInput, ApprovalRequest, CurrentUser
from app.services.approval_setup import replace_allocations
from app.services.audit import record_event
from app.services.exports import current_approved_pdf
from app.services.identity import identity_display, synchronize_oidc_identity
from app.services.supplier_registry import verify_supplier
from app.services.validation import run_validations
from app.services.workflow import WorkflowError, decide, submit_for_approval, update_invoice_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,name,expected",
    [
        (200, "GEOVAP, spol. s r.o.", "MATCH"),
        (200, "Jiný dodavatel", "MISMATCH"),
        (404, "", "NOT_FOUND"),
        (503, "", "UNAVAILABLE"),
    ],
)
async def test_ares_nonblocking_comparison(status, name, expected):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status,
            json={
                "ico": "15049248",
                "obchodniJmeno": "GEOVAP, spol. s r.o.",
                "sidlo": {"textovaAdresa": "Testovací 1"},
            },
        )
    )
    result = await verify_supplier("15049248", name, transport=transport)
    assert result["status"] == expected
    assert result["blocking"] is False


@pytest.mark.asyncio
async def test_ares_timeout_and_invalid_ico_do_not_block():
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    transport = httpx.MockTransport(timeout)
    assert (await verify_supplier("15049248", "", transport=transport))["status"] == "UNAVAILABLE"
    assert (await verify_supplier("15049249", "", transport=transport))["status"] == "INVALID_ICO"


@pytest.mark.asyncio
async def test_ares_rejects_wrong_identity_and_oversized_json():
    wrong = httpx.MockTransport(lambda r: httpx.Response(200, json={"ico": "28652240"}))
    huge = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * (256 * 1024 + 1)))
    for transport in (wrong, huge):
        assert (await verify_supplier("15049248", "", transport=transport))[
            "status"
        ] == "UNAVAILABLE"


@pytest.mark.parametrize("action", [ApprovalAction.RETURN, ApprovalAction.REJECT])
@pytest.mark.parametrize("comment", [None, "", "  ", " Doplnit údaje "])
def test_optional_negative_comments_are_canonical_and_audited(db, action, comment):
    invoice, assignments = prepared_invoice(db)
    submit_for_approval(db, invoice, "manager")
    payload = ApprovalRequest(action=action, comment=comment)
    decision = decide(db, assignments[0], action, "approver-1", payload.comment)
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == action.value + "ED"
            if action == ApprovalAction.REJECT
            else AuditEvent.event_type == "RETURNED"
        )
    )
    assert decision.comment == (comment.strip() or None if comment else None)
    assert event is not None and event.comment == decision.comment


@pytest.mark.asyncio
async def test_ares_also_compares_address_without_replacing_invoice():
    transport = httpx.MockTransport(lambda r: httpx.Response(200,json={"ico":"15049248","obchodniJmeno":"GEOVAP","sidlo":{"textovaAdresa":"Testovací 1, Praha"}}))
    match = await verify_supplier("15049248","GEOVAP","Testovací 1, Praha",transport=transport)
    mismatch = await verify_supplier("15049248","GEOVAP","Jiná 2, Brno",transport=transport)
    assert match["status"] == "MATCH" and match["address_match"] is True
    assert mismatch["status"] == "MISMATCH" and mismatch["blocking"] is False


@pytest.mark.parametrize("role,status",[("ADMIN",200),("QUEUE_MANAGER",403),("APPROVER",403),("INVOICE_SUBMITTER",403)])
def test_system_audit_http_rbac(db,role,status):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(subject="viewer",username="viewer",roles=[role])
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/admin/system-audit")
        assert response.status_code == status
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("invalidate", [None, "permission", "role", "identity", "center"])
def test_allocation_revision_carries_only_eligible_pending_assignments(db, invalidate):
    invoice, assignments = prepared_invoice(db)
    old = assignments[0]
    submit_for_approval(db, invoice, "manager")
    decision = decide(db, old, ApprovalAction.RETURN, "approver-1", None)
    center = old.allocation.cost_center
    if invalidate == "permission":
        from app.models import ApproverSectionPermission

        db.scalar(select(ApproverSectionPermission)).active = False
    elif invalidate == "role":
        db.get(UserIdentity, "approver-1").roles = []
    elif invalidate == "identity":
        db.get(UserIdentity, "approver-1").active = False
    elif invalidate == "center":
        from app.models import CostCenter

        center = CostCenter(code="OTHER", name="Other", pohoda_code="OTHER")
        db.add(center)
        db.flush()
    replace_allocations(
        db,
        invoice,
        [
            AllocationInput(
                cost_center_id=center.id, amount=Decimal("121"), note="Opravená poznámka"
            )
        ],
        "manager",
    )
    db.flush()
    current = db.scalars(
        select(ApprovalAssignment).where(
            ApprovalAssignment.revision_id == invoice.current_revision.id,
            ApprovalAssignment.active.is_(True),
        )
    ).all()
    assert not decision.valid and old.status == ApprovalAssignmentStatus.INVALIDATED
    if invalidate:
        assert current == []
    else:
        assert len(current) == 1 and current[0].status == ApprovalAssignmentStatus.PENDING
        assert current[0].id != old.id and current[0].decisions == []
        tasks = my_approvals(
            db=db, user=CurrentUser(subject="approver-1", username="approver1", roles=["APPROVER"])
        )
        assert tasks == []  # still waiting for manager review, not silently approved


def test_single_section_recalculates_only_an_unsubmitted_draft(db):
    invoice, assignments = prepared_invoice(db)
    assignments[0].allocation.percentage = Decimal("100")
    update_invoice_data(db, invoice, {"total_amount": "122.00"}, "manager")
    allocation = db.scalar(
        select(Allocation).where(
            Allocation.revision_id == invoice.current_revision.id, Allocation.active.is_(True)
        )
    )
    assert allocation.amount == Decimal("122")
    invoice.current_revision.queue_manager_reviewed_at = datetime.now(UTC)
    update_invoice_data(db, invoice, {"total_amount": "123.00"}, "manager")
    allocation = db.scalar(
        select(Allocation).where(
            Allocation.revision_id == invoice.current_revision.id, Allocation.active.is_(True)
        )
    )
    assert allocation.amount == Decimal(
        "122"
    )  # explicit re-save required for previously confirmed allocation


def test_readable_identity_and_business_history_hide_system_payload(db):
    invoice, _ = prepared_invoice(db)
    user = synchronize_oidc_identity(
        db,
        {
            "sub": "manager",
            "preferred_username": "queue-manager",
            "name": "Jana Správcová",
            "realm_access": {"roles": ["QUEUE_MANAGER"]},
        },
        "client",
    )
    record_event(
        db,
        "XML_VALIDATION_PASSED",
        actor=user.subject,
        invoice=invoice,
        metadata={"raw": "technical"},
    )
    record_event(
        db,
        "INVOICE_FIELD_CHANGED",
        actor=user.subject,
        invoice=invoice,
        new_value={"description": "note"},
    )
    db.flush()
    history = invoice_audit(
        invoice.id,
        db,
        CurrentUser(subject="manager", username="queue-manager", roles=["QUEUE_MANAGER"]),
    )
    assert identity_display(db, "manager") == "Jana Správcová"
    assert any(event["actor"] == "Jana Správcová" for event in history)
    assert all(
        event["event_type"] != "XML_VALIDATION_PASSED" and "raw" not in event["metadata"]
        for event in history
    )
    for role in ("QUEUE_MANAGER", "APPROVER", "INVOICE_SUBMITTER"):
        with pytest.raises(HTTPException):
            require_roles("ADMIN")(CurrentUser(subject="manager", username="manager", roles=[role]))
    assert any(
        event["event_type"] == "XML_VALIDATION_PASSED"
        for event in system_audit(
            invoice.id, 200, db, CurrentUser(subject="admin", username="admin", roles=["ADMIN"])
        )
    )


def test_self_supplier_warning_never_rewrites_data(db, monkeypatch):
    invoice, _ = prepared_invoice(db)
    update_invoice_data(db, invoice, {"supplier_ico": "15049248"}, "manager")
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(pohoda_target_ico="15049248"))
    results = run_validations(db, invoice)
    warning = next(row for row in results if row.code == "SUPPLIER_IS_TARGET_UNIT")
    assert warning.severity.value == "WARNING"
    assert invoice.current_revision.data["supplier_ico"] == "15049248"


@pytest.mark.parametrize("source,warning", [
    ("Statutární město Pardubice Smlouva/objednávka", True),
    ("Dodavatel: GEOVAP, spol.\n s r.o.", False),
])
def test_supplier_name_must_match_its_evidence_without_rewriting(db, source, warning):
    invoice, _ = prepared_invoice(db)
    update_invoice_data(db, invoice, {"supplier_name": "GEOVAP, spol. s r.o."}, "manager")
    db.add(ExtractedField(
        revision_id=invoice.current_revision.id, field_name="supplier_name",
        value="GEOVAP, spol. s r.o.", source_text=source,
    ))
    db.flush()
    results = run_validations(db, invoice)
    mismatches = [row for row in results if row.code == "SUPPLIER_EVIDENCE_MISMATCH"]
    assert bool(mismatches) is warning
    if mismatches:
        assert mismatches[0].severity.value == "WARNING"
    assert invoice.current_revision.data["supplier_name"] == "GEOVAP, spol. s r.o."


def test_logout_deletes_local_session_and_redirects_to_fixed_keycloak(db):
    settings = Settings(
        app_base_url="http://approval.test", keycloak_public_url="http://identity.test"
    )
    user = CurrentUser(subject="approver-1", username="approver1", roles=["APPROVER"])
    encrypted = token_cipher(settings).encrypt(b"signed.id.token").decode()
    assert "signed.id.token" not in encrypted
    db.add(
        OidcSession(
            id="local",
            subject=user.subject,
            csrf_token="csrf",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            id_token_encrypted=encrypted,
        )
    )
    db.commit()
    request = Request({"type": "http", "headers": [(b"cookie", b"pia_session=local")]})
    response = Response()
    result = logout(response, request, db, user, settings)
    url = urlsplit(result["logout_url"])
    assert url.hostname == "identity.test" and url.path.endswith("/protocol/openid-connect/logout")
    assert parse_qs(url.query)["post_logout_redirect_uri"] == ["http://approval.test/"]
    assert parse_qs(url.query)["id_token_hint"] == ["signed.id.token"]
    assert db.get(OidcSession, "local") is None
    with pytest.raises(HTTPException):
        get_current_user("local", db)


@pytest.mark.asyncio
async def test_export_only_uses_immutable_current_approved_pdf(db):
    invoice = approved_invoice(db)
    pdf, artifact = await current_approved_pdf(db, FakePaperless(), invoice)
    assert artifact.revision_id == invoice.current_revision.id
    assert hashlib.sha256(pdf).hexdigest() == artifact.approved_pdf_sha256
    artifact.approved_pdf_sha256 = "0" * 64
    with pytest.raises(WorkflowError, match="Hash"):
        await current_approved_pdf(db, FakePaperless(), invoice)
    update_invoice_data(db, invoice, {"supplier_name": "Changed"}, "manager")
    with pytest.raises(WorkflowError, match="aktuální revize"):
        await current_approved_pdf(db, FakePaperless(), invoice)
