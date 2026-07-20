# SiteHits product-metrics design system

## Scope and source of truth

This document governs the redesign of `/dashboard/<site-slug>/product-metrics` from a long technical form into one continuous AI-assisted **Describe → Review → Install** journey. The three labels are consecutive steps, not three alternative designs.

Use these sources in order:

1. `templates/dashboard/product_metrics_settings.html` — current rendered page and reproduction ground truth.
2. `assets/design-system.css` and `assets/tailwind.css` — exact visual tokens and reusable CSS conventions.
3. `templates/base.html` — document shell and loaded assets.
4. `dashboard/forms.py` — current manual field classes, labels, placeholders, and validation surface.
5. `templates/onboarding/install.html` — closest existing install/copy composition.
6. `dashboard/product_tracking.py` — deterministic instruction content and environment shape.
7. `dashboard/views.py:233:315`, `analytics/models.py:107:188`, and `dashboard/urls.py` — route, authorization, persistence, and model limits.
8. `templates/dashboard/dashboard.html:94:103,213:258` — entry point and downstream product-metrics reporting vocabulary.

Do not infer the target layout from the dashboard header: the current product-metrics route extends `base.html` directly and does not render the authenticated sticky dashboard shell.

## Product context

SiteHits is a small, cookieless, multi-site analytics service. Product events are authoritative business events emitted by the tracked application's backend (or identified browser events signed by that backend). A selected site may define:

- product events measured as event count, unique actors, sum, or average;
- a unit for numeric sum/average metrics; and
- one optional two-event activation funnel.

The settings page's job is to turn business intent into a safe, validated tracking contract and a reliable implementation instruction for a coding agent. It is not an analytics dashboard, schema editor by default, chatbot, or open-ended AI playground.

## User problem and target outcome

The current page asks users to fill technical fields (`event_name`, `display_name`, firing condition, aggregation, unit), configure activation separately, and then understand a long generated instruction. That sequence assumes event-model expertise and shows all work at once.

The target experience asks one understandable question first:

> What do you want to track?

SiteHits then drafts a structured plan, lets the user verify the business meaning, and only after explicit approval persists the plan and generates the final agent instruction.

Success means a user can describe outcomes such as signup, activation, adoption, and revenue without naming events or choosing aggregation jargon; still understand exactly what will be tracked before saving; and hand a clear instruction to their coding agent.

## Hard interaction model

```text
Describe business intent
→ AI returns a strict structured draft
→ server validates against SiteHits capabilities
→ one focused clarification only when materially necessary
→ Review human-readable plan and implementation details
→ explicit user approval
→ atomic persistence
→ deterministic agent instruction
→ Install/copy handoff
```

Hard rules:

- Describe, Review, and Install form one ordered journey.
- Show one dominant state at a time; do not stack three full panels like the current page.
- Nothing is saved during Describe or initial Review.
- Approval is the only action that persists the proposed plan.
- AI drafts structured data; the final agent instruction is compiled from the approved, validated plan.
- Never send OpenAI credentials, site private keys, or `.env` contents to the AI model or Superdesign.
- Never silently discard unsupported intent, invent unsupported configuration, delete existing definitions, or overwrite meaning without review.
- Keep the existing manual forms available as secondary `Advanced setup`, collapsed by default.

## Information architecture

### Persistent flow frame

- Back link: `← <site name>` returning to the selected-site dashboard.
- Product context: `Product metrics`.
- Short sentence explaining that SiteHits turns important outcomes into a tracking plan and agent instruction.
- Three-step progress indicator: `Describe`, `Review`, `Install`.
- One centered dominant `.sh-panel` for the active state.
- Optional saved/draft/error status near the flow header or active panel, never detached at the bottom of the page.

The current page's `max-w-5xl`, `p-4 md:p-8`, and warm paper canvas remain the outer layout contract.

### Step progress semantics

- Use an ordered list or similarly meaningful structure.
- Always show all three text labels.
- Current state uses explicit text or `aria-current="step"`; completed steps include a check/complete label; upcoming steps are muted.
- A connecting hairline is acceptable. Avoid circles/pills large enough to make this look like generic SaaS onboarding.
- Do not implement a tablist unless completed states are actually navigable.
- On mobile, compress spacing or stack metadata, not the labels into unreadable abbreviations.

