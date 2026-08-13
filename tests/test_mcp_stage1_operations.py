import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from mcp_gateway import release
from mcp_gateway.checks import _stage1_database_errors
from mcp_gateway.management.commands.check_mcp_oauth_cleanup_health import (
    cleanup_health,
)
from mcp_gateway.management.commands.cleanup_mcp_oauth import (
    CLEANUP_RETENTION_DAYS_BY_TARGET,
)
from mcp_gateway.models import (
    MCPAccessToken,
    MCPOAuthAccessToken,
    MCPOAuthAuthorizationCode,
    MCPOAuthAuthorizationRequest,
    MCPOAuthClient,
    MCPOAuthRefreshToken,
)
from mcp_oauth.models import (
    OAuthAccessToken,
    OAuthApplication,
    OAuthCleanupRun,
    OAuthConsent,
    OAuthGrant,
    OAuthSecurityEvent,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "agent" / "contract.yaml"
CONTRACT_DESCRIPTOR_PATH = ROOT / "release" / "contract-release.json"
MCP_RELEASE_SCHEMA_PATH = ROOT / "release" / "mcp-release.schema.json"
CLIENT_COMPATIBILITY_PATH = ROOT / "integration" / "client-compatibility.yaml"
SOURCES_PATH = ROOT / "release" / "sources.yaml"
ADR_PATH = ROOT / "agent" / "decisions" / "0002-mcp-oauth-v1.yaml"
RUNBOOK_PATH = ROOT / "docs" / "runbooks" / "mcp-stage1.md"


def _load(path):
    return json.loads(path.read_text())


def _smoke_evidence(contract, *, commit, image_digest):
    def client(version, *, diagnostic=False):
        status = "diagnostic-passed" if diagnostic else "passed"
        return {
            "tested_version": version,
            "registration_method": "cimd",
            "registration_status": status,
            "fallback_registration_method": "dcr",
            "fallback_status": status,
        }

    return {
        "clients": {
            "ChatGPT": client("1.2026.210"),
            "Codex": client("0.147.0-alpha.6.5"),
            "Claude/Claude Desktop": client("1.2026.210"),
            "Claude Code": client("2.1.212"),
        },
        "diagnostic_clients": {"MCP Inspector": client("0.18.0", diagnostic=True)},
        "flows": sorted(release.REQUIRED_SMOKE_FLOWS),
        "git_commit": commit,
        "image_digest": image_digest,
        "issuer": release.ISSUER,
        "resource": release.RESOURCE,
        "tool_registry_sha256": release.canonical_tool_registry_digest(contract),
        "client_compatibility_sha256": (
            f"sha256:{hashlib.sha256(CLIENT_COMPATIBILITY_PATH.read_bytes()).hexdigest()}"
        ),
        "tested_at": "2026-08-12T12:34:56Z",
        "evidence_uri": (
            "https://github.com/onurmatik/site-hits/releases/download/"
            "sitehits-mcp-v0.3.0/mcp-smoke.json"
        ),
    }


def test_released_contract_descriptor_can_seal_the_mcp_candidate(tmp_path):
    contract = _load(CONTRACT_PATH)
    commit = "0123456789abcdef0123456789abcdef01234567"
    image_digest = f"sha256:{'a' * 64}"
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(_smoke_evidence(contract, commit=commit, image_digest=image_digest))
    )
    assert contract["agent_contract_version"] == "2.0.0"
    assert _load(CONTRACT_DESCRIPTOR_PATH)["agent_contract_version"] == "2.0.0"
    descriptor = release.build_descriptor(
        server_version="0.3.0",
        contract_path=CONTRACT_PATH,
        contract_descriptor_path=CONTRACT_DESCRIPTOR_PATH,
        git_commit=commit,
        image_digest=image_digest,
        smoke_evidence_path=evidence_path,
    )
    assert descriptor["agent_contract"]["agent_contract_version"] == "2.0.0"


def test_pinned_runtime_dependencies_and_shared_adapter_public_seam():
    assert importlib.metadata.version("django-oauth-toolkit") == "3.4.0"
    assert importlib.metadata.version("mcp") == "2.0.0"
    shared_oauth = (
        ROOT / "packages" / "django-embedded-mcp" / "src" / "django_embedded_mcp" / "oauth.py"
    ).read_text()
    assert "OAuth2Validator" not in shared_oauth
    assert "super()._" not in shared_oauth
    assert "def _create_" not in shared_oauth


def test_clean_cut_legacy_models_are_cleanup_only():
    assert not hasattr(MCPAccessToken, "issue")
    assert not hasattr(MCPAccessToken, "authenticate")
    assert not hasattr(MCPOAuthAccessToken, "issue")
    assert not hasattr(MCPOAuthAccessToken, "authenticate")
    assert not hasattr(MCPOAuthAuthorizationCode, "issue")
    assert not hasattr(MCPOAuthRefreshToken, "issue")
    assert not (ROOT / "mcp_gateway" / "management" / "commands" / "create_mcp_token.py").exists()


