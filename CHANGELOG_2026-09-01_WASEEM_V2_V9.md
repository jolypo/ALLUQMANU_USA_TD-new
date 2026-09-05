# v9 — Waseem V2

## New isolated engines
- `Waseem V2` for Equity Options.
- `Waseem V2` for SPX/SPXW options.
- Existing engines are retained: Core, Confirmed Setup, SPX Core, SPX V20, Waseem V1.

## Waseem V2 market context
### SPX/SPXW
Best-effort free context from yfinance plus existing Alpaca data:
- ES, NQ, YM, RTY futures
- VIX
- DXY
- US10Y
- SPY / QQQ context
- existing Alpaca news headline when available

### Equity Options
- SPY / QQQ
- sector ETF mapping
- Relative Strength already calculated by the base analyzer
- VIX
- existing Alpaca company news when available

Missing public data is explicitly represented as `UNAVAILABLE`; delayed/stale bars are labelled from their timestamp age. No value is fabricated.

## Decision logic
- Keeps direction analysis separate from contract selection.
- Adds soft market-context scoring rather than stacking new hard vetoes.
- Adds reversal/regime-transition evidence from liquidity sweep, momentum, VWAP and structure.
- `WATCH` is retained internally below READY and is rechecked by continuous monitoring.
- READY floor remains 90 (91 in selected caution contexts); contract/underlying freshness and unusable execution remain hard protections.

## Contract selection
- New `WaseemV2ContractSelector` builds on Waseem V1 Near-OTM selection without changing V1.
- Adds `Strike Efficiency` using expected-move fit, delta/gamma response when available, theta, spread/execution and V1 contract quality.
- Daily 0DTE does not invent missing Greeks.

## Monitoring
- Waseem V2 monitor is continuous until manual Stop or US market close.
- It does not auto-stop after 3 detected opportunities.
- Legacy monitors retain their existing 3-opportunity behavior.
- Candidate TTL remains 3 minutes; TTL applies to an individual candidate, not to the V2 scanner session.

## Telegram visibility
Every Waseem V2 candidate/signal identifies:
- engine source (`Waseem V2`)
- detection date/time ET
- expiration, DTE and horizon (Daily/Weekly/Monthly)
- Strike Efficiency
- free market-context status

## Channel privacy fix
- Automatic `TIME_EXIT` messages -> admin private DM only.
- Automatic `STOP_LOSS` messages -> admin private DM only.
- No channel reply linkage is used for those private messages.
- Manual close remains private-only.
