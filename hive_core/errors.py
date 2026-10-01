"""Error codes, exit codes and the HiveError exception per DESIGN §16."""
from __future__ import annotations

# code -> (cli_exit, http_status)
ERROR_TABLE = {
    "INVALID_ARGUMENT": (2, 400),
    "INVALID_JSON": (2, 400),
    "SCHEMA_UNSUPPORTED": (2, 422),
    "MUTUALLY_EXCLUSIVE": (2, 400),
    "REQUIRED_ARGUMENT": (2, 400),
    "DEADLINE_INVALID": (2, 400),
    "CALLBACK_TARGET_REQUIRED": (2, 400),
    "TARGET_TYPE_REJECTED": (2, 422),
    "CHANNEL_REQUIRED": (2, 422),
    "CONTEXT_TOO_LARGE": (8, 422),
    "WORKSPACE_UNAVAILABLE": (8, 503),
    "AUTH_REQUIRED": (3, 401),
    "TOKEN_INVALID": (3, 401),
    "TOKEN_REVOKED": (3, 401),
    "FORBIDDEN": (3, 403),
    "GRANT_SCOPE_EXCEEDED": (3, 403),
    "TEST_ONLY": (3, 403),
    "NOT_FOUND": (4, 404),
    "NOT_VISIBLE": (4, 404),
    "INSTANCE_FENCED": (5, 409),
    "CLAIM_FENCED": (5, 409),
    "VERSION_CONFLICT": (5, 409),
    "TASK_SPEC_CHANGED": (5, 409),
    "IDEMPOTENCY_CONFLICT": (5, 409),
    "PRODUCER_EVENT_CONFLICT": (5, 409),
    "TASK_ALREADY_CLAIMED": (5, 409),
    "TASK_TERMINAL": (5, 409),
    "CAPACITY_EXCEEDED": (5, 409),
    "AGENT_PAUSED": (5, 409),
    "AGENT_RETIRED": (5, 409),
    "PORT_PAUSED": (5, 409),
    "PORT_RETIRED": (5, 409),
    "WAIT_EXISTS": (5, 409),
    "WAIT_CYCLE": (5, 409),
    "INVALID_DEPENDENCY": (5, 409),
    "RECLAIM_REQUIRED": (5, 409),
    "RESPONSIBILITY_BLOCKED": (5, 409),
    "OP_NOT_CANCELLABLE": (5, 409),
    "STALE_INSTANCE": (5, 409),
    "NO_LIVE_INSTANCE": (5, 409),
    "NO_BINDING": (5, 409),
    "UPSTREAM_UNSUPPORTED": (8, 422),
    "UPSTREAM_ERROR": (8, 502),
    "UPSTREAM_TIMEOUT": (7, 504),
    "HARNESS_BUSY": (8, 409),
    "RUNTIME_OUTCOME_UNKNOWN": (7, 503),
    "STORAGE_UNAVAILABLE": (8, 503),
    "TRANSPORT_UNAVAILABLE": (7, 503),
    "SERVICE_UNAVAILABLE": (8, 503),
    "HARNESS_NOT_CONFIGURED": (8, 503),
    "HARNESS_UNAVAILABLE": (8, 503),
    "NOT_IMPLEMENTED": (1, 500),
    "INTERNAL_ERROR": (1, 500),
    "COMMAND_UNKNOWN": (2, 404),
}


class HiveError(Exception):
    """Domain error with machine code; converted to error envelope."""

    def __init__(self, code: str, message: str, details: dict | None = None,
                 retryable: bool | None = None):
        super().__init__(message)
        self.code = code if code in ERROR_TABLE else "INTERNAL_ERROR"
        self.message = message
        self.details = details or {}
        default_retry = self.code in (
            "RUNTIME_OUTCOME_UNKNOWN", "TRANSPORT_UNAVAILABLE", "STORAGE_UNAVAILABLE",
            "HARNESS_BUSY", "SERVICE_UNAVAILABLE", "HARNESS_UNAVAILABLE",
            "UPSTREAM_TIMEOUT",
        )
        self.retryable = default_retry if retryable is None else retryable

    @property
    def exit_code(self) -> int:
        return ERROR_TABLE[self.code][0]

    @property
    def http_status(self) -> int:
        return ERROR_TABLE[self.code][1]

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "details": self.details, "retryable": self.retryable}


def bad(msg: str, code: str = "INVALID_ARGUMENT", details: dict | None = None) -> HiveError:
    return HiveError(code, msg, details)
