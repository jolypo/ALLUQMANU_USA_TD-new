# V24 — Symbol Diversity + Global Cooldown

Date: 2026-09-05

## Scope
Telegram opportunity-delivery layer only. No strategy/engine logic was changed.

## Changes
- Added a global per-underlying Telegram cooldown shared across Waseem V2/V3/V4/V5/V6 monitors.
- Default cooldown: 20 minutes (`monitor_symbol_cooldown_seconds=1200`).
- Engines continue scanning during cooldown; only duplicate delivery is suppressed.
- Expanded continuous-engine internal candidate pool to at least 20 before Telegram selection.
- Added unique-symbol selection so multiple contracts for NVDA/INTC cannot consume all three Telegram discovery slots.
- Best score per symbol is retained; lower-score duplicate contracts are suppressed from discovery slots.
- Material overrides can bypass cooldown:
  - WATCH -> READY
  - confirmed CALL <-> PUT reversal
  - READY score improvement of at least 3 points
- Added an in-memory suppression/near-miss audit trail with reasons such as:
  - DUPLICATE_SYMBOL_LOWER_SCORE
  - UNIQUE_SYMBOL_RANK_BELOW_CUTOFF
  - GLOBAL_SYMBOL_COOLDOWN
- No database is required for this delivery state; it resets on process restart.

## Safety
- V2/V3/V4/V5/V6 logic unchanged.
- Option selection logic unchanged.
- WATCH registries still re-evaluate continuously.
- Paper mode unchanged.
