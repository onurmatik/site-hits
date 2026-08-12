from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from analytics.models import AgentIdempotencyRecord

from .context import RequestContext
from .contract import idempotency_retention_days
from .errors import ApplicationError, invalid_input
from .hashing import canonical_hash, private_digest


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    value: dict[str, object]
    idempotency_id: str
    replayed: bool


class IdempotencyStore:
    def execute(
        self,
        *,
        context: RequestContext,
        tool_name: str,
        idempotency_key: str,
        canonical_input: dict[str, object],
        operation,
    ) -> IdempotencyResult:
        if not isinstance(idempotency_key, str) or len(idempotency_key.strip()) < 8:
            raise invalid_input(
                "idempotency_key must contain at least 8 characters.",
                fields={"idempotency_key": ["Use between 8 and 128 characters."]},
            )
        if len(idempotency_key) > 128:
            raise invalid_input(
                "idempotency_key is too long.",
                fields={"idempotency_key": ["Use between 8 and 128 characters."]},
            )

        key_digest = private_digest(idempotency_key)
        input_hash = canonical_hash(canonical_input)
        lookup = {
            "authenticated_actor_id": context.authenticated_actor_id,
            "authenticated_client_id": context.authenticated_client_id,
            "tool_name": tool_name,
            "key_digest": key_digest,
        }
        with transaction.atomic():
            created = False
            now = timezone.now()
            try:
                record = AgentIdempotencyRecord.objects.select_for_update().get(**lookup)
            except AgentIdempotencyRecord.DoesNotExist:
                try:
                    with transaction.atomic():
                        record = AgentIdempotencyRecord.objects.create(
                            **lookup,
                            input_hash=input_hash,
                            expires_at=now + timedelta(days=idempotency_retention_days()),
                        )
                        created = True
                except IntegrityError:
                    record = AgentIdempotencyRecord.objects.select_for_update().get(**lookup)
            if not created and record.expires_at <= now:
                record.delete()
                record = AgentIdempotencyRecord.objects.create(
                    **lookup,
                    input_hash=input_hash,
                    expires_at=now + timedelta(days=idempotency_retention_days()),
                )
                created = True
            if record.input_hash != input_hash:
                raise ApplicationError(
                    code="idempotency_conflict",
                    message="The idempotency key was already used with different input.",
                    details={"idempotency_id": str(record.idempotency_id)},
                )
            if not created and record.status == AgentIdempotencyRecord.Status.COMPLETED:
                return IdempotencyResult(
                    value=deepcopy(record.result),
                    idempotency_id=str(record.idempotency_id),
                    replayed=True,
                )
            if not created:
                raise ApplicationError(
                    code="rate_limited",
                    message="An operation with this idempotency key is still in progress.",
                    retryable=True,
                    details={
                        "idempotency_id": str(record.idempotency_id),
                        "retry_after_seconds": 1,
                    },
                )

            value = operation()
            record.status = AgentIdempotencyRecord.Status.COMPLETED
            record.result = value
            record.save(update_fields=["status", "result", "updated_at"])
            return IdempotencyResult(
                value=deepcopy(value),
                idempotency_id=str(record.idempotency_id),
                replayed=False,
            )
