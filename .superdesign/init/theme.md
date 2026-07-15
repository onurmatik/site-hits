# SiteHits Theme

Updated from `assets/design-system.css`, `assets/tailwind.css`, `static/css/widget.css`, and the dashboard Chart.js configuration on 2026-07-15.

## Source chain

`assets/tailwind.css` imports Tailwind CSS v4 and `assets/design-system.css`, scans `templates/` and `dashboard/`, then builds the minified `static/css/sitehits.css` loaded by `templates/base.html`. Use both source CSS files as Superdesign context; do not use the generated one-line stylesheet as the design source.

The standalone public widget does not load Tailwind. Its complete rules live in `static/css/widget.css`.

## Design tokens

| Role | CSS variable / Tailwind alias | Value |
| --- | --- | --- |
| Paper canvas | `--sh-paper` / `paper` | `#f7f7f5` |
| Panel | `--sh-panel` / `panel` | `#ffffff` |
| Ink | `--sh-ink` / `ink` | `#17211b` |
| Muted ink | `--sh-muted` / `muted` | `#667069` |
| Forest | `--sh-forest` / `forest` | `#1a3c2b` |
| Forest soft | `--sh-forest-soft` / `forest-soft` | `#dce7df` |
| Coral | `--sh-coral` / `coral` | `#e78468` |
| Gold | `--sh-gold` / `gold` | `#d6ae45` |
| Success | `--sh-success` / `success` | `#287a4b` |
| Danger | `--sh-danger` / `danger` | `#a94736` |
| Hairline | `--sh-hairline` | `rgba(23, 33, 27, 0.14)` |
| Radius | `--sh-radius` | `2px` |

The external FeatureRequest launcher receives `#06B6D4` from `templates/base.html`; cyan is not a SiteHits application token and should not be introduced into dashboard components.

## Typography

- Sans: `"Space Grotesk", Inter, ui-sans-serif, system-ui, sans-serif`.
- Mono: `"JetBrains Mono", "SFMono-Regular", Consolas, monospace`.
- No remote font files are loaded, so runtime rendering commonly uses an installed/system fallback.
- `.sh-mono`: mono, 11px, uppercase, `0.08em` tracking.
- `.sh-tabular`: tabular numeric figures.
- Dashboard page title: 28px/34px, semibold, tight tracking.
- KPI values: 30px (`text-3xl`), semibold, tabular.

## Surfaces and interaction

- `.sh-panel`: white panel, 1px 15%-ink border, 2px radius; no decorative shadow.
- Buttons and inputs use 2px radius and visible 1px structural borders.
- Primary actions are forest with white text and hover to ink.
- Global focus-visible outline is 2px coral with 2px offset.
- Status dots may be circular; panels and controls remain square/technical.
- Reduced-motion media query collapses transition/animation duration and iteration count.

## Dashboard data styling

- Lead KPI and data bars use forest.
- Human-traffic chart uses forest (`#1a3c2b`) for Visitors with an 8% forest fill, and coral (`#e78468`) for Pageviews with no fill.
- Chart grid/border colors use 8% and 14% ink.
- Positive deltas use success; negative deltas use danger; bounce-rate improvement reverses the usual delta direction.
- Skeletons use a 10% forest fill and pulse animation.
- Period controls are mono uppercase; the active segment is forest with white text.

## Layout and responsive conventions

- 4px spacing rhythm; common gaps are 12, 16, 20, 24, and 32px.
- Header height: 64px.
- Dashboard maximum width: 1440px.
- Mobile gutters: 16px; desktop dashboard padding: 32px from `md`.
- Tailwind breakpoints used by the dashboard: `sm` 640px, `md` 768px, `lg` 1024px.
- KPI and bot category grids: 1 column → 2 at `sm` → 5 at `lg`.
- Breakdown grids: 1 column → 2 at `lg`.
- Main chart height: 300px.
- No gradients, glass effects, or decorative shadows are part of the current dashboard language.
