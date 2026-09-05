# Final Review — V24

## Objective
Prevent repeated opportunity messages from the same underlying from monopolizing Telegram while preserving continuous engine analysis and WATCH transitions.

## Architecture
A new independent module `app/telegram/signal_delivery_policy.py` sits downstream of all trading engines. It does not alter scores, directions, contracts, READY/WATCH decisions, or strategy calculations.

## Delivery policy
- Internal scan pool: >=20 candidates for continuous Waseem monitors.
- Telegram discovery: top 3 unique underlying symbols by score.
- Cross-engine symbol cooldown: 20 minutes.
- Material-event overrides: WATCH_TO_READY, confirmed direction reversal, or >=3-point READY score upgrade.
- Continuous internal scanning remains active during cooldown.

## Verification
- `pytest -q --ignore=tests/test_waseem_v17_v4_watch_transition.py`: 151 passed.
- The ignored file imports `python-telegram-bot`, which is unavailable in the review environment; this is the same environment limitation as previous releases.
- `py_compile` passed for changed Python modules.
- `app/market`, `app/options`, `app/strategies`, and `app/trading` are unchanged from V23 (ignoring generated caches).

## New tests
`tests/test_v24_signal_delivery_policy.py` verifies:
- unique-symbol ranking surfaces the fourth unique symbol when top rows duplicate NVDA;
- cross-engine cooldown;
- 20-minute expiry;
- WATCH->READY override;
- confirmed direction-reversal override;
- material score-upgrade override.
