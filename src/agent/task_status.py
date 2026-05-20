"""Task status constants and helpers (delegation phase 5)."""

from __future__ import annotations

TASK_STATUS_PENDING = "pending"
TASK_STATUS_SENT = "sent"
TASK_STATUS_AWAITING_PROOF = "awaiting_proof"
TASK_STATUS_REVIEW = "review"
TASK_STATUS_DONE = "done"
TASK_STATUS_BLOCKED = "blocked"

CLOSED_STATUSES = frozenset(
    {
        TASK_STATUS_DONE,
        TASK_STATUS_BLOCKED,
        "cancelled",
        "отмена",
        "отменено",
    }
)

PROOF_REPORT_MARKER = "__TASK_PROOF_REPORT__"
PENDING_PROOF_FACT_PREFIX = "_pending_proof:"


def is_closed_status(status: str) -> bool:
    return str(status or "").strip().lower() in CLOSED_STATUSES


def normalize_status(status: str) -> str:
    return str(status or "").strip().lower() or TASK_STATUS_PENDING
