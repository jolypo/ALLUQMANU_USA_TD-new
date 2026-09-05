# Waseem V4 — v16

- Added Waseem V4 as a fully separate engine; no previous strategy logic changed.
- Equity options: Daily 0DTE + Weekly 1–7 DTE.
- SPX/SPXW: Daily 0DTE + Weekly 1–7 DTE; inherits V3 GTH/RTH session support.
- V4 combines V2 setup/contract selection, V3 entry efficiency/anti-chase, and an independent OHLCV liquidity/pre-move overlay.
- Added internal/external liquidity references, liquidity density, volume acceleration, momentum acceleration, range compression and pre-move score.
- Explicit flow confidence prevents pretending Level2/DOM/institutional flow is available.
- Telegram opportunity messages identify Engine Source: Waseem V4 and show V4 diagnostics.
- Telegram menus/monitors include Equity Waseem V4 and SPX Waseem V4.
- /health and /state include V4 availability/monitor state.
