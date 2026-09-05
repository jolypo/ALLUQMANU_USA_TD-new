# FINAL RELEASE AUDIT — v9

## Scope
Senior-code/trading review focused on adding Waseem V2 without changing legacy engine decision logic.

## PASS
- Waseem V2 Equity exists as a separate Telegram strategy and service path.
- Waseem V2 SPX/SPXW exists as a separate Telegram strategy and service path.
- Core / Confirmed Setup / SPX V20 / Waseem V1 remain available.
- CALL/PUT choice remains driven by underlying direction before contract selection.
- Waseem V2 uses Near-OTM + Expected Move + Strike Efficiency.
- SPX and equity V2 use different context weighting.
- Free context failures are explicit and do not create synthetic values.
- Daily 0DTE / Weekly 1–7 / Monthly 8–35 remain independent.
- Daily+Weekly dual scan is supported in V2.
- V2 continuous monitor runs until Stop/market close; legacy 3-opportunity limit is preserved.
- Candidate TTL remains separate from scanner lifetime.
- Published/candidate messages identify engine, horizon and detection date/time.
- TIME_EXIT and STOP_LOSS notifications route to admin DM only.
- Dedicated 60-second option Profit Watcher from v8 is retained unchanged.
- Manual publication approval remains required.
- Paper-only safety settings are retained.

## LIMITATIONS / EXPLICIT UNAVAILABLE DATA
Free/best-effort sources do not guarantee institutional real-time coverage. The engine explicitly reports unavailable data rather than fabricating it. In v9, these remain unavailable unless a future reliable source is integrated:
- Economic Calendar as a dedicated structured feed
- NYSE TICK / TRIN institutional feed
- Full DOM / Level 2 order book
- Institutional options flow / sweep classification
- Institutional GEX / gamma-wall feed
- Full OPRA real-time feed

Yahoo/yfinance public data is best-effort and can be delayed, rate-limited or temporarily absent. Alpaca option data remains configured as `indicative` unless the deployment changes its entitlement/feed.

## TESTS
- Python compileall: PASS
- Pytest regression + v9 tests: PASS
- Existing v8 tests retained.
- Added v9 tests for Strike Efficiency, separate Telegram menus, continuous V2 monitoring, private automatic exits, and signal identification.
