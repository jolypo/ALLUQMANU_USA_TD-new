# Option report pending windows — 2026-08-29

- Options that reach the configured cash success threshold are recorded as WIN immediately, regardless of DTE.
- Closed option trades that did not reach the success threshold are finalized as LOSS immediately because the threshold can no longer be reached.
- Open 0DTE trades remain pending until the expiration session closes, then become LOSS if the threshold was not reached.
- Open 1–7 DTE trades remain PENDING across daily reports until success, real close, or actual expiration session close.
- Open 8–35 DTE trades remain PENDING across daily/weekly reports until success, real close, or actual expiration session close.
- Actual option expiration date is preferred; entered_at + DTE is used only as a legacy fallback.
- Daily/weekly cohort membership still uses entered_at only, so open swing trades do not repeat in later daily cohorts.
- Added regression tests for weekly/monthly pending behavior, same-day weekly success, expiry loss, and early close loss.
