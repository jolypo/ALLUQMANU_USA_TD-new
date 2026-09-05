# Waseem V6 V22 — 2026-09-05

## Scope
Independent Waseem V6 engine. V2/V3/V4/V5 strategy files were not modified.

## V6
- Equity Options + SPX/SPXW.
- Daily 0DTE + Weekly 1–7 DTE dual scan.
- Delayed-feed awareness: indicative/non-OPRA or non-SIP feeds are treated as limited even when timestamps look fresh.
- Session awareness: PREMARKET / RTH / AFTER_HOURS / CLOSED.
- Multi-timeframe structure: 15m / 1h / 4h / Daily / Weekly / Monthly.
- Momentum Decay / anti-end-of-move filter.
- Late Entry penalty and anti-chase confirmation.
- Room-to-Target score from support/resistance structure.
- Breakout Quality and Reversal Risk.
- ICT-derived structure: liquidity pools, FVG, order blocks when observable from bars.
- Fibonacci swing alignment.
- Positive/Negative Cross layer: EMA9/EMA20 with MACD histogram confirmation.
- Order Flow from V5 remains secondary and is automatically down-weighted on delayed/limited feeds.
- READY / WATCH / NO TRADE behavior.
- SPX structural price levels use SPX itself (^GSPC public reference), not SPY prices.

## Telegram
- Added Waseem V6 to Equity and SPX strategy menus.
- V6 defaults to Daily 0DTE + Weekly 1–7 DTE combined search.
- Added continuous monitor / automatic WATCH re-evaluation for V6.
- Added V6-only detailed candidate message section.
- Added private `Waseem V6` message preview test.

## Watchlist
Added MRVL, MSTR, and GOOGL to the default Master Equity Watchlist. They are appended even when Render still has an older STOCK_SYMBOLS environment value.

## Safety
- Paper/live guards unchanged.
- Existing approved images unchanged.
- Missing/non-observable market data is not fabricated.
