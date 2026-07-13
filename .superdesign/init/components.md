# Components

SiteHits uses Django templates and Tailwind utilities rather than extracted component files.

Existing visual patterns:
- Public header with the real SiteHits SVG mark, wordmark, and sign-in action (`templates/onboarding/landing.html`).
- Anonymous website URL form with inline validation and a single forest CTA.
- Flat dashboard preview assembled from the same KPI, filter, and chart vocabulary as the authenticated dashboard.
- Centered confirmation/install panels for steps two and three of onboarding.
- Authenticated application header with the CSS-built mark, site selector, status, and logout action.
- Period control, KPI metric cards, time-series chart panel, ranked breakdown tables, loading skeletons, and inline errors.
- Selected-site embed action with a responsive preview/code dialog and clipboard feedback.
- Standalone public last-hour widget with a 60-bar minute chart, aggregate visitor total, top-three country list, and SiteHits attribution.
