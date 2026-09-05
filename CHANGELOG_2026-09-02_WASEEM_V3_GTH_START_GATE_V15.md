# v15 — Waseem V3 GTH Start Gate Fix

- Fixed Telegram `monitor:start:index:waseem_v3` so it uses the SPX option-session gate (`GTH`/`RTH`) instead of the regular US cash-market clock.
- During Cboe GTH, SPX Waseem V3 can now start even when Alpaca's regular market clock reports the US cash market closed.
- Other monitors remain unchanged and continue to require their existing regular-market gate.
- The continuous SPX Waseem V3 monitor loop already used the option-session gate and remains unchanged.
- Manual SPX Waseem V3 scan already used the option-session gate and remains unchanged.
