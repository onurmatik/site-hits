# SiteHits Design System

## Product context

SiteHits is a small, privacy-first, multi-site web analytics service. It collects cookieless, privacy-safe traffic signals and gives site owners an operational dashboard without raw IP storage, raw user-agent storage, cross-site profiles, or long-lived visitor identity.

The primary jobs to be done are:

- Start tracking a website with the least possible setup friction.
- Understand traffic volume, engagement, acquisition, content, geography, devices, and custom events at a glance.
- Compare recent performance with a previous period.
- Move from an anonymous website entry to an owned account without losing the submitted website.
- Install one tracking script and immediately reach the relevant site dashboard.

The product is server-rendered with Django templates and Tailwind utilities. It has no sidebar and no extracted client-side component library. The current visual source of truth is `assets/design-system.css`, `assets/tailwind.css`, the templates under `templates/`, and `static/sitehits-mark.svg`.

## Information architecture and key flow

1. `/` is the anonymous website-first landing page: public header, compact hero, domain form, privacy/setup reassurance, and a representative dashboard preview.
2. Posting a valid domain begins onboarding and preserves the normalized website in the session.
3. Anonymous onboarding must show a **sign-up** experience, not the existing internal username/password sign-in form. Use the supplied DataFast screen only as a structural reference: product-context headline, Google option, divider, email option, and concise supporting copy. Retain SiteHits branding and tokens.
4. Sign-up supports Google and passwordless email. Email sends a `django-sesame` magic link; it does not ask the user to create or enter a password.
5. Successful authentication resumes the preserved onboarding context, confirms the website, creates or reuses the tracked site, and proceeds to tracker installation.
6. `/onboarding/` is the centered website confirmation step.
7. `/onboarding/<site-slug>/` is the centered tracker installation step.
8. `/dashboard/all` and `/dashboard/<site-slug>` are authenticated reporting views.
9. Django admin and API documentation remain secondary, operational destinations.

An existing account must be able to continue through the same authentication screen. Provider/email authentication should resolve to sign-in when the identity already exists and sign-up when it does not; the UI should not force users to understand that distinction. A small "Already have an account? Sign in" affordance may be shown where a distinct legacy sign-in route remains necessary.

## Visual direction

Use a technical-minimalist analytics interface adapted from Mosaic Grid Architecture. SiteHits should feel precise, calm, trustworthy, and operational. The anonymous landing and sign-up screens may use larger type and more whitespace, but they remain direct product-entry surfaces rather than long marketing pages.

Core visual rules:

- Flat, paper-like surfaces with structural one-pixel hairlines.
- Compact mono metadata paired with clear sans-serif headings.
- Forest is the dominant action and data color; coral and gold are restrained accents.
- No gradients, glass effects, soft decorative blobs, or large drop shadows.
- Avoid inflated rounded cards. Default radius is 2px; circular shapes are reserved for indicators.
- DataFast informs sign-up hierarchy only. Do not copy its orange palette, heavy shadow, large rounded card, or typography.
- Use the real SiteHits SVG mark for public/auth brand lockups. Do not substitute a generic logo. The CSS-built square mark used in the current authenticated shell may remain there.

## Color tokens

| Role | Token | Value | Usage |
| --- | --- | --- | --- |
| Page background | `paper` / `--sh-paper` | `#f7f7f5` | Global canvas and quiet control hover |
| Surface | `panel` / `--sh-panel` | `#ffffff` | Cards, inputs, headers, panels |
| Primary text | `ink` / `--sh-ink` | `#17211b` | Headings, body, dark hover |
| Secondary text | `muted` / `--sh-muted` | `#667069` | Supporting copy and metadata |
| Primary action | `forest` / `--sh-forest` | `#1a3c2b` | CTA, active filter, brand mark, primary series |
| Soft primary | `forest-soft` / `--sh-forest-soft` | `#dce7df` | Quiet selected or informational surfaces |
| Accent | `coral` / `--sh-coral` | `#e78468` | Focus outline, errors, secondary chart series |
| Accent | `gold` / `--sh-gold` | `#d6ae45` | Rare tertiary data emphasis |
| Success | `success` / `--sh-success` | `#287a4b` | Operational state and positive delta |
| Danger text | `danger` / `--sh-danger` | `#a94736` | Error copy |
| Hairline | `hairline` / `--sh-hairline` | `rgba(23, 33, 27, 0.14)` | Default borders and separators |

White-on-forest is the primary filled-button treatment. Ink-on-white with a hairline is the secondary treatment. Google branding is the only provider-brand exception: use an official multicolor Google `G` asset and preserve its approved colors; do not recolor or redraw it. Error surfaces use a coral border, subtle coral tint, and danger text. Do not introduce new semantic colors when an existing token serves the state.

