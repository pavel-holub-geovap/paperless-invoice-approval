from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.api.routes import admin as admin_routes
from app.api.routes.admin import bulk_purge_admin_invoices, purge_admin_invoice
from app.api.routes.cost_centers import create_cost_center
from app.api.routes.section_permissions import update_section_permission
from app.auth import ROLE_ADMIN, ROLE_APPROVER, ROLE_QUEUE_MANAGER, require_roles
from app.config import Settings
from app.integrations.paperless import PaperlessClient, PaperlessError, PaperlessNotFound
from app.models import (
    AdminPurgeAudit,
    AIExtraction,
    AIExtractionStatus,
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalDecision,
    ApprovedPdfArtifact,
    ApprovedPdfStatus,
    AuditEvent,
    CostCenter,
    DocumentUpload,
    DocumentUploadStatus,
    ExportArtifact,
    ExportArtifactStatus,
    ExportBatch,
    ExportBatchItem,
    ExtractedField,
    Invoice,
    IsdocExtraction,
    PohodaResponseUpload,
    ProcessingJob,
    UserIdentity,
    ValidationResult,
    ValidationSeverity,
)
from app.schemas import (
    AdminBulkPurgeRequest,
    AdminPurgeRequest,
    CostCenterIn,
    CurrentUser,
    SectionPermissionSet,
)
from app.services.admin_purge import AdminPurgeExternalError, purge_invoice
from app.services.identity import UnsupportedApplicationRole, synchronize_oidc_identity
from app.services.section_permissions import set_section_permission
from app.services.workflow import create_invoice


class FakePaperless:
    def __init__(self, *, missing: set[int] | None = None, failing: set[int] | None = None):
        self.missing = missing or set()
        self.failing = failing or set()
        self.deleted: list[int] = []

    async def delete_document(self, document_id: int) -> None:
        if document_id in self.missing:
            raise PaperlessNotFound("already absent")
        if document_id in self.failing:
            raise PaperlessError("Paperless unavailable")
        self.deleted.append(document_id)

    async def close(self) -> None:
        return None


def user(*roles: str) -> CurrentUser:
    return CurrentUser(subject="actor-sub", username="actor", roles=list(roles), csrf_token="csrf")


