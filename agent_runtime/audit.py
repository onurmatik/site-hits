from analytics.models import AgentAuditEvent

from .context import RequestContext
from .hashing import canonical_json, private_digest


class AuditRecorder:
    def record(
        self,
        *,
        context: RequestContext,
        tool_name: str,
        target_resource_type: str,
        target_resource_id: str,
        authorization: dict[str, object],
        inputs: dict[str, object],
        outcome_code: str,
        idempotency_id: str = "",
        operation_id: str = "",
    ) -> AgentAuditEvent:
        return AgentAuditEvent.objects.create(
            request_id=context.request_id,
            authenticated_actor_id=context.authenticated_actor_id,
            authenticated_client_id=context.authenticated_client_id,
            tenant_id=context.tenant_id or "",
            tool_name=tool_name,
            target_resource_type=target_resource_type,
            target_resource_id=target_resource_id,
            authorization=authorization,
            input_hash=private_digest(canonical_json(inputs)),
            outcome_code=outcome_code,
            idempotency_id=idempotency_id,
            operation_id=operation_id,
        )
