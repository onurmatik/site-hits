# Extractable components

Updated from the product-metrics render path on 2026-07-20.

## Extraction decision

No existing layout component should be extracted into a Superdesign `<sd-component>` for this task.

SiteHits is server-rendered Django. `templates/dashboard/product_metrics_settings.html` is one page template with inline sections, and `templates/base.html` is a document skeleton rather than a visual navigation component. The target route does not render the dashboard header, sidebar, footer, or a reusable application-shell partial. Under the Superdesign extraction rules, basic panels, buttons, inputs, event rows, and code boxes are too small to justify component extraction.

Therefore Step 2.5 component extraction should be skipped. Pixel fidelity must come from passing the complete target template and CSS sources to each design command.

## Existing patterns that are context, not extractable components

| Pattern | Source | Why not extract |
| --- | --- | --- |
| Product-metrics page shell | `templates/dashboard/product_metrics_settings.html:6-74` | Page-specific structure with Django formsets and state. |
| Event fieldset | `templates/dashboard/product_metrics_settings.html:27-38` | Repeated server-rendered form markup; a basic form composition, not a reusable layout component. |
| Copy panel | `templates/onboarding/install.html:12-31`, `43-58` | Repeated inline markup without a shared source abstraction; use as visual context. |
| CSS-built brand lockup | `templates/onboarding/install.html:8` | Small static primitive; inline it in a draft if needed. |
| Dashboard header | `templates/dashboard/dashboard.html:13-80` | Reusable in concept, but not rendered on this route and would make the reproduction inaccurate. |

## Future production refactor boundaries

These are possible future components, not existing extractable components and must not be registered in Superdesign as source-backed components:

- `ProductMetricsFlowProgress`
- `TrackingIntentComposer`
- `TrackingPlanReview`
- `CopyInstructionPanel`
- `AdvancedProductMetricSetup`

Design drafts may depict these structures inline. Component extraction should wait until production code establishes real shared boundaries.