def populated_invoice(db, tmp_path: Path) -> tuple[Invoice, list[Path]]:
    invoice = create_invoice(db, 9201, "system")
    invoice.paperless_title = "Synthetic purge invoice"
    invoice.current_revision.data = {
        "invoice_number": "PURGE-9201",
        "supplier_name": "Synthetic supplier",
        "total_amount": "121.00",
    }
    revision = invoice.current_revision
    db.add(
        ExtractedField(
            revision_id=revision.id,
            field_name="invoice_number",
            value="PURGE-9201",
        )
    )
    db.add(
        ValidationResult(
            revision_id=revision.id,
            code="TEST",
            severity=ValidationSeverity.OK,
            message="ok",
        )
    )
    db.add(
        AIExtraction(
            invoice_id=invoice.id,
            invoice_revision_id=revision.id,
            extraction_revision=1,
            model="qwen3:8b",
            schema_version="test",
            prompt_version="test",
            status=AIExtractionStatus.AI_COMPLETED,
        )
    )
    db.add(
        IsdocExtraction(
            invoice_id=invoice.id,
            invoice_revision_id=revision.id,
            filename="invoice.isdoc",
            isdoc_sha256="a" * 64,
            version="6.0.2",
            namespace="http://isdoc.cz/namespace/2013",
            mapped_data={"invoice_number": "PURGE-9201"},
            provenance={},
        )
    )
    approved = ApprovedPdfArtifact(
        invoice_id=invoice.id,
        revision_id=revision.id,
        status=ApprovedPdfStatus.STORED,
        stamp_version="v1",
        approval_snapshot={"invoice_id": invoice.id},
        approval_snapshot_sha256="b" * 64,
        original_pdf_sha256="c" * 64,
        approved_pdf_sha256="d" * 64,
        paperless_document_id=9202,
    )
    db.add(approved)
    center = CostCenter(code="PURGE", name="Purge test", pohoda_code="PURGE")
    db.add(center)
    db.flush()
    allocation = Allocation(
        invoice_id=invoice.id,
        revision_id=revision.id,
        cost_center_id=center.id,
        amount=121,
    )
    db.add(allocation)
    db.flush()
    assignment = ApprovalAssignment(
        invoice_id=invoice.id,
        revision_id=revision.id,
        allocation_id=allocation.id,
        approver_subject="approver-test",
    )
    db.add(assignment)
    db.flush()
    db.add(
        ApprovalDecision(
            assignment_id=assignment.id,
            revision_id=revision.id,
            action=ApprovalAction.APPROVE,
            actor_subject="approver-test",
        )
    )
    xml_path = tmp_path / "purge.xml"
    zip_path = tmp_path / "purge.zip"
    response_path = tmp_path / "response.xml"
    xml_path.write_text("<xml/>", encoding="utf-8")
    zip_path.write_bytes(b"zip")
    response_path.write_text("<response/>", encoding="utf-8")
    artifact = ExportArtifact(
        invoice_id=invoice.id,
        revision_id=revision.id,
        status=ExportArtifactStatus.XSD_VALID,
        generator_version="test",
        xsd_bundle_version="test",
        encoding="UTF-8",
        source_snapshot={"invoice_id": invoice.id},
        xml_path=str(xml_path),
        xml_sha256="e" * 64,
        xml_size=6,
        generated_by="manager",
    )
    db.add(artifact)
    batch = ExportBatch(
        batch_number="PURGE-BATCH",
        archive_path=str(zip_path),
        archive_sha256="f" * 64,
        created_by="manager",
    )
    db.add(batch)
    db.flush()
    db.add(
        ExportBatchItem(
            batch_id=batch.id,
            invoice_id=invoice.id,
            revision_id=revision.id,
            export_artifact_id=artifact.id,
            pdf_filename="invoice.pdf",
            xml_filename="invoice.xml",
        )
    )
    db.add(
        PohodaResponseUpload(
            export_artifact_id=artifact.id,
            batch_id=batch.id,
            filename="response.xml",
            artifact_path=str(response_path),
            sha256="0" * 64,
            parse_status="PARSED",
            uploaded_by="manager",
        )
    )
    db.add(
        ProcessingJob(
            job_type="TEST",
            invoice_id=invoice.id,
            idempotency_key=f"purge:{invoice.id}",
        )
    )
    db.add(
        DocumentUpload(
            idempotency_key=f"upload:{invoice.id}",
            actor_subject="manager",
            actor_username="manager",
            actor_role="QUEUE_MANAGER",
            filename="purge.pdf",
            file_size=100,
            mime_type="application/pdf",
            sha256="1" * 64,
            status=DocumentUploadStatus.OCR_COMPLETE,
            invoice_id=invoice.id,
            correlation_id="purge-test",
        )
    )
    db.flush()
    return invoice, [xml_path, zip_path, response_path]


@pytest.mark.asyncio
async def test_admin_purge_removes_complete_invoice_and_preserves_only_minimal_audit(
    db, tmp_path: Path
) -> None:
    invoice, files = populated_invoice(db, tmp_path)
    db.commit()
    invoice_id = invoice.id
    paperless = FakePaperless()

    result = await purge_invoice(
        db,
        Settings(export_archive_dir=tmp_path),
        paperless,
        invoice_id=invoice_id,
        actor_subject="admin-sub",
        actor_display_name="admin1",
        reason="Synthetic regression document",
    )
    db.commit()

    assert result.status == "PURGED"
    assert paperless.deleted == [9202, 9201]
    assert db.get(Invoice, invoice_id) is None
    assert not db.scalars(select(AuditEvent).where(AuditEvent.invoice_id == invoice_id)).all()
    assert not db.scalars(select(ExportArtifact).where(ExportArtifact.invoice_id == invoice_id)).all()
    assert all(not path.exists() for path in files)
    audit = db.scalar(
        select(AdminPurgeAudit).where(AdminPurgeAudit.original_invoice_id == invoice_id)
    )
    assert audit is not None
    assert audit.paperless_document_ids == [9202, 9201]
    assert audit.reason == "Synthetic regression document"
    assert audit.artifact_counts["approved_pdf_artifacts"] == 1
    assert "supplier" not in str(audit.__dict__).lower()
    assert "121.00" not in str(audit.__dict__)

    repeated = await purge_invoice(
        db,
        Settings(export_archive_dir=tmp_path),
        paperless,
        invoice_id=invoice_id,
        actor_subject="admin-sub",
        actor_display_name="admin1",
        reason="retry",
    )
    assert repeated.status == "ALREADY_PURGED"
    assert repeated.audit_id == audit.id


