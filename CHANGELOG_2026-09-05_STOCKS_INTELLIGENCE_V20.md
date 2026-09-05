# V20 — Stocks Intelligence / News / Ephemeral Watchlist

## Scope
This release changes the Telegram stock-management and stock-intelligence layer only. Existing trading engines V1/V2/V3/V4/V5, option selectors, profit watcher, reports, and image assets are unchanged.

## Telegram Stocks menu
- Added persistent top-level `📊 الأسهم` beside Trading.
- Added three stock workflows:
  - `📊 تحليل السهم`
  - `⚙️ إدارة الأسهم`
  - `📰 أخبار الأسهم`
- Moved Equity Watchlist management out of the Trading submenu.

## Temporary watchlist without DATABASE_URL
- When PostgreSQL is configured, behavior remains persistent.
- Without `DATABASE_URL`, the watchlist is now writable in server memory.
- Add/Remove/Enable/Disable works immediately and affects upcoming equity scans.
- Memory changes may be lost on restart, crash, or redeploy.

## Stock analysis
New read-only `StockIntelligenceEngine`:
- Multi-timeframe support/resistance: 15m, 1h, 4h, daily, weekly, monthly.
- Explicit breakout confirmation timeframe for every displayed resistance.
- Nearest support and nearest resistance.
- The nearest resistance is treated as the trigger; the next resistance is the probable target.
- Target horizon classification: intraday / daily / weekly / monthly.
- ATR-based target plausibility.
- ICT-derived price-structure context: buy-side/sell-side liquidity pools, FVG, order-block approximation.
- Fibonacci retracement/extensions from observed swing range.
- VWAP, RVOL, ATR, short momentum.
- Fake-breakout/liquidity-sweep guidance.
- Arabic RTL Telegram rendering.
- Analysis and message timestamps in New York and Riyadh.

Important: ICT liquidity is derived from price structure, not Level 2/DOM. Missing inputs remain unavailable.

## Stock news
New read-only `StockNewsEngine`:
- Per-stock latest news from configured market-data source.
- News type classification: earnings/guidance, contract/agreement, dividends, buyback, analyst action, regulatory, operating/market.
- Positive/negative/neutral impact score.
- Separate risk score and possible priced-in flag.
- ATR-aware potential price reaction range; not a guaranteed forecast.
- SPY 5-session context in risk assessment when available.
- Type-specific numeric fields are displayed only when explicitly present in source text.
- Arabic RTL rendering and New York/Riyadh timestamps.

## Private message tests
Added:
- `📊 اختبار تحليل السهم`
- `📰 اختبار أخبار السهم`

## Verification
- 139 non-Telegram-dependent tests passed.
- New V20 feature tests passed.
- `compileall` passed for application source.
- Direct Telegram-dependent test collection could not run in the review environment because `python-telegram-bot` is not installed there; it remains declared in requirements.txt.
