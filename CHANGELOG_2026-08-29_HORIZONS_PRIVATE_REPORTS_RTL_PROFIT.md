# 2026-08-29 — Option Horizons, Private Reports, RTL Profit Updates

- Equity Options and SPX option searches now require an expiration horizon in the Telegram UI:
  - DAILY: 0–1 DTE
  - WEEKLY: 2–7 DTE
  - MONTHLY: 8–35 DTE
- The selected horizon is passed into the real option-chain scan and shown in READY candidate data.
- SPX Core and SPX V20 both support the same horizon selector.
- Near Stop Loss remains an internal state only and is not posted to the public Telegram channel.
- Daily/weekly reports are manual/private-only; scheduled public report publication is disabled.
- Profit update caption is compact and Arabic/RTL-friendly, including Entry, Current, P&L %, USD profit, SAR profit, Saudi observation time, New York observation time, and Trade ID.
- Existing profit-step logic remains anchored to entry (default $0.10) for Equity Options and SPX options.
