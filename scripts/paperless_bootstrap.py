"""Idempotent bootstrap executed by Paperless-ngx's own Django runtime.

This is intentionally not imported by the approval application. It configures only the
isolated test Paperless database through Paperless's ORM and writes a runtime API token
to a Docker volume; no token is printed or committed.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import Group, Permission, User
from documents.models import Tag
from rest_framework.authtoken.models import Token


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


queue_manager, _ = Group.objects.get_or_create(name="QUEUE_MANAGER")
approver, _ = Group.objects.get_or_create(name="APPROVER")

document_permissions = Permission.objects.filter(content_type__app_label="documents")
queue_manager.permissions.set(document_permissions.exclude(codename__startswith="delete_"))
approver.permissions.set(document_permissions.filter(codename__startswith="view_"))

tag_settings = (
    ("PAPERLESS_INBOX_TAG", "#1976d2", True),
    ("PAPERLESS_TAG_PROCESSING", "#7e57c2", False),
    ("PAPERLESS_TAG_QUEUE_REVIEW", "#f9a825", False),
    ("PAPERLESS_TAG_APPROVAL", "#ef6c00", False),
    ("PAPERLESS_TAG_APPROVED", "#2e7d32", False),
    ("PAPERLESS_TAG_REJECTED", "#c62828", False),
    ("PAPERLESS_TAG_POHODA_READY", "#00838f", False),
    ("PAPERLESS_TAG_EXPORTED", "#455a64", False),
    ("PAPERLESS_TAG_IMPORTED", "#37474f", False),
    ("PAPERLESS_TAG_DUPLICATE", "#6d4c41", False),
    ("PAPERLESS_TAG_IGNORED", "#757575", False),
)
for setting_name, color, is_inbox_tag in tag_settings:
    name = required(setting_name)
    tag, _ = Tag.objects.get_or_create(
        name=name,
        defaults={"color": color, "is_inbox_tag": is_inbox_tag},
    )
    changed = False
    if tag.color != color:
        tag.color = color
        changed = True
    if tag.is_inbox_tag != is_inbox_tag:
        tag.is_inbox_tag = is_inbox_tag
        changed = True
    if changed:
        tag.save(update_fields=["color", "is_inbox_tag"])

# A dedicated test-only service identity keeps the admin browser account separate.
# Superuser scope is deliberate for the isolated integration tenant so document-level
# permissions cannot hide uploaded fixtures from discovery. Production must replace it
# with a least-privileged service account.
service_username = os.getenv("PAPERLESS_SERVICE_USERNAME", "approval-api")
service_user, created = User.objects.get_or_create(username=service_username)
if created:
    service_user.set_unusable_password()
service_user.is_active = True
service_user.is_staff = False
service_user.is_superuser = True
service_user.save()

token, _ = Token.objects.get_or_create(user=service_user)
token_path = Path(required("PAPERLESS_API_TOKEN_PATH"))
token_path.parent.mkdir(parents=True, exist_ok=True)
token_path.write_text(token.key, encoding="utf-8")
token_path.chmod(0o600)

print("Paperless test groups, tags and runtime API token are ready.")
