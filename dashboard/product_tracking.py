from django.conf import settings

from analytics.models import ActivationDefinition, ProductEventDefinition


def server_event_settings(site):
    return (
        f"SITEHITS_EVENT_ENDPOINT={settings.SITEHITS_BASE_URL}/api/server-events\n"
        f"SITEHITS_SITE_KEY={site.public_key}\n"
        f"SITEHITS_SERVER_EVENT_KEY={site.server_event_key}"
    )


def product_tracking_agent_instruction(site):
    definitions = list(ProductEventDefinition.objects.filter(site=site))
    try:
        activation = ActivationDefinition.objects.select_related(
            "start_event",
            "goal_event",
        ).get(site=site)
    except ActivationDefinition.DoesNotExist:
        activation = None

    catalog = "\n".join(
        (
            f"- {definition.event_name}: {definition.description} "
            f"Aggregation={definition.aggregation}"
            + (f", unit={definition.unit}" if definition.unit else "")
        )
        for definition in definitions
    ) or "- No product events are configured yet; do not invent event names."
    activation_text = (
        f"Activation starts with {activation.start_event.event_name} and succeeds with "
        f"{activation.goal_event.event_name}."
        if activation
        else "No activation funnel is configured."
    )
    endpoint = f"{settings.SITEHITS_BASE_URL}/api/server-events"
    return f"""Add SiteHits product-event tracking to {site.name}. Inspect the target repository before changing code and preserve existing behavior.

Server configuration:
SITEHITS_EVENT_ENDPOINT={endpoint}
SITEHITS_SITE_KEY={site.public_key}
SITEHITS_SERVER_EVENT_KEY=<set this in the tracked application's server environment>

Event catalog:
{catalog}

Activation:
{activation_text}

Implement a small, tested local adapter rather than adding a SiteHits SDK. Send authoritative events with POST {endpoint}, Authorization: Bearer $SITEHITS_SERVER_EVENT_KEY, and JSON containing event_id, event_name, actor_id, timestamp, optional value/unit, optional query-free path, and at most 10 short scalar properties. Use an immutable internal primary key or UUID as actor_id; never use email, name, phone, access tokens, or other PII. Give every logical event a stable idempotency event_id.

Emit sales, terms acceptance, signup, and activation events only after the corresponding domain change is durably successful, never merely on a button click. Use the project's existing task queue or outbox when available. Otherwise send after transaction commit with a short timeout, treat delivery as best-effort, and never fail the product action because analytics is unavailable.

For authenticated browser pageviews and browser custom events, create a one-hour HS256 JWT on the application server. Set iss={site.public_key}, aud=sitehits, iat/current Unix time, exp/no more than one hour later, and sub=hex HMAC-SHA256(SITEHITS_SERVER_EVENT_KEY, the exact UTF-8 actor_id). Pass only this signed token to the SiteHits tracker as data-actor-token, or call window.sitehits(\"identify\", token) after SPA authentication changes. Never expose SITEHITS_SERVER_EVENT_KEY or the raw actor_id in browser code. Be careful not to leak a user's token through shared page caches.

Add tests that mock network calls and prove events fire once at the authoritative success point, retries reuse event_id, failures do not break the application flow, JWT claims and expiry are correct, and no PII is sent."""
