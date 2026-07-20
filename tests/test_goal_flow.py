from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from analytics.models import ActivationDefinition, ProductEventDefinition
from dashboard.goal_planning import (
    GoalActivationCandidate,
    GoalEventCandidate,
    GoalPlanCandidate,
    reconcile_goal_plan,
)


def event(
    event_name,
    display_name,
    description,
    aggregation="unique_actors",
    unit="",
):
    return GoalEventCandidate(
        event_name=event_name,
        display_name=display_name,
        description=description,
        aggregation=aggregation,
        unit=unit,
    )


def tracking_plan(*, existing=(), clarification=None):
    candidate = GoalPlanCandidate(
        title="Signup to first value",
        summary="Measure signup, first value, and confirmed subscription revenue.",
        events=[
            event(
                "signup_completed",
                "Completed sign-ups",
                "Fire after account creation is durably committed.",
            ),
            event(
                "first_project_created",
                "First project created",
                "Fire after the person's first project is durably committed.",
            ),
            event(
                "subscription_revenue",
                "Subscription revenue",
                "Fire after payment is durably confirmed.",
                aggregation="sum",
                unit="TRY",
            ),
        ],
        activation=GoalActivationCandidate(
            start_event="signup_completed",
            goal_event="first_project_created",
        ),
        assumptions=["Signup means a successfully committed account."],
        clarification=clarification,
        unsupported_reasons=[],
    )
    return reconcile_goal_plan(candidate, existing)


def draft_id_from(response):
    return parse_qs(urlsplit(response.url).query)["draft"][0]


@pytest.mark.django_db
def test_ai_draft_review_and_atomic_confirm_create_the_install_handoff(
    client, tracked_site, superuser, settings
):
    settings.OPENAI_API_KEY = "test-openai-key"
    settings.SITEHITS_BASE_URL = "https://stats.example"
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])
    proposed = tracking_plan()

    with patch("dashboard.views.GoalPlanningService") as service:
        service.return_value.plan.return_value = proposed
        drafted = client.post(
            url,
            {
                "action": "goal_draft",
                "intent": (
                    "Track completed signup, first project activation, and confirmed "
                    "subscription revenue in TRY."
                ),
            },
        )

    assert drafted.status_code == 302
    assert ProductEventDefinition.objects.filter(site=tracked_site).count() == 0
    planner_args = service.return_value.plan.call_args.args
    assert tracked_site.server_event_key not in repr(planner_args)
    assert settings.OPENAI_API_KEY not in repr(planner_args)

    draft_id = draft_id_from(drafted)
    review = client.get(drafted.url)
    assert review.status_code == 200
    assert b"Review your tracking plan" in review.content
    assert b"Subscription revenue" in review.content
    assert b"Nothing has been saved yet" in review.content

    confirmed = client.post(
        url,
        {"action": "goal_confirm", "draft_id": draft_id},
    )
    assert confirmed.status_code == 302
    assert "step=install" in confirmed.url
    assert set(
        ProductEventDefinition.objects.filter(site=tracked_site).values_list(
            "event_name", flat=True
        )
    ) == {
        "signup_completed",
        "first_project_created",
        "subscription_revenue",
    }
    activation = ActivationDefinition.objects.get(site=tracked_site)
    assert activation.start_event.event_name == "signup_completed"
    assert activation.goal_event.event_name == "first_project_created"

    install = client.get(confirmed.url)
    assert install.status_code == 200
    assert b"Your tracking plan is ready" in install.content
    assert b"https://stats.example/api/server-events" in install.content
    instruction = install.context["agent_instruction"]
    assert tracked_site.server_event_key not in instruction
    assert "SITEHITS_SERVER_EVENT_KEY=<set this" in instruction


@pytest.mark.django_db
def test_goal_draft_never_renders_or_sends_site_secrets_before_install(
    client, tracked_site, superuser, settings
):
    settings.OPENAI_API_KEY = "test-openai-key"
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])

    describe = client.get(url)
    assert describe.status_code == 200
    assert tracked_site.server_event_key.encode() not in describe.content

    with patch("dashboard.views.GoalPlanningService") as service:
        service.return_value.plan.return_value = tracking_plan()
        response = client.post(
            url,
            {"action": "goal_draft", "intent": "Track completed signup and first value."},
        )

    assert response.status_code == 302
    _intent, catalog = service.return_value.plan.call_args.args
    assert list(catalog) == []
    assert tracked_site.server_event_key not in repr(service.call_args)