def test_release_descriptor_rejects_runtime_version_drift(tmp_path):
    contract = _load(CONTRACT_PATH)
    commit = "0123456789abcdef0123456789abcdef01234567"
    image_digest = f"sha256:{'a' * 64}"
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(_smoke_evidence(contract, commit=commit, image_digest=image_digest))
    )
    with pytest.raises(ValueError, match="deployed runtime artifact"):
        release.build_descriptor(
            server_version="0.3.1",
            contract_path=CONTRACT_PATH,
            contract_descriptor_path=CONTRACT_DESCRIPTOR_PATH,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )


def test_release_cli_loads_without_django_runtime_initialization():
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [sys.executable, "-m", "mcp_gateway.release", "--help"],
        cwd=ROOT,
        env={**environment, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--smoke-evidence" in result.stdout
    assert "--client-compatibility" in result.stdout


def test_release_registry_is_contract_derived_exact_and_content_addressed():
    contract = _load(CONTRACT_PATH)
    registry = release.canonical_tool_registry(contract)
    assert len(registry) == len(contract["tools"]) == 22
    assert [tool["name"] for tool in registry] == sorted(contract["tools"])
    for tool in registry:
        canonical = contract["tools"][tool["name"]]
        assert set(tool) == {
            "name",
            "title",
            "description",
            "inputSchema",
            "outputSchema",
            "securitySchemes",
            "annotations",
            "exposure",
            "authentication",
            "requiredCapabilities",
            "ownership",
            "resourceType",
            "_meta",
        }
        assert tool["title"] == canonical["title"]
        assert tool["description"] == canonical["description"]
        assert tool["securitySchemes"] == [
            {"type": "oauth2", "scopes": canonical["required_scopes"]}
        ]
        assert tool["exposure"] == canonical["exposure"]
    rendered = json.dumps(
        registry, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert release.canonical_tool_registry_digest(contract) == (
        f"sha256:{hashlib.sha256(rendered).hexdigest()}"
    )


def test_release_sealing_rejects_incomplete_or_mismatched_evidence(tmp_path, monkeypatch):
    contract = _load(CONTRACT_PATH)
    commit = "0123456789abcdef0123456789abcdef01234567"
    image_digest = f"sha256:{'b' * 64}"
    candidate_descriptor = _load(CONTRACT_DESCRIPTOR_PATH)
    candidate_descriptor["agent_contract_version"] = contract["agent_contract_version"]
    candidate_descriptor["contract_sha256"] = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
    candidate_descriptor_path = tmp_path / "candidate-contract-release.json"
    candidate_descriptor_path.write_text(json.dumps(candidate_descriptor))
    monkeypatch.setattr(
        release,
        "PINNED_AGENT_CONTRACT_SHA256",
        hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        release,
        "PINNED_AGENT_CONTRACT_DESCRIPTOR_SHA256",
        hashlib.sha256(candidate_descriptor_path.read_bytes()).hexdigest(),
    )
    evidence = _smoke_evidence(contract, commit=commit, image_digest=image_digest)
    evidence["clients"].pop("Codex")
    evidence_path = tmp_path / "missing-client.json"
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(
        ValueError,
        match="exact primary and cross-agent baseline",
    ):
        release.build_descriptor(
            server_version="0.3.0",
            contract_path=CONTRACT_PATH,
            contract_descriptor_path=candidate_descriptor_path,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )

    evidence = _smoke_evidence(contract, commit=commit, image_digest=image_digest)
    evidence["tool_registry_sha256"] = f"sha256:{'c' * 64}"
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="tool_registry_sha256"):
        release.build_descriptor(
            server_version="0.3.0",
            contract_path=CONTRACT_PATH,
            contract_descriptor_path=candidate_descriptor_path,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )

    evidence = _smoke_evidence(contract, commit=commit, image_digest=image_digest)
    evidence["client_compatibility_sha256"] = f"sha256:{'d' * 64}"
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="client_compatibility_sha256"):
        release.build_descriptor(
            server_version="0.3.0",
            contract_path=CONTRACT_PATH,
            contract_descriptor_path=candidate_descriptor_path,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )

    evidence = _smoke_evidence(contract, commit=commit, image_digest=image_digest)
    evidence["evidence_uri"] = (
        "https://github.com/onurmatik/site-hits/releases/download/"
        "sitehits-mcp-v0.3.1/mcp-smoke.json"
    )
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="server version's immutable release tag"):
        release.build_descriptor(
            server_version="0.3.0",
            contract_path=CONTRACT_PATH,
            contract_descriptor_path=candidate_descriptor_path,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )

    evidence = _smoke_evidence(contract, commit=commit, image_digest=image_digest)
    evidence["evidence_sha256"] = f"sha256:{'4' * 64}"
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="Smoke evidence keys differ"):
        release.build_descriptor(
            server_version="0.3.0",
            contract_path=CONTRACT_PATH,
            contract_descriptor_path=candidate_descriptor_path,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )

    tampered_contract = _load(CONTRACT_PATH)
    tampered_contract["server_instructions"]["summary"] = "tampered"
    tampered_contract_path = tmp_path / "tampered-contract.json"
    tampered_contract_path.write_text(json.dumps(tampered_contract))
    upstream_path = tmp_path / "tampered-contract-release.json"
    evidence_path.write_text(
        json.dumps(_smoke_evidence(contract, commit=commit, image_digest=image_digest))
    )
    with pytest.raises(ValueError, match="Contract content"):
        release.build_descriptor(
            server_version="0.3.0",
            contract_path=tampered_contract_path,
            contract_descriptor_path=candidate_descriptor_path,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )

    upstream = _load(candidate_descriptor_path)
    upstream["compatibility_strategy"] = "tampered"
    upstream_path.write_text(json.dumps(upstream))
    with pytest.raises(ValueError, match="pinned immutable artifact"):
        release.build_descriptor(
            server_version="0.3.0",
            contract_path=CONTRACT_PATH,
            contract_descriptor_path=upstream_path,
            git_commit=commit,
            image_digest=image_digest,
            smoke_evidence_path=evidence_path,
        )


