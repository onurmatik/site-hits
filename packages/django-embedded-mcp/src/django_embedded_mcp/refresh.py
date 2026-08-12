"""Model-neutral refresh-family replay and exact-binding policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RefreshFamilyDecisionCode(StrEnum):
    """Stable machine-readable outcomes for refresh-family decisions."""

    ACTIVE = "active"
    CLAIMED = "claimed"
    FAMILY_REVOKED = "family_revoked"
    FAMILY_EXPIRED = "family_expired"
    MEMBER_CONSUMED = "member_consumed"
    CONCURRENT_REPLAY = "concurrent_replay"
    USER_INACTIVE = "user_inactive"
    CLIENT_INACTIVE = "client_inactive"
    CLIENT_MISMATCH = "client_mismatch"
    FAMILY_USER_MISMATCH = "family_user_mismatch"
    FAMILY_CLIENT_MISMATCH = "family_client_mismatch"
    FAMILY_IDENTITY_MISMATCH = "family_identity_mismatch"
    REQUEST_RESOURCE_MISMATCH = "request_resource_mismatch"
    MEMBER_RESOURCE_MISMATCH = "member_resource_mismatch"
    FAMILY_RESOURCE_MISMATCH = "family_resource_mismatch"


@dataclass(frozen=True)
class RefreshFamilyDecision:
    """One policy result; persistence adapters execute the requested transition."""

    code: RefreshFamilyDecisionCode
    rotation_allowed: bool
    revoke_family: bool
    replay_detected: bool = False


@dataclass(frozen=True)
class RefreshFamilyState:
    """Persistence-neutral state of the durable refresh-family lock row."""

    family_id: object
    user_id: object
    client_id: object
    resource: str
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class RefreshMemberState:
    """Persistence-neutral state of the presented rotating refresh member."""

    user_id: object
    client_id: object
    family_id: object
    family_mirror_id: object
    resources: tuple[str, ...]
    consumed_at: datetime | None


_ACTIVE = RefreshFamilyDecision(
    code=RefreshFamilyDecisionCode.ACTIVE,
    rotation_allowed=True,
    revoke_family=False,
)
_CLAIMED = RefreshFamilyDecision(
    code=RefreshFamilyDecisionCode.CLAIMED,
    rotation_allowed=True,
    revoke_family=False,
)


def _rejection(
    code: RefreshFamilyDecisionCode,
    *,
    replay_detected: bool = False,
) -> RefreshFamilyDecision:
    return RefreshFamilyDecision(
        code=code,
        rotation_allowed=False,
        revoke_family=True,
        replay_detected=replay_detected,
    )


class RefreshFamilyPolicy:
    """Own refresh eligibility, replay, and exact family-binding decisions."""

    def __init__(self, *, expected_resource: str):
        if not isinstance(expected_resource, str) or not expected_resource:
            raise ValueError("expected_resource must be a non-empty string.")
        self.expected_resource = expected_resource

    def evaluate_family(
        self,
        *,
        family: RefreshFamilyState,
        requested_resources: tuple[str, ...],
        now: datetime,
    ) -> RefreshFamilyDecision:
        """Classify the durable family before a member may rotate or be issued."""

        if family.revoked_at is not None:
            return _rejection(RefreshFamilyDecisionCode.FAMILY_REVOKED)
        if family.expires_at <= now:
            return _rejection(RefreshFamilyDecisionCode.FAMILY_EXPIRED)
        if requested_resources != (self.expected_resource,):
            return _rejection(RefreshFamilyDecisionCode.REQUEST_RESOURCE_MISMATCH)
        if family.resource != self.expected_resource:
            return _rejection(RefreshFamilyDecisionCode.FAMILY_RESOURCE_MISMATCH)
        return _ACTIVE

    def evaluate_rotation(
        self,
        *,
        family: RefreshFamilyState,
        member: RefreshMemberState,
        presented_client_id: object,
        requested_resources: tuple[str, ...],
        user_active: bool,
        client_active: bool,
        now: datetime,
    ) -> RefreshFamilyDecision:
        """Decide whether one presented member is eligible for one rotation."""

        family_decision = self.evaluate_family(
            family=family,
            requested_resources=requested_resources,
            now=now,
        )
        if not family_decision.rotation_allowed:
            return family_decision
        if member.consumed_at is not None:
            return _rejection(
                RefreshFamilyDecisionCode.MEMBER_CONSUMED,
                replay_detected=True,
            )
        if not user_active:
            return _rejection(RefreshFamilyDecisionCode.USER_INACTIVE)
        if not client_active:
            return _rejection(RefreshFamilyDecisionCode.CLIENT_INACTIVE)
        if member.client_id != presented_client_id:
            return _rejection(RefreshFamilyDecisionCode.CLIENT_MISMATCH)
        binding_decision = self.evaluate_member_binding(
            family=family,
            member=member,
        )
        if not binding_decision.rotation_allowed:
            return binding_decision
        return _ACTIVE

    def evaluate_member_binding(
        self,
        *,
        family: RefreshFamilyState,
        member: RefreshMemberState,
    ) -> RefreshFamilyDecision:
        """Validate one persisted member's immutable family identity."""

        if family.user_id != member.user_id:
            return _rejection(RefreshFamilyDecisionCode.FAMILY_USER_MISMATCH)
        if family.client_id != member.client_id:
            return _rejection(RefreshFamilyDecisionCode.FAMILY_CLIENT_MISMATCH)
        if member.family_id != family.family_id or member.family_mirror_id != family.family_id:
            return _rejection(RefreshFamilyDecisionCode.FAMILY_IDENTITY_MISMATCH)
        if member.resources != (self.expected_resource,):
            return _rejection(RefreshFamilyDecisionCode.MEMBER_RESOURCE_MISMATCH)
        if family.resource != self.expected_resource:
            return _rejection(RefreshFamilyDecisionCode.FAMILY_RESOURCE_MISMATCH)
        return _ACTIVE

    @staticmethod
    def family_is_expired(*, expires_at: datetime, now: datetime) -> bool:
        """Classify the absolute family deadline without persistence assumptions."""

        return expires_at <= now

    def evaluate_claim(self, *, claimed_rows: int) -> RefreshFamilyDecision:
        """Classify the single-winner member-consumption transition."""

        if claimed_rows == 1:
            return _CLAIMED
        return _rejection(
            RefreshFamilyDecisionCode.CONCURRENT_REPLAY,
            replay_detected=True,
        )
