# Design QA: last-hour embeddable widget

## Comparison target

- Source visual truth: `/var/folders/hg/sj0h9z5d7r75rpy6rqfq8_vr0000gn/T/TemporaryItems/NSIRD_screencaptureui_1ZDrAw/Screenshot 2026-07-13 at 10.34.13.png`
- Browser-rendered implementation: `/tmp/sitehits-widget-400x600.png`
- Mobile implementation: `/tmp/sitehits-widget-320x600.png`
- Full-view combined comparison: `/tmp/sitehits-widget-comparison.png`
- Primary viewport: 400 × 600
- Responsive viewport: 320 × 600
- State: public widget with 49 temporary QA visitors across 24 non-empty minute buckets and three country rows

The complete card remains readable in the combined full-view comparison, so a separate focused crop was not needed.

## Findings

- No actionable P0, P1, or P2 differences remain.
- The implementation keeps the reference hierarchy: recent-visitor label, large live total, minute bars with time labels, country ranking, and attribution.
- The 60-minute window, sixty one-minute buckets, SiteHits forest palette, 2px frame, square data bars, ISO country-code metadata, and shadow-free surface are intentional product requirements rather than fidelity defects.
- The reference's flag emoji, orange palette, inflated rounded card, decorative shadow, and DataFast attribution were intentionally replaced by SiteHits design-system equivalents.

## Required fidelity surfaces

- Fonts and typography: the widget uses the existing Space Grotesk/system sans and JetBrains Mono/system mono stacks. The hierarchy remains close to the reference while matching SiteHits metadata and tabular-number conventions.
- Spacing and layout rhythm: the 400 × 600 card has 28px primary padding, a 168px chart, clear country separation, and a compact footer. At 320px it reduces to 22px padding with no horizontal overflow.
- Colors and visual tokens: panel white, ink, muted ink, forest chart bars, success live indicator, coral focus, and hairline borders all use checked-in SiteHits tokens.
- Image quality and asset fidelity: the only image asset is the real checked-in SiteHits mark. Dynamic country values use text codes instead of fake or generated flag assets.
- Copy and content: "Visitors in the last 60 minutes", "Live", "Countries", and "Powered by SiteHits" are concise and describe the actual data contract.

## Interaction and responsive verification

- Opened the public widget in the Codex in-app browser at 400 × 600 and 320 × 600.
- Confirmed exactly 60 chart bars, three country rows, and 24 non-empty QA minute buckets.
- Confirmed page width equals scroll width at both breakpoints: 400/400 and 320/320.
- Confirmed the card occupies 398px at the primary viewport and 318px at the responsive viewport.
- Checked browser console warnings/errors at both viewports: none.
- The public card has no controls. Dashboard modal open/close, copy success, Escape dismissal, and focus restoration are covered by the JavaScript interaction suite.
- Temporary QA analytics events were deleted after capture; the development server and browser tab were also cleaned up.

## Comparison history

1. The first browser capture showed the empty/flat distribution correctly but did not exercise varied bar heights. Temporary aggregate-only QA events were added, the final browser capture showed multiple bar heights and ranked country counts, and those events were then deleted.
2. The final combined comparison found no actionable P0/P1/P2 mismatch. No source-code visual fix was required after the final comparison.

## Follow-up polish

- No P3 follow-up is required for this scope.

final result: passed
