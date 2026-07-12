# SiteHits Design System

## Product context

SiteHits is a privacy-first, multi-site web analytics service. Anonymous visitors begin with a fast website-first onboarding flow; authenticated administrators use site-level reporting and an aggregate all-sites view. The primary reporting job is to answer, at a glance: how much traffic arrived, whether engagement improved, where visitors came from, which pages they viewed, and which custom events fired.

## Visual direction

Use a technical-minimalist interface adapted from the Mosaic Grid Architecture system. The product should feel precise, calm, and operational. The anonymous landing may use larger type and more whitespace, but must remain a direct product-entry surface rather than a long marketing page. Use flat paper-like surfaces, structural hairlines, compact labels, and generous whitespace around dense data.

## Tokens

- Background `#f7f7f5` (paper)
- Surface `#ffffff`
- Primary `#1a3c2b` (forest)
- Primary soft `#dce7df`
- Text `#17211b`
- Secondary text `#667069`
- Hairline `rgba(23, 33, 27, 0.14)`
- Coral accent `#e78468`
- Gold accent `#d6ae45`
- Success `#287a4b`
- Danger `#a94736`
- Font sans: `Space Grotesk`, `Inter`, system sans-serif
- Font mono: `JetBrains Mono`, `SFMono-Regular`, monospace

## Layout

- Header height: 64px with a single bottom hairline.
- Content max width: 1440px; 24px desktop gutters and 16px mobile gutters.
- Section spacing: 24px; component internal spacing: 16px or 20px.
- KPI cards: five equal columns on large screens, two on medium screens, one on small screens.
- Breakdown panels: two equal columns on desktop and one column below 900px.
- Border radius: 2px for panels and controls; pills may use 999px only for status dots/tags.
- No gradients. No large drop shadows. Hover states use background and border color changes.

## Typography

- Product wordmark: 18px/24px, 700.
- Page title: 28px/34px, 650, tight tracking.
- KPI value: 30px/34px, 650, tabular numbers.
- Body: 14px/21px.
- Labels and metadata: mono, 10px-12px, uppercase, letter spacing 0.08em.
- Tables use 13px labels and tabular numeric values.

## Components

- Public header: real SiteHits SVG mark, wordmark, and one sign-in action.
- Website start form: one domain field, inline validation, one forest CTA, and short privacy/setup reassurance.
- Dashboard preview: browser-framed flat panel using real dashboard KPI and chart patterns; on narrow screens crop the preview inside its panel without causing page-level overflow.
- Onboarding steps: centered confirmation and install panels, one dominant action per step.
- Header: forest square mark, wordmark, site selector, optional status and logout action.
- Filters: flat segmented period controls with a distinct active forest state.
- KPI card: label, large value, previous-period delta, subtle top accent for one highlighted metric.
- Chart panel: title/legend row, fixed-height chart area, no surrounding chrome beyond the panel border.
- Breakdown table: title row, ranked items, proportional forest bar, value aligned right.
- Empty state: calm explanatory copy and one clear link to Django admin.
- Loading state: preserve layout; use low-contrast skeleton blocks.
- Errors: inline bordered coral notice; never use blocking browser alerts.

## Responsive behavior

- Preserve the site selector and period controls on mobile; allow horizontal scrolling for the period row.
- Do not hide KPIs or breakdowns; stack them.
- Table rows must remain readable at 320px width.
- All interactive controls have at least a 40px target size and visible keyboard focus.

## Motion

- 120-180ms ease-out for hover/focus transitions.
- Chart may animate once on initial load; respect `prefers-reduced-motion`.
- No continuous decorative animation.
