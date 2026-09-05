# v8 — Waseem V1 + Dedicated Profit Watcher

## New engine only
- Added `Waseem V1` to Equity Options and SPX Index Options menus.
- Existing Core, Confirmed Setup, SPX Core and SPX V20 strategy logic remains available and is not replaced.
- Added independent monitor keys `option:waseem` and `index:waseem`.
- Added Daily 0DTE, Weekly 1–7 DTE, Monthly 8–35 DTE and Waseem-only Daily+Weekly dual scan.

## Waseem contract logic
- Directional CALL/PUT mirror based on underlying LONG/SHORT analysis.
- Near-OTM adaptive strike search.
- Equity strike window: tighter for 0DTE, wider for Weekly/Monthly.
- SPX Waseem uses actual SPX spot and limits near-OTM search to 40 points.
- Strike ranking includes expected-move fit, premium affordability, bid/ask spread, bid/ask size when available, volume and Greeks where reliable.
- Missing Greeks do not hard-reject 0DTE; Weekly/Monthly still require Delta.
- Added reject diagnostics and top alternate contracts for internal review.
- Candidate/signal messages identify `Waseem V1` as engine source.

## Profit alerts
- Added `OpenOptionProfitWatcher` running on an anchored 60-second cadence.
- It monitors confirmed OPEN equity/index option trades only.
- Alert comparison is based on the last price that actually generated an alert.
- Telegram Profit Alert Step remains the source of the configured increment.
- No duplicate alert for a previously alerted high after a dip/recovery.
- If a poll observes a jump larger than the step, it sends the real current observed price; it does not invent synthetic intermediate prices.
- Sends the approved profit image + Arabic profit caption.
- Legacy TradeMonitor profit-alert calls are disabled only at application wiring level; its entry/SL/TP/time-exit behavior remains active.
- Quote failures/staleness are logged by the dedicated watcher instead of silently disappearing.

## Tests
- Added Waseem selector tests for CALL/PUT symmetry, 0DTE missing Greeks, far-strike rejection and SPX 40-point cap.
- Added Telegram menu source checks.
- Added dedicated profit watcher increment/no-duplicate test.