## State 1 — Describe

### Content

- Eyebrow: `Step 1 of 3 · Describe`.
- Primary heading/question: `What do you want to track?`.
- Helper: ask for meaningful product outcomes and moments in ordinary language; SiteHits will turn them into a reviewable plan.
- One large, visibly labelled textarea.
- Example/placeholder:
  `I want to know how many people sign up, create their first project, and how much subscription revenue we collect in TRY.`
- Reassurance: `Nothing is saved until you review and approve the plan.`
- Primary action: `Draft tracking plan`.
- Secondary disclosure/link: `Advanced setup` for manual configuration.

Optional example prompts may cover:

- `Track signup → first value activation`
- `Measure a key feature's adoption`
- `Track confirmed subscription revenue`

Keep examples compact and secondary. Clicking one may populate or append to the textarea; examples must not imply these are the only supported goals.

### Visual priority

- The question and textarea dominate.
- Avoid chat bubbles, assistant avatars, AI gradients, sparkle motifs, floating suggestion cards, or a multi-message transcript.
- The page should feel like a precise setup tool, not a chatbot.
- The primary action appears after the reassurance in the natural reading order.

### States

- **Empty/invalid**: concise linked error beneath the textarea.
- **Drafting**: disable repeat submission, keep button geometry stable, use text such as `Drafting plan…`, and announce progress through a status region.
- **Clarification**: present one focused question with simple choices/free text and keep the original description visible. Example: `Should signup count after account creation or after email verification?`
- **Recoverable failure**: coral/danger alert, original input retained, retry available.

Do not ask a user to choose event identifiers, database terminology, aggregation enum values, or JWT/collector implementation details.

## State 2 — Review

### Content

- Eyebrow: `Step 2 of 3 · Review`.
- Heading: `Review your tracking plan`.
- Safety note: `Nothing has been saved yet.`
- Human-readable proposed event list.
- Optional activation journey.
- Assumptions and unsupported/attention items.
- Native `Implementation details` disclosure.
- Secondary `Edit description` action.
- Primary `Approve & create instruction` action.

### Event presentation

Each proposed event must expose business meaning before machine fields:

1. Display label/outcome.
2. When it fires.
3. How it is measured.
4. Unit when numeric.

Representative plan:

| Outcome | When it fires | Measurement | Technical detail |
| --- | --- | --- | --- |
| Completed sign-ups | After account creation is durably saved | Count each person once | `signup_completed` · `unique_actors` |
| First project created | After a person's first project is durably saved | Count each person once | `first_project_created` · `unique_actors` |
| Subscription revenue | After payment is durably confirmed | Sum successful payments in TRY | `subscription_revenue` · `sum` · `TRY` |

Do not use this representative content as hidden real data. It is safe design copy only.

### Activation

- Show only when the draft includes activation.
- Use a compact labelled relationship: `Completed sign-up → First project created`.
- Explain that each identified actor enters at their first start event and converts at the first later goal event.
- Do not depict a multi-step funnel or custom time-window control; the current model does not support them.

### Assumptions and unsupported intent

- Assumptions are explicit, plain language, and visually neutral (paper or restrained forest-soft surface).
- Unsupported requests use clear attention/danger treatment and text. Examples include multi-step funnels, property-filtered goals, retention goals, and custom conversion windows.
- A blocking issue prevents approval. A non-blocking assumption can be approved after review.
- Existing catalogs need visible `Added`, `Reused`, or `Changed` status when relevant. Never imply silent deletion.

### Implementation details

Use a native `<details>` disclosure, collapsed by default, for:

- stable `event_name`;
- aggregation enum;
- unit;
- raw start/goal mapping; and
- other machine-facing values.

Technical text uses the existing mono stack and paper inset surface. Do not make these details the first content a user scans.

## State 3 — Install

### Content

- Eyebrow: `Step 3 of 3 · Install`.
- Heading such as `Your tracking plan is ready`.
- Success status and compact approved-plan summary.
- `Server environment` copy panel.
- `Instruction for your agent` copy panel.
- Private-key safety note.
- Completion actions: `Back to <site> dashboard` and `Edit tracking plan`.

