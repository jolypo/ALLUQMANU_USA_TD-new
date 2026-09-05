# CHANGELOG — 2026-08-28

## 1. Statistical success rules

- Added a dedicated private Telegram **Success Rules** menu.
- Independent thresholds:
  - Stocks: percent P&L, default OFF (`0`).
  - Equity Options: cash P&L USD, default `+$50`.
  - Index Options: cash P&L USD, default `+$50`.
- Selecting a category asks the admin to send the new numeric value as a private message.
- Sending `0` disables the success rule for that category.
- A success threshold is stored once and never duplicated.
- `success_reached` is separate from final `WIN/LOSS/BREAKEVEN`.
- Existing `success_100_reached` trades remain recognized for backward compatibility.
- Performance/daily/weekly reporting now separates statistical success from final realized results.

## 2. SPX 0DTE + Swing

- SPX Index Options now evaluate two paths:
  - 0DTE (same-day expiry only)
  - Swing
- 0DTE contract selection now permits `DTE == 0` explicitly.
- New SPX 0DTE gates:
  - max spread 8%
  - delta 0.40–0.65
  - minimum contract score 72
  - risk cap 0.30%
- Expiration/DTE calculations use `America/New_York` trading date.
- SPXW roots remain accepted for SPX weekly contracts.
- Candidate buttons show `0DTE` or `SWING` when available.

## 3. Option publication card + one Telegram post

- Replaced the old option card with the requested horizontal dark design.
- CALL = green theme; PUT = red theme.
- Dynamic fields: symbol/company, date, strike, entry price, CALL/PUT.
- Watermark: `ALLUQMANI_USA_TD` by default.
- Equity Options and Index Options now publish image + signal details in one Telegram media message.
- The original detailed signal text is used unchanged when <= Telegram's media-caption limit; oversized signals are compacted safely to remain within 1024 characters.

## Validation

- `python -m compileall -q .` — PASS
- `pytest -q` — **17 passed**
