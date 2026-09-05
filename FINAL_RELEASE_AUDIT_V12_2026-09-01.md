# FINAL RELEASE AUDIT — v12

## Trading-logic scope

- PASS — Core logic unchanged.
- PASS — Confirmed Setup logic unchanged.
- PASS — SPX V20 logic unchanged.
- PASS — Waseem V1 logic unchanged.
- PASS — Waseem V2 logic unchanged.
- PASS — New entry/GTH logic isolated to Waseem V3.

## Waseem V3 — Equity

- PASS — Dedicated Equity V3 menu/monitor path.
- PASS — Entry Quality applied after V2 setup/contract selection.
- PASS — Anti-Chase converts extended premium into WATCH.
- PASS — Preferred entry is anchored to actual bid/ask and observed option-session range when available.
- PASS — Current premium and preferred premium are both retained in candidate diagnostics.
- PASS — WATCH cannot be manually published as a Trade.
- PASS — Continuous scanner remains active while WATCH exists.

## Waseem V3 — SPX/SPXW

- PASS — Dedicated SPX V3 GTH/RTH path.
- PASS — GTH does not require US cash market to be open.
- PASS — GTH cash SPX is labeled PREVIOUS_CLOSE.
- PASS — Futures-led direction uses ES/NQ/YM/RTY availability.
- PASS — VIX/DXY/yields/economic context is soft and missing-data aware.
- PASS — Indicative SPX reference is labeled as indicative.
- PASS — Actual executable option quote remains mandatory.
- PASS — GTH-to-RTH session distinction is explicit.
- PASS — Friday-evening/weekend guard included.

## Timestamps/messages

- PASS — System detection timestamp.
- PASS — Market/option data timestamp.
- PASS — Detection lag.
- PASS — First detected timestamp for V3 monitor candidates.
- PASS — WATCH-added timestamp.
- PASS — Entry-ready timestamp.
- PASS — Publication timestamp when a trade is published.
- PASS — V2/V3 context lines expose AVAILABLE/DELAYED/STALE/UNAVAILABLE information supplied by providers.

## Health/state

- PASS — Telegram Status includes V3 monitors.
- PASS — Telegram Status includes V3 SPX session state.
- PASS — `/health` includes V3 state and live data diagnostics.
- PASS — `/state` exposes monitor states, V3 SPX session, and live data diagnostics.

## Static/automated verification

- PASS — Python compile checks.
- PASS — Existing test suite preserved.
- PASS — New V3 entry/session tests added.
- PASS — 121 tests passed.

## Runtime limitations

- NOT TESTABLE OFFLINE — live Alpaca SPX/SPXW GTH quote availability depends on the configured account/feed.
- NOT TESTABLE OFFLINE — actual GTH premium movement, live spreads and fills require a live session.
- LIMITATION — public futures/context sources are best-effort and can be DELAYED/STALE/UNAVAILABLE.
- LIMITATION — Indicative SPX reference during GTH is an estimate; it is not the live cash SPX index.