### Environment panel

The current environment shape is:

```text
SITEHITS_EVENT_ENDPOINT=https://sitehits.io/api/server-events
SITEHITS_SITE_KEY=sh_…
SITEHITS_SERVER_EVENT_KEY=shs_…
```

Design artifacts must use only placeholders/masked values. The user's actual UI may offer a deliberate `Copy environment` action, but the private value must never be passed to the AI model. The generated agent instruction refers to `Authorization: Bearer $SITEHITS_SERVER_EVENT_KEY` rather than embedding the secret in AI-authored content.

### Agent instruction panel

- Treat as a deterministic artifact generated from the approved plan.
- Show enough preview to identify its purpose; very long text lives in a bounded mono region or expandable preview.
- Primary copy action is clear and adjacent to the artifact.
- Copy state changes to `Copied`, announces success politely, then restores the label without layout shift.
- Clipboard failure selects/exposes text and gives a manual-copy fallback.

Reuse the composition and inline copy/check icon language from `templates/onboarding/install.html` rather than inventing a new code-editor aesthetic.

## Advanced setup

The current form remains available for expert/manual editing, but is secondary:

- Use a native `<details>` block or a clearly secondary link.
- Default collapsed.
- Label it `Advanced setup`, with helper text explaining that it exposes event IDs, aggregations, units, and activation mapping.
- When expanded, retain current field labels, widget classes, validation, delete safeguards, and save behavior.
- Do not display Advanced setup expanded beside the natural-language Describe surface in the default state.

## Capability boundaries shown honestly in UI

Supported:

- event count;
- unique actors;
- sum with unit;
- average with unit; and
- one two-event activation relationship.

Unsupported in the current model:

- multi-step funnels;
- property-filtered goals;
- retention/cohort-return goals;
- custom conversion windows; and
- more than one activation funnel.

If intent needs an unsupported capability, Review says so. The design must not fake a configuration the backend cannot store.

## Visual character

SiteHits is technical-minimalist, calm, precise, and operational.

- Warm off-white paper canvas.
- Flat white panels.
- One-pixel structural borders.
- 2px radii.
- Sans headings/body paired with restrained uppercase mono metadata.
- Forest for primary action and approved/current structure.
- Coral for focus and error/attention.
- Success green for saved/approved/copied feedback.
- No decorative elevation.

Forbidden departures:

- gradients;
- glass, blur, or glow;
- large rounded cards or pills;
- decorative shadows;
- purple/blue AI styling;
- assistant avatars, sparkles, or chat transcripts;
- photography, illustrations, emoji, or flags; and
- a sidebar or unrelated dashboard navigation.

## Color tokens

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Canvas | `paper` | `#f7f7f5` | Page and inset code surfaces |
| Panel | `panel` | `#ffffff` | Dominant flow panel and controls |
| Text | `ink` | `#17211b` | Headings, body, dark hover |
| Secondary | `muted` | `#667069` | Helpers, upcoming steps |
| Primary | `forest` | `#1a3c2b` | Primary action, active/completed structure |
| Soft primary | `forest-soft` | `#dce7df` | Restrained assumption/completed surface |
| Focus/attention | `coral` | `#e78468` | Focus outline, alert border/tint |
| Success | `success` | `#287a4b` | Approved/copied/saved status |
| Error | `danger` | `#a94736` | Error/unsupported text |
| Optional accent | `gold` | `#d6ae45` | Available but unnecessary here |
| Hairline | `--sh-hairline` | `rgba(23,33,27,.14)` | Structural border |

Ink opacity utilities `ink/10`, `ink/15`, and `ink/20` are canonical. Coral tints use `coral/10`. Do not introduce colors outside this table.

## Typography