## Typography

- Sans stack: `"Space Grotesk", Inter, ui-sans-serif, system-ui, sans-serif`.
- Mono stack: `"JetBrains Mono", "SFMono-Regular", Consolas, monospace`.
- The project does not currently load remote font files; system fallbacks must remain visually acceptable and layouts must not depend on a specific font being installed.
- Product wordmark: 18-20px, 700, tight tracking.
- Landing display: 40/44px on small screens and 60/64px from medium screens, 600.
- Page title: 28/34px, 600, tight tracking.
- Auth/onboarding title: 30px on compact screens and up to 36px where width allows, 600.
- KPI value: 30/34px, 600, tabular figures.
- Body: 14/21px by default; 16/28px for prominent explanatory copy; landing lead may be 18/28px.
- Labels and metadata: mono, 10-12px, uppercase, `0.08em` tracking.
- Tables: 13px labels with tabular numeric values.
- Use sentence case for buttons and headings. Avoid all caps except mono labels and compact metadata.

## Spacing and sizing

Use a 4px base grid. Preferred spacing values are 4, 8, 12, 16, 20, 24, 32, 40, 56, 64, 80, and 96px.

- Desktop page gutters: 24-32px; mobile gutters: 16px.
- Standard section gap: 24px on small screens, 32px on larger screens.
- Panel padding: 16-24px; focused onboarding/auth panels may use 24px mobile and 32px desktop.
- Form field gap: 20px; label-to-control gap: 8px.
- Public form controls and primary CTAs: at least 48px high.
- Utility/header actions and period controls: at least 40px high.
- Header height: 64px.
- Default radius: 2px for panels, inputs, buttons, and segmented controls.
- Status dots may be circular; tags may use a fully rounded shape only when their compact semantic role requires it.

## Layout conventions

- Global content maximum: 1440px.
- Public landing content: centered within 1440px; dashboard preview capped at 1200px.
- Public header: full-width 64px bar with bottom hairline, real logo/wordmark left, one authentication or dashboard action right.
- Anonymous sign-up: single centered column, approximately 448-576px wide, with enough top/bottom breathing room; it must fit a 320px viewport without horizontal scrolling. Use one bordered flat panel only if it helps distinguish the form from the paper background.
- Confirmation panel: centered, maximum 576px.
- Installation panel: centered, maximum 672px.
- Dashboard shell: sticky full-width header and centered content capped at 1440px.
- KPI grid: five equal columns on large screens, two on medium screens, one on small screens.
- Breakdown grid: two equal columns on large screens, one column below the large breakpoint.
- The chart is full width and 300px high in the authenticated dashboard.
- No sidebar is required for the MVP.

## Component specifications

### Brand lockup

Use `static/sitehits-mark.svg` at 32-36px with the SiteHits wordmark and a 12px gap. The SVG is a forest 32px square with 2px radius and a centered white outlined square at 60% opacity. Auth and onboarding pages should use this real asset when feasible; decorative or provider icons must not compete with it.

### Public header

Use the brand lockup on the left and a single quiet text action on the right. Anonymous users see "Sign in" before beginning onboarding; authenticated administrators see "Dashboard". Actions are at least 40px high and change from muted to ink on hover.

### Website start form

Use one URL input with globe icon, inline validation, one forest CTA, and short reassurance: no cookies, no credit card, two-minute setup. Desktop may place input and CTA in one row; stack below the small breakpoint. Never discard the submitted domain when authentication is required.

### Anonymous sign-up and authentication

This is the required replacement for the password-based screen reached from anonymous onboarding.

- Context heading: welcoming sign-up language tied to the submitted website when available, for example "Create your SiteHits account" and "Start tracking example.com". Do not label this flow "Internal analytics".
- First action: full-width Google provider button with official `G` mark and explicit text such as "Continue with Google" or "Sign up with Google".
- Divider: one-pixel hairlines with a compact muted "or" label.
- Email field: type `email`, explicit visible label, useful placeholder, `autocomplete="email"`, and inline validation.
- Email action: full-width forest CTA such as "Continue with email" or "Send magic link". Its result is a `django-sesame` sign-in link; do not show a password field.
- Pending state: disable repeated submission, preserve control width, and use action-specific copy such as "Sending link…".
- Confirmation state: replace or update the form with a calm message that the link was sent, show the masked/entered address, explain that the link continues setup, and offer "Use a different email".
- Google pending/error states and email delivery errors are inline in the same panel; do not use browser alerts or redirect to an unbranded error page.
- Include concise terms/privacy consent copy only if real destinations exist. Do not add dead links to imitate the reference screenshot.
- Keep a low-emphasis route back to SiteHits or to edit the website.
- Preserve `next` and the website session context across Google and magic-link redirects so authentication resumes onboarding rather than dropping users at a generic dashboard.

