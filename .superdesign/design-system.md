# SiteHits Dashboard Design System

## Scope and source of truth

This document describes the current authenticated SiteHits reporting UI at `/dashboard/all` and `/dashboard/<site-slug>`, plus the explicit requirement for the next all-sites design pass. It is intentionally dashboard-specific; onboarding, authentication, the public widget document, and speculative future components are outside its visual contract unless they appear inside a dashboard dialog.

Use these files as the implementation source of truth, in this order:

1. `templates/dashboard/dashboard.html` — rendered structure, copy, layout, responsive classes, controls, dialogs, and states.
2. `assets/design-system.css` and `assets/tailwind.css` — tokens, global behavior, and shared component utilities.
3. `dashboard/static/dashboard/dashboard.js` — rendered metric rows, charts, interaction behavior, loading/empty/error states, and motion.
4. `dashboard/views.py`, `analytics/reporting.py`, and `analytics/api.py` — route, access, reporting, and metric semantics.
5. `templates/base.html` and `static/sitehits-mark.svg` — document shell, favicon/brand asset, and the externally injected feedback control.

Do not invent fonts, colors, gradients, radii, shadows, navigation, or data states that are not defined here or in those files.

## Product context and jobs to be done

SiteHits is a small, cookieless, multi-site analytics service. Authenticated owners can inspect their own active sites; superusers can inspect every active site. The dashboard is an operational reporting surface, not a marketing page.

Primary dashboard jobs:

- Understand traffic volume, engagement, acquisition, content, geography, devices, campaigns, and custom events for a selected reporting period.
- Compare the current period with the immediately preceding period.
- Inspect known server-side crawler traffic separately from browser analytics.
- Switch quickly between all visible sites and one site without losing the selected period.
- Add a site from the dashboard.
- For a selected site, reach server-side bot setup and generate a public last-hour widget embed.
- On `/dashboard/all`, understand the overall portfolio at a glance without obscuring which site produced which metrics.

## Routes and reporting architecture

- `/dashboard/all` is the all-visible-sites dashboard.
- `/dashboard/<site-slug>` is the single-site dashboard.
- Both routes render the same template and application shell.
- The client requests `overview`, `timeseries`, `bots`, and eight breakdown endpoints with `site` and `period` query parameters.
- Valid periods are Today, 24H, 7D, 30D, and 90D. Today/24H use hourly granularity; 7D/30D/90D use daily granularity in the rendered period links.
- The single-site report uses that site's timezone. The all-sites report uses the application timezone.
- Visitor and session identities are scoped to a site. The current all-sites totals combine site-scoped counts and therefore never imply cross-site person deduplication.

## Explicit all-sites redesign requirement

The current `/dashboard/all` page shows one aggregate KPI row, one aggregate traffic chart, one aggregate bot section, and aggregate breakdown tables. Its subtitle warns that visitors are not deduplicated between sites, but the page does not expose site attribution inside those metrics.

The next design must make site identity explicit in the metrics shown on `/dashboard/all` so a user can assess every site's condition at a glance.

- The five core metrics must be attributable per site: Visitors, Sessions, Pageviews, Bounce rate, and Avg. session.
- Every site-level metric group must have a clear site name; domain may support identification when useful.
- Current-period values and previous-period deltas must remain legible for each site.
- Aggregate values may remain as a clearly labelled secondary summary, but they must not be the only view of performance.
- Do not visually imply that visitors are deduplicated across sites.
- Preserve `/dashboard/<site-slug>` as the focused detail route and preserve the site selector as the way to move into it.
- Keep the existing period control global to the page so site comparisons use one consistent time window.
- The requirement defines information hierarchy, not a preselected component solution. Do not assume cards, tables, tabs, or accordions unless the approved design chooses them.

## Visual character

The existing dashboard is a technical-minimalist analytics interface with a flat paper canvas and structural hairlines.

- Calm, precise, compact, and operational.
- Flat white panels on a warm off-white page.
- One-pixel borders provide structure; decorative elevation does not.
- Sans-serif headings and values pair with small uppercase monospace metadata.
- Forest is the primary action and data color. Coral is a restrained chart, focus, and error accent. Gold is available but is not used in the current dashboard template.
- No gradients, glass, blur, decorative textures, inflated rounded cards, or decorative drop shadows.
- Default radius is 2px. Fully round shapes are limited to small status dots and data bars.

## Color tokens