- Sans: `"Space Grotesk", Inter, ui-sans-serif, system-ui, sans-serif`.
- Mono: `"JetBrains Mono", "SFMono-Regular", Consolas, monospace`.
- No remote font is loaded; allow system fallback.
- Main question/page title: approximately 28–30px, semibold, tight tracking.
- State/section title: 20px, semibold.
- Standard content and controls: 14–16px.
- Helper copy: 14px / 24px, muted.
- `.sh-mono`: 11px uppercase, `0.08em` tracking.
- Technical preview: 12px mono / approximately 24px.
- Sentence case for headings/actions; uppercase only through `.sh-mono`.

## Spacing, sizing, borders, and elevation

- Tailwind 4px rhythm; prefer 8, 12, 16, 20, 24, 32, and 40px.
- Outer max width: 1024px (`max-w-5xl`).
- Outer padding: 16px, increasing to 32px at `md`.
- Main vertical rhythm: 24–32px.
- Primary panel padding: 20px mobile, 24–32px desktop.
- Form/action targets: at least 44px; state-transition actions 48px when practical.
- Describe textarea: roughly 160–220px minimum height, responsive width.
- Default radius: 2px.
- Borders: one pixel, normally `ink/15` or `ink/20`.
- No box shadows. Structure comes from canvas/panel contrast and hairlines.
- Global focus-visible: 2px coral outline with 2px offset.

## Responsive behavior

Tailwind breakpoints in current use: `sm` 640px, `md` 768px, `lg` 1024px.

- Mobile-first; 16px viewport gutters and no page-level horizontal scrolling.
- Full step names remain visible. Compress connector spacing before abbreviating labels.
- Review event rows stack measurement and firing detail below the label.
- Multi-column review composition collapses to one column below `md`/`lg`.
- Button groups stack full-width on narrow screens, then become inline from `sm`.
- Long IDs/code scroll or wrap inside their own bounded surface.
- Advanced form fields retain their existing single-column mobile and two-column `md` layout.
- At 200% zoom, reading order remains Describe content → primary action → Advanced setup.

## Feedback, loading, and error patterns

- **Drafting**: disabled primary action with stable dimensions and visible `Drafting plan…`; status announced politely.
- **Empty input**: field-associated danger text; do not use browser alert.
- **Clarification**: one focused question in a bordered neutral/attention panel; preserve original response.
- **AI/network failure**: coral border/tint, danger copy, retry; preserve input/draft.
- **Invalid structured plan**: explain the conflicting item in Review and prevent approval.
- **Approval pending**: stable `Creating instruction…` action, no duplicate submission.
- **Saved/ready**: success text plus explicit statement; do not rely on a green check alone.
- **Copy**: `Copied` label and polite live status for two seconds; fallback selects text.

## Accessibility and content rules

- Keep native labels, textarea, buttons, links, forms, and `<details>` semantics.
- Use `aria-current="step"` or explicit current-step text in the progress indicator.
- Do not automatically move focus except to a newly displayed blocking clarification/error heading when necessary; preserve logical focus return when editing.
- Associate helper and error text with the prompt field.
- Use `role="alert"` for blocking failures and `aria-live="polite"` for drafting/copy/success status.
- Status, step, assumption, and unsupported meaning always use text plus visual treatment.
- Maintain 40px minimum keyboard/pointer targets; 44–48px for important actions.
- Preserve entered text across validation and recoverable service errors.
- English product copy remains concise and sentence case.
- Site identity is always visible as text in the back link/page context.

## Motion

- Short color and transform transitions only.
- No continuous decorative animation.
- A restrained progress indicator is allowed while drafting, but textual status is mandatory.
- `prefers-reduced-motion: reduce` collapses animation/transition duration and iteration count through existing global CSS.

## Reproduction and design-draft context

For a pixel-faithful current-page reproduction, pass all of:

- `.superdesign/design-system.md`
- `assets/design-system.css`
- `assets/tailwind.css`
- `templates/base.html`
- `templates/dashboard/product_metrics_settings.html`
- `dashboard/forms.py`
- `dashboard/product_tracking.py`
- `static/sitehits-mark.svg`

For the approved redesign iteration, add `templates/onboarding/install.html` as copy/install visual context and retain the exact same core context files. None of these files exceeds 1000 lines, so pass them whole. Do not pass `.env*`, screenshots or rendered HTML containing live keys, database files, generated minified CSS, or vendor bundles.
