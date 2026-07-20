# SiteHits theme

Updated from the complete current CSS sources on 2026-07-20.

## CSS source chain

`assets/tailwind.css` imports Tailwind CSS v4 and `assets/design-system.css`, scans `templates/` and `dashboard/`, then builds the minified `static/css/sitehits.css` loaded by `templates/base.html`. Use the two source files as design context; do not use the generated one-line stylesheet.

### `assets/design-system.css` (complete)

```css
:root {
  --sh-paper: #f7f7f5;
  --sh-panel: #ffffff;
  --sh-ink: #17211b;
  --sh-muted: #667069;
  --sh-forest: #1a3c2b;
  --sh-forest-soft: #dce7df;
  --sh-coral: #e78468;
  --sh-gold: #d6ae45;
  --sh-success: #287a4b;
  --sh-danger: #a94736;
  --sh-hairline: rgba(23, 33, 27, 0.14);
  --sh-radius: 2px;
  --sh-font-sans: "Space Grotesk", Inter, ui-sans-serif, system-ui, sans-serif;
  --sh-font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}

html {
  background: var(--sh-paper);
  color: var(--sh-ink);
  font-family: var(--sh-font-sans);
}

*:focus-visible {
  outline: 2px solid var(--sh-coral);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
```

### `assets/tailwind.css` (complete)

```css
@import "tailwindcss";
@import "./design-system.css";

@source "../templates";
@source "../dashboard";

@theme {
  --color-paper: #f7f7f5;
  --color-panel: #ffffff;
  --color-ink: #17211b;
  --color-muted: #667069;
  --color-forest: #1a3c2b;
  --color-forest-soft: #dce7df;
  --color-coral: #e78468;
  --color-gold: #d6ae45;
  --color-success: #287a4b;
  --color-danger: #a94736;
}

@layer base {
  body { @apply m-0 min-h-screen bg-paper text-ink antialiased; }
  button, select, input { font: inherit; }
}

@layer components {
  .sh-panel { @apply rounded-[2px] border border-ink/15 bg-panel; }
  .sh-mono { @apply font-mono text-[11px] tracking-[0.08em] uppercase; }
  .sh-tabular { font-variant-numeric: tabular-nums; }
  .sh-data-bar { @apply h-1 rounded-full bg-forest/15; }
  .sh-data-bar > span { @apply block h-full rounded-full bg-forest; }
  .sh-period { @apply min-h-10 border-r border-ink/10 px-4 py-2 font-mono text-[11px] tracking-[0.08em] uppercase transition-colors last:border-r-0 hover:bg-paper; }
  .sh-period-active { @apply bg-forest text-white hover:bg-forest; }
  .sh-skeleton { @apply animate-pulse bg-forest/10; }
  .sh-preview-bar { @apply min-w-6 flex-1 bg-forest/20; }
}
```

## Token reference

| Role | CSS variable / utility alias | Value | Product-metrics use |
| --- | --- | --- | --- |
| Paper canvas | `--sh-paper` / `paper` | `#f7f7f5` | Page and inset/code backgrounds |
| Panel | `--sh-panel` / `panel` | `#ffffff` | Primary state panel and controls |
| Ink | `--sh-ink` / `ink` | `#17211b` | Headings, body, hover-dark primary |
| Muted | `--sh-muted` / `muted` | `#667069` | Helpers, metadata, inactive steps |
| Forest | `--sh-forest` / `forest` | `#1a3c2b` | Primary actions, active/completed indicator |
| Forest soft | `--sh-forest-soft` / `forest-soft` | `#dce7df` | Restrained selected/completed surface |
| Coral | `--sh-coral` / `coral` | `#e78468` | Focus outline, attention/error tint |
| Gold | `--sh-gold` / `gold` | `#d6ae45` | Available accent; not required for this flow |
| Success | `--sh-success` / `success` | `#287a4b` | Approved/copied/saved status |
| Danger | `--sh-danger` / `danger` | `#a94736` | Errors and unsupported/blocking feedback |
| Hairline | `--sh-hairline` | `rgba(23,33,27,.14)` | Structural borders |
| Radius | `--sh-radius` | `2px` | Panels, inputs, buttons |

The FeatureRequest launcher receives forest from `templates/base.html`, but remains an external control. No cyan or external-widget color belongs in the flow.

## Typography

- Sans stack: `"Space Grotesk", Inter, ui-sans-serif, system-ui, sans-serif`.
- Mono stack: `"JetBrains Mono", "SFMono-Regular", Consolas, monospace`.
- No webfont files are loaded; designs must remain stable with system fallbacks.
- Page title and state question: 28–30px, semibold, tight tracking.
- Section/state title: 20px, semibold.
- Standard body and controls: 14–16px, regular/medium, 20–24px line height.
- Helper text: 14px with 24px line height, muted.
- `.sh-mono`: 11px uppercase with `0.08em` tracking.
- Code/technical details: 12px mono with approximately 24px line height.
- Use sentence case. Uppercase is reserved for `.sh-mono` metadata; do not uppercase buttons or the main question.

## Surfaces, spacing, and controls

- Flat paper canvas with white panels and structural hairlines; no decorative elevation.
- 4px spacing rhythm. Common flow values: 8, 12, 16, 20, 24, 32, and 40px.
- Outer gutters: 16px mobile, 32px from `md`; outer maximum width 1024px.
- Main state panel padding: 20px mobile, 24–32px desktop.
- Controls: at least 44px high; primary state-transition actions should be 48px where practical.
- Large natural-language textarea: comfortable multi-line entry (roughly 160–220px), white or paper inset surface, visible label, no floating label.
- Borders: one pixel at `ink/15` or `ink/20`. Focus adds the global 2px coral outline and may strengthen the internal border to forest.
- Default radius remains 2px. Do not create rounded pills; example prompts may use small rectangular bordered buttons.
- Primary action: forest/white, hover to ink. Secondary: white/ink with hairline, hover forest. Tertiary: text-only muted to ink.
- Code/instruction regions use paper background, mono text, and bounded overflow.
- Do not use gradients, glass/blur, oversized corner radii, decorative shadows, floating cards, or illustration-led empty states.

## Progress and semantic states

- Three steps are always named `Describe`, `Review`, and `Install`.
- Active state uses ink/forest and explicit text such as `Current step`; completed state uses a check icon or `Complete` text; upcoming uses muted text. Never communicate state through color alone.
- Drafting/loading keeps button size fixed, disables repeat submission, and uses visible status copy. A subtle existing-style skeleton or restrained spinner is acceptable; no decorative AI sparkle treatment.
- Review assumptions are neutral paper/forest-soft notes. Unsupported requests or blocking clarification use coral/danger treatment with explicit text.
- Approval/saved/copy success uses success text and an `aria-live` status.
- Form/API errors use a coral border, coral translucent background, danger text, and `role="alert"` where blocking.

## Motion and accessibility

- Use short color/transform transitions only.
- No continuous animation. Generation progress may animate only when necessary and must stop in reduced-motion mode.
- Preserve the global 2px coral focus-visible outline with 2px offset.
- Use native form, button, `<details>`, and link semantics.
- Every textarea/control has a visible label; helper/error text is programmatically associated.
- Keep keyboard target heights at least 40px and the main actions 44–48px.
- Copy, save, draft, and approval feedback is announced without moving focus unexpectedly.
- Step order is conveyed as an ordered semantic structure; it is not a tablist unless users can actually navigate completed states as tabs.
- Text and layout remain usable at 200% zoom and on 320px-wide screens without page-level horizontal scrolling.
