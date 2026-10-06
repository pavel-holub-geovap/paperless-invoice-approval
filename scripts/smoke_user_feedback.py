#!/usr/bin/env python3
"""Synthetic-only real API/OIDC smoke; leaves fixtures intact for UI checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.services.isdoc import enumerate_attachments
from generate_isdoc_smoke_fixtures import isdoc_xml, with_attachment
from smoke_isdoc_approved_pdf import api, detail, upload, wait_detail
from smoke_stage_b import KeycloakLoginForm, login, require, response_json


def logout_relogin(client, base, user, username, password):
    response = api(client, "POST", base + "/api/auth/logout", user)
    logout_url = response.json()["logout_url"]
    require("/protocol/openid-connect/logout?" in logout_url, "Missing RP logout")
    end = client.get(logout_url)
    require(end.status_code == 200, "Keycloak logout failed")
    require(
        client.get(base + "/api/auth/me").status_code == 401,
        "Local session survived logout",
    )
    next_login = client.get(base + "/api/auth/login")
    parser = KeycloakLoginForm()
    parser.feed(next_login.text)
    require(parser.action is not None, "Keycloak SSO survived logout")
    callback = client.post(
        parser.action,
        data={"username": username, "password": password, "credentialId": ""},
    )
    require(callback.status_code == 200, "Credential re-login failed")
    return response_json(client.get(base + "/api/auth/me"), "re-login")


def configure(manager, base, user, invoice, permissions, subjects, amounts):
    centers = {
        row["approver_subject"]: row["cost_center"]
        for row in permissions
        if row["active"] and row["approver_subject"] in subjects
    }
    require(
        all(subject in centers for subject in subjects),
        "Required test section permission missing",
    )
    require(
        len({centers[s]["id"] for s in subjects}) == len(subjects),
        "Test subjects require distinct sections",
    )
    rows = [
        {
            "cost_center_id": centers[subject]["id"],
            "note": f"Synthetic section {index + 1}",
            **(
                {"percentage": "100"}
                if len(subjects) == 1
                else {"amount": amounts[index]}
            ),
        }
        for index, subject in enumerate(subjects)
    ]
    invoice = response_json(
        api(
            manager,
            "PUT",
            f"{base}/api/invoices/{invoice['id']}/allocations",
            user,
            {
                "allocations": rows,
                "expected_revision": invoice["current_revision_number"],
            },
        ),
        "allocations",
    )
    for subject in subjects:
        allocation = next(
            row
            for row in invoice["allocations"]
            if row["cost_center"]["id"] == centers[subject]["id"]
        )
        invoice = response_json(
            api(
                manager,
                "PUT",
                f"{base}/api/invoices/{invoice['id']}/allocations/{allocation['id']}/approvers",
                user,
                {
                    "approver_subjects": [subject],
                    "expected_revision": invoice["current_revision_number"],
                },
            ),
            "assignments",
        )
    return invoice


def submit(manager, base, user, invoice):
    api(manager, "POST", f"{base}/api/invoices/{invoice['id']}/confirm-original", user)
    return response_json(
        api(manager, "POST", f"{base}/api/invoices/{invoice['id']}/submit", user),
        "submit",
    )


def task_for(client, base, invoice_id):
    return next(
        row
        for row in response_json(client.get(base + "/api/approvals/mine"), "tasks")
        if row["invoice_id"] == invoice_id
    )


def synthetic_pdf(number):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdfmetrics.registerFont(
        TTFont("SmokeDejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    )
    pdf = canvas.Canvas(output)
    pdf.setFont("SmokeDejaVu", 11)
    for index, line in enumerate(
        [
            f"Syntetická faktura {number}",
            "Dodavatel: Smoke ISDOC s.r.o.",
            "IČO: 28652240 DIČ: CZ28652240",
            "Adresa dodavatele: Testovací 1, Praha, 10000",
            "Odběratel: GEOVAP, spol. s r.o.",
            "IČO odběratele: 15049248 DIČ: CZ15049248",
            "Datum vystavení 01.10.2026",
            "Datum uskutečnění zd. plnění 30.09.2026",
            "Datum splatnosti 15.10.2026",
            "Základ bez DPH 1237,50",
            "DPH 21 % 259,88",
            "Celkem 1497,38 Kč",
            "Zaokrouhlení 0,00",
            "Popis: Syntetické ověření uživatelských připomínek",
            "Variabilní symbol 20261006",
            "Účet 123456789/0100",
        ]
    ):
        pdf.drawString(42, 790 - index * 24, line)
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def verify_xml_export(manager, base, user, plain):
    require(str(plain["data"].get("invoice_number", "")).startswith("FEEDBACK-XML-"), "Only synthetic XML fixtures may be exported")
    require(plain["status"] in {"APPROVED", "EXPORT_CREATED"}, "Invoice is not approved")
    require(plain["data"].get("supplier_ico") == "28652240", "Supplier identity mismatch")
    require(plain["data"].get("taxable_supply_date") == "2026-09-30", "DUZP mismatch")
    generated = response_json(api(
        manager, "POST", f"{base}/api/exports/invoices/{plain['id']}/generate",
        user, {"reason": "Synthetic feedback smoke"}, expected=201,
    ), "generated XML")
    xml_response = manager.get(f"{base}/api/exports/artifacts/{generated['id']}/xml")
    require(xml_response.status_code == 200, "Actual XML download failed")
    root = ET.fromstring(xml_response.content)
    require(root.attrib["ico"] == "15049248", "XML target unit mismatch")
    current = detail(manager, base, plain["id"])
    require(current["status"] == "EXPORT_CREATED", "Generated XML did not create export")
    batch = response_json(api(
        manager, "POST", base + "/api/exports", user,
        {"invoice_ids": [plain["id"]]}, expected=201,
    ), "ZIP")
    downloaded = manager.get(f"{base}/api/exports/{batch['id']}/download")
    require(downloaded.status_code == 200, "Actual ZIP download failed")
    pdf = manager.get(f"{base}/api/invoices/{plain['id']}/approved-pdf").content
    original = manager.get(f"{base}/api/invoices/{plain['id']}/pdf").content
    require(pdf != original and pdf.startswith(b"%PDF"), "Accountant PDF must be derived")
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        zip_pdf = archive.read(next(name for name in archive.namelist() if name.endswith(".pdf")))
        require(zip_pdf == pdf, "ZIP does not contain current approved PDF")
    return {
        "xml_invoice_id": plain["id"], "paperless_document_id": plain["paperless_document_id"],
        "ai_supplier_ico": plain["data"]["supplier_ico"],
        "duzp": plain["data"]["taxable_supply_date"],
        "ocr_length": len(plain["source"]["ocr_text"]),
        "xml_artifact_id": generated["id"],
        "xml_sha256": hashlib.sha256(xml_response.content).hexdigest(),
        "xml_target_ico": root.attrib["ico"], "xml_key": root.attrib.get("key"),
        "xsd": generated["status"], "zip_batch_id": batch["id"],
        "approved_pdf_sha256": hashlib.sha256(pdf).hexdigest(), "zip_approved_pdf": "PASS",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["prepare", "finish", "export"], default="prepare")
    parser.add_argument("--invoice")
    args = parser.parse_args()
    base = os.environ["APP_BASE_URL"].rstrip("/")
    names = {
        "manager": ("queue-manager", "TEST_QUEUE_MANAGER_PASSWORD"),
        "approver1": ("approver1", "TEST_APPROVER_1_PASSWORD"),
        "approver2": ("approver2", "TEST_APPROVER_2_PASSWORD"),
        "admin": ("admin1", "TEST_ADMIN_PASSWORD"),
    }
    clients = {
        key: login(base, name, os.environ[variable])
        for key, (name, variable) in names.items()
    }
    users = {
        key: response_json(client.get(base + "/api/auth/me"), key)
        for key, client in clients.items()
    }
    manager, user = clients["manager"], users["manager"]
    report = {
        "phase": args.phase,
        "login_roles": {key: row["roles"] for key, row in users.items()},
    }
    try:
        permissions = response_json(
            manager.get(base + "/api/section-permissions"), "permissions"
        )
        for key in ("manager", "approver1", "approver2"):
            require(
                clients[key].get(base + "/api/admin/system-audit").status_code == 403,
                "System audit RBAC failed",
            )
        require(
            clients["admin"].get(base + "/api/admin/system-audit").status_code == 200,
            "Admin audit missing",
        )
        if args.phase == "prepare":
            if args.invoice:
                invoice = detail(manager, base, args.invoice)
                require(
                    str(invoice["data"].get("invoice_number", "")).startswith(
                        "FEEDBACK-"
                    ),
                    "Only a synthetic fixture can be resumed",
                )
            else:
                number = "FEEDBACK-" + uuid.uuid4().hex[:8]
                xml = (
                    isdoc_xml()
                    .replace(b"SMOKE-ISDOC-2026-001", number.encode())
                    .replace(b"1210.00", b"1497.38")
                    .replace(b"1000.00", b"1237.50")
                    .replace(b"210.00", b"259.88")
                )
                original = with_attachment(synthetic_pdf(number), "invoice.isdoc", xml)
                tracking = upload(manager, base, user, number + ".pdf", original)
                invoice = wait_detail(
                    manager,
                    base,
                    tracking["invoice_id"],
                    lambda row: row["isdoc"]["status"] == "VALID",
                    "valid ISDOC",
                    300,
                )
            invoice = response_json(
                api(
                    manager,
                    "PATCH",
                    f"{base}/api/invoices/{invoice['id']}",
                    user,
                    {
                        "changes": {
                            "description": "Synthetic user-feedback workflow",
                            "payment_required": True,
                        },
                        "expected_revision": invoice["current_revision_number"],
                    },
                ),
                "fields",
            )
            invoice = response_json(
                api(
                    manager,
                    "PUT",
                    f"{base}/api/invoices/{invoice['id']}/classification",
                    user,
                    {
                        "document_type": "RECEIVED_INVOICE",
                        "processing_mode": "FOR_APPROVAL",
                        "expected_revision": invoice["current_revision_number"],
                    },
                ),
                "classification",
            )
            invoice = configure(
                manager,
                base,
                user,
                invoice,
                permissions,
                [users["approver1"]["subject"]],
                [],
            )
            require(
                str(invoice["allocations"][0]["amount"]) == "1497.38",
                "Single section not whole total",
            )
            invoice = configure(
                manager,
                base,
                user,
                invoice,
                permissions,
                [users["approver1"]["subject"], users["approver2"]["subject"]],
                ["700.00", "797.38"],
            )
            invoice = submit(manager, base, user, invoice)
            task = task_for(clients["approver1"], base, invoice["id"])
            require(
                len(task["allocations"]) == 2
                and task["invoice_data"]["payment_required"] is True,
                "Incomplete read-only context",
            )
            report.update(
                {
                    "invoice_id": invoice["id"],
                    "url": base + "/invoices/" + invoice["id"],
                    "paperless_document_id": invoice["paperless_document_id"],
                    "status": invoice["status"],
                    "total": "1497.38",
                    "isdoc_sha256": invoice["isdoc"]["sha256"],
                    "single_then_multiple": "PASS",
                    "rbac": "PASS",
                }
            )
        elif args.phase == "export":
            require(args.invoice is not None, "--invoice required")
            report.update(verify_xml_export(manager, base, user, detail(manager, base, args.invoice)))
        else:
            require(args.invoice is not None, "--invoice required")
            invoice = detail(manager, base, args.invoice)
            require(
                str(invoice["data"].get("invoice_number", "")).startswith("FEEDBACK-"),
                "Refusing to modify a non-smoke invoice",
            )
            if invoice["status"] == "AWAITING_APPROVAL":
                task = task_for(clients["approver1"], base, invoice["id"])
                api(
                    clients["approver1"],
                    "POST",
                    f"{base}/api/approvals/{task['id']}/decision",
                    users["approver1"],
                    {"action": "RETURN", "comment": None},
                )
            invoice = detail(manager, base, args.invoice)
            require(
                invoice["status"] == "RETURNED", "Synthetic invoice must be returned"
            )
            history = response_json(
                manager.get(f"{base}/api/invoices/{invoice['id']}/audit"), "history"
            )
            returned = next(
                event
                for event in reversed(history)
                if event["event_type"] == "RETURNED"
            )
            require(
                returned["comment"] is None
                and returned["actor"]
                and returned["timestamp"],
                "Return audit incomplete",
            )
            old_revision = invoice["current_revision_number"]
            invoice = response_json(
                api(
                    manager,
                    "PATCH",
                    f"{base}/api/invoices/{invoice['id']}",
                    user,
                    {
                        "changes": {"supplier_street": "Testovací 2"},
                        "expected_revision": old_revision,
                    },
                ),
                "revision correction",
            )
            require(
                invoice["current_revision_number"] > old_revision, "No new revision"
            )
            require(
                all(
                    row["assignments"]
                    and all(item["status"] == "PENDING" for item in row["assignments"])
                    for row in invoice["allocations"]
                ),
                "Assignments not carried pending",
            )
            invoice = submit(manager, base, user, invoice)
            for key in ("approver1", "approver2"):
                task = task_for(clients[key], base, invoice["id"])
                api(
                    clients[key],
                    "POST",
                    f"{base}/api/approvals/{task['id']}/decision",
                    users[key],
                    {"action": "APPROVE"},
                )
            invoice = wait_detail(
                manager,
                base,
                invoice["id"],
                lambda row: (row.get("approved_pdf") or {}).get("status") == "STORED",
                "approved PDF",
                360,
            )
            require(
                invoice["status"] == "EXPORT_CREATED",
                "PDF_ISDOC export state incorrect",
            )
            original = manager.get(f"{base}/api/invoices/{invoice['id']}/pdf").content
            approved_response = manager.get(
                f"{base}/api/invoices/{invoice['id']}/approved-pdf"
            )
            require(
                approved_response.status_code == 200, "Approved PDF endpoint failed"
            )
            approved = approved_response.content
            require(
                hashlib.sha256(approved).hexdigest()
                == invoice["approved_pdf"]["approved_pdf_sha256"],
                "Approved PDF hash mismatch",
            )
            require(original != approved, "Accountant received original")
            require(
                {
                    (item.filename, item.sha256)
                    for item in enumerate_attachments(original)
                }
                == {
                    (item.filename, item.sha256)
                    for item in enumerate_attachments(approved)
                },
                "Embedded ISDOC changed",
            )
            report.update(
                {
                    "invoice_id": invoice["id"],
                    "status": invoice["status"],
                    "revision": invoice["current_revision_number"],
                    "return_no_comment": "PASS",
                    "carry_forward": "PASS",
                    "approved_pdf_sha256": hashlib.sha256(approved).hexdigest(),
                    "isdoc_integrity": "PASS",
                }
            )
            # Plain PDF exercises actual OCR/AI, then deterministic reviewed XML export.
            number = "FEEDBACK-XML-" + uuid.uuid4().hex[:8]
            tracking = upload(
                manager, base, user, number + ".pdf", synthetic_pdf(number)
            )
            plain = wait_detail(
                manager,
                base,
                tracking["invoice_id"],
                lambda row: row["ai_status"] in {"AI_COMPLETED", "AI_FAILED"},
                "OCR/AI",
                1200,
            )
            require(plain["ai_status"] == "AI_COMPLETED", "Real extraction failed")
            require(
                plain["data"].get("supplier_ico") == "28652240",
                "AI mixed supplier and customer",
            )
            require(
                plain["data"].get("taxable_supply_date") == "2026-09-30",
                "DUZP extraction failed",
            )
            plain = response_json(
                api(
                    manager,
                    "PATCH",
                    f"{base}/api/invoices/{plain['id']}",
                    user,
                    {
                        "changes": {
                            "payment_required": True,
                            "description": "Synthetic reviewed XML export",
                            "invoice_number": number,
                        },
                        "expected_revision": plain["current_revision_number"],
                    },
                ),
                "reviewed fields",
            )
            plain = response_json(
                api(
                    manager,
                    "PUT",
                    f"{base}/api/invoices/{plain['id']}/classification",
                    user,
                    {
                        "document_type": "RECEIVED_INVOICE",
                        "processing_mode": "FOR_APPROVAL",
                        "expected_revision": plain["current_revision_number"],
                    },
                ),
                "classification",
            )
            plain = configure(
                manager,
                base,
                user,
                plain,
                permissions,
                [users["approver1"]["subject"]],
                [],
            )
            plain = submit(manager, base, user, plain)
            task = task_for(clients["approver1"], base, plain["id"])
            api(
                clients["approver1"],
                "POST",
                f"{base}/api/approvals/{task['id']}/decision",
                users["approver1"],
                {"action": "APPROVE"},
            )
            plain = wait_detail(
                manager,
                base,
                plain["id"],
                lambda row: (row.get("approved_pdf") or {}).get("status") == "STORED",
                "XML approved PDF",
                360,
            )
            require(
                plain["status"] == "APPROVED", "Plain approval prematurely exported"
            )
            generated = response_json(
                api(
                    manager,
                    "POST",
                    f"{base}/api/exports/invoices/{plain['id']}/generate",
                    user,
                    {"reason": "Synthetic feedback smoke"},
                    expected=201,
                ),
                "generated XML",
            )
            xml_response = manager.get(
                f"{base}/api/exports/artifacts/{generated['id']}/xml"
            )
            require(xml_response.status_code == 200, "Actual XML download failed")
            root = ET.fromstring(xml_response.content)
            require(root.attrib["ico"] == "15049248", "XML target unit mismatch")
            current = detail(manager, base, plain["id"])
            require(
                current["status"] == "EXPORT_CREATED",
                "Generated XML did not create export",
            )
            batch = response_json(
                api(
                    manager,
                    "POST",
                    base + "/api/exports",
                    user,
                    {"invoice_ids": [plain["id"]]},
                    expected=201,
                ),
                "ZIP",
            )
            downloaded = manager.get(f"{base}/api/exports/{batch['id']}/download")
            require(downloaded.status_code == 200, "Actual ZIP download failed")
            pdf = manager.get(f"{base}/api/invoices/{plain['id']}/approved-pdf").content
            with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
                zip_pdf = archive.read(
                    next(name for name in archive.namelist() if name.endswith(".pdf"))
                )
                require(zip_pdf == pdf, "ZIP does not contain current approved PDF")
            report.update(
                {
                    "xml_invoice_id": plain["id"],
                    "ai_supplier_ico": plain["data"]["supplier_ico"],
                    "duzp": plain["data"]["taxable_supply_date"],
                    "xml_artifact_id": generated["id"],
                    "xml_sha256": hashlib.sha256(xml_response.content).hexdigest(),
                    "xml_target_ico": root.attrib["ico"],
                    "xml_key": root.attrib.get("key"),
                    "xsd": generated["status"],
                    "zip_batch_id": batch["id"],
                    "zip_approved_pdf": "PASS",
                }
            )
        for key in ("manager", "approver1"):
            name, variable = names[key]
            users[key] = logout_relogin(
                clients[key], base, users[key], name, os.environ[variable]
            )
        report["oidc_logout_relogin"] = "PASS (queue-manager + approver1)"
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        for client in clients.values():
            client.close()


if __name__ == "__main__":
    main()
