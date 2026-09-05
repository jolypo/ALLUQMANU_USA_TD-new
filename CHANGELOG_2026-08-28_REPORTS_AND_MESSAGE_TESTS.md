# Update — Message Tests + Daily/Weekly Reports + Performance Scoring

## Contract performance rule
- Equity Options and Index Options use the admin-configured USD success threshold.
- The moment an entered option reaches the threshold (default $50), its performance result is frozen as `WIN`.
- If the entered option has not reached the threshold by the end of its New York trading session, its performance result is frozen as `LOSS`.
- A swing option may remain operationally open after this performance result is frozen; the performance result does not execute or close a trade.
- Actual realized close P&L remains stored separately for audit/risk review.
- A later realized loss never erases an already-earned threshold WIN.
- A threshold-miss LOSS does not later turn into a WIN on a later session.

## Stock performance rule
- Stocks do not use a configurable cash/percentage threshold.
- Stock performance WIN is based on hitting TP1/TP2/TP3.
- A closed entered stock that never hit a target is scored as LOSS.

## Telegram Success Rules
- Stock row is read-only and displays `حسب الأهداف`.
- Equity Option and Index Option thresholds remain independently editable from private Telegram by sending a numeric value.

## Private message tests
New isolated private menu `🧪 اختبارات الرسائل`:
- Equity Option signal image + production-format caption.
- Index Option signal image + production-format caption.
- Equity Option profit image + production-format caption.
- Index Option profit image + production-format caption.
- Test actions never create trades and never change history/performance/statistics.

## Reports
- Daily and Weekly reports use one unified dark/beige report design.
- Separate report types: Stocks, Equity Options, SPX/Index Options.
- Option report WIN/LOSS counts follow the threshold/end-of-session rule.
- The threshold wording inside the image is dynamic (e.g. $50, $100).
- Option report profit uses the best observed cash profit for scored winners.
- A scored option loss uses the full paid premium as the statistical loss, matching the requested report model.
- Actual realized P&L remains available separately in report data.
- Automatic daily reports publish after the configured post-close Riyadh hour.
- Weekly reports use the same design and remain scheduled for Thursday after the US close.

## Verification
- `pytest -q`: 26 passed.
- `python -m compileall -q .`: passed.
