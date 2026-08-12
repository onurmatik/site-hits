from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.utils.text import slugify

from analytics.models import ActivationDefinition, ProductEventDefinition
from analytics.product_reporting import product_metrics
from analytics.reporting import bot_traffic, breakdown, overview, site_overviews, timeseries
from dashboard.product_tracking import product_tracking_agent_instruction
from websites.models import TrackedSite

from .audit import AuditRecorder
from .capabilities import (
    BOT_ANALYTICS,
    GLOBAL_RESOURCE_ACCESS,
    PRODUCT_MEASUREMENT,
    SITE_MANAGEMENT,
    TRACKING_SETUP,
    TRAFFIC_ANALYTICS,
    CapabilityEvaluator,
    DefaultCapabilityEvaluator,
)
from .context import ApprovalAssertion, RequestContext, require_agent_approval
from .contract import get_tool_contract, validate_tool_input, validate_tool_output
from .errors import ApplicationError, invalid_input
from .idempotency import IdempotencyStore
from .limits import (
    DefaultLimitEvaluator,
    LimitEvaluator,
    actor_capacity_guard,
    count_owned_sites,
)
from .revisions import require_creation_revision, require_revision, revision_for

AUTHENTICATED_ACTOR_OWNERSHIP = "authenticated_actor_only"


