# SiteHits pages and dependency trees

Updated from the current render path on 2026-07-20. The target is the selected-site product-metrics settings journey.

## Target: product metrics settings

Route: `/dashboard/<site-slug>/product-metrics`

```text
config/urls.py
└─ include("dashboard.urls") at /dashboard/
   └─ dashboard/urls.py
      └─ path("<slug:site_slug>/product-metrics", product_metrics_settings)
         └─ dashboard/views.py:240-315
            ├─ `_visible_site()` owner/superuser scope
            ├─ ProductEventDefinition queryset
            ├─ optional ActivationDefinition
            ├─ ProductEventDefinitionFormSet (`dashboard/forms.py`)
            ├─ ActivationDefinitionForm (`dashboard/forms.py`)
            ├─ server_event_settings (`dashboard/product_tracking.py`)
            ├─ product_tracking_agent_instruction (`dashboard/product_tracking.py`)
            └─ templates/dashboard/product_metrics_settings.html
               ├─ extends templates/base.html
               │  ├─ static/css/sitehits.css
               │  │  └─ generated from assets/tailwind.css
               │  │     └─ imports assets/design-system.css
               │  ├─ external, nonvisual SiteHits tracker
               │  └─ external FeatureRequest launcher
               └─ inline clipboard behavior
```

Model dependencies:

```text
TrackedSite
├─ ProductEventDefinition[]
│  ├─ event_name (stable, site-unique)
│  ├─ display_name
│  ├─ description / firing condition
│  ├─ aggregation (count | unique_actors | sum | average)
│  └─ unit (sum/average only)
└─ ActivationDefinition? (one per site)
   ├─ start_event
   └─ goal_event (must differ and belong to same site)
```

## Current rendered page

The complete target template is below 1000 lines and should be passed whole to Superdesign.

### Current happy path

1. Header shows `← <site name>`, `Product metrics`, and technical explanatory copy.
2. Step 1 displays the complete event catalog formset with one blank form plus existing definitions.
3. Step 2 displays activation enable, start event, and goal event controls.
4. Step 3 always displays environment values and the generated agent instruction.
5. The three independent actions save event catalog, save activation, or copy text.

This is the long technical form shown in the referenced screenshot. It is the reproduction ground truth, not the desired final interaction.

### Current validation and conditional states

- Invalid event name, duplicate event, bad aggregation/unit combination, protected deletion, and formset non-form errors render in or above event fieldsets.
- Activation requires two different configured events when enabled.
- `Settings saved` is a header status controlled by a query parameter.
- When there are no definitions, the generated instruction explicitly says no product events are configured.
- The existing generated instruction and environment text can contain a private site key at render time. Never include a real rendered value in design context or AI input.

## Required page flow: one journey, three states

The user explicitly confirmed that Describe, Review, and Install are consecutive steps. Do not present them as three selectable concepts or design variants.

### State 1 — Describe

Purpose: capture business intent without exposing event-schema work.

Required content hierarchy:

1. Back link to the selected-site dashboard.
2. `Product metrics` context and ordered three-step progress.
3. Eyebrow such as `Step 1 of 3 · Describe`.
4. Main question: **What do you want to track?**
5. Supporting copy: describe outcomes and key moments in ordinary language; SiteHits will draft the events and agent instruction.
6. One large labelled natural-language textarea.
7. Example input, preferably as placeholder/support rather than prefilled data: `I want to know how many people sign up, create their first project, and how much subscription revenue we collect in TRY.`
8. Optional compact examples for activation, adoption, and revenue.
9. Reassurance: nothing is saved until review/approval.
10. Primary action: `Draft tracking plan`.
11. Secondary Advanced setup disclosure for the manual technical flow.

Interaction states:

- Empty submission: associate a concise validation message with the textarea.
- Drafting: disable repeat submission, keep action geometry stable, announce progress.
- Material ambiguity: ask one focused plain-language clarification in place, preserving the original answer. Example: whether signup means account creation or email verification.
- Provider/server failure: keep the answer, show a recoverable alert, and offer retry.

Do not ask the user to choose event IDs, aggregations, units, or activation database relations unless the request itself is genuinely ambiguous.

### State 2 — Review

