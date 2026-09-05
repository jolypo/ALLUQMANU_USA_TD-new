# v18 — Waseem V5 + Telegram Dynamic Equity Watchlist

Date: 2026-09-04

## Added

- Independent Waseem V5 for Equity Options and SPX/SPXW.
- V5 = existing V4 candidate + observable order-flow/execution overlay.
- Top-of-book bid/ask pressure from sizes when the provider supplies them.
- Latest-trade position versus bid/ask as a trade-aggression signal.
- Cross-scan mid-price movement as an execution-pressure signal.
- Explicit `UNAVAILABLE` for multi-level Book Imbalance, Absorption and Replenishment when current data cannot support them.
- V5 Flow Confidence and quote/trade status.
- V5 READY/WATCH multi-gate decision with configurable READY floor (default 88) and minimum flow score (default 55).
- V5 continuous Auto-Watch with WATCH -> READY transition timestamps, isolated from V4 state.
- V5 Telegram menus for Equity and SPX, Daily 0DTE + Weekly 1–7 DTE dual mode.
- V5 health/state monitor visibility.
- Persistent master Equity Watchlist menu: Add, Remove, Disable, Enable, Refresh.
- Symbol validation for market data + options before Add.
- PostgreSQL-backed runtime watchlist through `DATABASE_URL`; no GitHub/code mutation and no ephemeral JSON writes.

## Preserved

- Core, Confirmed Setup, Waseem V1, V2, V3, V4 and SPX V20 strategy logic remains unchanged.
- Paper Trading remains enforced (`paper_mode=True`, `live_trading=False`).
- Missing/unsupported data remains explicit rather than fabricated.

## Entry / Stop / Targets

- Entry uses the existing V3 Preferred Entry range and V5 execution/order-flow gate.
- Stop keeps the existing V3 structural option invalidation rather than applying a new arbitrary fixed percentage.
- Targets preserve existing targets and may be extended when V4 external-liquidity structure plus usable Delta and V5 flow support a larger move.

## Storage requirement

Dynamic watchlist writes require a persistent PostgreSQL `DATABASE_URL`. Without it, the system remains operational using the configured static list but Telegram mutations are intentionally disabled.
