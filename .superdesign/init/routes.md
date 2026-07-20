# SiteHits routes

Updated from `config/urls.py`, `dashboard/urls.py`, `dashboard/views.py`, `dashboard/forms.py`, and related templates on 2026-07-20.

## Target route

| Route | Name | View | Template | Access |
| --- | --- | --- | --- | --- |
| `/dashboard/<site-slug>/product-metrics` | `product-metrics-settings` | `dashboard.views.product_metrics_settings` | `templates/dashboard/product_metrics_settings.html` | Login required; site owner or superuser |

Resolution chain:

```text
config/urls.py
└─ path("dashboard/", include("dashboard.urls"))
   └─ dashboard/urls.py
      └─ path("<slug:site_slug>/product-metrics", product_metrics_settings)
         └─ dashboard/views.py:240-315
            └─ templates/dashboard/product_metrics_settings.html
               └─ templates/base.html
```

The route intentionally has no trailing slash. `product_metrics_settings()` scopes the site through `_visible_site()`, returning 404 for a signed-in non-owner rather than revealing its existence.

## Existing request behavior

### GET

The view loads, for one selected site:

- all `ProductEventDefinition` rows;
- the optional one-to-one `ActivationDefinition`;
- an event model formset with one extra blank row;
- the activation form whose start/goal choices are restricted to that site;
- `server_event_settings(site)`;
- `product_tracking_agent_instruction(site)`; and
- the optional `saved` query value.

### POST `action=events`

- Validates and saves the event catalog formset.
- Deletes checked rows unless protected by the activation definition.
- Enforces site-local unique event names and aggregation/unit rules.
- Redirects back to the same route with `?saved=events`.

### POST `action=activation`

- Validates start and goal as two different events from the selected site.
- Creates/updates the one activation definition, or removes it when disabled.
- Redirects back with `?saved=activation`.

There is no AI drafting endpoint, review state, or atomic approve operation in the current source. Superdesign should depict the requested experience, but must not assume an undocumented URL or network contract.

## Required UI state journey on the target route

The approved product direction is one continuous **Describe → Review → Install** flow on this product-metrics settings surface.

1. **Describe** — user answers `What do you want to track?` in natural language; submitting requests an AI-authored structured draft.
2. **Review** — SiteHits displays proposed events, optional activation journey, assumptions, and unsupported requests. The user can edit the original description or approve. Nothing is persisted before approval.
3. **Install** — after approval and successful persistence, SiteHits generates the final deterministic agent instruction and presents secret-safe environment/copy actions.

These are states of the same journey, not three pages to choose between. URL/state implementation is intentionally left to production code. A design may use same-route POST/redirect or progressive enhancement, but must preserve a functional server-rendered path and must not invent visible browser URLs as settled architecture.

## Entry and exit routes

| Route | Relationship to product-metrics flow |
| --- | --- |
| `/dashboard/<site-slug>` (`dashboard-site`) | Primary entry and back destination. Product metrics action at `templates/dashboard/dashboard.html:94-98`; configure/setup actions at lines 213-258. |
| `/onboarding/<site-slug>/` (`onboarding-install`) | Secondary entry through `Configure product metrics` at `templates/onboarding/install.html:33-38`. |
| `/dashboard/all` (`dashboard-all`) | Portfolio dashboard; no product-metrics setup because product events are site-scoped. |
| `/api/analytics/product-metrics?site=<site-slug>&period=<period>` | Downstream read-only reporting endpoint used by the selected-site dashboard after configuration. It rejects `site=all`. |
| `POST /api/server-events` | Collector endpoint the generated implementation instruction tells the tracked application to call. It is not called by the settings screen. |
| `/onboarding/<site-slug>/` | Existing server/browser tracker install context; the product-metrics Install state should remain visually consistent with this page. |

## Model and instruction dependencies

```text
Natural-language description (new requirement)
└─ AI structured draft (new requirement; never includes private keys)
   ├─ events[]
   │  ├─ event_name
   │  ├─ display_name
   │  ├─ description / firing condition
   │  ├─ aggregation: count | unique_actors | sum | average
   │  └─ unit (required only for sum/average)
   ├─ optional activation: start_event + goal_event
   ├─ assumptions / clarification
   └─ unsupported requests
      └─ user approval
         ├─ ProductEventDefinition rows
         ├─ optional ActivationDefinition
         └─ deterministic `product_tracking_agent_instruction(site)`
```

Current model limits that the Review UI must represent honestly:

- Event count, unique actors, sum, or average.
- Units only for sum/average.
- At most one activation funnel.
- Activation is exactly two events: start and later goal.
- No multi-step funnel, property-filtered goal, retention goal, or custom conversion window exists in the current model.

## Other user-facing routes

- `/`: anonymous landing.
- `/start/`: onboarding POST.
- `/onboarding/`: authenticated site confirmation.
- `/accounts/signup/`, `/accounts/login/`, provider and magic-link routes: authentication.
- `/dashboard/all`: all-sites reporting.
- `/dashboard/<site-slug>`: selected-site reporting.
- `/widget/<public-key>/`: public last-hour widget.
- `/admin/`: Django admin.
- `/api/docs`: generated API documentation.

## Security boundary relevant to the flow

- The OpenAI/API credential is server-only and has no visual representation.
- The site's `SITEHITS_SERVER_EVENT_KEY` is also server-only for the tracked application. It must never be sent as AI prompt context.
- Superdesign examples use placeholders such as `sh_…`, `shs_…`, and `$SITEHITS_SERVER_EVENT_KEY`; never real values from rendered pages, attachments, local environment files, or database records.
