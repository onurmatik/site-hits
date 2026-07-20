# Design QA — AI-assisted product metrics

Date: 2026-07-20

## Source visual truth

- Current technical-form reference: `/Users/onurmatik/.codex/attachments/48731775-1791-40e7-b90d-84ef1e5283e7/image-1.png`
- Describe direction: `/Users/onurmatik/.codex/generated_images/019f7f10-d7e3-76a1-a8c2-ea1453a663a4/exec-0e1aa469-34e4-4850-8728-9a1a493ef509.png`
- Review direction: `/Users/onurmatik/.codex/generated_images/019f7f10-d7e3-76a1-a8c2-ea1453a663a4/exec-84f21eae-fdc6-47e8-a476-462ff8cdff42.png`
- Install direction: `/Users/onurmatik/.codex/generated_images/019f7f10-d7e3-76a1-a8c2-ea1453a663a4/exec-20c92937-67d3-47dc-a630-24de7b18ec0d.png`

The three directions were treated as consecutive states of one flow: Describe → Review → Install.

## Implementation evidence

- Desktop Describe, 1280 px: `.superdesign/qa/describe-desktop.png`
- Desktop Review, 1280 px: `.superdesign/qa/review-desktop.png`
- Desktop Install, 1280 px: `.superdesign/qa/install-desktop.png`
- Mobile Install, 390 × 844: `.superdesign/qa/install-mobile-390x844.png`
- Same-input full-view comparison: `.superdesign/qa/desktop-comparison.png`
- Focused responsive comparison: `.superdesign/qa/install-mobile-comparison.png`

The full-view board places each source direction next to its implemented state. The focused mobile capture covers the most crowded region—approved outcomes, environment values, masked secret, and the start of the agent instruction—so a second crop was not necessary.

## Fidelity review

- Typography: restrained sans-serif hierarchy, compact labels, monospace identifiers, and readable body copy match the product's technical-minimal character.
- Spacing and layout: the desktop flow keeps a narrow centered work surface and clear step rhythm; mobile stacks controls without horizontal overflow (`clientWidth = scrollWidth = 390`).
- Colors: neutral surfaces, subtle borders, and the existing SiteHits green are used consistently for progress, status, and primary actions.
- Images and assets: the flow is intentionally asset-light. Decorative reference icons were omitted because the product has no matching icon system and they do not change meaning.
- Copy and content: all three states preserve the reference intent while making the sequence explicit. The implementation retains the existing Product metrics header and Advanced setup fallback.

## Interaction QA

- Describe: example outcomes populate the textarea; drafting disables the submit control and exposes an accessible busy state; no database record is saved.
- Review: proposed events, Added/Reused/Changed status, aggregation, firing conditions, activation, assumptions, conflicts, and an optional clarification are visible before approval.
- Install: confirmation persists atomically, the private key starts masked, Show/Hide works, environment and agent-instruction copy actions work, and the generated prompt references an environment variable rather than embedding the secret.
- Responsive: the 390 × 844 viewport keeps step labels, fields, buttons, and long identifiers inside the viewport.
- JavaScript: no page-level error appeared during browser interaction. The interaction suite also covers loading, copy, masking, and clipboard fallback behavior; direct browser-log retrieval was unavailable after the isolated QA server was shut down.

## Comparison history and findings

1. Initial live-model output interpreted three independent outcomes as a three-step funnel and blocked approval. This was a semantic P1 issue.
2. The planning instruction was tightened to allow at most one sensible two-event activation and to track remaining outcomes independently.
3. A second real-model pass returned an approvable plan with signup, first project creation, confirmed TRY subscription revenue, and a signup → first project activation.
4. Final source-versus-implementation review found no remaining P0, P1, or P2 visual or interaction defects.

Intentional variance: the Install state uses compact approved-outcome rows instead of decorative icon cards. It retains all required information and better matches the existing SiteHits component language.

final result: passed
