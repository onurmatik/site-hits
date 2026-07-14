import json

import pytest

from analytics.bots import CRAWLER_RULES, classify_crawler
from analytics.models import BotEvent


@pytest.mark.parametrize(
    ("user_agent", "provider", "crawler", "category"),
    [
        ("Mozilla/5.0; ChatGPT-User/1.0", "OpenAI", "ChatGPT-User", "answer"),
        ("Mozilla/5.0 compatible; Googlebot/2.1", "Google", "Googlebot", "indexing"),
        ("Applebot-Extended/1.0", "Apple", "Applebot-Extended", "training"),
        ("Grok-DeepSearch/1.0", "xAI", "Grok-DeepSearch", "answer"),
        ("facebookexternalhit/1.1", "Meta", "facebookexternalhit", "other"),
    ],
)
def test_crawler_catalog_classifies_specific_known_tokens(
    user_agent,
    provider,
    crawler,
    category,
):
    match = classify_crawler(user_agent)

    assert len(CRAWLER_RULES) == 61
    assert (match.provider, match.crawler, match.category) == (
        provider,
        crawler,
        category,
    )


def test_crawler_catalog_ignores_human_browsers():
    assert classify_crawler("Mozilla/5.0 Chrome/126.0 Safari/537.36") is None


@pytest.mark.django_db
def test_server_collector_stores_sanitized_known_bot_request(client, tracked_site):
    raw_user_agent = "Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)"
    response = client.post(
        "/api/bot-events",
        data=json.dumps(
            {
                "url": "https://example.com/docs/start?secret=hidden&utm_source=bot",
                "user_agent": raw_user_agent,
                "status_code": 404,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tracked_site.bot_key}",
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    event = BotEvent.objects.get()
    assert event.site == tracked_site
    assert event.path == "/docs/start"
    assert event.status_code == 404
    assert event.provider == "OpenAI"
    assert event.crawler == "GPTBot"
    assert event.category == BotEvent.Category.TRAINING
    assert event.verification == BotEvent.Verification.USER_AGENT
    assert "hidden" not in repr(event.__dict__)
    assert raw_user_agent not in repr(event.__dict__)


@pytest.mark.django_db
def test_server_collector_ignores_humans_and_rejects_bad_auth_or_host(client, tracked_site):
    payload = {
        "url": "https://example.com/",
        "user_agent": "Mozilla/5.0 Chrome/126.0 Safari/537.36",
        "status_code": 200,
    }
    ignored = client.post(
        "/api/bot-events",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tracked_site.bot_key}",
    )
    missing_key = client.post(
        "/api/bot-events",
        data=json.dumps(payload),
        content_type="application/json",
    )
    wrong_host = client.post(
        "/api/bot-events",
        data=json.dumps(
            {
                **payload,
                "url": "https://evil.example/",
                "user_agent": "ClaudeBot/1.0",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tracked_site.bot_key}",
    )

    assert ignored.status_code == 202
    assert ignored.json() == {"accepted": False}
    assert missing_key.status_code == 401
    assert wrong_host.status_code == 400
    assert BotEvent.objects.count() == 0


@pytest.mark.django_db
def test_bot_collector_uses_the_same_payload_size_limit(client, tracked_site, settings):
    settings.SITEHITS_MAX_EVENT_BYTES = 32

    response = client.post(
        "/api/bot-events",
        data=json.dumps(
            {
                "url": "https://example.com/",
                "user_agent": "GPTBot/1.0",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tracked_site.bot_key}",
    )

    assert response.status_code == 413
