# Design QA: signed-in add website modal

## Comparison target

- Source visual truth: `/var/folders/hg/sj0h9z5d7r75rpy6rqfq8_vr0000gn/T/TemporaryItems/NSIRD_screencaptureui_av5iiR/Screenshot 2026-07-12 at 17.13.57.png`
- Implementation screenshot: `/tmp/sitehits-new-site-modal.png`
- Full-view comparison: `/tmp/sitehits-new-site-modal-comparison.png`
- Focused modal comparison: `/tmp/sitehits-new-site-modal-focused-comparison.png`
- Mobile implementation screenshot: `/tmp/sitehits-new-site-modal-mobile.png`
- Desktop viewport: 1120 × 682
- Mobile viewport: 390 × 844
- State: authenticated dashboard, site menu closed, Add a new website modal open, empty domain field focused

## Findings

- No actionable P0, P1, or P2 differences remain.
- The implementation preserves the reference hierarchy: separated title row, Domain control with protocol prefix, Timezone row with local time, explanatory copy, and one full-width Add website action.
- The narrower 576px panel, 2px radius, forest CTA, flat border, and absence of a decorative shadow are intentional SiteHits design-system constraints rather than fidelity defects.
- The Close action is an intentional accessibility and recovery affordance added to the reference structure.

## Required fidelity surfaces

- Fonts and typography: SiteHits keeps its Space Grotesk/system sans and JetBrains Mono/system mono stacks. Heading, labels, helper copy, metadata, and CTA weights follow the existing dashboard hierarchy without introducing a reference-specific font.
- Spacing and layout rhythm: header divider, 20px form rhythm, 48px controls, responsive 576px maximum width, and 16px mobile viewport gutters are consistent. At 390px, the page has no horizontal overflow and the dialog occupies 358px.
- Colors and visual tokens: paper, panel, ink, muted, forest, coral focus, and hairline borders all come from the checked-in SiteHits tokens. The DataFast orange is intentionally not copied.
- Image quality and asset fidelity: no raster assets are required. The domain globe reuses the existing SiteHits form icon treatment; no placeholder or newly drawn decorative asset was introduced.
- Copy and content: title, Domain, Timezone, local-time context, “today” helper copy, and Add website action match the requested flow. The neutral `example.com` placeholder follows the existing product copy style.

## Interaction and responsive verification

- Opened the site selector and launched the modal from New site.
- Verified focus enters the Domain field.
- Verified Escape closes the modal and returns focus to the visible site-menu trigger.
- Submitted an invalid domain and verified the dashboard reloads with the modal open, the entered value preserved, and an inline error announced.
- Verified the server-side success path creates or reuses the owned site and redirects directly to tracker installation.
- Checked the in-app browser console: no errors.
- Checked 390 × 844 layout: viewport width 390, page scroll width 390, dialog bounds 16–374px.

## Comparison history

1. Pass 1 found a P2 keyboard issue: the browser surface did not reliably apply native Escape cancellation. Fixed by making Escape closure an explicit application behavior.
2. Pass 2 found a P2 focus issue: restoring focus to New site failed because that control is hidden when the menu closes. Fixed by restoring focus to the visible site-menu trigger.
3. Pass 3 rechecked desktop visual structure, error recovery, focus behavior, mobile overflow, and console output. No actionable P0/P1/P2 findings remain.

## Follow-up polish

- No P3 follow-up is required for this scope.

final result: passed
