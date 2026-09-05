# V13 — SPX/SPXW GTH Data Diagnostics

## Scope
- No legacy engine trading logic changed.
- Waseem V3 entry logic remains the V12 logic for both equity options and SPX/SPXW options.
- V13 adds auditable SPX/SPXW GTH market-data diagnostics and exposes them in signal/watch messages, Telegram Status, `/health`, and `/state`.

## Added
- Real SPX/SPXW option-data probe through the configured Alpaca options feed.
- Explicit `AVAILABLE / DELAYED / STALE / UNAVAILABLE` classification based on the newest actual option quote timestamp.
- Snapshot, quote, and trade counts.
- Latest option quote contract, timestamp, age, bid and ask.
- Latest option trade contract, timestamp, age and price.
- Chain source (`underlying_chain`, `contracts_snapshots`, or unavailable).
- Latest cash SPX reference point, timestamp, age, and whether it belongs to the current or a previous RTH session.
- GTH/RTH trade date and exact ET/KSA session start/end timestamps.
- Diagnostic check timestamp and explicit errors.
- 60-second diagnostics cache to avoid unnecessary repeat API probes.

## Waseem V3 GTH message behavior
Every GTH Waseem V3 WATCH/READY candidate now carries the above data diagnostics. Missing values are shown as unavailable rather than fabricated.