class SiteHitsService:
    """Canonical product operations for all agent transports."""

    def __init__(
        self,
        context: RequestContext,
        *,
        capability_evaluator: CapabilityEvaluator | None = None,
        audit_recorder: AuditRecorder | None = None,
        idempotency_store: IdempotencyStore | None = None,
        limit_evaluator: LimitEvaluator | None = None,
        integration_status_provider: Callable[[str | None], dict[str, object]] | None = None,
    ):
        self.context = context
        self._capability_evaluator = capability_evaluator or DefaultCapabilityEvaluator()
        self._audit = audit_recorder or AuditRecorder()
        self._idempotency = idempotency_store or IdempotencyStore()
        self._limit_evaluator = limit_evaluator or DefaultLimitEvaluator()
        self._integration_status_provider = integration_status_provider
        self._user_cache = None
        self._authorization_decision: dict[str, object] | None = None
        self._capability_decision = None

    @property
    def user(self):
        if self._user_cache is None:
            user_model = get_user_model()
            try:
                self._user_cache = user_model.objects.get(
                    pk=self.context.authenticated_actor_id,
                    is_active=True,
                )
            except (user_model.DoesNotExist, ValueError) as exc:
                raise ApplicationError(
                    code="permission_denied",
                    message="The authenticated actor is unavailable.",
                ) from exc
        return self._user_cache

    @property
    def capabilities(self):
        if self._capability_decision is not None:
            return self._capability_decision
        return self._capability_evaluator.evaluate(self.user)

    @staticmethod
    def _approval_input(approval: ApprovalAssertion | None) -> dict[str, object] | None:
        if approval is None:
            return None
        return {
            "owner": approval.owner,
            "action": approval.action,
            "resource_id": approval.resource_id,
            "confirmed": approval.confirmed,
        }

    def _require_approval(self, tool_name, approval, *, resource_id):
        spec = get_tool_contract(tool_name)
        if not spec.approval_required or spec.approval_action is None:
            raise RuntimeError(f"Runtime approval drift for {tool_name}.")
        action = spec.approval_action
        allowed = bool(
            approval is not None
            and approval.owner == "agent"
            and approval.action == action
            and approval.resource_id == resource_id
            and approval.confirmed is True
        )
        if self._authorization_decision is not None:
            self._authorization_decision["approval_allowed"] = allowed
        require_agent_approval(approval, action=action, resource_id=resource_id)

    def _run(
        self,
        *,
        tool_name: str,
        capability: str | None,
        target_resource_type: str,
        target_resource_id: str | Callable[[], str],
        inputs: dict[str, object],
        operation: Callable[[], Any],
        idempotency_id: str | Callable[[], str] = "",
    ):
        spec = get_tool_contract(tool_name)
        if capability != spec.required_capability:
            raise RuntimeError(f"Runtime capability drift for {tool_name}.")
        if target_resource_type != spec.resource_type:
            raise RuntimeError(f"Runtime resource type drift for {tool_name}.")
        ownership = spec.ownership
        granted_scopes = sorted(self.context.granted_scopes)
        approval_input = inputs.get("approval")
        authorization: dict[str, object] = {
            "authentication_required": True,
            "authenticated": False,
            "required_scopes": list(spec.required_scopes),
            "granted_scopes": granted_scopes,
            "scope_allowed": set(spec.required_scopes).issubset(self.context.granted_scopes),
            "capability": spec.required_capability,
            "capability_allowed": "not_evaluated",
            "ownership": ownership,
            "ownership_allowed": "not_evaluated",
            "limit_applicable": spec.limit_name is not None,
            "limit_name": spec.limit_name,
            "limit_allowed": "not_evaluated" if spec.limit_name else True,
            "limit_details": {},
            "approval_required": spec.approval_required,
            "approval_confirmed": bool(
                isinstance(approval_input, dict) and approval_input.get("confirmed") is True
            ),
            "approval_allowed": "not_evaluated" if spec.approval_required else True,
        }
        try:
            with transaction.atomic():
                self._authorization_decision = authorization
                user = self.user
                authorization["authenticated"] = True
                if not authorization["scope_allowed"]:
                    # Proper transports reject this before service dispatch with their
                    # protocol-native challenge. Refuse mutation here as a defense-in-depth
                    # invariant without redefining missing-scope as an application error.
                    raise RuntimeError("Tool was dispatched without its required scopes.")
                validate_tool_input(spec, inputs)
                self._capability_decision = self._capability_evaluator.evaluate(user)
                authorization["capability_allowed"] = (
                    capability is None or self.capabilities.has(capability)
                )
                if capability is not None:
                    self.capabilities.require(capability)
                if ownership == AUTHENTICATED_ACTOR_OWNERSHIP:
                    authorization["ownership_allowed"] = True
                result = operation()
                validate_tool_output(spec, result)
                self._audit.record(
                    context=self.context,
                    tool_name=tool_name,
                    target_resource_type=target_resource_type,
                    target_resource_id=(
                        target_resource_id()
                        if callable(target_resource_id)
                        else target_resource_id
                    ),
                    authorization=authorization,
                    inputs=inputs,
                    outcome_code="success",
                    idempotency_id=(
                        idempotency_id() if callable(idempotency_id) else idempotency_id
                    ),
                )
        except ApplicationError as original_error:
            error = original_error
            if error.code not in spec.declared_errors:
                error = ApplicationError(
                    code="internal_error",
                    message="The operation could not be completed.",
                )
            audit_id = idempotency_id() if callable(idempotency_id) else idempotency_id
            if not audit_id:
                audit_id = str(error.details.get("idempotency_id", ""))
            self._audit.record(
                context=self.context,
                tool_name=tool_name,
                target_resource_type=target_resource_type,
                target_resource_id=(
                    target_resource_id() if callable(target_resource_id) else target_resource_id
                ),
                authorization=authorization,
                inputs=inputs,
                outcome_code=error.code,
                idempotency_id=audit_id,
            )
            if error is not original_error:
                raise error from original_error
            raise
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                fields = getattr(exc, "message_dict", None)
                message = "; ".join(exc.messages)
            else:
                fields = None
                message = str(exc)
            error = invalid_input(message, fields=fields)
            self._audit.record(
                context=self.context,
                tool_name=tool_name,
                target_resource_type=target_resource_type,
                target_resource_id=(
                    target_resource_id() if callable(target_resource_id) else target_resource_id
                ),
                authorization=authorization,
                inputs=inputs,
                outcome_code=error.code,
                idempotency_id=(idempotency_id() if callable(idempotency_id) else idempotency_id),
            )
            raise error from exc
        except Exception as exc:
            self._audit.record(
                context=self.context,
                tool_name=tool_name,
                target_resource_type=target_resource_type,
                target_resource_id=(
                    target_resource_id() if callable(target_resource_id) else target_resource_id
                ),
                authorization=authorization,
                inputs=inputs,
                outcome_code="internal_error",
                idempotency_id=(
                    idempotency_id() if callable(idempotency_id) else idempotency_id
                ),
            )
            raise ApplicationError(
                code="internal_error",
                message="The operation could not be completed.",
            ) from exc
        finally:
            self._authorization_decision = None
            self._capability_decision = None
        return result

    def _visible_sites(self, *, include_inactive: bool = False):
        if self._authorization_decision is not None:
            self._authorization_decision["ownership_allowed"] = True
        sites = TrackedSite.objects.all()
        if not include_inactive:
            sites = sites.filter(is_active=True)
        if self.capabilities.has(GLOBAL_RESOURCE_ACCESS):
            return sites
        return sites.filter(owner=self.user)

    def _site(self, site_slug: str, *, include_inactive: bool = True, lock: bool = False):
        sites = self._visible_sites(include_inactive=include_inactive)
        if lock:
            sites = sites.select_for_update()
        try:
            site = sites.get(slug=site_slug)
            if self._authorization_decision is not None:
                self._authorization_decision["ownership_allowed"] = True
            return site
        except TrackedSite.DoesNotExist as exc:
            if self._authorization_decision is not None:
                self._authorization_decision["ownership_allowed"] = not TrackedSite.objects.filter(
                    slug=site_slug
                ).exists()
            raise ApplicationError(
                code="resource_not_found",
                message="The requested resource was not found.",
                details={"resource_type": "site"},
            ) from exc

    @staticmethod
    def _serialize_site(site: TrackedSite) -> dict[str, object]:
        return {
            "slug": site.slug,
            "name": site.name,
            "allowed_domains": site.allowed_domains,
            "timezone": site.timezone,
            "is_active": site.is_active,
            "created_at": site.created_at.isoformat(),
            "updated_at": site.updated_at.isoformat(),
            "revision": revision_for(site),
        }

    @staticmethod
    def _serialize_event(definition: ProductEventDefinition) -> dict[str, object]:
        return {
            "event_name": definition.event_name,
            "display_name": definition.display_name,
            "description": definition.description,
            "aggregation": definition.aggregation,
            "unit": definition.unit,
            "created_at": definition.created_at.isoformat(),
            "updated_at": definition.updated_at.isoformat(),
            "revision": revision_for(definition),
        }

    def get_account_capabilities(self) -> dict[str, object]:
        def operation():
            used_sites = count_owned_sites(TrackedSite.objects.all(), self.user)
            site_limit = self._limit_evaluator.site_limit(self.user)
            return {
                "capabilities": self.capabilities.serialize(),
                "limits": [site_limit.serialize(used=used_sites)],
            }

        return self._run(
            tool_name="get_account_capabilities",
            capability=None,
            target_resource_type="account",
            target_resource_id=self.context.authenticated_actor_id,
            inputs={},
            operation=operation,
        )

    def list_sites(self, *, include_inactive: bool = False) -> dict[str, object]:
        return self._run(
            tool_name="list_sites",
            capability=SITE_MANAGEMENT,
            target_resource_type="site_collection",
            target_resource_id="",
            inputs={"include_inactive": include_inactive},
            operation=lambda: {
                "sites": [
                    self._serialize_site(site)
                    for site in self._visible_sites(include_inactive=include_inactive)
                ]
            },
        )

    def get_site(self, *, site_slug: str) -> dict[str, object]:
        return self._run(
            tool_name="get_site",
            capability=SITE_MANAGEMENT,
            target_resource_type="site",
            target_resource_id=site_slug,
            inputs={"site_slug": site_slug},
            operation=lambda: self._serialize_site(self._site(site_slug)),
        )

    @staticmethod
    def _unique_site_slug(name: str) -> str:
        base = slugify(name)[:70] or "website"
        slug = base
        suffix = 2
        while TrackedSite.objects.filter(slug=slug).exists():
            slug = f"{base[: 79 - len(str(suffix))]}-{suffix}"
            suffix += 1
        return slug

    def create_site(
        self,
        *,
        name: str,
        allowed_domains: list[str],
        timezone: str = "Europe/Istanbul",
        idempotency_key: str,
    ) -> dict[str, object]:
        canonical_input = {
            "name": name,
            "allowed_domains": allowed_domains,
            "timezone": timezone,
        }
        inputs = {**canonical_input, "idempotency_key": idempotency_key}
        idempotency_result = {}

        def operation():
            result = self._idempotency.execute(
                context=self.context,
                tool_name="create_site",
                idempotency_key=idempotency_key,
                canonical_input=canonical_input,
                operation=lambda: self._create_site(
                    name=name,
                    allowed_domains=allowed_domains,
                    timezone=timezone,
                ),
            )
            idempotency_result["id"] = result.idempotency_id
            idempotency_result["slug"] = str(result.value.get("slug", ""))
            return result.value

        with actor_capacity_guard(self.context.authenticated_actor_id):
            return self._run(
                tool_name="create_site",
                capability=SITE_MANAGEMENT,
                target_resource_type="site",
                target_resource_id=lambda: str(idempotency_result.get("slug", "")),
                inputs=inputs,
                operation=operation,
                idempotency_id=lambda: idempotency_result.get("id", ""),
            )

    def _create_site(self, *, name, allowed_domains, timezone):
        user_model = get_user_model()
        locked_user = user_model.objects.select_for_update().get(pk=self.user.pk)
        if self._authorization_decision is not None:
            # A successful create always assigns this actor as owner; establish
            # that authorization decision before evaluating capacity.
            self._authorization_decision["ownership_allowed"] = True
        used_sites = count_owned_sites(TrackedSite.objects.all(), locked_user)
        site_limit = self._limit_evaluator.site_limit(locked_user)
        limit_details = site_limit.serialize(used=used_sites)
        limit_details.pop("name")
        if self._authorization_decision is not None:
            self._authorization_decision["limit_details"] = limit_details
            self._authorization_decision["limit_allowed"] = (
                site_limit.limit is None or used_sites < site_limit.limit
            )
        site_limit.require_capacity(used=used_sites)
        site = TrackedSite(
            owner=self.user,
            name=name,
            slug=self._unique_site_slug(name),
            allowed_domains=allowed_domains,
            timezone=timezone,
        )
        site.full_clean()
        site.save()
        return self._serialize_site(site)

    def update_site(
        self,
        *,
        site_slug: str,
        expected_revision: str,
        name: str | None = None,
        allowed_domains: list[str] | None = None,
        timezone: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, object]:
        inputs = {
            "site_slug": site_slug,
            "expected_revision": expected_revision,
        }
        inputs.update(
            {
                key: value
                for key, value in {
                    "name": name,
                    "allowed_domains": allowed_domains,
                    "timezone": timezone,
                    "is_active": is_active,
                }.items()
                if value is not None
            }
        )
        @transaction.atomic
        def operation():
            if all(value is None for value in (name, allowed_domains, timezone, is_active)):
                raise invalid_input("At least one site field must be provided.")
            site = self._site(site_slug, lock=True)
            require_revision(site, expected_revision)
            if name is not None:
                site.name = name
            if allowed_domains is not None:
                site.allowed_domains = allowed_domains
            if timezone is not None:
                site.timezone = timezone
            if is_active is not None:
                site.is_active = is_active
            site.full_clean()
            site.save()
            return self._serialize_site(site)

        return self._run(
            tool_name="update_site",
            capability=SITE_MANAGEMENT,
            target_resource_type="site",
            target_resource_id=site_slug,
            inputs=inputs,
            operation=operation,
        )

    def delete_site(
        self,
        *,
        site_slug: str,
        expected_revision: str,
        approval: ApprovalAssertion | None = None,
    ) -> dict[str, object]:
        inputs = {
            "site_slug": site_slug,
            "expected_revision": expected_revision,
        }
        if approval is not None:
            inputs["approval"] = self._approval_input(approval)

        @transaction.atomic
        def operation():
            site = self._site(site_slug, lock=True)
            self._require_approval("delete_site", approval, resource_id=site_slug)
            require_revision(site, expected_revision)
            site.delete()
            return {"deleted": True, "site_slug": site_slug}

        return self._run(
            tool_name="delete_site",
            capability=SITE_MANAGEMENT,
            target_resource_type="site",
            target_resource_id=site_slug,
            inputs=inputs,
            operation=operation,
        )

    def get_analytics_overview(self, *, site_slug="all", period="last7d"):
        return self._report(
            "get_analytics_overview",
            TRAFFIC_ANALYTICS,
            "traffic_analytics",
            site_slug,
            {"site_slug": site_slug, "period": period},
            lambda: overview(site_slug, period, sites=self._visible_sites()),
        )

    def get_sites_overview(self, *, period="last7d"):
        return self._report(
            "get_sites_overview",
            TRAFFIC_ANALYTICS,
            "traffic_analytics",
            "all",
            {"period": period},
            lambda: site_overviews(period, sites=self._visible_sites()),
        )

    def get_analytics_timeseries(self, *, site_slug="all", period="last7d", granularity="auto"):
        return self._report(
            "get_analytics_timeseries",
            TRAFFIC_ANALYTICS,
            "traffic_analytics",
            site_slug,
            {"site_slug": site_slug, "period": period, "granularity": granularity},
            lambda: timeseries(
                site_slug,
                period,
                granularity,
                sites=self._visible_sites(),
            ),
        )

    def get_analytics_breakdown(
        self,
        *,
        site_slug,
        dimension,
        period="last7d",
        limit=8,
    ):
        return self._report(
            "get_analytics_breakdown",
            TRAFFIC_ANALYTICS,
            "traffic_analytics",
            site_slug,
            {
                "site_slug": site_slug,
                "dimension": dimension,
                "period": period,
                "limit": limit,
            },
            lambda: breakdown(
                site_slug,
                period,
                dimension,
                limit=limit,
                sites=self._visible_sites(),
            ),
        )

    def get_bot_analytics(self, *, site_slug="all", period="last7d", limit=8):
        return self._report(
            "get_bot_analytics",
            BOT_ANALYTICS,
            "bot_analytics",
            site_slug,
            {"site_slug": site_slug, "period": period, "limit": limit},
            lambda: bot_traffic(
                site_slug,
                period,
                limit=limit,
                sites=self._visible_sites(),
            ),
        )

    def get_product_metrics(self, *, site_slug, period="last7d"):
        return self._report(
            "get_product_metrics",
            PRODUCT_MEASUREMENT,
            "product_analytics",
            site_slug,
            {"site_slug": site_slug, "period": period},
            lambda: product_metrics(site_slug, period, sites=self._visible_sites()),
        )

    def _report(self, tool_name, capability, resource_type, site_slug, inputs, operation):
        def authorized_operation():
            if site_slug != "all":
                self._site(site_slug, include_inactive=False)
            return operation()

        return self._run(
            tool_name=tool_name,
            capability=capability,
            target_resource_type=resource_type,
            target_resource_id=site_slug,
            inputs=inputs,
            operation=authorized_operation,
        )

    def get_measurement_config(self, *, site_slug: str) -> dict[str, object]:
        def operation():
            site = self._site(site_slug)
            definitions = ProductEventDefinition.objects.filter(site=site)
            activation = (
                ActivationDefinition.objects.filter(site=site)
                .select_related("start_event", "goal_event")
                .first()
            )
            return {
                "site_slug": site.slug,
                "events": [self._serialize_event(item) for item in definitions],
                "activation": (
                    {
                        "site_slug": site.slug,
                        "start_event": activation.start_event.event_name,
                        "goal_event": activation.goal_event.event_name,
                        "updated_at": activation.updated_at.isoformat(),
                        "revision": revision_for(activation),
                    }
                    if activation
                    else None
                ),
            }

        return self._run(
            tool_name="get_measurement_config",
            capability=PRODUCT_MEASUREMENT,
            target_resource_type="measurement_configuration",
            target_resource_id=site_slug,
            inputs={"site_slug": site_slug},
            operation=operation,
        )

    def _event(self, site, event_name, *, lock=False):
        definitions = ProductEventDefinition.objects.filter(site=site, event_name=event_name)
        if lock:
            definitions = definitions.select_for_update()
        try:
            return definitions.get()
        except ProductEventDefinition.DoesNotExist as exc:
            raise ApplicationError(
                code="resource_not_found",
                message="The requested resource was not found.",
                details={"resource_type": "measurement_event"},
            ) from exc

    def create_measurement_event(
        self,
        *,
        site_slug,
        event_name,
        display_name,
        description,
        aggregation="count",
        unit="",
    ):
        inputs = {
            "site_slug": site_slug,
            "event_name": event_name,
            "display_name": display_name,
            "description": description,
            "aggregation": aggregation,
            "unit": unit,
        }

        @transaction.atomic
        def operation():
            site = self._site(site_slug, lock=True)
            existing = ProductEventDefinition.objects.select_for_update().filter(
                site=site,
                event_name=event_name,
            ).first()
            if existing:
                requested = (display_name, description, aggregation, unit)
                current = (
                    existing.display_name,
                    existing.description,
                    existing.aggregation,
                    existing.unit,
                )
                if requested == current:
                    return {"created": False, "event": self._serialize_event(existing)}
                raise ApplicationError(
                    code="idempotency_conflict",
                    message="The event name already exists with different input.",
                    details={
                        "natural_key": {
                            "site_slug": site_slug,
                            "event_name": event_name,
                        }
                    },
                )
            definition = ProductEventDefinition(
                site=site,
                event_name=event_name,
                display_name=display_name,
                description=description,
                aggregation=aggregation,
                unit=unit,
            )
            definition.full_clean()
            definition.save()
            return {"created": True, "event": self._serialize_event(definition)}

        return self._run(
            tool_name="create_measurement_event",
            capability=PRODUCT_MEASUREMENT,
            target_resource_type="measurement_event",
            target_resource_id=f"{site_slug}/{event_name}",
            inputs=inputs,
            operation=operation,
        )

    def update_measurement_event(
        self,
        *,
        site_slug,
        event_name,
        expected_revision,
        display_name=None,
        description=None,
    ):
        inputs = {
            "site_slug": site_slug,
            "event_name": event_name,
            "expected_revision": expected_revision,
        }
        inputs.update(
            {
                key: value
                for key, value in {
                    "display_name": display_name,
                    "description": description,
                }.items()
                if value is not None
            }
        )
        @transaction.atomic
        def operation():
            if display_name is None and description is None:
                raise invalid_input("At least one measurement event field must be provided.")
            site = self._site(site_slug)
            definition = self._event(site, event_name, lock=True)
            require_revision(definition, expected_revision)
            if display_name is not None:
                definition.display_name = display_name
            if description is not None:
                definition.description = description
            definition.full_clean()
            definition.save()
            return self._serialize_event(definition)

        return self._run(
            tool_name="update_measurement_event",
            capability=PRODUCT_MEASUREMENT,
            target_resource_type="measurement_event",
            target_resource_id=f"{site_slug}/{event_name}",
            inputs=inputs,
            operation=operation,
        )

    def change_measurement_event_contract(
        self,
        *,
        site_slug,
        event_name,
        expected_revision,
        aggregation,
        unit,
        approval: ApprovalAssertion | None = None,
    ):
        resource_id = f"{site_slug}/{event_name}"
        inputs = {
            "site_slug": site_slug,
            "event_name": event_name,
            "expected_revision": expected_revision,
            "aggregation": aggregation,
            "unit": unit,
        }
        if approval is not None:
            inputs["approval"] = self._approval_input(approval)

        @transaction.atomic
        def operation():
            site = self._site(site_slug)
            definition = self._event(site, event_name, lock=True)
            self._require_approval(
                "change_measurement_event_contract",
                approval,
                resource_id=resource_id,
            )
            require_revision(definition, expected_revision)
            definition.aggregation = aggregation
            definition.unit = unit
            definition.full_clean()
            definition.save()
            return self._serialize_event(definition)

        return self._run(
            tool_name="change_measurement_event_contract",
            capability=PRODUCT_MEASUREMENT,
            target_resource_type="measurement_event",
            target_resource_id=resource_id,
            inputs=inputs,
            operation=operation,
        )

    def delete_measurement_event(
        self,
        *,
        site_slug,
        event_name,
        expected_revision,
        approval: ApprovalAssertion | None = None,
    ):
        resource_id = f"{site_slug}/{event_name}"
        inputs = {
            "site_slug": site_slug,
            "event_name": event_name,
            "expected_revision": expected_revision,
        }
        if approval is not None:
            inputs["approval"] = self._approval_input(approval)

        @transaction.atomic
        def operation():
            site = self._site(site_slug)
            definition = self._event(site, event_name, lock=True)
            self._require_approval(
                "delete_measurement_event",
                approval,
                resource_id=resource_id,
            )
            require_revision(definition, expected_revision)
            try:
                definition.delete()
            except ProtectedError as exc:
                raise ApplicationError(
                    code="referenced_resource_conflict",
                    message="The resource is referenced by an activation definition.",
                    details={"references": [{"resource_type": "activation"}]},
                ) from exc
            return {"deleted": True, "event_name": event_name}

        return self._run(
            tool_name="delete_measurement_event",
            capability=PRODUCT_MEASUREMENT,
            target_resource_type="measurement_event",
            target_resource_id=resource_id,
            inputs=inputs,
            operation=operation,
        )

    def set_activation(
        self,
        *,
        site_slug,
        start_event,
        goal_event,
        expected_revision,
    ):
        inputs = {
            "site_slug": site_slug,
            "start_event": start_event,
            "goal_event": goal_event,
            "expected_revision": expected_revision,
        }

        @transaction.atomic
        def operation():
            site = self._site(site_slug, lock=True)
            if start_event == goal_event:
                raise invalid_input("Activation start and goal events must be different.")
            activation = ActivationDefinition.objects.select_for_update().filter(site=site).first()
            if activation:
                require_revision(activation, expected_revision)
            else:
                require_creation_revision(expected_revision)
            definitions = {
                definition.event_name: definition
                for definition in ProductEventDefinition.objects.filter(
                    site=site,
                    event_name__in={start_event, goal_event},
                )
            }
            if set(definitions) != {start_event, goal_event}:
                raise ApplicationError(
                    code="resource_not_found",
                    message="Both activation events must exist.",
                    details={"resource_type": "measurement_event"},
                )
            if activation is None:
                activation = ActivationDefinition(site=site)
            activation.start_event = definitions[start_event]
            activation.goal_event = definitions[goal_event]
            activation.full_clean()
            activation.save()
            return {
                "site_slug": site.slug,
                "start_event": activation.start_event.event_name,
                "goal_event": activation.goal_event.event_name,
                "updated_at": activation.updated_at.isoformat(),
                "revision": revision_for(activation),
            }

        return self._run(
            tool_name="set_activation",
            capability=PRODUCT_MEASUREMENT,
            target_resource_type="activation",
            target_resource_id=site_slug,
            inputs=inputs,
            operation=operation,
        )

    def clear_activation(
        self,
        *,
        site_slug,
        expected_revision,
        approval: ApprovalAssertion | None = None,
    ):
        inputs = {
            "site_slug": site_slug,
            "expected_revision": expected_revision,
        }
        if approval is not None:
            inputs["approval"] = self._approval_input(approval)

        @transaction.atomic
        def operation():
            site = self._site(site_slug)
            activation = ActivationDefinition.objects.select_for_update().filter(site=site).first()
            if activation is None:
                raise ApplicationError(
                    code="resource_not_found",
                    message="The requested resource was not found.",
                    details={"resource_type": "activation"},
                )
            self._require_approval(
                "clear_activation",
                approval,
                resource_id=site_slug,
            )
            require_revision(activation, expected_revision)
            activation.delete()
            return {"site_slug": site.slug, "cleared": True}

        return self._run(
            tool_name="clear_activation",
            capability=PRODUCT_MEASUREMENT,
            target_resource_type="activation",
            target_resource_id=site_slug,
            inputs=inputs,
            operation=operation,
        )

    def get_tracking_setup(self, *, site_slug, section="all"):
        return self._tracking_setup(
            tool_name="get_tracking_setup",
            site_slug=site_slug,
            section=section,
        )

    def render_tracking_setup(self, *, site_slug, section="all"):
        return self._tracking_setup(
            tool_name="render_tracking_setup",
            site_slug=site_slug,
            section=section,
        )

    def _tracking_setup(self, *, tool_name, site_slug, section):
        inputs = {"site_slug": site_slug, "section": section}

        def operation():
            if section not in {"all", "browser", "bot", "product"}:
                raise invalid_input("Unknown tracking setup section.")
            site = self._site(site_slug)
            base_url = settings.SITEHITS_BASE_URL
            result: dict[str, object] = {
                "site_slug": site.slug,
                "site_name": site.name,
            }
            if section in {"all", "browser"}:
                result["browser"] = {
                    "snippet": (
                        f'<script defer src="{base_url}/js/script.js" '
                        f'data-site-key="{site.public_key}" '
                        f'data-api-url="{base_url}/api/events"></script>'
                    )
                }
            if section in {"all", "bot"}:
                endpoint = f"{base_url}/api/bot-events"
                result["bot"] = {
                    "environment": (
                        f"SITEHITS_BOT_ENDPOINT={endpoint}\n"
                        "SITEHITS_BOT_KEY=<set-in-server-environment>"
                    ),
                    "setup_guidance": (
                        f"Configure best-effort server-side bot event delivery to {endpoint}. "
                        "Read the key from SITEHITS_BOT_KEY and never expose or log it."
                    ),
                }
            if section in {"all", "product"}:
                environment = (
                    f"SITEHITS_EVENT_ENDPOINT={base_url}/api/server-events\n"
                    f"SITEHITS_SITE_KEY={site.public_key}\n"
                    "SITEHITS_SERVER_EVENT_KEY=<set-in-server-environment>"
                )
                result["product"] = {
                    "environment": environment,
                    "setup_guidance": product_tracking_agent_instruction(site),
                }
            serialized = str(result)
            if site.bot_key in serialized or site.server_event_key in serialized:
                raise RuntimeError("Private tracking credentials escaped redaction")
            return result

        return self._run(
            tool_name=tool_name,
            capability=TRACKING_SETUP,
            target_resource_type="tracking_setup",
            target_resource_id=site_slug,
            inputs=inputs,
            operation=operation,
        )

    def get_integration_status(self, *, skill_version: str | None = None):
        def operation():
            if self._integration_status_provider is None:
                raise RuntimeError("An integration status provider is required for this adapter.")
            return self._integration_status_provider(skill_version)

        return self._run(
            tool_name="get_integration_status",
            capability=None,
            target_resource_type="integration",
            target_resource_id=self.context.authenticated_client_id,
            inputs={"skill_version": skill_version},
            operation=operation,
        )
