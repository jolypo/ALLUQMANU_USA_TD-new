# v11 — Live Data Diagnostics in Telegram Status

- Added live data-source diagnostics to Telegram Status.
- AVAILABLE is shown only after a real fetch succeeds.
- FRED Calendar and Treasury yields are verified through actual provider calls.
- Alpha Vantage earnings is verified with one cached symbol lookup to protect free quotas.
- ES/NQ/YM/RTY/VIX/DXY show AVAILABLE, DELAYED, STALE, or UNAVAILABLE with data age when known.
- Alpaca market clock and one latest stock bar are probed live.
- NYSE TICK, Institutional GEX, Institutional Options Flow, and Full Level 2/DOM remain explicitly UNAVAILABLE.
- Status now lists all monitor modes, including Confirmed Setup, Waseem V1, and Waseem V2 for equity options and SPX.
- No trading strategy, scoring thresholds, contract selection logic, risk logic, or monitoring cadence was changed.
