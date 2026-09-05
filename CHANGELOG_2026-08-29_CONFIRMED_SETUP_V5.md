# v5 — Confirmed Setup + Hunter/Judge

## Scope
This release adds a new optional strategy path while preserving the production logic of Equity Options Core, SPX Core, and SPX V20.

## Telegram paths
- Trading → Equity Options → Core → Daily/Weekly/Monthly
- Trading → Equity Options → Confirmed Setup → Daily/Weekly/Monthly
- Trading → Index Options → SPX V20 → Daily/Weekly/Monthly
- Trading → Index Options → SPX Core → Daily/Weekly/Monthly
- Trading → Index Options → Confirmed Setup → Daily/Weekly/Monthly

## Confirmed Setup pipeline
1. Hunter uses the existing production option engine as the first quality gate.
2. Setup Confirmation requires a recent breakout/breakdown.
3. It then accepts either Break + Hold or Break + Retest.
4. Structure must remain valid after the break.
5. Directional momentum must confirm continuation.
6. Existing Dynamic Market Gate, freshness, contract, spread and risk protections remain in force through the Hunter path.
7. Judge independently ranks candidates using signal quality, contract quality and execution spread.
8. Correlation guard prevents multiple same-sector/same-direction equity option bets from dominating one batch.
9. Judge must score >= 90 to APPROVE.
10. Nothing is published without the existing manual approval workflow.

## Direction symmetry
Confirmed Setup reads `option.underlying_direction`, not the generic long-option position direction. CALL/LONG and PUT/SHORT therefore use mirrored structural confirmation rules.

## Legacy preservation
- `/option` remains the existing Core equity-options scan.
- SPX Core implementation is unchanged.
- SPX V20 implementation is unchanged.
- Existing DTE, contract price, reporting, success rules, alert and manual-close behavior is retained.
