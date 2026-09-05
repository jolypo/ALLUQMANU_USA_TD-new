# FINAL RELEASE AUDIT — v6 Learning

## Release objective
Add learning from completed Confirmed Setup trades without changing the previously approved trading engines.

## Trading review
- [x] Core remains unchanged.
- [x] SPX Core remains unchanged.
- [x] SPX V20 remains unchanged.
- [x] Confirmed Setup remains the only strategy affected by learning.
- [x] CALL/PUT uses underlying LONG/SHORT direction.
- [x] 0DTE / 1–7 DTE / 8–35 DTE horizons are retained.
- [x] Dynamic Market Gate remains authoritative.
- [x] Contract spread/freshness/quality gates remain authoritative.
- [x] Raw Judge < 90 cannot be rescued by learning.
- [x] Historical weak profiles can be penalized.
- [x] Strong profiles can receive only a bounded ranking bonus.
- [x] Statistical success semantics are preserved for option learning labels.

## Engineering review
- [x] Learning memory uses JSON and de-duplicates by trade ID.
- [x] Learning starts in COLLECTING mode.
- [x] Activation requires a minimum sample count.
- [x] Bayesian prior reduces small-sample overfitting.
- [x] Learning adjustment is bounded.
- [x] GitHub token is never stored in source or example values.
- [x] Optional GitHub persistence writes to `learning-data`, not `main`.
- [x] GitHub failures do not block signal scans.
- [x] `/learning` endpoint reports learning status.
- [x] Python compileall PASS.
- [x] Full pytest suite PASS: 97/97.

## Operational limitation
Without `LEARNING_GITHUB_TOKEN` (or another persistent store), local learning memory can be lost when an ephemeral Render instance is replaced/restarted. The optional GitHub sync exists specifically to preserve the memory across such events.

## Conclusion
v6 is approved for paper-trading evaluation. Learning performance should be evaluated over a meaningful sample; one week may show collection progress but may not provide enough samples for reliable calibration.
