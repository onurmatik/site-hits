# Design QA: all-sites comparison table

## Comparison target

- Source visual truth: `/Users/onurmatik/.codex/visualizations/2026/07/15/019f648e-0553-7f60-b853-813a7d0ed490/sitehits-dashboard-audit/04-superdesign-comparison-table.png`
- Browser-rendered desktop implementation: `/Users/onurmatik/.codex/visualizations/2026/07/15/019f648e-0553-7f60-b853-813a7d0ed490/sitehits-dashboard-audit/05-all-sites-implemented-desktop.png`
- Browser-rendered mobile implementation: `/Users/onurmatik/.codex/visualizations/2026/07/15/019f648e-0553-7f60-b853-813a7d0ed490/sitehits-dashboard-audit/06-all-sites-implemented-mobile.png`
- Full-view combined comparison: `/Users/onurmatik/.codex/visualizations/2026/07/15/019f648e-0553-7f60-b853-813a7d0ed490/sitehits-dashboard-audit/07-reference-vs-implementation.png`
- Primary viewport: 1280 x 720
- Responsive viewport: 390 x 844
- State: authenticated superuser, last seven days, daily granularity, two active sites with live local analytics data

The full comparison keeps the reference and implementation at the same 1280 x 720 viewport. The table remains large enough to judge directly, so a separate focused crop was not needed.

## Findings

- No actionable P0, P1, or P2 differences remain.
- The implementation matches the selected direction's hierarchy: site-performance table first, one row per site, five comparable metrics with period deltas, per-site detail links, an aggregate-summary divider, and the aggregate chart below it.
- The first comparison found the table about 20px taller than the reference and used an underlined desktop action. Desktop header/row padding and action styling were tightened; the final table height is 232.5px, matching the reference's approximately 232px frame.
- The reference uses illustrative values and UTC. The implementation intentionally renders current database values and the product's configured Europe/Istanbul reporting timezone.
- The reference includes a small arrow beside each detail action. The checked-in product has no matching icon-library asset, so the implementation keeps the text link without inventing a replacement asset.

## Required fidelity surfaces

- Fonts and typography: the existing SiteHits sans, mono metadata, tabular numerals, weights, and uppercase labels are preserved.
- Spacing and layout rhythm: the desktop frame, table header, two compact rows, aggregate divider, and chart align closely with the selected reference. Mobile converts each row into a two-column metric grid without horizontal scrolling.
- Colors and visual tokens: panel white, paper background, ink, muted ink, forest, success, danger, coral, and hairline borders all use checked-in SiteHits tokens.
- Image quality and asset fidelity: no new raster or decorative assets were needed. Existing brand assets remain unchanged.
- Copy and content: "Site performance", the five metric labels, "View details", "All-site aggregate summary", and "Aggregate volume" mirror the selected direction while using live site names and domains.

## Interaction and responsive verification

- Opened the authenticated all-sites dashboard in the Codex in-app browser at 1280 x 720 and 390 x 844.
- Confirmed two site rows, five populated metrics per row, and no placeholder em dashes after data loading.
- Confirmed page width equals scroll width at both breakpoints: 1280/1280 and 390/390.
- Confirmed each detail link preserves `period=last7d` and `granularity=daily` and points to its corresponding site dashboard.
- Confirmed the mobile detail action has a 40px minimum touch height.
- Confirmed the selected-site dashboard still renders five KPI cards, the embed-widget control, and no comparison table.
- Checked browser console warnings/errors on all-sites desktop, all-sites mobile, and the selected-site route: none.

## Comparison history

1. The first implementation comparison confirmed the correct hierarchy and data but found a taller table and a visually heavy desktop link treatment.
2. Desktop table padding was tightened and the detail action was aligned to the reference; mobile retained a larger underlined touch target for usability.
3. The final combined comparison found no actionable P0/P1/P2 mismatch.

## Follow-up polish

- The source's tiny trailing arrow is optional P3 polish if a matching product icon library is introduced later.

final result: passed
