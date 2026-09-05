# v14 — GTH Partial Freshness + Telegram Callback Hardening

- Split SPXW GTH option-feed health into independent quote and trade freshness.
- Overall GTH state is PARTIAL when one side is usable (AVAILABLE/DELAYED) while the other is stale/unavailable.
- Preserved AVAILABLE/DELAYED when both quote and trade are usable.
- Marked cash-SPX bars from the preceding regular session as PREVIOUS_RTH while current session is GTH.
- Added quote/trade status lines to Telegram Status and Waseem V3 diagnostics.
- Hardened Telegram menu callbacks: expired/invalid callback-query acknowledgements no longer break the menu flow.
- No trading-strategy changes to legacy engines or Waseem V3 entry logic.
