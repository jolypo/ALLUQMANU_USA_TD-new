# v7 — Telegram Learning Controls Fix

This release fixes the missing Telegram UI wiring for the v6 Learning Memory feature.

## Added
- `⚙️ System → 🧠 Learning`
- `📊 Learning Status`
- `📤 Export Learning File`
- `📥 Import Learning File`
- Explicit cancel flow for pending imports.
- Telegram document handler for JSON imports.
- 5 MB import size limit.
- JSON/version/outcome validation.
- Merge-by-`trade_id` behavior to avoid duplicate samples.
- Export refreshes completed Confirmed Setup results before sending the backup.

## Unchanged
No trading strategy logic was changed in this release. Core, SPX Core, SPX V20, Confirmed Setup, Hunter, Judge, Dynamic Market Gate, DTE rules, contract filters, and the hard score floor remain unchanged.
