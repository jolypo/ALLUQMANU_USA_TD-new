# FINAL RELEASE AUDIT — v8

## Scope
This release adds Waseem V1 and a dedicated open-option profit watcher. It does not replace the previous option engines.

## Trading review
- PASS — CALL and PUT use mirrored directional selection.
- PASS — Daily, Weekly and Monthly remain distinct horizons.
- PASS — Waseem can scan Daily + Weekly independently in one request.
- PASS — Near-OTM selection is bounded; far lottery strikes are rejected.
- PASS — Equity Waseem uses expected-move-relative distance and horizon-aware bounds.
- PASS — SPX Waseem uses actual SPX spot and a maximum 40-point near-OTM search radius.
- PASS — 0DTE does not fail only because Greeks are absent.
- PASS — Weekly/Monthly retain Delta as a quality requirement.
- PASS — Spread, bid/ask, bid/ask size when available, volume, premium and expected-move fit contribute to ranking.
- PASS — Dynamic market hard vetoes and required score remain in force.
- PASS — Waseem hard READY floor is at least 90.
- PASS — Contract reject diagnostics are retained.

## Profit watcher review
- PASS — Confirmed OPEN option trades only.
- PASS — 60-second independent cadence.
- PASS — Increment comes from Telegram Profit Alert Step.
- PASS — Comparison anchor is the last alerted price.
- PASS — No repeated old high after dip/recovery.
- PASS — Real observed current price is sent after a jump.
- PASS — Profit image and Arabic caption are sent together.
- PASS — Bid/Ask and quote timestamp diagnostics are persisted.
- PASS — Existing entry/stop/TP/time-exit monitor remains active.

## Engineering review
- PASS — New selector lives in `app/options/waseem_selector.py`.
- PASS — New watcher lives in `app/scheduler/profit_watcher.py`.
- PASS — Atomic per-trade repository merge helper added for watcher fields.
- PASS — Heavier monitor preserves watcher fields before replacing the open-trades snapshot.
- PASS — Legacy engine routes remain present.
- PASS — Telegram exposes Waseem V1 independently for Equity and SPX.
- PASS — Runtime secrets are not embedded by this change.

## Automated verification
- `python -m compileall -q .` — PASS
- `pytest -q` — 107 passed

## Limits / not claimed
- Live profitability is not proven by unit tests.
- Option market data quality remains limited by the configured Alpaca feed.
- A 60-second poll cannot observe an intermediate price that exists only between polls. It sends the latest real price observed at the next poll.
- This remains a paper/signal system, not a guarantee of execution or returns.
