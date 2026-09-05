# ALLUQMANU_USA_TD v18 — Final Review

## Scope implemented

1. Telegram Dynamic Equity Watchlist.
2. Independent Waseem V5 — V4 + observable Order Flow/Execution layer.

## Senior software review

- Existing V4 liquidity source file: unchanged byte-for-byte versus uploaded v17.
- Existing V3 entry engine: unchanged byte-for-byte.
- Existing V2 contract selector: unchanged byte-for-byte.
- Core Strategy, SPX V20 and Confirmed Setup engine files: unchanged byte-for-byte.
- Paper safety remains `paper_mode=True` / `live_trading=False`.
- Dynamic watchlist is a runtime-configuration feature and not strategy logic.
- PostgreSQL is required for writable persistent watchlist state; no local Render JSON is used for mutations.
- Full Python compile/AST audit: PASS.

## Trading/engine review

- V5 consumes V4 candidates; it does not mutate V4.
- V5 uses best bid/ask, sizes when supplied, latest trade, and cross-scan quote movement.
- Unsupported depth/DOM concepts are explicit `UNAVAILABLE`.
- READY is multi-gate: V4/V3 READY + premium in Preferred Entry + fresh quote + acceptable spread + usable flow + V5 score floor.
- Default V5 READY floor: 88; default minimum flow score: 55.
- Stop preserves V3 structural option invalidation.
- Existing targets remain intact; V5 may extend them only when V4 external-liquidity target + usable Delta + V5 flow support it.
- WATCH is automatically retained/rechecked by the V5 monitor and can transition to READY without blocking discovery of other candidates.

## Tests

- Focused V2/V4/options/freshness + new V5/watchlist tests: 18 passed.
- Broad suite excluding Telegram-dependent modules unavailable in this offline review environment: 120 passed.
- `python -m compileall -q app main.py tests`: PASS.
- Telegram-dependent full-suite collection could not be executed locally because `python-telegram-bot` is not installed in this sandbox and internet package installation is unavailable. The dependency remains correctly declared in requirements.txt.

## Deployment note

Set `DATABASE_URL` to a persistent PostgreSQL database in Render before using Telegram Add/Remove/Enable/Disable. Without it, the bot safely shows the configured static watchlist and refuses mutations rather than saving ephemeral state.
