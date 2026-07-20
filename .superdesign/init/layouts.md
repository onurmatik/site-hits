# SiteHits layouts

Updated from the current source on 2026-07-20 for `/dashboard/<site-slug>/product-metrics`.

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
      data-fr-color="#1A3C2B"
      defer
    ></script>
  </body>
</html>
```

The self-tracking script is nonvisual. The FeatureRequest script injects a visible feedback launcher at the right edge at runtime, but its implementation is external and it must not be recreated as part of the product-metrics design.

## Current product-metrics settings layout

Complete page implementation: `templates/dashboard/product_metrics_settings.html` (93 lines; pass it whole).

```text
body (paper canvas from shared CSS)
└─ main (min-height: 100vh; max-width: 1024px; 16px padding → 32px at md; 32px vertical gaps)
   ├─ page header
   │  ├─ back link to selected-site dashboard
   │  ├─ Product metrics title and supporting sentence
   │  └─ optional Settings saved status
   ├─ Event catalog panel
   │  ├─ Step 1 label and explanatory header
   │  ├─ repeated event fieldsets (1 column → 2 at md)
   │  └─ Save event catalog action
   ├─ Activation funnel panel
   │  ├─ Step 2 label and explanation
   │  ├─ enable checkbox and start/goal selects (1 column → 2 at md)
   │  └─ Save activation funnel action
   └─ Install with your agent panel
      ├─ Step 3 label and explanation
      ├─ server-environment textarea + copy action
      ├─ generated-agent-instruction textarea + copy action
      └─ polite copy-status line
```

### Actual render branch

`dashboard.views.product_metrics_settings()` always renders this template for an authenticated user who owns the selected site (or a superuser). There is no responsive or feature-flag branch that swaps layouts. Conditional pieces are:

- `Settings saved` appears when `?saved=events` or `?saved=activation` is present.
- Existing event rows include a delete checkbox; the one extra blank form does not.
- Form and non-field errors render only after invalid POSTs.
- Activation selects contain the selected site's current event definitions.
- Generated environment and agent-instruction text always render, even when no events exist.

This route extends `base.html` directly. It does **not** include the sticky dashboard navigation, dashboard site menu, dashboard footer, or a SiteHits brand lockup. The only route context is the `← <site name>` back link.

## Required Describe → Review → Install layout

The selected direction is one three-step journey, not three alternatives. Preserve the current route and SiteHits visual language while changing the information hierarchy.

```text
body (paper canvas)
└─ main (min-height: 100vh; max-width: 1024px; 16px → 32px outer padding)
   ├─ flow header
   │  ├─ back link to selected-site dashboard
   │  ├─ Product metrics context/title
   │  └─ three-step progress: Describe — Review — Install
   ├─ one dominant state panel
   │  ├─ Describe: question + natural-language textarea + helper/examples + primary action
   │  ├─ Review: proposed plan + activation + assumptions/unsupported items + approve/edit actions
   │  └─ Install: approved summary + environment/instruction copy surfaces + completion actions
   └─ optional Advanced setup disclosure
      └─ existing manual event catalog and activation controls
```

Only the active state occupies the dominant panel. Avoid showing all three full forms/panels at once. Completed steps remain visible through the progress indicator and can be represented as completed text states; do not make users choose a "design 1/2/3" option.

## Desktop composition

- Keep the existing `max-w-5xl` outer width and `md:p-8` gutters.
- The flow header may place the step indicator below the title or align it in a second row; it must not look like dashboard period tabs.
- Use a single primary panel with 20px mobile / 24–32px desktop padding.
- Describe should give the textarea most of the visual weight; do not surround it with a dense technical form.
- Review may use a two-area layout only when it improves scanning: main proposed plan and a narrower assumptions/activation summary. It must collapse cleanly.
- Install code/instruction surfaces may stack; copying is more important than side-by-side density. Long instruction content must wrap or scroll inside its own surface without page-level horizontal scrolling.
- Primary actions sit at the end of the reading order. Secondary back/edit actions remain visually quieter.

## Mobile composition

- Retain 16px viewport gutters; never create page-level horizontal scrolling.
- Render the three steps as a compact ordered row or stacked progress summary with full text labels. The current step must be announced textually.
- All event-plan rows stack their label, firing condition, and measurement.
- Action groups stack into full-width 48px targets when necessary.
- Technical identifiers and code may scroll within bounded paper surfaces.
- Keep advanced manual controls collapsed by default; if opened, preserve the existing one-column field layout.

## Related layouts

- `templates/onboarding/install.html`: centered `max-w-2xl` install/copy panel and the closest reusable visual reference for the new Install state.
- `templates/dashboard/dashboard.html`: selected-site dashboard entry point and downstream product-metrics report. Its sticky authenticated shell is contextual reference only; it is not part of the current settings route.
- `templates/onboarding/landing.html`, `templates/onboarding/confirm.html`, and registration templates are outside this design task.
