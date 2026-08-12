from dataclasses import dataclass
from typing import Protocol

from .errors import ApplicationError

SITE_MANAGEMENT = "site_management"
TRAFFIC_ANALYTICS = "traffic_analytics"
BOT_ANALYTICS = "bot_analytics"
PRODUCT_MEASUREMENT = "product_measurement"
TRACKING_SETUP = "tracking_setup"
GLOBAL_RESOURCE_ACCESS = "global_resource_access"

CAPABILITY_CATALOG = (
    SITE_MANAGEMENT,
    TRAFFIC_ANALYTICS,
    BOT_ANALYTICS,
    PRODUCT_MEASUREMENT,
    TRACKING_SETUP,
    GLOBAL_RESOURCE_ACCESS,
)
BASE_CAPABILITIES = frozenset(CAPABILITY_CATALOG[:-1])


@dataclass(frozen=True, slots=True)
class CapabilityEvaluation:
    available: frozenset[str]

    def has(self, capability: str) -> bool:
        return capability in self.available

    def require(self, capability: str) -> None:
        if not self.has(capability):
            raise ApplicationError(
                code="feature_unavailable",
                message="The required capability is not available.",
                details={"capability": capability},
            )

    def serialize(self) -> list[dict[str, object]]:
        return [
            {"name": name, "available": name in self.available}
            for name in CAPABILITY_CATALOG
        ]


class CapabilityEvaluator(Protocol):
    def evaluate(self, user) -> CapabilityEvaluation: ...


class DefaultCapabilityEvaluator:
    """MVP entitlement adapter; plan names never cross this boundary."""

    def evaluate(self, user) -> CapabilityEvaluation:
        available = set(BASE_CAPABILITIES)
        if user.is_superuser:
            available.add(GLOBAL_RESOURCE_ACCESS)
        return CapabilityEvaluation(frozenset(available))
