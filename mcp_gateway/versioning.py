import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from agent_runtime.contract import contract_version

SERVER_VERSION = "0.2.0"
AGENT_CONTRACT_VERSION = contract_version()
SKILL_VERSION = "1.0.0"
MINIMUM_SKILL_VERSION = "1.0.0"
PLUGIN_VERSION = "0.2.0"

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _semver_core(value):
    match = SEMVER_PATTERN.fullmatch(value or "")
    if match is None:
        raise ValueError(f"Invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups()[:3])


def validate_version_contract():
    versions = {
        "server_version": SERVER_VERSION,
        "agent_contract_version": AGENT_CONTRACT_VERSION,
        "skill_version": SKILL_VERSION,
        "minimum_skill_version": MINIMUM_SKILL_VERSION,
        "plugin_version": PLUGIN_VERSION,
    }
    try:
        parsed = {name: _semver_core(value) for name, value in versions.items()}
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from exc
    if parsed["minimum_skill_version"] > parsed["skill_version"]:
        raise ImproperlyConfigured(
            "SiteHits minimum skill version cannot exceed the latest skill version."
        )


def integration_manifest():
    return {
        "server_version": SERVER_VERSION,
        "agent_contract_version": AGENT_CONTRACT_VERSION,
        "skill_version": SKILL_VERSION,
        "minimum_skill_version": MINIMUM_SKILL_VERSION,
        "plugin_version": PLUGIN_VERSION,
        "skill_update_url": settings.SITEHITS_MCP_SKILL_UPDATE_URL,
    }


def integration_status(reported_skill_version=None):
    status = "unknown"
    upgrade_required = False
    update_available = False
    if reported_skill_version:
        try:
            reported = _semver_core(reported_skill_version)
            minimum = _semver_core(MINIMUM_SKILL_VERSION)
            latest = _semver_core(SKILL_VERSION)
        except ValueError:
            pass
        else:
            if reported < minimum:
                status = "upgrade_required"
                upgrade_required = True
                update_available = True
            elif reported < latest:
                status = "update_available"
                update_available = True
            elif reported == latest:
                status = "current"
            else:
                status = "newer_than_server"
    return {
        "server_version": SERVER_VERSION,
        "agent_contract_version": AGENT_CONTRACT_VERSION,
        "latest_skill_version": SKILL_VERSION,
        "minimum_skill_version": MINIMUM_SKILL_VERSION,
        "reported_skill_version": reported_skill_version,
        "skill_status": status,
        "upgrade_required": upgrade_required,
        "update_available": update_available,
        "skill_update_url": settings.SITEHITS_MCP_SKILL_UPDATE_URL,
    }


validate_version_contract()
