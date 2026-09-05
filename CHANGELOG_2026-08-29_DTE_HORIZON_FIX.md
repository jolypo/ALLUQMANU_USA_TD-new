# DTE Horizon Fix — 2026-08-29

- Equity Options now passes the selected min/max DTE window into ContractSelector.
- Daily search is strictly 0DTE.
- Weekly search is strictly 1-7 DTE.
- Monthly search is strictly 8-35 DTE.
- SPX Core no longer appends legacy SWING when a Telegram horizon is explicitly selected.
- SPX V20 no longer appends legacy SWING when a Telegram horizon is explicitly selected.
- Added regression tests for Equity 0DTE, SPX Core Weekly, and SPX V20 Daily.
- Full suite: 58 passed. compileall: PASS.
