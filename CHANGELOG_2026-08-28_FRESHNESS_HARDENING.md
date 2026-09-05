# Freshness Hardening + Continuous Monitors — 2026-08-28

## Critical data-integrity fix
- Intraday stock analysis now rejects bars older than the configured freshness window.
- Intraday market data must belong to the current New York trading date; prior-session bars cannot create READY candidates.
- Every stock candidate requires a fresh Alpaca latest bar before it can become READY.
- Historical strategy entry zones that have drifted materially away from the fresh current price are rejected instead of being published as stale entries.
- Equity option and SPX/SPXW contracts now require a timestamped fresh `latestQuote`; missing or prior-session option quotes are rejected.
- SPX V20 now requires fresh same-session SPX 15m reference bars and a fresh SPY volume proxy.
- SPX Core requires a fresh same-session SPX reference price before generating an index-option candidate.
- TradeMonitor ignores stale stock/option prices so old snapshots cannot trigger entry, TP/SL, or profit alerts.
- While the market is open, monitor prices must be within the freshness window. After the close, only a quote/bar from the same New York session may be used for closing an intraday trade.
- Candidate messages expose market/quote age and timestamps for auditability.

## User-requested opportunity monitoring
- Independent monitors: Stocks, Equity Options, SPX V20, SPX Core.
- Start / Stop / Scan Now for each monitor.
- Each monitor continues after a detected opportunity and auto-stops after 3 READY opportunities.
- Restarting starts a new 0/3 session.
- Candidate approval TTL is 3 minutes.
- Expired candidates cannot publish.
- Duplicate and cooldown protection remain enabled.
- No automatic publishing; Approve is still mandatory.

## Contract search
- Telegram-editable maximum contract price for Equity Options and SPX Options.
- `0` restores Default / Unlimited behavior.
- Restore Defaults is available.
- Max premium is enforced against the conservative Ask price.

## Profit alerts and success logic
- No profit/update alert is sent while an option remains at or below its confirmed entry price.
- Once above entry, normal upward profit alerts continue.
- Reaching the configured cash-success threshold records statistical success once and does not close the trade.
- Profit updates continue after the success threshold is reached.
- Stocks remain target-based for success.

## Verification
- Freshness guard tests added for prior-session bars, missing timestamps, stale option quotes, and stale current stock snapshots.