def test_stage1_sources_adr_and_runbook_capture_resolved_operations():
    sources = _load(SOURCES_PATH)["descriptors"]["mcp_server"]
    assert sources["authoritative_store"] == "github_release_asset"
    assert sources["runtime_artifact_store"] == "ghcr_oci_digest"
    assert sources["immutable_ref_pattern"] == "sitehits-mcp-v{server_version}"
    assert sources["descriptor_path"] == "release/mcp-release.json"
    assert "ChatGPT-plus-Codex-plus-Claude-plus-Claude-Code" in sources["consumer_resolution"]

    matrix = _load(CLIENT_COMPATIBILITY_PATH)
    assert matrix["registration_policy"]["preferred"] == "cimd"
    assert matrix["registration_policy"]["fallback"] == "dcr"
    assert matrix["registration_policy"]["owner_role"] == "repository-maintainer"
    assert matrix["registration_policy"]["review_interval_days"] == 90
    assert [record["client"] for record in matrix["clients"]] == [
        "chatgpt",
        "codex",
        "claude",
        "claude-code",
        "mcp-inspector",
    ]
    claude_code = matrix["clients"][3]
    assert claude_code["callback_profile"] == "localhost-loopback-dynamic-port"
    assert claude_code["release_acceptance"] == "required"
    assert claude_code["registration_method"] == "cimd"
    assert claude_code["fallback_registration_method"] == "dcr"

    adr = _load(ADR_PATH)
    assert adr["status"] == "accepted"
    assert adr["owner"] == "Onur"
    serialized = json.dumps(adr)
    for required in (
        "https://sitehits.io/mcp",
        "ChatGPT",
        "Codex",
        "Claude/Claude Desktop",
        "Claude Code",
        "MCP Inspector",
        "127.0.0.1",
        "[::1]",
        "localhost",
        "PostgreSQL 17",
        "systemd",
        "GHCR",
        "GitHub Release",
        "client_id_metadata_document_supported=true",
        "repository-maintainer",
        "zero successful DCR use",
        "90 days",
        "30 days",
        "no legacy compatibility window",
    ):
        assert required in serialized

    runbook = RUNBOOK_PATH.read_text()
    assert "PostgreSQL 17" in runbook
    assert "Do not commit `release/mcp-release.json`" in runbook
    assert "do not downgrade code or reverse" in runbook
    assert "Never unconsume an authorization code" in runbook
    assert not (ROOT / "release" / "mcp-release.json").exists()


def test_systemd_process_boundary_and_daily_cleanup_contract():
    web = (ROOT / "deploy" / "systemd" / "sitehits-web.service").read_text()
    mcp = (ROOT / "deploy" / "systemd" / "sitehits-mcp.service").read_text()
    cleanup = (ROOT / "deploy" / "systemd" / "sitehits-mcp-cleanup.service").read_text()
    timer = (ROOT / "deploy" / "systemd" / "sitehits-mcp-cleanup.timer").read_text()
    health = (ROOT / "deploy" / "systemd" / "sitehits-mcp-cleanup-health.service").read_text()
    health_timer = (ROOT / "deploy" / "systemd" / "sitehits-mcp-cleanup-health.timer").read_text()
    alert = (ROOT / "deploy" / "systemd" / "sitehits-mcp-alert@.service").read_text()
    nginx = (ROOT / "deploy" / "nginx" / "sitehits-mcp.locations.conf").read_text()
    for unit in (web, mcp, cleanup):
        assert "${SITEHITS_MCP_IMAGE_REF}" in unit
        assert "/srv/apps/sitehits/venv/" not in unit
    assert "sitehits-web" in web and " web" in web
    assert "--env WEB_HOST=127.0.0.1" in web
    assert web.index("python manage.py check --deploy") < web.index(
        "python manage.py migrate --noinput"
    )
    assert "sitehits-mcp" in mcp and " mcp" in mcp
    assert "cleanup_mcp_oauth" in cleanup
    assert "purge_old_events" in cleanup
    assert "OnCalendar=daily" in timer and "Persistent=true" in timer
    assert "check_mcp_oauth_cleanup_health" in health
    assert "OnUnitActiveSec=1h" in health_timer and "Persistent=true" in health_timer
    assert "OnFailure=sitehits-mcp-alert@%n.service" in cleanup
    assert "send-mcp-alert.py %i" in alert
    assert "location = /mcp" in nginx
    assert "location ^~ /oauth/" in nginx
    assert "location ^~ /accounts/" in nginx
    assert "127.0.0.1:8001" in nginx
    assert "--no-access-log" in (ROOT / "scripts" / "start.sh").read_text()
    assert "access_log off" in nginx
    assert "proxy_set_header X-Request-ID $request_id" in nginx


