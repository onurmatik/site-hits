# SiteHits UI Components

Updated from the current source on 2026-07-15. SiteHits is server-rendered Django with Tailwind CSS v4 utilities. There is no React/Vue component library; most visual components are template fragments inside page templates and some dashboard rows are created by vanilla JavaScript.

## Authoritative visual sources

- Global document shell: `templates/base.html`
- Shared tokens and base rules: `assets/design-system.css`
- Tailwind theme aliases and reusable utility classes: `assets/tailwind.css`
- Generated application stylesheet loaded in production: `static/css/sitehits.css`
- Authenticated dashboard markup: `templates/dashboard/dashboard.html`
- Dashboard interaction and data rendering: `dashboard/static/dashboard/dashboard.js`
- Public widget markup and styling: `templates/dashboard/widget.html`, `static/css/widget.css`
- Brand mark: `static/sitehits-mark.svg`
- Google provider mark: `static/google-g.svg`

`static/css/sitehits.css` is generated from the two files under `assets/`; edit and pass the source CSS files rather than the one-line minified output. `static/vendor/chart.umd.js` is vendored Chart.js and is not project-authored visual context.

## `/dashboard/all` component inventory

All line references below are in `templates/dashboard/dashboard.html` unless another file is named.

- **Authenticated header and brand lockup** (lines 13-80): sticky 64px white header, CSS-built forest square mark, SiteHits wordmark, site menu, operational status, and logout form.
- **Site menu** (lines 22-68): downward-opening custom dropdown. The `All sites` link and every visible tracked site are rendered by Django. The selected item receives a paper background, medium weight, check icon, and `aria-current`. A `New site` action opens the add-site dialog.
- **Page heading and period controls** (lines 82-108): `All sites` heading, aggregate caveat, and Today/24H/7D/30D/90D segmented control. `Embed widget` exists only on a single-site route.
- **Inline dashboard error** (line 110): hidden coral notice populated by JavaScript when an analytics request fails.
- **Human-traffic KPI cards** (lines 112-129): Visitors, Sessions, Pageviews, Bounce rate, and Avg. session. Values and deltas are populated by `renderKpis()` in `dashboard/static/dashboard/dashboard.js` lines 213-224.
- **Traffic volume chart** (lines 131-137): fixed 300px canvas with inline legend. `renderChart()` in dashboard JS lines 226-257 creates a two-series Chart.js line chart: forest Visitors and coral Pageviews.
- **Bot traffic section** (lines 139-197): heading, verification metadata, empty state, five category cards, crawler-provider rows, and requested-path rows. Single-site-only setup links are absent on `/dashboard/all`. JavaScript populates it at lines 292-354.
- **Traffic breakdown cards** (lines 199-208): eight server-configured panels for pages, referrers, countries, regions, cities, devices, campaigns, and custom events. Each starts with skeleton rows; `renderRows()` in dashboard JS lines 259-290 replaces them with ranked rows, proportional forest bars, and right-aligned counts.
- **Dashboard footer** (lines 211-214): SiteHits MVP label, staff-only Django admin link, and API docs link.
- **Add-site dialog** (lines 216-260): responsive modal with domain field, timezone summary, error state, and full-width primary action. Dashboard JS lines 77-120 manage focus, backdrop close, submit state, and reopening after a server validation error.
- **Public-widget dialog** (lines 262-314): rendered only when `selected_site` is set, so it is not present on `/dashboard/all`. The single-site layout uses a live iframe preview, embed code, agent instruction, and copy feedback.

## Important current behavior for the all-sites page

The current all-sites body does **not** render one metric group per site. The page has one shared KPI grid, one shared chart, one bot section, and one set of breakdown panels. `dashboard/static/dashboard/dashboard.js` reads `data-site="all"` from the root and sends that value to every analytics endpoint. Site names appear individually only inside the header dropdown.

This distinction is the core constraint for the requested redesign: per-site identity must become visible in the main information hierarchy, not remain hidden in navigation.

## Other page-level patterns

- **Public header, start form, and dashboard preview**: `templates/onboarding/landing.html`
- **Centered confirmation panel**: `templates/onboarding/confirm.html`
- **Centered install and copy panels**: `templates/onboarding/install.html`
- **Passwordless signup panel**: `templates/registration/signup.html`
- **Legacy username/password login panel**: `templates/registration/login.html`
- **Google authentication error panel**: `templates/socialaccount/authentication_error.html`
- **Standalone last-hour widget**: `templates/dashboard/widget.html`, with all component styling in `static/css/widget.css`

## Icon and asset rules

- The authenticated dashboard uses inline SVGs for the menu chevron, check marks, plus action, and domain icon. Preserve those exact SVG paths from the template.
- The authenticated dashboard mark is built from nested spans, not `static/sitehits-mark.svg`.
- Public/auth pages and the public widget use the real `static/sitehits-mark.svg`.
- Do not pass the PNG files under `static/brand/` as Superdesign context; they are generated favicon/app-icon variants rather than dashboard content imagery.
