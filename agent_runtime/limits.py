from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from django.db.models import QuerySet

from .errors import ApplicationError

_CAPACITY_LOCKS_GUARD = Lock()
_CAPACITY_LOCKS: dict[str, tuple[Lock, int]] = {}


@contextmanager
def actor_capacity_guard(actor_id: str) -> Iterator[None]:
    """Serialize one actor's capacity mutations within a worker process.

    The database actor-row lock remains authoritative across workers. This
    companion guard gives SQLite and other no-op ``select_for_update`` backends
    the same in-process behavior and closes the transaction before the next
    local contender evaluates capacity.
    """

    with _CAPACITY_LOCKS_GUARD:
        lock, references = _CAPACITY_LOCKS.get(actor_id, (Lock(), 0))
        _CAPACITY_LOCKS[actor_id] = (lock, references + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _CAPACITY_LOCKS_GUARD:
            current_lock, references = _CAPACITY_LOCKS[actor_id]
            if references == 1:
                del _CAPACITY_LOCKS[actor_id]
            else:
                _CAPACITY_LOCKS[actor_id] = (current_lock, references - 1)


@dataclass(frozen=True, slots=True)
class SiteLimit:
    limit: int | None = None
    period: str = "permanent"
    reset_at: None = None

    def serialize(self, *, used: int) -> dict[str, object]:
        return {
            "name": "sites",
            "used": used,
            "limit": self.limit,
            "period": self.period,
            "reset_at": self.reset_at,
        }

    def require_capacity(self, *, used: int) -> None:
        if self.limit is not None and used >= self.limit:
            details = self.serialize(used=used)
            details.pop("name")
            raise ApplicationError(
                code="capacity_reached",
                message="The site capacity has been reached.",
                details=details,
            )


class LimitEvaluator(Protocol):
    def site_limit(self, user) -> SiteLimit: ...


class DefaultLimitEvaluator:
    """MVP limit adapter; entitlement plan names remain outside the agent Contract."""

    def site_limit(self, user) -> SiteLimit:
        return SiteLimit()


def count_owned_sites(queryset: QuerySet, user) -> int:
    """Count the resources whose capacity is consumed by this actor."""

    return queryset.filter(owner=user).count()
