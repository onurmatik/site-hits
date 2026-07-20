import importlib.util
from pathlib import Path


SYNC_ENV_PATH = Path(__file__).resolve().parents[1] / ".deploy" / "sync_env.py"
FABFILE_PATH = Path(__file__).resolve().parents[1] / ".deploy" / "fabfile.py"
SPEC = importlib.util.spec_from_file_location("sitehits_sync_env", SYNC_ENV_PATH)
sync_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_env)


def test_merge_env_replaces_and_appends_without_losing_other_settings(tmp_path):
    target = tmp_path / ".env"
    target.write_text(
        "DJANGO_DEBUG=false\n"
        "GOOGLE_OAUTH_CLIENT_ID=old-client\n"
        "# Keep this comment\n"
    )

    sync_env.merge_env(
        target,
        {
            "GOOGLE_OAUTH_CLIENT_ID": "123-example.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "secret-with=equals",
        },
    )

    assert target.read_text() == (
        "DJANGO_DEBUG=false\n"
        "GOOGLE_OAUTH_CLIENT_ID=123-example.apps.googleusercontent.com\n"
        "# Keep this comment\n\n"
        "GOOGLE_OAUTH_CLIENT_SECRET=secret-with=equals\n"
    )
    assert target.stat().st_mode & 0o777 == 0o600


def test_deploy_contract_syncs_goal_planning_configuration():
    contract = FABFILE_PATH.read_text()

    assert '"OPENAI_API_KEY"' in contract
    assert '"SITEHITS_GOAL_PLANNING_MODEL"' in contract
    assert '"SITEHITS_GOAL_PLANNING_TIMEOUT_SECONDS"' in contract
    assert '"SITEHITS_GOAL_PLANNING_RATE_LIMIT"' in contract