Purpose: make the AI draft legible and safe before any write.

Required content hierarchy:

1. Completed Describe step and current Review step in progress.
2. Eyebrow `Step 2 of 3 · Review` and title `Review your tracking plan`.
3. Short note that nothing has been saved yet.
4. Proposed event list. Each entry shows:
   - outcome/display label;
   - when it fires, in plain language;
   - how it is measured, in plain language; and
   - unit when numeric.
5. Optional activation journey as labelled `Start → Goal`.
6. Explicit assumptions.
7. Unsupported requests or constraints, if any; never silently omit them.
8. Collapsed `Implementation details` disclosure containing stable identifiers and raw aggregation values.
9. Secondary `Edit description` and primary `Approve & create instruction` actions.

Representative review data for design only:

```text
Completed sign-ups
Count each person once
Fire after account creation is durably saved
Technical id: signup_completed · unique_actors

First project created
Count each person once
Fire after their first project is durably saved
Technical id: first_project_created · unique_actors

Subscription revenue
Sum successful payments in TRY
Fire only after payment is durably confirmed
Technical id: subscription_revenue · sum · TRY

Activation: Completed sign-up → First project created
Assumption: signup means successful account creation, not email verification
```

Review guards:

- Approval remains disabled if a blocking clarification or unsupported contract prevents a valid plan.
- Editing returns to Describe with the original answer intact.
- Approval is the only transition that persists the validated plan.
- Existing catalogs should ultimately expose `Added / Reused / Changed` semantics rather than silently overwrite or delete events; the visual review must leave room for this status.

### State 3 — Install

Purpose: hand the approved, persisted plan to the coding agent.

Required content hierarchy:

1. Describe and Review shown complete; Install current.
2. Success/status statement that the tracking plan is ready.
3. Compact approved-plan summary.
4. `Server environment` copy surface using masked/example values in design artifacts.
5. `Instruction for your agent` preview and primary copy action.
6. Clear note that the private key belongs only in the tracked application's server environment.
7. Completion actions: `Back to <site> dashboard` and `Edit tracking plan`.
8. Polite copy feedback that does not disrupt layout.

Security/fidelity rules:

- The AI drafting request receives only product intent and safe catalog context, never OpenAI credentials or the site server-event key.
- The final agent instruction is compiled from the approved validated plan, not accepted as untrusted free-form AI text.
- The instruction references `$SITEHITS_SERVER_EVENT_KEY`; design previews use `shs_…` only.
- Do not reproduce the real key visible in any screenshot or local rendered page.

## Advanced setup

The existing manual event catalog and activation forms remain a power-user/fallback path under a secondary disclosure or link. They are not the default first state and must not appear expanded alongside the Describe composer.

When shown, preserve all existing validation and field relationships:

- lowercase stable event name;
- display name;
- authoritative firing condition;
- aggregation;
- numeric unit rule;
- protected activation events; and
- two distinct activation events.

## Entry and downstream pages

### Selected-site dashboard (`/dashboard/<site-slug>`)

- Links to the target route from the header action and the Activation & product metrics section.
- After install, it displays configured activation/product metrics from `GET /api/analytics/product-metrics`.
- Use its vocabulary and status conventions, but do not import its sticky shell into the current target-page reproduction.

### Onboarding install (`/onboarding/<site-slug>/`)

- Links to the target route from `Optional · Product events`.
- Its install/copy panels are the closest existing SiteHits visual pattern for State 3.

## Superdesign context set for this target

Pass these implementation files in full; none exceeds 1000 lines:

- `.superdesign/design-system.md`
- `assets/design-system.css`
- `assets/tailwind.css`
- `templates/base.html`
- `templates/dashboard/product_metrics_settings.html`
- `dashboard/forms.py`
- `dashboard/product_tracking.py`
- `templates/onboarding/install.html`
- `static/sitehits-mark.svg`

For route/data semantics, optional supporting context:

- `dashboard/views.py:233:315`
- `analytics/models.py:107:188`
- `dashboard/urls.py`

Do not pass `.env*`, rendered pages containing live secrets, database files, `static/css/sitehits.css`, vendor JavaScript, screenshots with private keys, or favicon PNG variants.
