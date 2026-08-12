from django.conf import settings


def tracking_snippet(site):
    base_url = settings.SITEHITS_BASE_URL
    return (
        f'<script defer src="{base_url}/js/script.js" '
        f'data-site-key="{site.public_key}" '
        f'data-api-url="{base_url}/api/events"></script>'
    )


def bot_tracking_settings(site, *, include_credentials=True):
    key = site.bot_key if include_credentials else "<set-in-server-environment>"
    return (
        f"SITEHITS_BOT_ENDPOINT={settings.SITEHITS_BASE_URL}/api/bot-events\nSITEHITS_BOT_KEY={key}"
    )


def bot_tracking_agent_instruction(site, *, include_credentials=True):
    endpoint = f"{settings.SITEHITS_BASE_URL}/api/bot-events"
    key = site.bot_key if include_credentials else "$SITEHITS_BOT_KEY"
    return (
        f"Add server-side SiteHits bot tracking to {site.name}'s backend middleware. "
        "Keep the existing browser tracker unchanged. For each document or crawler-facing "
        "request, send a best-effort POST after the response is known (or schedule it with "
        "waitUntil when the runtime provides it); never delay the page response. Exclude APIs, "
        "framework internals, and obvious static assets, but keep robots.txt, llms.txt, "
        "llms-full.txt, sitemap XML files, and Markdown content trackable. POST to "
        f"{endpoint} with Authorization: Bearer {key} and Content-Type: "
        "application/json. The JSON body must contain url and user_agent, and may contain "
        "status_code and an ISO-8601 timestamp. Keep the bot key server-side and do not expose "
        "it in browser code. Treat HTTP 202 with accepted=false as a healthy unrecognized "
        "user-agent response. Log network failures and non-2xx responses with only the HTTP "
        "status, request path, and returned error message; never log the bot key, full URL, or "
        "user-agent. Collector failures must remain best-effort and never break requests."
    )
