from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings


HIGH_IMPACT_RELEASE_KEYWORDS = (
    "consumer price", "cpi", "producer price", "ppi", "employment situation",
    "nonfarm", "payroll", "personal income and outlays", "personal consumption",
    "gross domestic product", "gdp", "fomc", "federal open market",
    "job openings", "jolts", "initial claims", "jobless claims", "retail sales",
    "ism", "pmi", "consumer confidence",
)


class EconomicContextProvider:
    """Low-frequency free context for Waseem V2 only.

    FRED supplies official economic release dates and daily Treasury series.
    Alpha Vantage supplies the forward earnings calendar. Missing/limited data is
    explicit and never synthesized. These feeds are context only, not execution
    price sources.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=12.0, follow_redirects=True)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self):
        await self.client.aclose()

    def _cached(self, key: str, ttl: int):
        row = self._cache.get(key)
        if row and time.monotonic() - row[0] <= ttl:
            return row[1]
        return None

    def _put(self, key: str, value: Any):
        self._cache[key] = (time.monotonic(), value)
        return value

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def fred_release_calendar(self, days_ahead: int = 3) -> dict:
        """Return high-impact FRED release dates.

        FRED release-date API does not promise an exact intraday publication
        timestamp. Consequently this result is DATE_ONLY and must remain a soft
        risk/context modifier, never an "event in N minutes" hard veto.
        """
        if not settings.fred_enabled or not settings.fred_api_key:
            return {"status": "UNAVAILABLE", "source": "FRED", "reason": "API_KEY_NOT_CONFIGURED", "events": []}
        days_ahead = max(0, min(int(days_ahead), 14))
        key = f"fred:calendar:{days_ahead}"
        cached = self._cached(key, settings.fred_calendar_cache_seconds)
        if cached is not None:
            return cached
        async with self._lock(key):
            cached = self._cached(key, settings.fred_calendar_cache_seconds)
            if cached is not None:
                return cached
            today = datetime.now(timezone.utc).date()
            end = today + timedelta(days=days_ahead)
            try:
                r = await self.client.get(
                    f"{settings.fred_base_url.rstrip('/')}/fred/releases/dates",
                    params={
                        "api_key": settings.fred_api_key,
                        "file_type": "json",
                        "realtime_start": today.isoformat(),
                        "realtime_end": end.isoformat(),
                        "include_release_dates_with_no_data": "true",
                        "sort_order": "asc",
                        "limit": 1000,
                    },
                )
                r.raise_for_status()
                payload = r.json()
                events: list[dict] = []
                for item in payload.get("release_dates", []) or []:
                    name = str(item.get("release_name") or "").strip()
                    raw_date = str(item.get("date") or "").strip()
                    try:
                        d = date.fromisoformat(raw_date)
                    except ValueError:
                        continue
                    if d < today or d > end:
                        continue
                    low = name.lower()
                    if not any(k in low for k in HIGH_IMPACT_RELEASE_KEYWORDS):
                        continue
                    events.append({
                        "date": d.isoformat(),
                        "name": name or f"FRED release {item.get('release_id', '')}".strip(),
                        "release_id": item.get("release_id"),
                        "timing": "DATE_ONLY",
                    })
                events.sort(key=lambda x: (x["date"], x["name"]))
                return self._put(key, {
                    "status": "AVAILABLE",
                    "source": "FRED",
                    "timing_precision": "DATE_ONLY",
                    "events": events,
                    "window_start": today.isoformat(),
                    "window_end": end.isoformat(),
                })
            except Exception as exc:
                return self._put(key, {"status": "UNAVAILABLE", "source": "FRED", "reason": type(exc).__name__, "events": []})

    async def fred_treasury_snapshot(self) -> dict:
        """Daily official Treasury constant-maturity yields from FRED."""
        if not settings.fred_enabled or not settings.fred_api_key:
            return {"status": "UNAVAILABLE", "source": "FRED", "series": {}}
        key = "fred:treasury"
        cached = self._cached(key, settings.fred_series_cache_seconds)
        if cached is not None:
            return cached
        async with self._lock(key):
            cached = self._cached(key, settings.fred_series_cache_seconds)
            if cached is not None:
                return cached
            series_ids = {"US2Y": "DGS2", "US10Y_FRED": "DGS10", "US30Y": "DGS30"}
            out = {}
            try:
                for label, series_id in series_ids.items():
                    r = await self.client.get(
                        f"{settings.fred_base_url.rstrip('/')}/fred/series/observations",
                        params={
                            "series_id": series_id,
                            "api_key": settings.fred_api_key,
                            "file_type": "json",
                            "sort_order": "desc",
                            "limit": 8,
                        },
                    )
                    r.raise_for_status()
                    observations = r.json().get("observations", []) or []
                    usable = []
                    for obs in observations:
                        try:
                            usable.append((obs.get("date"), float(obs.get("value"))))
                        except (TypeError, ValueError):
                            continue
                    if not usable:
                        out[label] = {"status": "UNAVAILABLE", "series_id": series_id}
                        continue
                    latest_date, latest_val = usable[0]
                    prior_val = usable[1][1] if len(usable) > 1 else latest_val
                    change_bp = (latest_val - prior_val) * 100.0
                    out[label] = {
                        "status": "DELAYED_DAILY",
                        "series_id": series_id,
                        "value": round(latest_val, 4),
                        "date": latest_date,
                        "change_bp": round(change_bp, 2),
                    }
                return self._put(key, {"status": "AVAILABLE" if any(v.get("value") is not None for v in out.values()) else "UNAVAILABLE", "source": "FRED", "series": out})
            except Exception as exc:
                return self._put(key, {"status": "UNAVAILABLE", "source": "FRED", "reason": type(exc).__name__, "series": out})

    async def alpha_vantage_earnings(self, symbol: str) -> dict:
        """Lookup the next scheduled earnings date from one cached AV calendar.

        The free key has tight quotas, so the full 3-month calendar is fetched at
        most once per cache window and reused for every equity scan.
        """
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return {"status": "UNAVAILABLE", "source": "Alpha Vantage", "reason": "INVALID_SYMBOL"}
        if not settings.alpha_vantage_enabled or not settings.alpha_vantage_api_key:
            return {"status": "UNAVAILABLE", "source": "Alpha Vantage", "reason": "API_KEY_NOT_CONFIGURED"}
        key = "alpha:earnings:3month"
        calendar = self._cached(key, settings.alpha_vantage_earnings_cache_seconds)
        if calendar is None:
            async with self._lock(key):
                calendar = self._cached(key, settings.alpha_vantage_earnings_cache_seconds)
                if calendar is None:
                    try:
                        r = await self.client.get(
                            settings.alpha_vantage_base_url,
                            params={"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": settings.alpha_vantage_api_key},
                        )
                        r.raise_for_status()
                        text = r.text
                        # Alpha Vantage may return a JSON/info message when a free-key
                        # rate limit is hit. Do not try to parse that as CSV.
                        if text.lstrip().startswith("{"):
                            calendar = {"status": "UNAVAILABLE", "reason": "API_LIMIT_OR_ERROR", "by_symbol": {}}
                        else:
                            by_symbol: dict[str, list[dict]] = {}
                            for row in csv.DictReader(io.StringIO(text)):
                                sym = str(row.get("symbol") or "").strip().upper()
                                report_date = str(row.get("reportDate") or "").strip()
                                if not sym or not report_date:
                                    continue
                                by_symbol.setdefault(sym, []).append({
                                    "report_date": report_date,
                                    "fiscal_date_ending": str(row.get("fiscalDateEnding") or "").strip() or None,
                                    "estimate": str(row.get("estimate") or "").strip() or None,
                                    "currency": str(row.get("currency") or "").strip() or None,
                                    "timing": "DATE_ONLY",
                                })
                            for rows in by_symbol.values():
                                rows.sort(key=lambda x: x["report_date"])
                            calendar = {"status": "AVAILABLE", "by_symbol": by_symbol}
                    except Exception as exc:
                        calendar = {"status": "UNAVAILABLE", "reason": type(exc).__name__, "by_symbol": {}}
                    self._put(key, calendar)
        if calendar.get("status") != "AVAILABLE":
            return {"status": "UNAVAILABLE", "source": "Alpha Vantage", "reason": calendar.get("reason", "UNKNOWN")}
        today = datetime.now(timezone.utc).date()
        future = []
        for row in calendar.get("by_symbol", {}).get(symbol, []):
            try:
                d = date.fromisoformat(row["report_date"])
            except ValueError:
                continue
            if d >= today:
                future.append((d, row))
        if not future:
            return {"status": "AVAILABLE", "source": "Alpha Vantage", "symbol": symbol, "next_earnings": None, "timing_precision": "DATE_ONLY"}
        d, row = min(future, key=lambda x: x[0])
        days = (d - today).days
        return {
            "status": "AVAILABLE",
            "source": "Alpha Vantage",
            "symbol": symbol,
            "next_earnings": row,
            "days_until": days,
            "timing_precision": "DATE_ONLY",
        }

    async def equity_context(self, symbol: str) -> dict:
        calendar, earnings = await asyncio.gather(self.fred_release_calendar(3), self.alpha_vantage_earnings(symbol))
        modifier = 0.0
        notes = []
        lines = []
        events = calendar.get("events", []) if calendar.get("status") == "AVAILABLE" else []
        today = datetime.now(timezone.utc).date().isoformat()
        today_events = [e for e in events if e.get("date") == today]
        if calendar.get("status") == "AVAILABLE":
            if today_events:
                modifier -= 1.0
                notes.append("high-impact macro release date today")
                lines.append("Economic Calendar (FRED): CAUTION — " + "; ".join(e["name"] for e in today_events[:2]) + " | DATE_ONLY")
            elif events:
                lines.append(f"Economic Calendar (FRED): AVAILABLE — next {events[0]['date']} {events[0]['name']} | DATE_ONLY")
            else:
                lines.append("Economic Calendar (FRED): AVAILABLE — no matched high-impact release in next 3d")
        else:
            lines.append(f"Economic Calendar (FRED): UNAVAILABLE — {calendar.get('reason','unknown')}")
        if earnings.get("status") == "AVAILABLE":
            nxt = earnings.get("next_earnings")
            if nxt:
                days = int(earnings.get("days_until", 999))
                if days == 0:
                    modifier -= 2.0
                    notes.append("earnings scheduled today")
                elif days <= 2:
                    modifier -= 1.0
                    notes.append("earnings very near")
                lines.append(f"Earnings (Alpha Vantage): {nxt.get('report_date')} | in {days}d | DATE_ONLY")
            else:
                lines.append("Earnings (Alpha Vantage): AVAILABLE — none in 3-month calendar")
        else:
            lines.append(f"Earnings (Alpha Vantage): UNAVAILABLE — {earnings.get('reason','unknown')}")
        return {"modifier": max(-3.0, modifier), "notes": notes, "lines": lines, "calendar": calendar, "earnings": earnings}

    async def index_context(self) -> dict:
        calendar, treasury = await asyncio.gather(self.fred_release_calendar(3), self.fred_treasury_snapshot())
        modifier = 0.0
        notes = []
        lines = []
        events = calendar.get("events", []) if calendar.get("status") == "AVAILABLE" else []
        today = datetime.now(timezone.utc).date().isoformat()
        today_events = [e for e in events if e.get("date") == today]
        if calendar.get("status") == "AVAILABLE":
            if today_events:
                modifier -= 1.0
                notes.append("high-impact macro release date today")
                lines.append("Economic Calendar (FRED): CAUTION — " + "; ".join(e["name"] for e in today_events[:2]) + " | DATE_ONLY")
            elif events:
                lines.append(f"Economic Calendar (FRED): AVAILABLE — next {events[0]['date']} {events[0]['name']} | DATE_ONLY")
            else:
                lines.append("Economic Calendar (FRED): AVAILABLE — no matched high-impact release in next 3d")
        else:
            lines.append(f"Economic Calendar (FRED): UNAVAILABLE — {calendar.get('reason','unknown')}")
        if treasury.get("status") == "AVAILABLE":
            s = treasury.get("series", {})
            for label in ("US2Y", "US10Y_FRED", "US30Y"):
                row = s.get(label, {})
                if row.get("value") is None:
                    lines.append(f"{label}: UNAVAILABLE")
                else:
                    lines.append(f"{label}: {row['value']:.3f}% | {row.get('change_bp',0):+.1f}bp | FRED DAILY {row.get('date')}")
        else:
            lines.append("FRED Treasury: UNAVAILABLE")
        return {"modifier": modifier, "notes": notes, "lines": lines, "calendar": calendar, "treasury": treasury}
