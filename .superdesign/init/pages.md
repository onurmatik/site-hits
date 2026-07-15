# SiteHits Pages and Dependency Trees

Updated from the current render paths on 2026-07-15.

## Target: all-sites dashboard (`/dashboard/all`)

```text
config/urls.py
└─ include("dashboard.urls") at /dashboard/
   └─ dashboard/urls.py: path("all", dashboard, {"site_slug": "all"})
      └─ dashboard/views.py: dashboard()
         └─ templates/dashboard/dashboard.html
            ├─ extends templates/base.html
            │  ├─ static/css/sitehits.css
            │  │  └─ generated from assets/tailwind.css
            │  │     └─ imports assets/design-system.css
            │  ├─ external, nonvisual SiteHits tracker
            │  └─ external FeatureRequest feedback launcher
            ├─ static/vendor/chart.umd.js (Chart.js runtime; do not pass as design context)
            └─ dashboard/static/dashboard/dashboard.js
               ├─ GET /api/analytics/overview?site=all
               ├─ GET /api/analytics/timeseries?site=all
               ├─ GET /api/analytics/breakdowns/<dimension>?site=all
               └─ GET /api/analytics/bots?site=all
```

### Confirmed render branch

`dashboard.views.dashboard()` leaves `selected_site=None` when `site_slug == "all"`. The complete template is below 1000 lines and should be passed whole. On this branch:

1. The site menu and page title say `All sites`.
2. The site menu loop contains every visible active tracked site.
3. The body subtitle explicitly describes an aggregate across active properties.
4. No embed-widget trigger/dialog is rendered.
5. No bot `Server setup` or `Set up bot tracking` links are rendered.
6. The human KPIs, traffic chart, bot section, and eight breakdowns render once and are populated from aggregate `site=all` requests.

### Current information-hierarchy gap

The page does not visually associate metrics with individual sites. A viewer must open the site menu and navigate to a site route to learn that site's numbers. The all-sites body exposes only totals/combined rankings, so it cannot provide a one-glance per-site operational comparison in its current structure.

### Superdesign context set for this page

Pass these files in full; none exceeds 1000 lines:

- `.superdesign/design-system.md`
- `assets/design-system.css`
- `assets/tailwind.css`
- `templates/base.html`
- `templates/dashboard/dashboard.html`
- `dashboard/static/dashboard/dashboard.js`
- `dashboard/views.py:229:295` (UI-affecting branch/context labels only)
- `static/sitehits-mark.svg` (repository brand asset; note that the current authenticated header itself uses a CSS-built mark)

Do not pass `static/css/sitehits.css` (generated/minified), `static/vendor/chart.umd.js` (vendor runtime), its source map, or PNG favicon/app-icon variants.

## Single-site dashboard (`/dashboard/<site-slug>`)

Uses the same dependency tree and complete dashboard template. `selected_site` is the visible `TrackedSite`; the heading shows name/domains, the site menu marks it current, the embed-widget button/dialog appears, and bot setup links point to its installation page. Analytics calls send the selected slug instead of `all`.

## Anonymous landing (`/`)

```text
dashboard.views.home()
└─ templates/onboarding/landing.html
   ├─ templates/base.html
   ├─ assets/tailwind.css + assets/design-system.css via compiled CSS
   └─ static/sitehits-mark.svg
```

Public header, privacy-first hero, website start form/error, and static dashboard preview. Authenticated users normally redirect to `/dashboard/all`.

## Passwordless auth (`/accounts/signup/`)

```text
dashboard.auth.signup()
└─ templates/registration/signup.html
   ├─ templates/base.html
   ├─ static/sitehits-mark.svg
   └─ static/google-g.svg
```

Branches for website-context signup vs generic sign-in, form vs sent confirmation, and inline email/provider errors.

## Legacy login (`/accounts/login/`)

`templates/registration/login.html` extends the base and renders a centered username/password panel. It uses the CSS-built mark rather than the SVG.

## Onboarding confirmation (`/onboarding/`)

`templates/onboarding/confirm.html` extends the base and renders website/domain details plus one create action and restart link.

## Tracker installation (`/onboarding/<site-slug>/`)

`templates/onboarding/install.html` extends the base and contains browser tracker code, agent instruction, optional server-side bot settings/instruction, four copy controls with inline JavaScript state, and dashboard/admin actions.

## Public last-hour widget (`/widget/<public-key>/`)

```text
dashboard.views.site_widget()
└─ templates/dashboard/widget.html (standalone document)
   ├─ static/css/widget.css
   └─ static/sitehits-mark.svg
```

It does not extend `templates/base.html`, does not load the SiteHits tracker or FeatureRequest launcher, and refreshes every 60 seconds.

## Google authentication error routes

`templates/socialaccount/authentication_error.html` extends the base; `templates/socialaccount/login_cancelled.html` extends that error page.
