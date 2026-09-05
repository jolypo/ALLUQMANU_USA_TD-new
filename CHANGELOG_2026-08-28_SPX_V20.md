# SPX V20 Integration — 2026-08-28

## Telegram workflow
`Trading -> Index Options` no longer starts an index scan immediately. It opens a strategy chooser:
- `SPX V20` — server-side port of the supplied `ALLUQMANI SPX Radar V2.1 Stable Fix` signal logic.
- `SPX Core` — the project's existing SPX strategy, preserved as a separate path.

Slash-command fallback remains unchanged and uses Core unless a menu strategy is explicitly selected.

## SPX V20 signal logic ported
The server engine implements the Pine signal gates needed for READY CALL / READY PUT:
- EMA 9 / 20 / 50 / 200
- RSI 14
- MACD 12/26/9
- DMI / ADX 14 and ADX rising state
- SPY Relative Volume proxy
- SPX cash-session VWAP
- MTF Smart Veto: 5m / 15m / 60m / 240m with 3-of-4 alignment
- Previous cash high/low/close
- Opening range 09:30–09:45 New York
- confirmed pivot support/resistance
- ATR budget
- VWAP extension soft/hard guards
- breakout -> retest state
- breakout volume requirement
- chase-distance guard
- prior 15m close confirmation
- R:R / ATR-based underlying levels

TradingView-only drawings, tables, labels and Hero Zero visualization are not used as server entry gates.

## Data separation
- SPX price reference for V20: free `yfinance` `^GSPC` bars (already present in requirements).
- SPY volume proxy: Alpaca IEX bars.
- SPX/SPXW option contracts and Bid/Ask/Greeks: Alpaca options data.
- If required SPX reference data is unavailable, V20 returns NO READY rather than substituting fabricated values.

## Contract modes
After V20 produces a READY direction, the existing index-option contract layer checks both:
- SPX 0DTE (strict spread/delta/contract-score/risk gates)
- SPX Swing (existing 7–35 DTE window)

Core retains its existing 0DTE + Swing behavior.

## Traceability
Every V20 option candidate stores `option.strategy_mode = SPX_V20` and V20 diagnostics. Core candidates store `SPX_CORE`.
The Telegram selection and published signal text show which SPX strategy produced the candidate.

## Validation
- `python -m compileall -q .` -> PASS
- `pytest -q` -> 29 passed
- Added tests for V20 structured output, V20 routing, 0DTE+Swing contract paths, and Telegram strategy-choice callbacks.
