# SiteHits domain semantics

- Standard traffic reports exclude events above SiteHits' explicit-automation threshold. Bot reporting separately exposes verified bot events and suspected browser automation; do not merge those concepts.
- Browser visitor identifiers are privacy-preserving daily hashes. They support within-day unique visitors, but not ordinary cross-day retention or returning-user claims.
- Product activation uses server events linked by a privacy-preserving actor hash. Activation results are only meaningful when the configured start and goal events are authoritative and actor coverage is adequate.
- Percentage deltas compare the selected period with the immediately preceding period of equal length. A null delta means the previous value was zero while the current value was nonzero.
- Breakdown percentages are calculated over the returned ranked rows, not necessarily every possible row when a limit is applied.
- Collector health indicates whether SiteHits recently received events. It does not prove that every eligible event was emitted.
- Traffic movement, campaign changes, releases, and bot activity may coincide. Treat them as evidence for follow-up, not proof of causality.
