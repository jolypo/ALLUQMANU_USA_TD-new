# Report Cohorts & DTE Breakdown — 2026-08-29

- Daily/weekly option reports are now assigned strictly by confirmed `entered_at` in New York time.
- Published-only/unfilled signals are excluded from period reports.
- Open swing positions do not repeat in future daily reports merely because they remain open.
- Option report horizons:
  - 0DTE: DTE = 0
  - Weekly: DTE 1–7
  - Monthly: DTE 8–35
- Separate horizon reports are available for Equity Options and SPX/Index Options.
- Comprehensive daily/weekly options reports combine all option horizons and both option categories, while preserving a horizon breakdown.
- `/report` remains private/on-demand and now sends a comprehensive weekly options report plus the stock weekly report.
