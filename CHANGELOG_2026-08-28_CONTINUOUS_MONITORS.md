# Continuous Opportunity Monitors — 2026-08-28

## Added
- Independent continuous monitors for Stocks, Equity Options, SPX V20 and SPX Core.
- Start Monitoring / Stop Monitoring / Scan Now controls.
- Every monitor starts at 0/3 and auto-stops after 3 detected READY opportunities.
- Restarting a stopped monitor starts a new 0/3 session.
- Exact-candidate duplicate protection plus a short per-symbol cooldown.
- Candidate timestamps in New York time and hard 3-minute approval TTL.
- Expired candidates cannot publish and return `Candidate expired / Please rescan`.
- Rich candidate details: strategy, signal strength, score, R/R, expiration, DTE,
  current premium, bid/ask/mid, underlying price, entry, stop and TP1/2/3.
- Telegram-editable maximum contract price for Equity Options and SPX Options.
- Restore Defaults button for contract price filters.

## Trading behavior
- No monitor publishes automatically. Approval remains mandatory.
- Market-open and global Pause gates remain active.
- All scan paths share one global async lock to avoid overlapping heavy scans.
- Contract max-price filter is enforced by ContractSelector using the Ask price,
  matching the conservative long-premium purchase cost.
- Profit alerts are suppressed while option price is at or below confirmed entry.
- Once price is above entry, normal profit updates continue.
- Reaching the configured cash success threshold records statistical success once,
  does not close the trade, and later profit updates continue.
- Stocks keep target-based success logic.

## Verification
- `python -m compileall -q .` PASS
- `pytest -q` => 38 passed
- Simulated Telegram continuous-monitor lifecycle PASS
- Simulated expired-candidate callback PASS
