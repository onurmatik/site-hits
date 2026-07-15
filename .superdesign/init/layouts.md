# SiteHits Layouts

Updated from the current source on 2026-07-15.

## Base document

Source: `templates/base.html` (complete shared implementation).

```html
{% load static %}
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>{% block title %}SiteHits{% endblock %}</title>
    <link rel="icon" href="{% static 'sitehits-mark.svg' %}" type="image/svg+xml">
    <link rel="icon" href="{% static 'brand/favicon-32x32-v3.png' %}" sizes="32x32" type="image/png">
    <link rel="apple-touch-icon" href="{% static 'brand/apple-touch-icon-v3.png' %}">
    <link rel="stylesheet" href="{% static 'css/sitehits.css' %}">
    {% block head %}{% endblock %}
    <script defer src="https://sitehits.io/js/script.js" data-site-key="sh_lWroxuMoxPD6KVEIyqmL63gf" data-api-url="https://sitehits.io/api/events"></script>
  </head>
  <body>
    {% block body %}{% endblock %}
    {% block scripts %}{% endblock %}
    <script
      src="https://featurerequest-assets.s3.amazonaws.com/static/projects/embed-widget.js"
      data-fr-origin="https://featurerequest.io"
      data-fr-owner="onurmatik"
      data-fr-project="site-hits"
      data-fr-label="Feedback"
      data-fr-position="right"
      data-fr-color="#06B6D4"
      defer
    ></script>
  </body>
</html>
```

The first external script is nonvisual SiteHits self-tracking. The second creates the third-party feedback launcher at the right viewport edge; it is visible at runtime but its implementation is not in this repository.

## Authenticated dashboard shell

Complete implementation: `templates/dashboard/dashboard.html`.

```text
#dashboard-app (min-height: 100vh; site/period/granularity data attributes)
├─ sticky 64px header
│  ├─ SiteHits CSS-built mark and wordmark
│  ├─ downward site menu
│  ├─ operational status
│  └─ logout form
├─ main (max-width: 1440px; 16px mobile / 32px desktop padding)
│  ├─ title, aggregate/site metadata, and period controls
│  ├─ inline error
│  ├─ five human-traffic KPI cards
│  ├─ full-width 300px traffic chart
│  ├─ bot traffic section
│  │  ├─ five category cards
│  │  └─ two-column provider/path breakdown
│  └─ two-column human-traffic breakdown grid
├─ footer (max-width: 1440px)
├─ add-site dialog
└─ selected-site-only public-widget dialog
```

There is no sidebar. KPI grids are one column by default, two from `sm`, and five from `lg`. Two-column breakdown regions collapse to one column below `lg`. The period control remains horizontally scrollable. The site dropdown is viewport-wide with 16px insets on mobile and a 256px anchored menu from `sm` upward.

## Actual `/dashboard/all` render branch

The route calls `dashboard.views.dashboard(request, site_slug="all")`. `selected_site` remains `None`, therefore:

- The title and site trigger both read `All sites`.
- The subtitle reads `Aggregate across active properties; visitors are not deduplicated between sites.`
- The site dropdown lists all sites visible to the current user and marks `All sites` current.
- The period control is shown.
- The `Embed widget` button and its dialog are not rendered.
- Bot traffic setup links are not rendered.
- All remaining KPI/chart/breakdown structures are the same as the single-site route and are populated as one aggregate dataset.

Do not reproduce the selected-site-only controls when drafting `/dashboard/all`.

## Other layouts

- **Anonymous landing** (`templates/onboarding/landing.html`): 64px public header, centered conversion hero and URL form, then a wide dashboard preview capped at 1200px.
- **Passwordless auth** (`templates/registration/signup.html`): centered panel capped at 448px, real logo, Google action, divider, email magic-link form, and sent/error branches.
- **Legacy login** (`templates/registration/login.html`): centered 448px username/password panel.
- **Onboarding confirmation** (`templates/onboarding/confirm.html`): centered panel capped at 576px.
- **Tracker installation** (`templates/onboarding/install.html`): centered panel capped at 672px with script, agent, server-side bot setup, copy states, and final actions.
- **Authentication error** (`templates/socialaccount/authentication_error.html`): centered 448px error panel.
- **Public widget** (`templates/dashboard/widget.html`): standalone, frameable document. It deliberately does not extend `base.html`, loads `static/css/widget.css`, refreshes every 60 seconds, and is capped at 400px by 600px.
