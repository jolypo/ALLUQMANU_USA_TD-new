# ALLUQMANU_USA_TD — Final Dynamic Market Release Audit

Date: 2026-08-29
Mode: Paper Trading / Signal Engine only

## Release decision

APPROVED for paper-trading deployment after local automated validation.

## Dynamic methodology

- Base READY floor: 90
- Caution: 92
- Range/Mixed: 93
- Counter-trend: 93
- High volatility: 94
- Low liquidity with clear direction: 94
- Low liquidity with unclear direction: NO TRADE
- Wide/poor option market: NO TRADE

CALL and PUT use the same market-quality policy.

## Preserved safeguards

- Freshness checks for stock bars, option quotes, SPX and SPY proxy data
- Manual approval before publish/open
- Candidate TTL 3 minutes
- DTE horizons: Daily=0DTE, Weekly=1–7, Monthly=8–35
- Independent contract-price caps per asset category and DTE horizon
- Profit alerts only above confirmed entry and according to configurable price step
- Manual-close Telegram cleanup while retaining History/Performance data
- Reports private/on-demand; no automatic daily/weekly channel reports
- Near Stop Loss is not published to the channel
- Cohort reporting based on entered_at and DTE-specific pending/finalization logic

## Validation

See pytest suite and compileall result from the release build. No real broker execution is enabled.

## Deployment hard floor

The effective READY score uses `ready_score_floor = max(90, MIN_SCORE)`. An old Render environment value such as `MIN_SCORE=75` cannot lower the production threshold below 90. A value above 90 remains supported.

## Final automated result

- pytest: 88 passed
- compileall: PASS
