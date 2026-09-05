# V21 — Arabic News + Expert Trader Outlook

## Scope
Presentation/intelligence layer only. Existing trading engines and images were not changed.

## Changes
- Stock-news headline and summary are translated to Arabic for Telegram display using a best-effort external translation request. If translation is unavailable, the original source text is preserved rather than fabricated.
- Added visible `---` separators between 15m, 1h, 4h, daily, weekly and monthly sections in `تحليل السهم`.
- Added `رأي فريق المتداولين الخبراء` before timestamps in stock analysis.
- Added `رأي فريق المتداولين الخبراء` before timestamps in stock news.
- Expert outlook is scenario-based and derived from available trend score, support/resistance, VWAP, momentum, RVOL, ATR, ICT/Fibonacci and news risk/impact inputs.
- Time windows such as first 30 minutes and midday are conditional scenarios; the system does not promise exact future prices or times.
- Updated focused tests for the new presentation behavior.

## Safety / Data honesty
- Missing inputs remain unavailable.
- No deterministic future-price promise is generated.
- V2/V3/V4/V5 strategy logic remains unchanged.
- Paper-mode behavior remains unchanged.