| Role | CSS/Tailwind token | Value | Current dashboard use |
| --- | --- | --- | --- |
| Canvas | `--sh-paper` / `paper` | `#f7f7f5` | Page background, hover rows, inset fields, dialog preview half |
| Panel | `--sh-panel` / `panel` | `#ffffff` | Header, controls, cards, panels, dialogs |
| Primary text | `--sh-ink` / `ink` | `#17211b` | Headings, labels, values, dark CTA hover |
| Secondary text | `--sh-muted` / `muted` | `#667069` | Supporting copy, metadata, inactive controls, chart ticks |
| Primary/action | `--sh-forest` / `forest` | `#1a3c2b` | Filled actions, selected period, lead accents, bars, visitor series |
| Soft primary | `--sh-forest-soft` / `forest-soft` | `#dce7df` | Available soft forest surface token; not a dominant current dashboard surface |
| Accent/error/focus | `--sh-coral` / `coral` | `#e78468` | Pageview series, global focus outline, error border/tint |
| Tertiary accent | `--sh-gold` / `gold` | `#d6ae45` | Available token; not used by the current dashboard template |
| Success | `--sh-success` / `success` | `#287a4b` | Operational dot, positive KPI delta, successful bot status |
| Danger text | `--sh-danger` / `danger` | `#a94736` | Negative KPI delta, errors, failed bot status |
| Hairline | `--sh-hairline` | `rgba(23, 33, 27, 0.14)` | Canonical structural border |

Implementation also uses ink opacity borders directly: `ink/10`, `ink/15`, and `ink/20`. Chart grid lines use `rgba(23,33,27,.08)`, and the visitor area fill uses `rgba(26,60,43,.08)`. Dialog backdrops use `ink/30`.

The external Feature Request script injects its own right-edge feedback control with `#06B6D4`. Treat this cyan as an external integration exception, not as a SiteHits design token and not as a color to reuse in dashboard components.

## Typography

### Families

- Sans: `"Space Grotesk", Inter, ui-sans-serif, system-ui, sans-serif`.
- Mono: `"JetBrains Mono", "SFMono-Regular", Consolas, monospace`.
- No remote font files are currently loaded. The chosen installed/system fallback is the real runtime font, so layouts must remain stable without Space Grotesk or JetBrains Mono.
- Global text rendering uses antialiasing.
- Numeric metrics and counts use tabular figures through `.sh-tabular`.

### Current dashboard scale

| Element | Size / line height | Weight / treatment |
| --- | --- | --- |
| SiteHits header wordmark | 18px / 28px | 700, tight tracking |
| Dashboard page title | 28px / 34px | 600, tight tracking |
| Section title | 20px / 28px | 600, tight tracking |
| Dialog title | 18px / 28px | 600, tight tracking |
| KPI value | 30px / 36px | 600, tabular |
| Standard body/control | 14px / 20px | 400 or 500 |
| Explanatory body | 14px / 24px | 400, muted |
| Table row label/count | 13px | 500; count is tabular |
| Secondary metadata | 12px / 16px | regular |
| `.sh-mono` label | 11px | uppercase, `0.08em` tracking |
| Compact mono metadata | 10px | uppercase, `0.08em` tracking |

Use sentence case for headings and actions. Uppercase transformation belongs only to `.sh-mono` metadata and period controls.

## Spacing, sizing, radii, borders, and elevation

The implementation uses Tailwind's 4px spacing scale. The most visible dashboard intervals are 8, 12, 16, 20, 24, 32, and 40px.

- Authenticated header: 64px high; 16px horizontal padding, increasing to 24px at `md`.
- Main container: 16px padding and 24px vertical section gaps; 32px padding and 32px gaps at `md`.
- Page heading/action cluster gap: 20px; action row gap: 12px.
- KPI and bot KPI grids: 16px gap; cards use 20px padding.
- Standard chart panel: 16px padding, increasing to 24px at `md`.
- Breakdown grid: 24px gap.
- Breakdown header: 20px horizontal and 16px vertical padding.
- Breakdown row: minimum 48px tall with 12px horizontal padding.
- Footer: 16/32px horizontal padding, 40px vertical padding.
- Header/menu/dialog utility controls: minimum 40px tall.
- Primary form and copy actions: minimum 48px tall.
- Default component radius: 2px.
- Panel border: one pixel, normally `ink/15` or canonical hairline.
- Dividers: one pixel at `ink/10` or `ink/15`.
- There are no dashboard box-shadow classes. Use the `ink/30` modal backdrop to separate dialogs instead of adding elevation.
- Focus-visible: 2px coral outline with 2px offset on every interactive element.

