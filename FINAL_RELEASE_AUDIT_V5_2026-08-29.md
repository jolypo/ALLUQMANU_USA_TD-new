# FINAL RELEASE AUDIT — ALLUQMANU_USA_TD v5

## Release goal
Add an optional Confirmed Setup workflow for Equity Options and SPX Options without replacing or rewriting the existing Core/V20 strategies.

## Architecture review
- [x] Equity Options Core remains callable independently.
- [x] SPX Core remains callable independently.
- [x] SPX V20 remains callable independently.
- [x] New Equity Confirmed Setup has its own monitor key and DTE selection.
- [x] New SPX Confirmed Setup has its own monitor key and DTE selection.
- [x] Hunter reuses existing production gates rather than bypassing them.
- [x] Confirmed Setup is a mandatory sequence gate, not an extra score bonus.
- [x] Hold and Retest are both supported confirmation paths.
- [x] Bullish and bearish logic is mirrored.
- [x] PUT confirmation uses underlying SHORT direction, not long-option position direction.
- [x] Judge is a second-stage independent ranker.
- [x] Same-sector/same-direction equity correlation guard is active in Judge.
- [x] Judge requires >= 90.
- [x] Existing manual Telegram approval is retained.

## Trading-safety review
- [x] No READY merely because a raw score is high; new mode requires structure confirmation.
- [x] Failed break waits rather than becoming READY.
- [x] Break without hold/retest waits.
- [x] Structure without momentum waits.
- [x] Existing stale-data guards remain upstream.
- [x] Existing Dynamic Market Gate remains upstream.
- [x] Existing option Bid/Ask/Spread/contract-quality checks remain upstream.
- [x] Existing 0DTE / 1–7 DTE / 8–35 DTE selection remains authoritative.
- [x] Existing contract-price-per-horizon limits remain authoritative.

## Regression verification
- Legacy test suite before v5: 88/88 PASS.
- Full suite after v5 additions: 92/92 PASS.
- Python compileall: PASS.

## Important limitation
Confirmed Setup is a deterministic technical confirmation layer. It does not claim predictive certainty and it does not use full Level-2 order-book depth. Current execution quality continues to rely on the data already available to the project (including top Bid/Ask/Spread and contract activity where available). Paper-trading validation remains required before treating thresholds as statistically calibrated.