@pytest.mark.asyncio
async def test_paperless_failure_retains_local_invoice_and_artifacts(db, tmp_path: Path) -> None:
    invoice, files = populated_invoice(db, tmp_path)
    db.commit()
    with pytest.raises(AdminPurgeExternalError, match="local invoice was retained"):
        await purge_invoice(
            db,
            Settings(export_archive_dir=tmp_path),
            FakePaperless(failing={9201}),
            invoice_id=invoice.id,
            actor_subject="admin-sub",
            actor_display_name="admin1",
            reason="failure test",
        )
    db.rollback()
    assert db.get(Invoice, invoice.id) is not None
    assert all(path.exists() for path in files)
    assert not db.scalars(select(AdminPurgeAudit)).all()


@pytest.mark.asyncio
async def test_approved_copy_failure_is_attempted_before_original(db, tmp_path: Path) -> None:
    invoice, files = populated_invoice(db, tmp_path)
    db.commit()
    paperless = FakePaperless(failing={9202})
    with pytest.raises(AdminPurgeExternalError):
        await purge_invoice(
            db,
            Settings(export_archive_dir=tmp_path),
            paperless,
            invoice_id=invoice.id,
            actor_subject="admin-sub",
            actor_display_name="admin1",
            reason="failure ordering test",
        )
    db.rollback()
    assert paperless.deleted == []
    assert db.get(Invoice, invoice.id) is not None
    assert all(path.exists() for path in files)


@pytest.mark.asyncio
async def test_paperless_404_is_safe_and_isdoc_has_no_orphan(db, tmp_path: Path) -> None:
    invoice, _ = populated_invoice(db, tmp_path)
    db.commit()
    invoice_id = invoice.id
    result = await purge_invoice(
        db,
        Settings(export_archive_dir=tmp_path),
        FakePaperless(missing={9201, 9202}),
        invoice_id=invoice_id,
        actor_subject="admin-sub",
        actor_display_name="admin1",
        reason="already absent",
    )
    db.commit()
    assert result.artifact_counts["paperless_already_absent"] == 2
    assert not db.scalars(select(IsdocExtraction).where(IsdocExtraction.invoice_id == invoice_id)).all()