## Page layout

### Application shell

- Light-only document (`color-scheme: light`) on the paper canvas.
- Sticky, full-width header at `z-40`.
- Main and footer are centered and capped at 1440px.
- No sidebar.
- The footer places `SiteHits MVP` left and operational links right; it stacks on mobile and becomes a row from `sm`.

### Header

- Left cluster: CSS-built 32px forest square with a centered 16px white/50 outlined square, SiteHits wordmark hidden below `sm`, 24px divider hidden below `md`, then the site selector.
- Site trigger is at least 40px tall. Its label truncates at 128px on mobile, 192px at `sm`, and 288px at `md`.
- Right cluster: Operational status is hidden below `sm`; Logout remains available.
- The selector menu always opens downward. On mobile it is fixed below the header with 16px left/right insets; from `sm` it is an absolute 256px-wide dropdown aligned to the trigger.
- Menu rows are at least 40px high. The active row uses the paper background, medium text, a forest checkmark, and `aria-current="page"`.
- The menu ends with a divider and New site action.

### Dashboard heading and periods

- Heading and controls stack by default, then sit in a bottom-aligned row from `md`.
- Eyebrow: `Privacy-first traffic intelligence` in muted mono.
- Single-site subtitle shows allowed domains.
- Current all-sites subtitle reads: `Aggregate across active properties; visitors are not deduplicated between sites.`
- Selected-site views may show the bordered Embed widget action before the period control; all-sites does not.
- The period control is one flat bordered segmented group with horizontal overflow contained inside the control. Each segment is at least 40px tall; active is forest with white text.

### Core KPI grid

- One column by default, two from `sm`, five from `lg`.
- Current order: Visitors, Sessions, Pageviews, Bounce rate, Avg. session.
- Each card has a mono label, large tabular value, and a 12px delta.
- Visitors has a 2px forest top accent; the remaining cards do not.
- Positive/negative coloring follows metric meaning: a lower bounce-rate delta is success; higher deltas are success for the other metrics; no prior comparison displays `New` in muted text.

### Traffic chart

- Full-width bordered panel with a fixed 300px plot height.
- Header contains title/timezone left and a small inline two-series key right.
- Chart.js responsive line chart with index hover and no built-in legend.
- Visitors: `#1a3c2b`, 2px stroke, 2px points, tension `0.28`, forest 8% area fill.
- Pageviews: `#e78468`, 2px stroke, 2px points, tension `0.28`, no fill.
- X-axis has no grid, hairline border, muted ticks, maximum ten tick labels.
- Y-axis starts at zero, uses ink 8% grid, no axis border, and integer ticks.

### Bot traffic section

- Separate section below the human traffic chart, with `Server-side` eyebrow and `Bot traffic` title.
- Header metadata reports user-agent matches and, when available, IP verification. Selected-site views also show Server setup.
- Empty state is one bordered panel with explanatory copy and a selected-site setup action.
- Populated state repeats a one/two/five-column KPI grid for Bot requests, AI answers, Indexing, Training, and Other.
- Bot requests has the 2px forest top accent. Category cards show share percentage.
- Provider and requested-path panels form a one-column grid, becoming two equal columns at `lg`.
- Requested paths pair a truncated path with status and request count. Status is success for 2xx/3xx, danger for 4xx/5xx, and muted when unknown.

### Traffic breakdowns

- One column by default, two equal columns from `lg`.
- Current panels: Top pages, Top referrers, Countries, Regions, Cities, Devices, Campaigns, and Custom events.
- Each panel has a mono title, compact unit label, and ranked rows.
- Rows use a 13px truncated label, 4px-high proportional forest data bar, and right-aligned 13px tabular count.
- Current API defaults to at most eight rows per breakdown.

### Dialogs

- Native modal dialogs are centered, have transparent outer boxes, no decorative shadow, `ink/30` backdrop, 16px viewport inset, and a maximum height of viewport minus 32px.
- Add website dialog: maximum 576px, bordered white panel, 20/24px responsive padding, domain control and read-only timezone context, full-width 48px forest action.
- Embed widget dialog appears only on a selected-site dashboard: maximum 1024px; one column by default and two columns from `lg`; preview side uses paper and contains a 400 × 600 iframe; code side contains read-only monospace textareas and full-width copy actions.

## Component and interaction patterns

### Panels

