# v12 — Waseem V3 Entry Quality + SPX GTH

## Scope lock

- No strategy-logic changes to Core, Confirmed Setup, SPX V20, Waseem V1, or Waseem V2.
- Old engines receive message/timestamp diagnostics only.
- New execution logic exists only in Waseem V3.

## Waseem V3 Equity Options

- Added independent Telegram mode and monitor.
- Reuses Waseem V2 setup + contract ranking.
- Added Entry Quality / Anti-Chase engine.
- Valid setup + poor current premium => WATCH.
- WATCH includes contract side, strike, expiration/DTE, current premium, preferred entry, reason, entry score, session-range diagnostics, quote time and lag.
- WATCH is not publishable and is not a Trade.
- Monitor remains continuous; READY and WATCH have distinct cooldown identities.
- First detection is preserved in monitor-session memory and READY gets an Entry Ready timestamp.

## Waseem V3 SPX/SPXW

- Added independent GTH/RTH monitor.
- GTH gate does not depend on Alpaca cash-market `is_open`.
- GTH: 20:15–09:25 ET; RTH: 09:30–16:15 ET.
- Cash SPX in GTH is labeled PREVIOUS_CLOSE.
- Futures-led GTH direction: ES (primary), NQ, YM, RTY; VIX/DXY/US10Y/economic context remains soft and availability-aware.
- Builds an explicitly labeled Indicative SPX Reference; does not call it live SPX.
- Requires a usable actual SPX/SPXW option quote. Missing/stale/unexecutable chain => no entry price.
- V3 Entry Quality applies to SPX options in both GTH and RTH.

## Diagnostics/messages

- Added Market Data Time.
- Added System Detected At.
- Added Detection/Data Lag.
- Added V3 First Detected / Watch Added / Entry Ready timestamps.
- Candidate detail prints all stored V2/V3 market-context lines, including UNAVAILABLE/STALE states.
- Signal text includes V3 entry and source diagnostics.

## Health/state

- Telegram Status includes V3 monitors, V3 session and V3 entry-engine state.
- `/health` includes live data diagnostics and V3 state.
- New `/state` endpoint includes all monitor states, SPX session state and live diagnostics.
