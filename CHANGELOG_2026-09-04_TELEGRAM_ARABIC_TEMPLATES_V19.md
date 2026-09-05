# ALLUQMANU_USA_TD — V19 Telegram Arabic Message Templates

Date: 2026-09-04

## Scope
Presentation-only Telegram message redesign. Trading engines, strategy logic, V5 order-flow logic, option selection, Paper Mode, and existing image generation were not changed.

## Implemented
- Arabic RTL-oriented Telegram templates using RLM and Telegram HTML bold formatting.
- Compact regular Options opportunity template.
- Full READY / WATCH / WAIT / ENTRY READY detail template with engine above symbol and signal strength directly below engine.
- V5 display section for OHLCV/liquidity and observable Order Flow fields. Unsupported data remains `غير متاح` / `UNAVAILABLE`; no Level-2/DOM values are invented.
- Compact statistical-success message with entry/current premium on one line.
- Compact entry-confirmation message using the approved wide spacing.
- Compact profit-update message with entry/current premium on one line and Riyadh/New York clocks.
- Private Telegram message-test buttons for Opportunity, READY, WATCH, Success, Entry, Profit Update, plus SPX opportunity test.
- Existing signal/profit images remain attached where they were already used. No image artwork or renderer was changed.

## Truthfulness guard
The profit-protection line reports protection as active only when the stored stop has actually moved to at least the entry price. It does not claim a trailing sell was activated when the trading logic has not activated it.

## Verification
- `python -m compileall` passes for project Python sources.
- 136 tests passed when excluding one legacy Telegram-import test that cannot be collected in this review environment because `python-telegram-bot` is not installed here.
- 12 focused message/V5/watchlist tests passed.
- Image hash comparison: unchanged.
- `app/strategies`, `app/market`, `app/options`, and `app/trading` match V18 byte-for-byte.
