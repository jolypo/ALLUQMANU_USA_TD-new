# Waseem V4 Auto-Watch Transition — V17

- Waseem V4 KEEP WATCH is now persistent for the active monitor session.
- V4 performs a wider internal scan pool while still showing at most the configured number of new discoveries per scan.
- Tracked V4 contracts are automatically re-evaluated on each monitor cycle.
- Repeated KEEP WATCH messages for the same contract are suppressed.
- When the same tracked contract changes from WATCH to READY, Telegram sends a new `WASEEM V4 WATCH → ENTRY READY` candidate with the original detection/watch timestamps plus a new Entry Ready timestamp.
- The WATCH→READY notification bypasses ordinary duplicate cooldown.
- Manual Publish remains unchanged: READY creates a fresh approval candidate; it does not auto-publish or open a paper trade without approval.
- V1/V2/V3/Core/Confirmed/SPX V20 logic is unchanged.