def test_deploy_seeds_cleanup_health_before_enabling_persistent_health_timer():
    deploy_source = (ROOT / ".deploy" / "fabfile.py").read_text()
    stop_timers = deploy_source.index('"systemctl disable --now sitehits-mcp-cleanup.timer "')
    migrate_and_start_web = deploy_source.index(
        'connection.sudo("systemctl start sitehits-web.service")'
    )
    seed_cleanup = deploy_source.index(
        'connection.sudo("systemctl start sitehits-mcp-cleanup.service")'
    )
    enable_timers = deploy_source.index('"systemctl enable --now sitehits-mcp-cleanup.timer "')

    assert stop_timers < migrate_and_start_web < seed_cleanup < enable_timers


def test_deploy_installs_archive_jobs_backs_up_and_verifies_public_identity():
    deploy_source = (ROOT / ".deploy" / "fabfile.py").read_text()
    backup_source = (ROOT / ".deploy" / "backup_database.py").read_text()
    archive_unit = (
        ROOT / "deploy" / "systemd" / "sitehits-archive-maintenance.service"
    ).read_text()
    historical_unit = (
        ROOT / "deploy" / "systemd" / "sitehits-historical-cache.service"
    ).read_text()

    for unit in (
        "sitehits-archive-maintenance.service",
        "sitehits-archive-maintenance.timer",
        "sitehits-historical-cache.service",
        "sitehits-historical-cache.timer",
    ):
        assert unit in deploy_source
    assert deploy_source.index("backup_database(connection)") < deploy_source.index(
        "install_stage1_topology(connection)"
    )
    assert "predeploy-{RELEASE_GIT_COMMIT}.dump" in deploy_source
    assert "agent-manifest.json" in deploy_source
    assert 'manifest.get("server_version") != "0.3.0"' in deploy_source
    assert 'manifest.get("agent_contract_version") != "2.0.0"' in deploy_source
    assert 'if mcp_status != "401"' in deploy_source
    assert "PGPASSWORD" in backup_source
    assert '"--dbname",' in backup_source
    assert '"--format=custom"' in backup_source
    for unit in (archive_unit, historical_unit):
        assert "--read-only" in unit
        assert "GeoLite2-City.mmdb" in unit


def test_stage1_ci_runs_full_postgres_suite_and_history_secret_scan():
    workflow = (ROOT / ".github" / "workflows" / "mcp-stage1-postgres.yml").read_text()

    assert "image: postgres:17.10" in workflow
    assert "show server_version_num" in workflow
    assert 'show server_version_num\')" = "170010"' in workflow
    assert "manage.py check --deploy --tag database" in workflow
    assert "fetch-depth: 0" in workflow
    assert "run: uv run pytest -q" in workflow
    assert ("gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e") in workflow


def test_release_seal_ci_generates_validates_and_publishes_exact_descriptor():
    workflow = (ROOT / ".github" / "workflows" / "mcp-stage1-release.yml").read_text()

    assert "environment: mcp-production-release" in workflow
    assert "gh release download" in workflow
    assert workflow.count("python -m mcp_gateway.release") == 2
    assert "--output release/mcp-release.json" in workflow
    assert "cmp \\" in workflow
    assert "release/mcp-release.schema.json" in workflow
    assert "Draft202012Validator" in workflow
    assert "gh release upload" in workflow
    assert "release/mcp-release.json" in workflow


def test_deploy_check_requires_the_postgresql_17_acceptance_engine():
    assert _stage1_database_errors(SimpleNamespace(vendor="postgresql", pg_version=170_004)) == []
    wrong_engine = _stage1_database_errors(SimpleNamespace(vendor="sqlite", pg_version=None))
    wrong_version = _stage1_database_errors(
        SimpleNamespace(vendor="postgresql", pg_version=160_009)
    )

    assert [error.id for error in wrong_engine] == ["mcp_gateway.E004"]
    assert [error.id for error in wrong_version] == ["mcp_gateway.E006"]


def test_container_build_installs_local_oauth_package_and_scans_mcp_templates():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert dockerfile.index("COPY packages ./packages") < dockerfile.index(
        "RUN pip install --no-cache-dir -r requirements.txt"
    )
    assert "COPY mcp_gateway ./mcp_gateway" in dockerfile
    assert "DJANGO_DEBUG=true python manage.py collectstatic --noinput" in dockerfile


