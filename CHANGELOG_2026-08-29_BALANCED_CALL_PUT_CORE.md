# Balanced CALL/PUT Core Engine — 2026-08-29

## Why
The legacy Core score mixed directional factors with non-directional quality factors. Strong volume/acceptable volatility could lift the single unified score and make bearish setups harder to classify as SHORT/PUT.

## Changes
- Added independent `bull_score` and `bear_score` to Core Strategy diagnostics.
- Directional score now uses Trend, Structure, Momentum, VWAP, and ICT only.
- Volume and Volatility are a separate `quality_score`; quality strengthens or weakens CALL and PUT symmetrically and never votes for direction.
- Added bearish MACD acceleration and bearish liquidity-sweep reasons.
- Added a Trend Activation Guard to prevent tiny EMA stacking in low-ADX range tape from creating false CALL/PUT signals.
- Market regime classification now uses Core direction + confidence instead of treating a low signed score as BEAR.
- Kept option selection symmetric: LONG underlying -> CALL, SHORT underlying -> PUT.
- SPX V20 was not rewritten; it already has independent CALL/PUT scoring and its existing veto/confirmation rules remain intact.

## Tests
- Mirrored bullish/bearish trend symmetry.
- Neutral/range tape remains NEUTRAL.
- PUT selection for SHORT direction.
- SPX Core SHORT requests a PUT chain.
