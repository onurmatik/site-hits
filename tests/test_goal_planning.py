import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from dashboard.goal_planning import (
    ExistingCatalogEvent,
    GoalActivationCandidate,
    GoalEventCandidate,
    GoalPlanCandidate,
    GoalPlanningService,
    GoalPlanningServiceError,
    reconcile_goal_plan,
    sanitize_existing_catalog,
)


def event(**overrides):
    values = {
        "event_name": "signup_completed",
        "display_name": "Completed sign-ups",
        "description": "Fire after account creation is durably committed.",
        "aggregation": "unique_actors",
        "unit": "",
    }
    values.update(overrides)
    return GoalEventCandidate(**values)


def candidate(**overrides):
    values = {
        "title": "Signup activation",
        "summary": "Measure completed signup and first value.",
        "events": [
            event(),
            event(
                event_name="first_trip_created",
                display_name="First trip created",
                description="Fire after the user's first trip is committed.",
            ),
        ],
        "activation": GoalActivationCandidate(
            start_event="signup_completed",
            goal_event="first_trip_created",
        ),
        "assumptions": ["Signup means the account transaction committed."],
        "clarification": None,
        "unsupported_reasons": [],
    }
    values.update(overrides)
    return GoalPlanCandidate(**values)


def test_candidate_models_are_strict_and_match_product_event_contract():
    with pytest.raises(ValidationError):
        event(event_name="Signup Completed")
    with pytest.raises(ValidationError):
        event(aggregation="count", unit="events")
    with pytest.raises(ValidationError):
        event(aggregation="sum", unit="")
    with pytest.raises(ValidationError):
        event(extra_field="not allowed")
    with pytest.raises(ValidationError):
        GoalPlanCandidate(
            title="Too many",
            summary="More than six events.",
            events=[event(event_name=f"event_{index}") for index in range(7)],
        )


def test_candidate_validates_unique_names_and_two_event_activation_references():
    with pytest.raises(ValidationError):
        candidate(events=[event(), event()])
    with pytest.raises(ValidationError):
        candidate(
            activation=GoalActivationCandidate(
                start_event="signup_completed",
                goal_event="purchase",
            )
        )
    with pytest.raises(ValidationError):
        GoalActivationCandidate(
            start_event="signup_completed",
            goal_event="signup_completed",
        )


def test_reconciliation_is_deterministic_and_flags_metric_contract_conflicts():
    existing = [
        ExistingCatalogEvent(**event().model_dump()),
        ExistingCatalogEvent(
            **event(
                event_name="first_trip_created",
                display_name="Created a trip",
                description="Old description.",
                aggregation="count",
            ).model_dump()
        ),
    ]

    result = reconcile_goal_plan(candidate(), reversed(existing))

    assert [change.action for change in result.event_changes] == ["reused", "changed"]
    assert result.event_changes[1].changed_fields == [
        "display_name",
        "description",
        "aggregation",
    ]
    assert result.warnings == []
    assert result.conflicts == [
        "first_trip_created changes its historical metric contract: aggregation. "
        "Use a new event name instead."
    ]
    assert result.can_install is False
    assert result.candidate == candidate()


def test_reconciliation_allows_visible_copy_changes_but_blocks_unresolved_output():
    existing = [
        ExistingCatalogEvent(
            **event(display_name="Signed up", description="Old wording.").model_dump()
        )
    ]
    changed = candidate(
        events=[event()],
        activation=None,
    )

    result = reconcile_goal_plan(changed, existing)

    assert result.event_changes[0].action == "changed"
    assert result.warnings == [
        "signup_completed updates existing catalog fields: display_name, description."
    ]
    assert result.conflicts == []
    assert result.can_install is True

    unresolved = reconcile_goal_plan(
        candidate(
            events=[event()],
            activation=None,
            clarification="Does signup require email verification?",
            unsupported_reasons=["A 30-day retention goal is not supported."],
        )
    )
    assert unresolved.can_install is False
    assert unresolved.warnings == [
        "Answer the clarification question before installing this plan.",
        "Part of this request is not supported by the current tracking model.",
    ]


def test_reconciliation_restores_local_fields_redacted_from_model_context():
    existing = ExistingCatalogEvent(
        **event(
            description="POST https://private.example/events after success."
        ).model_dump()
    )
    model_visible = candidate(
        events=[event(description="POST [endpoint removed] after success.")],
        activation=None,
    )

    result = reconcile_goal_plan(model_visible, [existing])

    assert result.event_changes[0].action == "reused"
    assert result.events[0].description == existing.description
    assert result.can_install is True


def test_catalog_sanitizer_whitelists_fields_and_redacts_endpoints():
    sanitized = sanitize_existing_catalog(
        [
            {
                **event().model_dump(),
                "description": "POST https://private.example/api after success.",
                "site_id": 42,
                "server_event_key": "do-not-send",
            }
        ]
    )

    assert sanitized[0].description == "POST [endpoint removed] after success."
    assert sanitized[0].model_dump() == {
        "event_name": "signup_completed",
        "display_name": "Completed sign-ups",
        "description": "POST [endpoint removed] after success.",
        "aggregation": "unique_actors",
        "unit": "",
    }


def test_service_uses_responses_parse_structured_output_and_sends_only_safe_context():
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=candidate())
    service = GoalPlanningService(client, model="test-goal-model")
    catalog = [
        {
            **event().model_dump(),
            "site_id": "private-site-id",
            "endpoint": "https://private.example/events",
            "secret": "private-secret",
        }
    ]

    result = service.plan(
        "Track signup at https://app.example/signup with "
        "OPENAI_API_KEY=sk-this-value-must-not-leave",
        catalog,
    )

    assert result.title == "Signup activation"
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["model"] == "test-goal-model"
    assert kwargs["reasoning"] == {"effort": "low"}
    assert kwargs["store"] is False
    assert kwargs["text_format"] is GoalPlanCandidate
    assert "do not assume they are all funnel steps" in kwargs["input"][0]["content"]
    prompt = json.dumps(kwargs["input"])
    assert "private-site-id" not in prompt
    assert "private-secret" not in prompt
    assert "private.example" not in prompt
    assert "app.example" not in prompt
    assert "sk-this-value-must-not-leave" not in prompt
    user_payload = json.loads(kwargs["input"][1]["content"])
    assert set(user_payload) == {"intent", "existing_catalog"}
    assert set(user_payload["existing_catalog"][0]) == {
        "event_name",
        "display_name",
        "description",
        "aggregation",
        "unit",
    }


@pytest.mark.parametrize("parsed", [None, {"events": []}])
def test_service_returns_safe_errors_for_missing_or_invalid_provider_output(parsed):
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=parsed)
    service = GoalPlanningService(client)

    with pytest.raises(GoalPlanningServiceError) as error:
        service.plan("Track completed signups")

    assert error.value.code in {"empty_response", "invalid_plan"}
    assert "provider" not in str(error.value).lower()


def test_service_wraps_openai_failures_without_leaking_details():
    client = MagicMock()
    client.responses.parse.side_effect = RuntimeError("secret upstream response")

    with pytest.raises(GoalPlanningServiceError) as error:
        GoalPlanningService(client).plan("Track completed signups")

    assert error.value.code == "provider_error"
    assert "secret upstream response" not in str(error.value)
