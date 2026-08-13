from dataclasses import dataclass, field
from typing import Any

APPLICATION_ERROR_CODES = frozenset(
    {
        "capacity_reached",
        "confirmation_required",
        "feature_unavailable",
        "idempotency_conflict",
        "historical_data_unavailable",
        "internal_error",
        "invalid_input",
        "permission_denied",
        "rate_limited",
        "referenced_resource_conflict",
        "resource_not_found",
        "revision_conflict",
    }
)


@dataclass(eq=False)
class ApplicationError(Exception):
    """Stable application-layer failure, independent of any transport protocol."""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.code not in APPLICATION_ERROR_CODES:
            raise ValueError(f"Unknown application error code: {self.code}")
        Exception.__init__(self, self.message)

    def to_envelope(self, request_id: str) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": request_id,
            "details": self.details,
        }


def invalid_input(message: str, *, fields: dict[str, Any] | None = None) -> ApplicationError:
    details = {"fields": fields} if fields else {}
    return ApplicationError(code="invalid_input", message=message, details=details)