### Buttons

- Primary: forest background, white text, 2px radius, medium weight; hover to ink.
- Secondary: white background, ink text, hairline border; hover/focus strengthens border to forest.
- Provider: white or ink surface with strong contrast, official provider mark, full width, and the same 48px height as the email CTA. It must look like an authentication action without introducing a new rounded/shadowed style.
- Disabled/pending: reduced contrast and `not-allowed` cursor, without changing dimensions.
- Button labels should describe the outcome: "Send magic link" is clearer than a generic "Submit".

### Inputs

Use a white surface, one-pixel `ink/20` border, 2px radius, and 12-16px horizontal padding. Public controls are at least 48px tall; compact internal controls may be 44px. Focus strengthens the border to forest while the global focus-visible outline remains coral. Errors use a danger/coral treatment and a text explanation associated with the input.

### Onboarding panels

Confirmation and installation are centered, bordered panels with one dominant action. Use mono step labels, a clear sans title, restrained supporting copy, and secondary actions below or alongside the primary. The install snippet sits in a paper inset with an accessible copy action.

### Dashboard header and filters

The authenticated header contains the mark/wordmark, site selector, optional operational status, and logout action. The period selector is a flat segmented control; active state is forest with white text. Preserve the selector and period controls on mobile, allowing horizontal scrolling for period choices.

### KPI cards, charts, and breakdowns

- KPI card: mono label, large tabular value, previous-period delta, and optional 2px top forest accent on the lead metric.
- Chart panel: title/legend row and fixed-height plot, with forest for visitors and coral for pageviews.
- Breakdown table: title row, ranked items, proportional forest bar, and right-aligned value.
- Dashboard preview: browser-framed flat panel composed from the real dashboard vocabulary. On narrow screens crop its 680px internal canvas inside the panel; never create page-level overflow.

### Feedback states

- Loading: preserve final layout with low-contrast forest skeleton blocks.
- Empty: calm explanatory copy and one clear next action.
- Error: inline coral-bordered notice with danger text; no blocking browser alerts.
- Success: concise confirmation with success color used sparingly.

## Responsive behavior

- Use mobile-first stacking; all functionality must remain available at 320px width.
- Do not hide KPI cards or breakdowns on small screens.
- Stack website input/CTA and paired onboarding actions below the small breakpoint.
- Auth provider, email input, and email CTA remain full width at every breakpoint.
- Permit horizontal scrolling only inside intentionally wide preview or segmented-control regions.
- Table rows and form error copy must wrap cleanly.
- Interactive targets are at least 40px, with 48px preferred for conversion and authentication actions.

## Accessibility

- Maintain WCAG AA text contrast; never use color as the only state signal.
- Every input has a persistent visible label. Placeholders are examples, not labels.
- Preserve the global 2px coral focus-visible outline with 2px offset and do not suppress it.
- Provider buttons have readable text in addition to logos; decorative marks use empty alt text or `aria-hidden`.
- Errors use `role="alert"` where immediate announcement is appropriate and are programmatically associated with their field.
- Authentication pending/sent states should be announced through an appropriate live region.
- Respect logical heading order and keep keyboard navigation order aligned with the visual order.

## Motion

- Hover and focus transitions: 120-180ms ease-out, limited to color, border, and subtle opacity changes.
- Button pending states may use a restrained spinner; keep the label and geometry stable.
- Charts may animate once on initial load.
- No continuous decorative animation, parallax, bouncing CTAs, or large entrance choreography.
- Honor `prefers-reduced-motion`; the existing CSS reduces transition and animation durations to near zero and limits iteration count.

## Implementation requirements

- Keep server-rendered Django templates and the existing Tailwind token layer as the source of visual truth.
- Use `django-sesame` for passwordless email magic-link authentication.
- Support Google sign-up/sign-in as the provider option.
- Do not ask anonymous onboarding users for a username or password.
- Keep CSRF protection, safe `next` handling, and the normalized website in the onboarding session through either authentication method.
- Reuse existing accounts and tracked sites when identity/domain already exists; avoid duplicate-account and duplicate-site dead ends.
- Keep auth, callback, validation, and delivery errors inside the SiteHits visual shell.
- Do not add colors, fonts, gradients, radii, or shadows outside this system when generating or implementing design variants.
