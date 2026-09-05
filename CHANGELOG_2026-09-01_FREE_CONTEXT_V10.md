# v10 — FRED + Alpha Vantage free context

- Added `app/providers/economic.py`.
- Added optional `FRED_API_KEY` and `ALPHA_VANTAGE_API_KEY` settings.
- Waseem V2 SPX now consumes FRED economic release dates and daily US 2Y/10Y/30Y yields as soft context.
- Waseem V2 Equity now consumes FRED release dates plus Alpha Vantage earnings calendar as soft context.
- Missing, limited, or failed feeds display `UNAVAILABLE`; no fabricated data.
- FRED and Alpha Vantage date-only calendars never become minute-level hard event vetoes.
- Alpha Vantage full 3-month earnings calendar is cached and reused across symbols to protect free API quota.
- Telegram Waseem V2 caption prioritizes Economic Calendar/Earnings and core ES/NQ/VIX context.
- `/health` exposes only whether FRED/Alpha Vantage are configured; it never exposes keys.
- No changes to Core, Confirmed Setup, SPX V20, or Waseem V1 trading logic.
- Secret values are not present in `.env.example`, source code, tests, ZIP documentation, or repository-tracked files.
