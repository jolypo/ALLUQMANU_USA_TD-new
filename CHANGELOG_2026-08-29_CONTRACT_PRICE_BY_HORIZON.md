# Contract price by expiration horizon — 2026-08-29

- Contract Search Price is now independent for Equity Options and SPX/Index Options.
- Each asset category has separate Daily/0DTE, Weekly/1–7 DTE, and Monthly/8–35 DTE maximum premium filters.
- 0 means unlimited/default for that specific horizon.
- Restore All Defaults resets all six values.
- Existing legacy per-category max prices are migrated to all three horizons so prior admin settings are preserved.
- SignalService passes the selected horizon's max premium into ContractSelector for Equity Options, SPX Core, and SPX V20.