`.sh-panel` is the shared structural surface: white background, one-pixel `ink/15` border, and 2px radius. It has no built-in padding or shadow.

### Buttons and links

- Primary: forest background, white text, 2px radius; hover to ink.
- Secondary: white background, ink text, `ink/15` border, 2px radius; hover strengthens border/text to forest.
- Quiet: muted text without a containing surface; hover to ink.
- Pending Add website state disables the action, preserves geometry, lowers opacity, uses `not-allowed`, and changes copy to `Adding website…`.

### Site menu behavior

- Trigger click toggles the menu and rotates the chevron 180 degrees.
- Arrow Up/Down opens the menu and moves focus; within the menu, Arrow Up/Down wraps, Home/End jumps, and Escape closes and returns focus.
- Outside click closes it.

### Data rows and bars

- Data bars use a 4px fully rounded forest/15 track and forest fill.
- Rows keep a 48px minimum target/reading height and use paper only on hover.
- Labels truncate rather than increasing row height; numeric values remain visible.

### Feedback states

- Loading: `.sh-skeleton` forest/10 blocks with pulse animation, sized to preserve the final breakdown layout.
- KPI loading values use an em dash.
- Empty breakdown: `No data in this period.` in muted 14px text with 20px padding.
- Bot empty state preserves a full panel and explains the server collector.
- Error: one shared inline alert above the metrics, coral border, coral 10% tint, danger text; no browser alert.
- Copy success: button label becomes `Copied`, an `aria-live="polite"` line announces success, and the default label returns after two seconds. Clipboard failure selects the text and explains the fallback.

## Responsive rules

Tailwind default breakpoints used by the current dashboard are `sm` 640px, `md` 768px, and `lg` 1024px.

- Mobile-first; all current dashboard information remains present at narrow widths.
- Header wordmark and Operational status progressively hide, but navigation and Logout remain.
- Site menu becomes a full available-width fixed dropdown on mobile.
- Main padding grows from 16px to 32px at `md`.
- Dashboard heading/actions change from stacked to a row at `md`.
- KPI grids change 1 → 2 → 5 columns at base/`sm`/`lg`.
- Breakdown and bot-detail grids change 1 → 2 columns at `lg`.
- Period choices scroll horizontally within their own segmented control.
- Dialogs remain 16px from the viewport edge and scroll internally when needed.
- Embed preview/code stacks until `lg`; the iframe is `width: 100%` with a 400px maximum.
- The site-attribution treatment introduced for `/dashboard/all` must keep every site's five metrics readable without page-level horizontal scrolling. Do not hide sites or core metrics on mobile.

## Motion

- Utility classes use short color and transform transitions; no continuous decorative animation is part of the dashboard.
- Opening the site menu rotates the chevron through its existing transform transition.
- The traffic chart animates once for 350ms on render.
- Skeletons pulse while loading.
- Copy feedback remains for 2000ms before resetting.
- `prefers-reduced-motion: reduce` disables chart animation and globally reduces transition/animation durations to `0.01ms`, limits iterations to one, and disables smooth scrolling.

## Accessibility and content rules

- Preserve the global 2px coral focus-visible outline and 2px offset.
- Keep native button, link, form, and dialog semantics.
- Site menu exposes `aria-haspopup`, `aria-expanded`, `aria-controls`, `aria-current`, and full keyboard navigation.
- Dashboard error uses `role="alert"`; copy status uses `aria-live="polite"`.
- Every form field has a visible label; error text is associated with the domain field.
- Use text plus color for status/delta meaning. Never rely on color alone.
- Keep controls at least 40px tall; primary form/copy actions are 48px.
- Use the dashboard's existing English, sentence-case product vocabulary.
- Site identity on `/dashboard/all` must be textual and unambiguous; do not depend on color alone to distinguish sites.
- Do not claim cross-site unique visitors, people, or identity resolution.

## Non-goals and fidelity constraints

- Do not add a sidebar; the current navigation model is a compact header and site selector.
- Do not redesign onboarding/authentication as part of the all-sites dashboard task.
- Do not reuse the external feedback widget's cyan in SiteHits reporting UI.
- Do not introduce decorative illustrations, stock photography, flags, gradients, glass effects, large radii, or shadows.
- Do not replace current browser analytics and bot analytics with one undifferentiated metric set.
- Do not remove the focused site route, add-site flow, bot setup, or selected-site embed action.
- When producing design variants, use only the fonts, colors, spacing, radii, border, shadow, motion, and component styles defined in this design system.
