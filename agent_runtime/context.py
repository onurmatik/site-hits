import re
from dataclasses import dataclass, field
from uuid import uuid4

from .errors import ApplicationError

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Authenticated identities and public correlation data for one agent call."""

    authenticated_actor_id: str
    authenticated_client_id: str
    granted_scopes: frozenset[str]
    tenant_id: str | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self):
        if not self.authenticated_actor_id.strip() or len(self.authenticated_actor_id) > 255:
            raise ValueError("authenticated_actor_id must contain 1 to 255 characters")
        if not self.authenticated_client_id.strip() or len(self.authenticated_client_id) > 512:
            raise ValueError("authenticated_client_id must contain 1 to 512 characters")
        if not isinstance(self.granted_scopes, frozenset) or not self.granted_scopes.issubset(
            {"read", "write"}
        ):
            raise ValueError("granted_scopes must be a frozenset containing only read/write")
        if self.tenant_id is not None and (
            not self.tenant_id.strip() or len(self.tenant_id) > 255
        ):
            raise ValueError("tenant_id must contain 1 to 255 characters when provided")
        if not _REQUEST_ID_PATTERN.fullmatch(self.request_id):
            raise ValueError(
                "request_id must contain 1 to 64 ASCII letters, digits, underscores, or hyphens"
            )


@dataclass(frozen=True, slots=True)
class ApprovalAssertion:
    """The agent's assertion that the current user request contains explicit intent."""

    owner: str
    action: str
    resource_id: str
    confirmed: bool


def require_agent_approval(
    approval: ApprovalAssertion | None,
    *,
    action: str,
    resource_id: str,
) -> None:
    expected = {
        "owner": "agent",
        "action": action,
        "resource_id": resource_id,
    }
    if (
        approval is None
        or approval.owner != expected["owner"]
        or approval.action != action
        or approval.resource_id != resource_id
        or approval.confirmed is not True
    ):
        raise ApplicationError(
            code="confirmation_required",
            message="Explicit user approval is required for this operation.",
            details=expected,
        )
