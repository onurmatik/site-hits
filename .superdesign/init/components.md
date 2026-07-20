# SiteHits UI components

Updated from the current source on 2026-07-20 for the product-metrics settings flow. SiteHits is a server-rendered Django application using Tailwind CSS v4 utilities and small page-local vanilla JavaScript blocks. It has no React/Vue component library and no template-partial component system.

## Authoritative visual sources

- Document shell: `templates/base.html`
- Product-metrics page: `templates/dashboard/product_metrics_settings.html`
- Form widget classes and field definitions: `dashboard/forms.py`
- Shared tokens and global behavior: `assets/design-system.css`
- Tailwind aliases and reusable utility classes: `assets/tailwind.css`
- Closely related copy/install patterns: `templates/onboarding/install.html`
- Product-metrics dashboard entry and result UI: `templates/dashboard/dashboard.html`
- Brand mark: `static/sitehits-mark.svg`

`static/css/sitehits.css` is generated and minified from the two files under `assets/`; use the source CSS files as design context. Do not pass secrets, `.env` files, database contents, or the generated server-event key to Superdesign.

## Existing shared primitives

These are CSS conventions rather than framework components.

- **Panel (`.sh-panel`)**: white panel, one-pixel `ink/15` border, 2px radius, no shadow. Padding is supplied at the call site.
- **Mono label (`.sh-mono`)**: 11px uppercase mono text with `0.08em` tracking. Used for steps, metadata, and operational labels.
- **Tabular figures (`.sh-tabular`)**: tabular number treatment for metrics.
- **Primary action**: forest background, white text, 2px radius, normally 44–48px minimum height; hover changes to ink.
- **Secondary action**: white background, `ink/15` border, 2px radius; hover strengthens to forest.
- **Form control**: `min-h-11`, full width, 2px radius, `ink/20` border, white background, 12px horizontal padding, forest focus border. Textareas add vertical padding and a minimum height.
- **Code/copy surface**: paper background, hairline border, mono 12px text, adjacent copy action, success feedback through a live region.
- **Status/error treatment**: success text uses `success`; validation and blocking feedback use danger text with a coral-tinted/bordered surface.
- **Brand lockup**: the dashboard and install screen often use a CSS-built 32–36px forest square containing a smaller white/50 outlined square. Public pages use `static/sitehits-mark.svg`.

## Current `/dashboard/<site-slug>/product-metrics` inventory

All line references are in `templates/dashboard/product_metrics_settings.html` unless noted.

- **Page shell** (lines 6–14): standalone `max-w-5xl` main area, back link to the selected site, page title, explanation, and optional `Settings saved` status. This route does not render the authenticated dashboard header or footer.
- **Event catalog panel** (lines 16–41): Step 1 label, event formset, validation errors, repeated fieldsets, and `Save event catalog` action.
  - Fieldset columns become two at `md`.
  - Fields: event name, display name, firing condition, aggregation, unit, and existing-row delete.
  - Widget placeholders and classes come from `dashboard/forms.py:8-34`.
- **Activation funnel panel** (lines 43–60): Step 2 label, enable checkbox, start/goal event selects, validation errors, and separate save action.
- **Install panel** (lines 62–73): Step 3 label, server-environment textarea, generated agent-instruction textarea, two copy actions, and one polite live status line.
- **Page-local copy behavior** (lines 77–92): selects the target textarea, uses the Clipboard API, and reports copied/fallback status.

The current screen presents all three panels at once and requires technical form completion. The requested redesign replaces this default experience with one continuous **Describe → Review → Install** flow.

## Required flow-level components

These are design requirements for the new flow, not components that already exist in source.

### Shared across all three states

- **Flow header**: selected-site back link, `Product metrics` context, short outcome-oriented explanation, and optional saved/status message.
- **Three-step progress indicator**: exactly `Describe`, `Review`, `Install`; communicates current and completed steps with text and structure, not color alone. It must remain readable on mobile and must not behave like three unrelated design choices.
- **Primary work panel**: one dominant `.sh-panel` per state, centered within the existing `max-w-5xl` page shell.
- **Advanced setup disclosure**: native `<details>`/`<summary>` entry to the manual technical controls for users who need them. It is secondary to the AI flow.

### Describe state

- **Natural-language prompt composer**: visible label/question `What do you want to track?`, large textarea, outcome-based placeholder/example, short privacy/no-save reassurance, and primary `Draft tracking plan` action.
- **Example prompts**: optional compact buttons or selectable text examples for activation, feature adoption, and revenue. They must remain visually secondary and should populate/guide the textarea rather than act as categories that constrain input.
- **Generation state**: action disables without changing geometry, progress is announced in a status region, and copy explains that SiteHits is drafting a plan.
- **Clarification state**: when a material ambiguity remains, ask one focused question in the same language as the description. Do not expose event-name or aggregation jargon as a clarification.

### Review state

- **Plan event row/card**: human-readable display name, plain-language firing condition, and plain-language measurement (`Count events`, `Count unique actors`, `Sum TRY`, or `Average seconds`). Stable event identifier and raw aggregation belong in collapsed implementation details.
- **Activation journey**: optional compact `Start event → Goal event` relationship with clear labels.
- **Assumption/attention note**: explicit assumptions and unsupported requests; uses text plus visual treatment. Unsupported requests must not be silently dropped.
- **Implementation details disclosure**: stable event IDs, aggregations, units, and any other machine-facing values in mono text.
- **Review actions**: secondary `Edit description`; primary `Approve & create instruction`. Nothing is persisted before approval.

### Install state

- **Approved summary**: concise success/status treatment and final event/activation summary.
- **Server environment copy panel**: secret-safe preview, with the actual private value never sent to the AI model or embedded in Superdesign context. The generated agent text refers to `$SITEHITS_SERVER_EVENT_KEY` rather than reproducing the secret.
- **Agent instruction copy panel**: readable/collapsible preview of the deterministic instruction and primary copy action.
- **Completion actions**: return to the selected-site dashboard and edit/revise the tracking plan.
- **Copy state**: preserves geometry, switches label to `Copied`, announces success through `aria-live`, and restores the default label after a short interval.

## Related source patterns worth reusing

- `templates/onboarding/install.html:7-61` has the closest SiteHits-native install/copy composition, including the brand lockup, paper code surfaces, copy icons, success state, and paired completion actions.
- `templates/dashboard/dashboard.html:213-258` is the downstream product-metrics report and its empty/configured state. The settings flow must use the same product vocabulary.
- `templates/dashboard/dashboard.html:13-80` is the authenticated shell, but it is not rendered on the current product-metrics route. Do not accidentally include that header in a pixel-faithful current-page reproduction.

## Icon and asset rules

- Reuse the real six-block `static/sitehits-mark.svg` wherever an SVG brand asset is needed.
- Reuse the inline copy/check SVG paths from `templates/onboarding/install.html` if the flow shows icon-bearing copy controls.
- The product-metrics page currently has no decorative images. Do not add illustrations, stock imagery, emoji icons, or arbitrary icon libraries.
- The external FeatureRequest launcher is injected by `templates/base.html`; it is not a SiteHits component and must not influence the page palette.