def test_admin_rbac_multi_role_and_confirmation_contracts(db) -> None:
    require_roles(ROLE_ADMIN)(user(ROLE_ADMIN))
    require_roles(ROLE_ADMIN)(user(ROLE_ADMIN, ROLE_QUEUE_MANAGER))
    with pytest.raises(HTTPException) as manager_denied:
        create_cost_center(
            CostCenterIn(code="NO", name="Denied", pohoda_code="NO"),
            db,
            user(ROLE_QUEUE_MANAGER),
        )
    assert manager_denied.value.status_code == 403
    with pytest.raises(HTTPException) as approver_denied:
        update_section_permission(
            SectionPermissionSet(
                approver_subject="approver",
                cost_center_id="missing",
                active=True,
            ),
            db,
            user(ROLE_APPROVER),
        )
    assert approver_denied.value.status_code == 403

    identity = UserIdentity(
        subject="new-route-approver",
        username="new-route-approver",
        roles=[ROLE_APPROVER],
    )
    db.add(identity)
    created = create_cost_center(
        CostCenterIn(code="ADMIN", name="Admin section", pohoda_code="ADMIN"),
        db,
        user(ROLE_ADMIN),
    )
    permission = update_section_permission(
        SectionPermissionSet(
            approver_subject=identity.subject,
            cost_center_id=created.id,
            active=True,
        ),
        db,
        user(ROLE_ADMIN),
    )
    assert permission["approver_subject"] == identity.subject
    with pytest.raises(ValidationError):
        AdminPurgeRequest(confirmation="ANO", reason="valid reason")
    with pytest.raises(ValidationError):
        AdminPurgeRequest(confirmation="SMAZAT", reason="")
    with pytest.raises(ValidationError):
        AdminPurgeRequest(confirmation="SMAZAT", reason="   ")
    with pytest.raises(ValidationError):
        AdminBulkPurgeRequest(
            invoice_ids=["same", "same"],
            confirmation="SMAZAT VYBRANÉ",
            reason="test",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_role", [ROLE_QUEUE_MANAGER, ROLE_APPROVER])
async def test_purge_routes_reject_non_admin_before_external_side_effects(
    db, tmp_path: Path, denied_role: str
) -> None:
    with pytest.raises(HTTPException) as single:
        await purge_admin_invoice(
            "invoice-id",
            AdminPurgeRequest(confirmation="SMAZAT", reason="RBAC regression"),
            db,
            Settings(export_archive_dir=tmp_path),
            user(denied_role),
        )
    assert single.value.status_code == 403
    with pytest.raises(HTTPException) as bulk:
        await bulk_purge_admin_invoices(
            AdminBulkPurgeRequest(
                invoice_ids=["invoice-id"],
                confirmation="SMAZAT VYBRANÉ",
                reason="RBAC regression",
            ),
            db,
            Settings(export_archive_dir=tmp_path),
            user(denied_role),
        )
    assert bulk.value.status_code == 403


@pytest.mark.asyncio
async def test_bulk_purge_reports_partial_failure_and_keeps_failed_invoice(
    db, tmp_path: Path, monkeypatch
) -> None:
    first = create_invoice(db, 9301, "system")
    second = create_invoice(db, 9302, "system")
    db.commit()
    paperless = FakePaperless(failing={9302})
    monkeypatch.setattr(admin_routes, "PaperlessClient", lambda _: paperless)

    result = await bulk_purge_admin_invoices(
        AdminBulkPurgeRequest(
            invoice_ids=[first.id, second.id],
            confirmation="SMAZAT VYBRANÉ",
            reason="Bulk partial failure regression",
        ),
        db,
        Settings(export_archive_dir=tmp_path),
        user(ROLE_ADMIN),
    )

    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert [row["status"] for row in result["results"]] == ["PURGED", "FAILED"]
    assert db.get(Invoice, first.id) is None
    assert db.get(Invoice, second.id) is not None
    assert db.scalar(
        select(AdminPurgeAudit).where(AdminPurgeAudit.original_invoice_id == first.id)
    ) is not None
    assert db.scalar(
        select(AdminPurgeAudit).where(AdminPurgeAudit.original_invoice_id == second.id)
    ) is None


@pytest.mark.asyncio
async def test_paperless_delete_uses_rest_and_preserves_failure_classification() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(204)

    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
    )
    client = PaperlessClient(settings, transport=httpx.MockTransport(handler))
    try:
        await client.delete_document(9201)
    finally:
        await client.close()
    assert seen == ["DELETE /api/documents/9201/"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(404, PaperlessNotFound), (503, PaperlessError)],
)
async def test_paperless_delete_classifies_404_and_server_failure(
    status_code: int, error_type: type[Exception]
) -> None:
    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
        external_retry_attempts=1,
    )
    client = PaperlessClient(
        settings,
        transport=httpx.MockTransport(lambda _: httpx.Response(status_code)),
    )
    try:
        with pytest.raises(error_type):
            await client.delete_document(9201)
    finally:
        await client.close()


def test_new_keycloak_user_projection_role_refresh_and_section_permission(db) -> None:
    claims = {
        "sub": "new-approver-sub",
        "preferred_username": "new-approver",
        "email": "new-approver@example.test",
        "realm_access": {"roles": [ROLE_APPROVER, "offline_access"]},
    }
    identity = synchronize_oidc_identity(db, claims, "approval-app")
    assert identity.subject == "new-approver-sub"
    assert identity.roles == [ROLE_APPROVER]
    center = CostCenter(code="NEW", name="New users", pohoda_code="NEW")
    db.add(center)
    db.flush()
    permission = set_section_permission(
        db,
        approver_subject=identity.subject,
        cost_center_id=center.id,
        active=True,
        actor="admin-sub",
    )
    assert permission.approver_subject == identity.subject

    refreshed = synchronize_oidc_identity(
        db,
        {**claims, "realm_access": {"roles": [ROLE_APPROVER, ROLE_ADMIN]}},
        "approval-app",
    )
    assert refreshed is identity
    assert refreshed.roles == [ROLE_ADMIN, ROLE_APPROVER]
    reduced = synchronize_oidc_identity(db, claims, "approval-app")
    assert reduced.roles == [ROLE_APPROVER]

    with pytest.raises(UnsupportedApplicationRole):
        synchronize_oidc_identity(
            db,
            {
                "sub": "unsupported-sub",
                "preferred_username": "unsupported",
                "realm_access": {"roles": ["offline_access"]},
            },
            "approval-app",
        )