@pytest.mark.django_db
def test_confirm_preserves_existing_copy_hidden_from_the_planning_model(
    client, tracked_site, superuser, settings
):
    settings.OPENAI_API_KEY = "test-openai-key"
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])
    local_description = "POST https://private.example/events after signup succeeds."
    existing = ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="signup_completed",
        display_name="Completed sign-ups",
        description=local_description,
        aggregation="unique_actors",
    )
    candidate = GoalPlanCandidate(
        title="Completed signup",
        summary="Measure completed account creation.",
        events=[
            event(
                "signup_completed",
                "Completed sign-ups",
                "POST [endpoint removed] after signup succeeds.",
            )
        ],
        activation=None,
        assumptions=[],
        clarification=None,
        unsupported_reasons=[],
    )
    proposed = reconcile_goal_plan(candidate, [existing])

    with patch("dashboard.views.GoalPlanningService") as service:
        service.return_value.plan.return_value = proposed
        drafted = client.post(
            url,
            {"action": "goal_draft", "intent": "Track completed signup."},
        )

    confirmed = client.post(
        url,
        {"action": "goal_confirm", "draft_id": draft_id_from(drafted)},
    )

    assert confirmed.status_code == 302
    existing.refresh_from_db()
    assert existing.description == local_description


@pytest.mark.django_db
def test_clarification_updates_the_draft_without_saving(client, tracked_site, superuser, settings):
    settings.OPENAI_API_KEY = "test-openai-key"
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])

    with patch("dashboard.views.GoalPlanningService") as service:
        service.return_value.plan.side_effect = [
            tracking_plan(clarification="Does signup require email verification?"),
            tracking_plan(),
        ]
        first = client.post(
            url,
            {"action": "goal_draft", "intent": "Track signup and first value."},
        )
        first_draft = draft_id_from(first)
        review = client.get(first.url)
        assert b"Does signup require email verification?" in review.content
        assert b"Approve &amp; create instruction" not in review.content

        clarified = client.post(
            url,
            {
                "action": "goal_clarify",
                "draft_id": first_draft,
                "clarification": "Count signup after the account transaction commits.",
            },
        )

    assert clarified.status_code == 302
    assert ProductEventDefinition.objects.filter(site=tracked_site).count() == 0
    planning_intent = service.return_value.plan.call_args_list[1].args[0]
    assert "Clarification question:" in planning_intent
    assert "account transaction commits" in planning_intent


@pytest.mark.django_db
def test_stale_catalog_blocks_confirmation_without_partial_writes(
    client, tracked_site, superuser, settings
):
    settings.OPENAI_API_KEY = "test-openai-key"
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])
    with patch("dashboard.views.GoalPlanningService") as service:
        service.return_value.plan.return_value = tracking_plan()
        drafted = client.post(
            url,
            {"action": "goal_draft", "intent": "Track signup and first value."},
        )

    ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="unrelated_event",
        display_name="Unrelated event",
        description="Existing catalog change.",
        aggregation="count",
    )
    response = client.post(
        url,
        {"action": "goal_confirm", "draft_id": draft_id_from(drafted)},
    )

    assert response.status_code == 409
    assert b"catalog changed" in response.content
    assert list(
        ProductEventDefinition.objects.filter(site=tracked_site).values_list(
            "event_name", flat=True
        )
    ) == ["unrelated_event"]


@pytest.mark.django_db
def test_activation_validation_failure_rolls_back_ai_event_writes(
    client, tracked_site, superuser, settings
):
    settings.OPENAI_API_KEY = "test-openai-key"
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])
    with patch("dashboard.views.GoalPlanningService") as service:
        service.return_value.plan.return_value = tracking_plan()
        drafted = client.post(
            url,
            {"action": "goal_draft", "intent": "Track signup and first value."},
        )

    with patch.object(
        ActivationDefinition,
        "full_clean",
        side_effect=ValidationError("forced activation failure"),
    ):
        response = client.post(
            url,
            {"action": "goal_confirm", "draft_id": draft_id_from(drafted)},
        )

    assert response.status_code == 409
    assert ProductEventDefinition.objects.filter(site=tracked_site).count() == 0
    assert not ActivationDefinition.objects.filter(site=tracked_site).exists()


@pytest.mark.django_db
def test_missing_openai_configuration_preserves_intent(client, tracked_site, superuser, settings):
    settings.OPENAI_API_KEY = ""
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])
    intent = "Track completed signup and first value."

    response = client.post(url, {"action": "goal_draft", "intent": intent})

    assert response.status_code == 503
    assert intent.encode() in response.content
    assert b"not configured yet" in response.content
