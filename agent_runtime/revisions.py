from .errors import ApplicationError, invalid_input
from .hashing import private_digest


def revision_for(instance) -> str:
    updated_at = getattr(instance, "updated_at", None)
    if updated_at is None:
        raise TypeError("Revisioned resources must define updated_at")
    meta = getattr(instance, "_meta", None)
    primary_key = getattr(instance, "pk", None)
    if meta is None or primary_key is None:
        raise TypeError("Revisioned resources must be persisted model instances")
    return private_digest(f"{meta.label_lower}:{primary_key}:{updated_at.isoformat()}")


def require_revision(instance, expected_revision: str | None) -> str:
    if not expected_revision:
        raise invalid_input(
            "expected_revision is required for this operation.",
            fields={"expected_revision": ["This field is required."]},
        )
    actual_revision = revision_for(instance)
    if expected_revision != actual_revision:
        raise ApplicationError(
            code="revision_conflict",
            message="The resource changed after it was read.",
            details={
                "expected_revision": expected_revision,
                "current_revision": actual_revision,
            },
        )
    return actual_revision


def require_creation_revision(expected_revision: str | None) -> None:
    if expected_revision is not None:
        raise ApplicationError(
            code="revision_conflict",
            message="The resource was created after it was read.",
            details={
                "expected_revision": expected_revision,
                "current_revision": None,
            },
        )
