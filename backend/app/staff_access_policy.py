from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


APPROVED_LEAVE_ACCESS_POLICY_VERSION = "2026-08-02-v1"

APPROVED_LEAVE_ACCESS_POLICY: dict[str, Any] = {
    "employment_status": "leave",
    "login": "blocked",
    "active_sessions": "revoke_on_transition",
    "active_websocket": "force_logout_on_transition",
    "existing_room_memberships": "retained_but_access_suspended",
    "new_room_memberships": "blocked",
    "workdesk": "blocked",
    "push": "blocked",
    "user_active_flag": "preserve_existing_value",
    "return_to_active": "restore_access_from_preserved_entitlements",
}


def approved_leave_access_policy_snapshot() -> dict[str, Any]:
    return {
        "version": APPROVED_LEAVE_ACCESS_POLICY_VERSION,
        **APPROVED_LEAVE_ACCESS_POLICY,
    }


def approved_leave_access_policy_fingerprint() -> str:
    canonical = json.dumps(
        approved_leave_access_policy_snapshot(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
