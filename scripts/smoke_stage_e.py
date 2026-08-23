#!/usr/bin/env python3
"""Exercise the deployed Stage E workflow with real Keycloak users and Paperless tags."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import httpx
from smoke_stage_b import login, require, response_json


def csrf(user: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": user["csrf_token"]}


def api(
    client: httpx.Client,
    method: str,
    url: str,
    user: dict[str, Any],
    payload: dict[str, Any] | None = None,
    expected: int = 200,
) -> httpx.Response:
    response = client.request(method, url, headers=csrf(user), json=payload)
    require(
        response.status_code == expected,
        f"{method} {url} returned HTTP {response.status_code}: {response.text[:500]}",
    )
    return response


def detail(manager: httpx.Client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(manager.get(f"{base_url}/api/invoices/{invoice_id}"), "invoice detail")


def audit(manager: httpx.Client, base_url: str, invoice_id: str) -> list[dict[str, Any]]:
    return response_json(manager.get(f"{base_url}/api/invoices/{invoice_id}/audit"), "invoice audit")


def wait_for_tag(
    manager: httpx.Client,
    base_url: str,
    invoice_id: str,
    tag_name: str,
    timeout: int = 75,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tag_name in detail(manager, base_url, invoice_id)["paperless"]["tags"]:
            return True
        time.sleep(2)
    return False


def active_assignments(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**assignment, "cost_center": allocation["cost_center"]["code"], "amount": allocation["amount"]}
        for allocation in invoice["allocations"]
        for assignment in allocation["assignments"]
    ]


def setup(
    manager: httpx.Client,
    base_url: str,
    manager_user: dict[str, Any],
    invoice_id: str,
    centres: dict[str, dict[str, Any]],
    subjects: dict[str, str],
    *,
    first_code: str = "200",
    first_amount: str = "700.00",
    second_code: str = "300",
    second_amount: str = "510.00",
) -> dict[str, Any]:
    allocations = response_json(
        api(
            manager,
            "PUT",
            f"{base_url}/api/invoices/{invoice_id}/allocations",
            manager_user,
            {
                "allocations": [
                    {"cost_center_id": centres[first_code]["id"], "amount": first_amount, "note": "Primární rozúčtování"},
                    {"cost_center_id": centres[second_code]["id"], "amount": second_amount, "note": "Sdílené rozúčtování"},
                ]
            },
        ),
        "set allocations",
    )
    by_code = {row["cost_center"]["code"]: row for row in allocations["allocations"]}
    api(
        manager,
        "PUT",
        f"{base_url}/api/invoices/{invoice_id}/allocations/{by_code[first_code]['id']}/approvers",
        manager_user,
        {"approver_subjects": [subjects["approver1"]]},
    )
    api(
        manager,
        "PUT",
        f"{base_url}/api/invoices/{invoice_id}/allocations/{by_code[second_code]['id']}/approvers",
        manager_user,
        {"approver_subjects": [subjects["approver2"], subjects["approver3"]]},
    )
    return detail(manager, base_url, invoice_id)


def confirm_and_submit(
    manager: httpx.Client,
    base_url: str,
    manager_user: dict[str, Any],
    invoice_id: str,
) -> dict[str, Any]:
    api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/confirm-original", manager_user)
    submitted = response_json(
        api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/submit", manager_user),
        "submit invoice",
    )
    require(submitted["status"] == "AWAITING_APPROVAL", "Invoice was not submitted")
    return submitted


def tasks(client: httpx.Client, base_url: str) -> list[dict[str, Any]]:
    return response_json(client.get(f"{base_url}/api/approvals/mine"), "approver tasks")


def decide(
    client: httpx.Client,
    base_url: str,
    user: dict[str, Any],
    assignment_id: str,
    action: str,
    comment: str | None = None,
    expected: int = 200,
) -> httpx.Response:
    return api(
        client,
        "POST",
        f"{base_url}/api/approvals/{assignment_id}/decision",
        user,
        {"action": action, "comment": comment},
        expected,
    )


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("SMOKE_PAPERLESS_DOCUMENT_ID", "1"))
    clients = {
        "manager": login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]),
        "approver1": login(base_url, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"]),
        "approver2": login(base_url, "approver2", os.environ["TEST_APPROVER_2_PASSWORD"]),
        "approver3": login(base_url, "approver3", os.environ["TEST_APPROVER_3_PASSWORD"]),
    }
    try:
        users = {
            name: response_json(client.get(f"{base_url}/api/auth/me"), f"{name} /me")
            for name, client in clients.items()
        }
        require("QUEUE_MANAGER" in users["manager"]["roles"], "Manager role missing")
        for name in ("approver1", "approver2", "approver3"):
            require("APPROVER" in users[name]["roles"], f"{name} role missing")
        subjects = {name: user["subject"] for name, user in users.items()}
        manager = clients["manager"]
        manager_user = users["manager"]
        invoices = response_json(manager.get(f"{base_url}/api/invoices"), "invoice list")
        row = next((item for item in invoices if item["paperless_document_id"] == document_id), None)
        require(row is not None, f"Paperless document {document_id} not found")
        invoice_id = row["id"]

        centre_rows = response_json(
            manager.get(f"{base_url}/api/cost-centers?include_inactive=true"),
            "cost centers",
        )
        centres = {item["code"]: item for item in centre_rows if item["active"]}
        require({"100", "200", "300"} <= set(centres), "Synthetic cost centers are missing")

        current = detail(manager, base_url, invoice_id)
        changes: dict[str, Any] = {}
        if current["data"].get("bank_account") != "0000000000":
            changes["bank_account"] = "0000000000"
        if current["data"].get("bank_code") != "0000":
            changes["bank_code"] = "0000"
        if changes:
            current = response_json(
                api(
                    manager,
                    "PATCH",
                    f"{base_url}/api/invoices/{invoice_id}",
                    manager_user,
                    {"changes": changes, "comment": "Ruční kontrola platebních údajů podle PDF"},
                ),
                "correct bank details",
            )
        require(Decimal(str(current["data"]["total_amount"])) == Decimal("1210.00"), "Unexpected invoice total")

        # Missing original review and missing approver are independently rejected.
        api(
            manager,
            "PUT",
            f"{base_url}/api/invoices/{invoice_id}/allocations",
            manager_user,
            {"allocations": [{"cost_center_id": centres["200"]["id"], "amount": "1210.00"}]},
        )
        original_missing = api(
            manager, "POST", f"{base_url}/api/invoices/{invoice_id}/submit", manager_user, expected=409
        )
        require("Originál" in original_missing.text, "Missing original review was not reported")
        api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/confirm-original", manager_user)
        approver_missing = api(
            manager, "POST", f"{base_url}/api/invoices/{invoice_id}/submit", manager_user, expected=409
        )
        require("nemá schvalovatele" in approver_missing.text, "Missing approver was not reported")

        # Mismatch can be saved, is visible as ALLOCATION_TOTAL_MISMATCH, and blocks submit.
        mismatch = setup(
            manager, base_url, manager_user, invoice_id, centres, subjects,
            first_amount="700.00", second_amount="500.00",
        )
        require(
            any(v["code"] == "ALLOCATION_TOTAL_MISMATCH" and v["severity"] == "BLOCKING_ERROR" for v in mismatch["validations"]),
            "Allocation mismatch validation missing",
        )
        mismatch_submit = api(
            manager, "POST", f"{base_url}/api/invoices/{invoice_id}/submit", manager_user, expected=409
        )
        require("Rozúčtování" in mismatch_submit.text or "blokující" in mismatch_submit.text, "Mismatch did not block submit")

        # Blocking accounting validation is recalculated by submit.
        setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        api(
            manager,
            "PATCH",
            f"{base_url}/api/invoices/{invoice_id}",
            manager_user,
            {"changes": {"currency": "CROWNS"}, "comment": "Negative blocking validation test"},
        )
        api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/confirm-original", manager_user)
        blocking = api(
            manager, "POST", f"{base_url}/api/invoices/{invoice_id}/submit", manager_user, expected=409
        )
        require("blokující" in blocking.text, "Blocking validation did not prevent submit")
        api(
            manager,
            "PATCH",
            f"{base_url}/api/invoices/{invoice_id}",
            manager_user,
            {"changes": {"currency": "CZK"}, "comment": "Restore valid ISO currency"},
        )

        # RETURN, required comment, and new revision on resubmit.
        setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        confirm_and_submit(manager, base_url, manager_user, invoice_id)
        first_task = tasks(clients["approver1"], base_url)[0]
        decide(clients["approver1"], base_url, users["approver1"], first_task["id"], "RETURN", expected=422)
        decide(
            clients["approver1"], base_url, users["approver1"], first_task["id"], "RETURN", "Doplňte kontrolu",
        )
        returned = detail(manager, base_url, invoice_id)
        require(returned["status"] == "RETURNED", "RETURN did not return the whole invoice")
        return_tag = wait_for_tag(manager, base_url, invoice_id, "Kontrola správce")
        returned_revision = returned["current_revision_number"]
        resubmitted = response_json(
            api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/submit", manager_user),
            "resubmit returned invoice",
        )
        require(resubmitted["current_revision_number"] == returned_revision + 1, "RETURN resubmit did not create revision")

        # REJECT stops every assignment; REOPEN preserves and invalidates history.
        second_task = tasks(clients["approver2"], base_url)[0]
        decide(clients["approver2"], base_url, users["approver2"], second_task["id"], "REJECT", expected=422)
        decide(
            clients["approver2"], base_url, users["approver2"], second_task["id"], "REJECT", "Plnění odmítnuto",
        )
        rejected = detail(manager, base_url, invoice_id)
        require(rejected["status"] == "REJECTED", "REJECT did not reject the invoice")
        reject_tag = wait_for_tag(manager, base_url, invoice_id, "Zamítnuto")
        stale_task = tasks(clients["approver1"], base_url)
        require(not stale_task, "Approver still sees a task after REJECT")
        decide(clients["approver1"], base_url, users["approver1"], first_task["id"], "APPROVE", expected=409)
        before_reopen_audit = audit(manager, base_url, invoice_id)
        reopened = response_json(
            api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/reopen", manager_user),
            "reopen rejected invoice",
        )
        require(reopened["status"] == "NEEDS_REVIEW", "REOPEN did not return invoice to review")
        after_reopen_audit = audit(manager, base_url, invoice_id)
        require(len(after_reopen_audit) > len(before_reopen_audit), "REOPEN history was not appended")

        invalidations: dict[str, bool] = {}

        def invalidation_count() -> int:
            return sum(event["event_type"] == "APPROVAL_INVALIDATED" for event in audit(manager, base_url, invoice_id))

        # Amount change after an approval.
        setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        confirm_and_submit(manager, base_url, manager_user, invoice_id)
        task = tasks(clients["approver1"], base_url)[0]
        decide(clients["approver1"], base_url, users["approver1"], task["id"], "APPROVE")
        before = invalidation_count()
        changed = setup(
            manager, base_url, manager_user, invoice_id, centres, subjects,
            first_amount="701.00", second_amount="509.00",
        )
        invalidations["amount"] = changed["status"] == "NEEDS_REVIEW" and invalidation_count() > before

        # Cost center change after an approval.
        setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        confirm_and_submit(manager, base_url, manager_user, invoice_id)
        task = tasks(clients["approver1"], base_url)[0]
        decide(clients["approver1"], base_url, users["approver1"], task["id"], "APPROVE")
        before = invalidation_count()
        changed = setup(
            manager, base_url, manager_user, invoice_id, centres, subjects,
            first_code="100", first_amount="700.00", second_code="300", second_amount="510.00",
        )
        invalidations["cost_center"] = changed["status"] == "NEEDS_REVIEW" and invalidation_count() > before

        # Approver list change after an approval.
        current = setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        confirm_and_submit(manager, base_url, manager_user, invoice_id)
        task = tasks(clients["approver1"], base_url)[0]
        decide(clients["approver1"], base_url, users["approver1"], task["id"], "APPROVE")
        before = invalidation_count()
        allocation_200 = next(row for row in current["allocations"] if row["cost_center"]["code"] == "200")
        changed = response_json(
            api(
                manager,
                "PUT",
                f"{base_url}/api/invoices/{invoice_id}/allocations/{allocation_200['id']}/approvers",
                manager_user,
                {"approver_subjects": [subjects["approver2"]]},
            ),
            "change approver",
        )
        invalidations["approver"] = changed["status"] == "NEEDS_REVIEW" and invalidation_count() > before

        # Significant invoice field change after an approval.
        setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        confirm_and_submit(manager, base_url, manager_user, invoice_id)
        task = tasks(clients["approver1"], base_url)[0]
        decide(clients["approver1"], base_url, users["approver1"], task["id"], "APPROVE")
        before = invalidation_count()
        old_number = detail(manager, base_url, invoice_id)["data"].get("invoice_number") or "TEST-2026-0001"
        changed = response_json(
            api(
                manager,
                "PATCH",
                f"{base_url}/api/invoices/{invoice_id}",
                manager_user,
                {"changes": {"invoice_number": f"{old_number}-E"}, "comment": "Stage E invalidation test"},
            ),
            "change invoice field",
        )
        invalidations["invoice_field"] = changed["status"] == "NEEDS_REVIEW" and invalidation_count() > before
        require(all(invalidations.values()), "One or more significant changes did not invalidate approval")

        # Actual concurrent decisions on different assignments, serialized by invoice lock.
        setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        confirm_and_submit(manager, base_url, manager_user, invoice_id)
        concurrent_tasks = {
            name: tasks(clients[name], base_url)[0] for name in ("approver2", "approver3")
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    decide,
                    clients[name],
                    base_url,
                    users[name],
                    concurrent_tasks[name]["id"],
                    "APPROVE",
                )
                for name in ("approver2", "approver3")
            ]
            for future in futures:
                require(future.result().status_code == 200, "Concurrent APPROVE failed")
        require(detail(manager, base_url, invoice_id)["status"] == "AWAITING_APPROVAL", "Concurrent partial approval finalized early")
        task = tasks(clients["approver1"], base_url)[0]
        decide(clients["approver1"], base_url, users["approver1"], task["id"], "APPROVE")
        require(detail(manager, base_url, invoice_id)["status"] == "APPROVED", "Concurrent cycle did not finalize")

        # Final deterministic flow and idempotent double APPROVE.
        final_detail = setup(manager, base_url, manager_user, invoice_id, centres, subjects)
        final_detail = confirm_and_submit(manager, base_url, manager_user, invoice_id)
        final_assignments = active_assignments(final_detail)
        require(len(final_assignments) == 3, "Expected three final assignments")
        flow: list[str] = [final_detail["status"]]
        task1 = tasks(clients["approver1"], base_url)[0]
        first_approve = response_json(
            decide(clients["approver1"], base_url, users["approver1"], task1["id"], "APPROVE"),
            "approver1 approve",
        )
        repeated = response_json(
            decide(clients["approver1"], base_url, users["approver1"], task1["id"], "APPROVE"),
            "repeated approve",
        )
        require(first_approve["id"] == repeated["id"], "Repeated APPROVE was not idempotent")
        flow.append(detail(manager, base_url, invoice_id)["status"])
        task2 = tasks(clients["approver2"], base_url)[0]
        decide(clients["approver2"], base_url, users["approver2"], task2["id"], "APPROVE")
        flow.append(detail(manager, base_url, invoice_id)["status"])
        task3 = tasks(clients["approver3"], base_url)[0]
        decide(clients["approver3"], base_url, users["approver3"], task3["id"], "APPROVE")
        final_detail = detail(manager, base_url, invoice_id)
        flow.append(final_detail["status"])
        require(flow == ["AWAITING_APPROVAL", "AWAITING_APPROVAL", "AWAITING_APPROVAL", "APPROVED"], "Final approval state sequence is invalid")
        approved_tag = wait_for_tag(manager, base_url, invoice_id, "Schváleno")

        # Authorization: foreign assignment and manager API remain protected.
        foreign_http = decide(
            clients["approver2"], base_url, users["approver2"], task1["id"], "APPROVE", expected=403
        ).status_code
        manager_list_http = clients["approver1"].get(f"{base_url}/api/invoices").status_code
        require(manager_list_http == 403, "Approver can access manager invoice list")

        final_audit = audit(manager, base_url, invoice_id)
        print(json.dumps({
            "app_url": base_url,
            "paperless_document_id": document_id,
            "invoice_id": invoice_id,
            "migration_expected": "0004",
            "cost_centers": len(centre_rows),
            "test_allocations": [
                {"cost_center": row["cost_center"]["code"], "amount": str(row["amount"])}
                for row in final_detail["allocations"]
            ],
            "invoice_total": str(final_detail["allocation_summary"]["invoice_total"]),
            "allocated_total": str(final_detail["allocation_summary"]["allocated"]),
            "remaining": str(final_detail["allocation_summary"]["remaining"]),
            "assignments": [
                {"approver": row["approver_subject"], "cost_center": row["cost_center"], "amount": str(row["amount"])}
                for row in final_assignments
            ],
            "original_review_confirmed": final_detail["original_review_confirmed"],
            "workflow_after_approvals": flow,
            "return_test": returned["status"],
            "reject_test": rejected["status"],
            "reopen_test": reopened["status"],
            "invalidations": invalidations,
            "foreign_assignment_http": foreign_http,
            "approver_manager_api_http": manager_list_http,
            "idempotent_decision_id": first_approve["id"],
            "concurrent_approvals": "OK",
            "paperless_tags": {"returned": return_tag, "rejected": reject_tag, "approved": approved_tag},
            "final_status": final_detail["status"],
            "audit_events": len(final_audit),
            "audit_types": sorted({event["event_type"] for event in final_audit}),
        }, ensure_ascii=False, indent=2))
    finally:
        for client in clients.values():
            client.close()


if __name__ == "__main__":
    main()
