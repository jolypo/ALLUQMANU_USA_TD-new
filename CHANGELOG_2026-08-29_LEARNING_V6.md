# ALLUQMANU_USA_TD v6 — Learning Layer

## Scope
Only a learning/calibration layer was added. Legacy Core and SPX V20 strategy logic was not changed.

## Added
- `app/learning.py`: portable JSON learning memory for completed `CONFIRMED_SETUP` trades.
- Conservative Bayesian calibration by direction, asset class, DTE horizon, market state and liquidity state.
- `JudgeEngine` now records `judge_raw_score`, learning adjustment, learning status and sample count.
- Raw Judge floor 90 is enforced before any learning adjustment.
- Optional GitHub persistence on a dedicated `learning-data` branch.
- `/learning` status endpoint.
- `.env.example` learning controls.
- Learning regression tests.

## Defaults
- Activation: 12 completed Confirmed Setup samples.
- Bucket minimum: 5 samples.
- Maximum bonus: +2 Judge points.
- Maximum penalty: -4 Judge points.
- GitHub sync interval: 300 seconds when configured.

## Important
The learning layer is calibration, not an autonomous ML model. It does not rewrite code or guarantee profitability. Small samples remain in collection mode.
