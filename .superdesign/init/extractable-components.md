# Extractable Components

Updated from the current source on 2026-07-15.

## Recommendation for the current dashboard task

Skip Superdesign component extraction and pass the complete implementation files directly.

SiteHits has no independent React/Vue layout components. The authenticated header, menu, dashboard body, footer, and dialogs are one 321-line Django template (`templates/dashboard/dashboard.html`). The header's site list is a server-rendered `{% for site in sites %}` loop with per-item URL/current-state conditions; converting it to a Petite-Vue component would require hardcoding representative site entries because the component template format forbids `v-for`. That would be less faithful than using the source template itself.

The other apparent pieces (KPI card, ranked row, buttons, inputs) are basic primitives and should remain inline according to the Superdesign extraction rules. Ranked rows and bot rows are also created dynamically by `dashboard/static/dashboard/dashboard.js`, not by reusable source components.

## Possible future extraction boundary

If SiteHits later extracts a real shared dashboard shell from the Django template, the first useful component would be:

- **DashboardHeader**
  - Current source: `templates/dashboard/dashboard.html:13:80`
  - Visual dependencies: inline SVGs and Tailwind utilities from `assets/tailwind.css`
  - Suitable future props: `activeSiteName` (active state), `allSitesHref` (navigation URL), `logoutHref` (navigation URL), `showOperational` (conditional visibility)
  - Constraint: site menu entries should be explicit slots or hardcoded preview entries, not a `v-for` prop collection.

This is a future refactor boundary, not an extracted component that exists today.
