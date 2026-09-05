# ALLUQMANU_USA_TD — Final Release Audit

Date: 2026-08-29
Release status: APPROVED FOR PAPER-TRADING / SIGNAL-ENGINE DEPLOYMENT

## Scope verified

- Stocks Core analysis
- Equity Options Core analysis and CALL/PUT routing
- SPX Core analysis and CALL/PUT routing
- SPX V20 independent strategy path
- Freshness guards for stocks, underlying data, SPX references, and option quotes
- Daily 0DTE / Weekly 1–7 DTE / Monthly 8–35 DTE routing
- Contract price caps independently configurable by asset category and horizon
- Manual approval workflow and 3-minute candidate TTL
- Continuous monitors and duplicate protection
- Entry confirmation before trade monitoring actions
- Profit alert step logic and positive-profit-only alerts
- Success threshold logic independent from final realized P&L
- Manual close cleanup and history preservation
- Private/on-demand reports and DTE cohort reporting
- Pending logic for still-open Weekly/Monthly options
- Telegram admin/private controls and public channel publishing boundaries
- Render configuration and health endpoint
- Secret hygiene (.env excluded; .env.example only)

## Directional-engine audit

Core now separates:

- Bull Score
- Bear Score
- Quality Score
- Directional Gap

Volume and volatility are quality inputs rather than automatic bullish votes. A trend-activation guard prevents weak/ranging EMA stacks from creating false directional trades. Mirrored bullish/bearish fixtures verify that Core can produce LONG/CALL and SHORT/PUT symmetrically, while ranging fixtures remain neutral.

## Expiration horizon rules

- Daily: 0 DTE only
- Weekly: 1–7 DTE
- Monthly: 8–35 DTE

The selected horizon is passed through Telegram -> SignalService -> provider option-chain request -> ContractSelector validation. SPX Core/V20 do not append an unrelated Swing search when a manual horizon is selected.

## Reporting rules

- Daily comprehensive cohorts use actual `entered_at` date, not publication date or current open status.
- Weekly comprehensive cohorts use actual entry week.
- A Weekly/Monthly trade that achieves its configured success threshold is WIN immediately in the corresponding entry-period report.
- Still-open Weekly/Monthly trades that have not achieved success remain PENDING until a real close/expiry/evaluation completion.
- 0DTE is finalized at its session/expiry boundary if not already resolved.
- Automatic channel report publication is disabled; reports remain private/on-demand.

## Verification

- pytest: 77 passed
- python -m compileall: PASS
- No hard-coded Telegram token/API key detected in application source
- Only `.env.example` is packaged; `.env` is gitignored

## Release limitation

This release is a paper-trading/signal engine. Data quality is constrained by the configured market-data feeds (including indicative options data where applicable). Passing local tests does not replace live-provider/deployment observation after Render rollout.
