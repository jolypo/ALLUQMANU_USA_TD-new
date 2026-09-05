# V23 — Waseem V6 Lifecycle Upgrade

## Scope
V6 only. V2/V3/V4/V5 strategy files were not modified.

## Changes
- Split V6 into underlying-first planning and contract-confirmation stages.
- Pre-Market / extended-session plan does not select a final option contract.
- RTH path analyzes the underlying first, then opens the option chain.
- Equity V6 no longer depends on the V5 candidate list to discover symbols.
- SPX V6 uses SPX structure first and selects SPX/SPXW contracts only during RTH.
- Session phases: PREMARKET, OPENING, RTH_MORNING, MIDDAY, RTH_AFTERNOON, POWER_HOUR, AFTER_HOURS, CLOSED.
- RTH contract confirmation checks spread, Volume/OI, Delta/Gamma/Theta/Vega, IV, contract quality, entry quality and anti-chase.
- Structural target is mapped to an approximate option-price response using Delta when usable.
- V6 Telegram message adds V6-only execution details and projected underlying/contract move.
- Delayed/indicative feeds continue to reduce order-flow weight.

## Safety
- Pre-market plan is WATCH/context only; no final contract is locked before RTH.
- V6 remains Paper Trading only.
- Missing metrics remain unavailable rather than fabricated.