def test_container_context_excludes_every_local_or_deploy_secret_env_file():
    patterns = set((ROOT / ".dockerignore").read_text().splitlines())

    assert {
        ".env",
        ".env.*",
        ".env-*",
        "**/.env",
        "**/.env.*",
        "**/.env-*",
        ".deploy/deploy.env",
    }.issubset(patterns)
    assert "!.env.example" in patterns


def test_sensitive_proxy_locations_disable_uri_bearing_access_and_error_logs():
    nginx = (ROOT / "deploy" / "nginx" / "sitehits-mcp.locations.conf").read_text()

    for marker in ("location = /mcp", "location ^~ /oauth/", "location ^~ /accounts/"):
        block = nginx.split(marker, 1)[1].split("}", 1)[0]
        assert "access_log off;" in block
        assert "error_log /dev/null emerg;" in block


def test_image_reference_validator_accepts_only_the_immutable_ghcr_digest():
    validator = ROOT / "deploy" / "validate-image-ref.sh"
    valid = "ghcr.io/onurmatik/site-hits@sha256:" + "a" * 64
    accepted = subprocess.run(
        [str(validator)],
        env={**os.environ, "SITEHITS_MCP_IMAGE_REF": valid},
        capture_output=True,
        text=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    for invalid in (
        "ghcr.io/onurmatik/site-hits:latest",
        "ghcr.io/onurmatik/site-hits@sha256:" + "A" * 64,
        "ghcr.io/other/site-hits@sha256:" + "a" * 64,
    ):
        rejected = subprocess.run(
            [str(validator)],
            env={**os.environ, "SITEHITS_MCP_IMAGE_REF": invalid},
            capture_output=True,
            text=True,
            check=False,
        )
        assert rejected.returncode == 78


def test_alert_webhook_validator_accepts_only_https_without_userinfo():
    sender = ROOT / "deploy" / "send-mcp-alert.py"
    accepted = subprocess.run(
        [sys.executable, str(sender), "--check"],
        env={**os.environ, "SITEHITS_MCP_ALERT_WEBHOOK_URL": "https://alerts.example/hook"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    for invalid in ("", "http://alerts.example/hook", "https://user@alerts.example/hook"):
        rejected = subprocess.run(
            [sys.executable, str(sender), "--check"],
            env={**os.environ, "SITEHITS_MCP_ALERT_WEBHOOK_URL": invalid},
            capture_output=True,
            text=True,
            check=False,
        )
        assert rejected.returncode == 78


def _client(name):
    return MCPOAuthClient.objects.create(
        client_id=f"{name}-{uuid4().hex}", metadata={"client_name": name}
    )


def _authorization_request(client, *, expires_at):
    return MCPOAuthAuthorizationRequest.objects.create(
        client=client,
        redirect_uri="https://client.example/callback",
        scopes=["read"],
        resource="https://sitehits.io/mcp",
        state="opaque",
        code_challenge="a" * 43,
        expires_at=expires_at,
    )


def _utc(value):
    return value.isoformat().replace("+00:00", "Z")


def _cleanup_eligibility_details(*, started_at, finished_at, lag_hours_by_type=None):
    lag_hours_by_type = lag_hours_by_type or {}
    evidence = {}
    for target, retention_days in CLEANUP_RETENTION_DAYS_BY_TARGET.items():
        lag_hours = lag_hours_by_type.get(target)
        if lag_hours is None:
            oldest_source_at = None
            eligible_since = None
            lag_seconds = 0
        else:
            eligible_since = finished_at - timedelta(hours=lag_hours)
            oldest_source_at = eligible_since - timedelta(days=retention_days)
            lag_seconds = int((finished_at - eligible_since).total_seconds())
        evidence[target] = {
            "retention_days": retention_days,
            "cutoff": _utc(started_at - timedelta(days=retention_days)),
            "oldest_source_at": (_utc(oldest_source_at) if oldest_source_at is not None else None),
            "eligible_since": _utc(eligible_since) if eligible_since is not None else None,
            "lag_seconds": lag_seconds,
        }
    return {"eligibility_by_type": evidence}


@pytest.mark.django_db
def test_cleanup_deletes_only_metadata_past_fixed_retention_and_emits_metrics():
    now = timezone.now()
    old = now - timedelta(days=31)
    recent = now - timedelta(days=29)
    future = now + timedelta(days=1)
    user = get_user_model().objects.create_user(username="stage1-cleanup")
    client = _client("credential-client")

    application = OAuthApplication.objects.create(
        client_id="cleanup-current-client",
        redirect_uris="https://client.example/callback",
        client_type=OAuthApplication.CLIENT_PUBLIC,
        authorization_grant_type=OAuthApplication.GRANT_AUTHORIZATION_CODE,
        client_secret="",
        hash_client_secret=False,
        skip_authorization=False,
        registration_source=OAuthApplication.RegistrationSource.DCR,
        allowed_scopes=["read"],
    )
    OAuthApplication.objects.filter(pk=application.pk).update(
        created=old,
        last_used_at=old,
    )
    current_access = OAuthAccessToken.objects.create(
        user=user,
        application=application,
        token="",
        token_checksum="9" * 64,
        scope="read",
        resource=["https://sitehits.io/mcp"],
        expires=old,
    )
    never_authorized_application = OAuthApplication.objects.create(
        client_id="cleanup-never-authorized",
        redirect_uris="https://client.example/callback",
        client_type=OAuthApplication.CLIENT_PUBLIC,
        authorization_grant_type=OAuthApplication.GRANT_AUTHORIZATION_CODE,
        client_secret="",
        hash_client_secret=False,
        skip_authorization=False,
        registration_source=OAuthApplication.RegistrationSource.DCR,
        allowed_scopes=["read"],
    )
    OAuthApplication.objects.filter(pk=never_authorized_application.pk).update(created=old)
    grant_only_application = OAuthApplication.objects.create(
        client_id="cleanup-unexchanged-grant-client",
        redirect_uris="https://client.example/callback",
        client_type=OAuthApplication.CLIENT_PUBLIC,
        authorization_grant_type=OAuthApplication.GRANT_AUTHORIZATION_CODE,
        client_secret="",
        hash_client_secret=False,
        skip_authorization=False,
        registration_source=OAuthApplication.RegistrationSource.DCR,
        allowed_scopes=["read"],
    )
    OAuthApplication.objects.filter(pk=grant_only_application.pk).update(
        created=old,
        # Set by the successful consent path before any code exchange occurs.
        last_used_at=old,
    )
    unexchanged_grant = OAuthGrant.objects.create(
        user=user,
        application=grant_only_application,
        code="",
        code_digest="d" * 64,
        expires=old,
        redirect_uri="https://client.example/callback",
        scope="read",
        code_challenge="e" * 43,
        code_challenge_method="S256",
        resource=["https://sitehits.io/mcp"],
    )
    active_consent = OAuthConsent.objects.create(
        user=user,
        application=application,
        resource="https://sitehits.io/mcp",
        scopes=["read"],
        redirect_uri_digest="a" * 64,
        decision=OAuthConsent.Decision.APPROVED,
    )
    revoked_consent = OAuthConsent.objects.create(
        user=user,
        application=application,
        resource="https://sitehits.io/mcp",
        scopes=["read"],
        redirect_uri_digest="b" * 64,
        decision=OAuthConsent.Decision.APPROVED,
    )
    denied_consent = OAuthConsent.objects.create(
        user=user,
        application=application,
        resource="https://sitehits.io/mcp",
        scopes=["read"],
        redirect_uri_digest="c" * 64,
        decision=OAuthConsent.Decision.DENIED,
    )
    OAuthConsent.objects.filter(pk__in=[active_consent.pk, denied_consent.pk]).update(
        created_at=old
    )
    OAuthConsent.objects.filter(pk=revoked_consent.pk).update(
        created_at=old,
        revoked_at=old,
    )
    old_security_event = OAuthSecurityEvent.objects.create(
        event="cleanup_fixture",
        outcome="success",
        resource="https://sitehits.io/mcp",
    )
    OAuthSecurityEvent.objects.filter(pk=old_security_event.pk).update(
        created_at=now - timedelta(days=91)
    )

    old_request = _authorization_request(client, expires_at=old)
    recent_request = _authorization_request(client, expires_at=recent)
    old_code = MCPOAuthAuthorizationCode.objects.create(
        user=user,
        client=client,
        prefix="old-code",
        code_digest="1" * 64,
        redirect_uri="https://client.example/callback",
        scopes=["read"],
        resource="https://sitehits.io/mcp",
        code_challenge="b" * 43,
        expires_at=old,
    )
    recent_code = MCPOAuthAuthorizationCode.objects.create(
        user=user,
        client=client,
        prefix="new-code",
        code_digest="2" * 64,
        redirect_uri="https://client.example/callback",
        scopes=["read"],
        resource="https://sitehits.io/mcp",
        code_challenge="c" * 43,
        expires_at=recent,
    )
    old_access = MCPOAuthAccessToken.objects.create(
        user=user,
        client=client,
        prefix="old-access",
        token_digest="3" * 64,
        scopes=["read"],
        resource="https://sitehits.io/mcp",
        family_id=uuid4(),
        expires_at=old,
    )
    recent_access = MCPOAuthAccessToken.objects.create(
        user=user,
        client=client,
        prefix="new-access",
        token_digest="4" * 64,
        scopes=["read"],
        resource="https://sitehits.io/mcp",
        family_id=uuid4(),
        expires_at=recent,
    )
    old_refresh = MCPOAuthRefreshToken.objects.create(
        user=user,
        client=client,
        prefix="old-refresh",
        token_digest="5" * 64,
        scopes=["read"],
        resource="https://sitehits.io/mcp",
        expires_at=old,
    )
    recent_refresh = MCPOAuthRefreshToken.objects.create(
        user=user,
        client=client,
        prefix="new-refresh",
        token_digest="6" * 64,
        scopes=["read"],
        resource="https://sitehits.io/mcp",
        expires_at=recent,
    )
    old_legacy = MCPAccessToken.objects.create(
        user=user,
        name="old legacy",
        prefix="old-legacy",
        token_digest="7" * 64,
        expires_at=old,
    )
    recent_legacy = MCPAccessToken.objects.create(
        user=user,
        name="new legacy",
        prefix="new-legacy",
        token_digest="8" * 64,
        expires_at=recent,
    )

    stale_client = _client("stale")
    MCPOAuthClient.objects.filter(pk=stale_client.pk).update(created_at=old)
    recent_client = _client("recent")
    protected_client = _client("protected")
    MCPOAuthClient.objects.filter(pk=protected_client.pk).update(created_at=old)
    _authorization_request(protected_client, expires_at=future)

    stdout = StringIO()
    call_command("cleanup_mcp_oauth", stdout=stdout)
    metrics = json.loads(stdout.getvalue())
    assert metrics["job"] == "cleanup_mcp_oauth"
    assert metrics["runs"] == 1
    assert metrics["retention_days_by_type"] == CLEANUP_RETENTION_DAYS_BY_TARGET
    assert metrics["retention_days_by_type"]["oauth_access_tokens"] == 30
    assert metrics["retention_days_by_type"]["oauth_security_events"] == 90
    assert metrics["retention_days_by_type"]["oauth_cleanup_runs"] == 90
    assert metrics["errors"] == 0
    assert metrics["deleted"] >= 6
    assert 23 * 60 * 60 <= metrics["oldest_eligible_age_seconds"] < 26 * 60 * 60
    assert metrics["eligibility_by_type"]["oauth_access_tokens"]["retention_days"] == 30
    assert metrics["eligibility_by_type"]["oauth_security_events"]["retention_days"] == 90
    assert metrics["eligibility_by_type"]["oauth_security_events"]["lag_seconds"] < (26 * 60 * 60)
    assert metrics["last_success_at"].endswith("Z")
    assert metrics["cleanup_run_id"]
    assert metrics["truncated"] == []
    cleanup_run = OAuthCleanupRun.objects.get(job_id=metrics["cleanup_run_id"])
    assert cleanup_run.status == OAuthCleanupRun.Status.SUCCEEDED
    assert cleanup_run.errors == 0
    assert cleanup_run.duration_ms >= 0
    assert cleanup_run.deleted == metrics["deleted_by_type"]
    assert cleanup_run.oldest_eligible_at is not None
    assert now - cleanup_run.oldest_eligible_at < timedelta(hours=26)
    assert cleanup_run.details["retention_days_by_type"] == CLEANUP_RETENTION_DAYS_BY_TARGET
    assert cleanup_run.details["eligibility_by_type"] == metrics["eligibility_by_type"]

    for model, primary_key in (
        (MCPOAuthAuthorizationRequest, old_request.pk),
        (MCPOAuthAuthorizationCode, old_code.pk),
        (MCPOAuthAccessToken, old_access.pk),
        (MCPOAuthRefreshToken, old_refresh.pk),
        (MCPAccessToken, old_legacy.pk),
        (MCPOAuthClient, stale_client.pk),
        (OAuthAccessToken, current_access.pk),
        (OAuthGrant, unexchanged_grant.pk),
        (OAuthApplication, never_authorized_application.pk),
        (OAuthConsent, revoked_consent.pk),
        (OAuthConsent, denied_consent.pk),
        (OAuthSecurityEvent, old_security_event.pk),
    ):
        assert not model.objects.filter(pk=primary_key).exists()
    for model, primary_key in (
        (MCPOAuthAuthorizationRequest, recent_request.pk),
        (MCPOAuthAuthorizationCode, recent_code.pk),
        (MCPOAuthAccessToken, recent_access.pk),
        (MCPOAuthRefreshToken, recent_refresh.pk),
        (MCPAccessToken, recent_legacy.pk),
        (MCPOAuthClient, recent_client.pk),
        (MCPOAuthClient, protected_client.pk),
        (OAuthApplication, application.pk),
        (OAuthApplication, grant_only_application.pk),
        (OAuthConsent, active_consent.pk),
    ):
        assert model.objects.filter(pk=primary_key).exists()

    # Old terminal credential rows are gone now. A second daily run must still
    # preserve their previously used client through its durable last_used_at.
    second_stdout = StringIO()
    call_command("cleanup_mcp_oauth", stdout=second_stdout)
    assert json.loads(second_stdout.getvalue())["errors"] == 0
    assert OAuthApplication.objects.filter(pk=application.pk).exists()
    assert OAuthApplication.objects.filter(pk=grant_only_application.pk).exists()


@pytest.mark.django_db
def test_cleanup_dry_run_and_batch_ceiling_are_honest():
    old = timezone.now() - timedelta(days=31)
    user = get_user_model().objects.create_user(username="stage1-batches")
    tokens = [
        MCPAccessToken.objects.create(
            user=user,
            name=f"legacy-{index}",
            prefix=f"old-{index}",
            token_digest=str(index + 1) * 64,
            expires_at=old,
        )
        for index in range(2)
    ]
    stdout = StringIO()
    call_command("cleanup_mcp_oauth", dry_run=True, stdout=stdout)
    dry_run = json.loads(stdout.getvalue())
    assert dry_run["dry_run"] is True
    assert dry_run["eligible"] == 2
    assert dry_run["deleted"] == 0
    assert MCPAccessToken.objects.filter(pk__in=[token.pk for token in tokens]).count() == 2

    stdout = StringIO()
    call_command(
        "cleanup_mcp_oauth",
        batch_size=1,
        max_batches=1,
        stdout=stdout,
    )
    bounded = json.loads(stdout.getvalue())
    assert bounded["deleted_by_type"]["legacy_access_tokens"] == 1
    assert "legacy_access_tokens" in bounded["truncated"]
    assert MCPAccessToken.objects.filter(pk__in=[token.pk for token in tokens]).count() == 1


@pytest.mark.django_db
def test_cleanup_health_detects_stale_success_failure_and_repeated_lag():
    now = timezone.now()
    success_started_at = now - timedelta(minutes=2)
    success_finished_at = now - timedelta(minutes=1)
    success = OAuthCleanupRun.objects.create(
        status=OAuthCleanupRun.Status.SUCCEEDED,
        started_at=success_started_at,
        finished_at=success_finished_at,
        details=_cleanup_eligibility_details(
            started_at=success_started_at,
            finished_at=success_finished_at,
        ),
    )
    assert cleanup_health(now)["healthy"] is True

    failure = OAuthCleanupRun.objects.create(
        status=OAuthCleanupRun.Status.FAILED,
        started_at=now,
        finished_at=now,
        errors=1,
    )
    failed = cleanup_health(now)
    assert failed["healthy"] is False
    assert failed["latest_run_id"] == str(failure.job_id)
    assert "latest_cleanup_failed" in failed["reasons"]

    failure.delete()
    success.delete()

    # Audit/run retention is 90 days. A source timestamp 91 days old has only
    # one day of eligible lag and must not be evaluated with the 30-day
    # credential retention window.
    for offset in (2, 1):
        finished_at = now - timedelta(hours=offset)
        started_at = finished_at - timedelta(minutes=1)
        OAuthCleanupRun.objects.create(
            status=OAuthCleanupRun.Status.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            details=_cleanup_eligibility_details(
                started_at=started_at,
                finished_at=finished_at,
                lag_hours_by_type={"oauth_security_events": 24},
            ),
        )
    retained = cleanup_health(now)
    assert retained["healthy"] is True
    assert retained["consecutive_lagged_runs"] is False
    assert retained["latest_eligible_lag_seconds_by_type"]["oauth_security_events"] == (
        24 * 60 * 60
    )

    OAuthCleanupRun.objects.all().delete()
    for offset in (2, 1):
        finished_at = now - timedelta(hours=offset)
        started_at = finished_at - timedelta(minutes=1)
        OAuthCleanupRun.objects.create(
            status=OAuthCleanupRun.Status.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            details=_cleanup_eligibility_details(
                started_at=started_at,
                finished_at=finished_at,
                lag_hours_by_type={"oauth_security_events": 37},
            ),
        )
    lagged = cleanup_health(now)
    assert lagged["healthy"] is False
    assert lagged["consecutive_lagged_runs"] is True
    assert "cleanup_lag_repeated" in lagged["reasons"]

    OAuthCleanupRun.objects.all().delete()
    old_started_at = now - timedelta(hours=38)
    old_finished_at = now - timedelta(hours=37)
    old_success = OAuthCleanupRun.objects.create(
        status=OAuthCleanupRun.Status.SUCCEEDED,
        started_at=old_started_at,
        finished_at=old_finished_at,
        details=_cleanup_eligibility_details(
            started_at=old_started_at,
            finished_at=old_finished_at,
        ),
    )
    stale = cleanup_health(now)
    assert stale["latest_run_id"] == str(old_success.job_id)
    assert "cleanup_success_is_stale" in stale["reasons"]


@pytest.mark.django_db
def test_cleanup_health_rejects_missing_or_tampered_retention_evidence():
    now = timezone.now()
    started_at = now - timedelta(minutes=2)
    finished_at = now - timedelta(minutes=1)
    run = OAuthCleanupRun.objects.create(
        status=OAuthCleanupRun.Status.SUCCEEDED,
        started_at=started_at,
        finished_at=finished_at,
    )
    missing = cleanup_health(now)
    assert missing["healthy"] is False
    assert "cleanup_eligibility_evidence_invalid" in missing["reasons"]

    run.details = _cleanup_eligibility_details(
        started_at=started_at,
        finished_at=finished_at,
        lag_hours_by_type={"oauth_security_events": 1},
    )
    run.details["eligibility_by_type"]["oauth_security_events"]["retention_days"] = 30
    run.save(update_fields=["details"])
    tampered = cleanup_health(now)
    assert tampered["healthy"] is False
    assert "cleanup_eligibility_evidence_invalid" in tampered["reasons"]
