# Profit Alert Step + Manual Close Cleanup

- Added persistent Telegram setting `Profit Alert Step` for equity and SPX options.
- Default increment: $0.10.
- Presets: $0.05, $0.10, $0.25, plus custom input and restore default.
- Alerts are anchored to confirmed entry and only fire at new profit increments.
- No negative/below-entry profit alerts.
- Re-crossing an already alerted level does not duplicate an alert.
- Option signal caption reorganized contract-first with Expiration, DTE, current premium, Bid/Ask, levels, strength, Greeks, underlying and detection timestamp.
- Option card displays actual `EXP ... • DTE ...` instead of publish date.
- Trade-related Telegram message IDs are tracked for signal/profit bots.
- Manual Close and Close All are private-only and delete tracked trade messages from the group while preserving History/Performance data.
- Full test suite: 49 passed.
