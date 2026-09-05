from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd


FUTURES = {"ES": "ES=F", "NQ": "NQ=F", "YM": "YM=F", "RTY": "RTY=F"}
MACRO = {"VIX": "^VIX", "DXY": "DX-Y.NYB", "US10Y": "^TNX", "SPY": "SPY", "QQQ": "QQQ"}
SECTOR_ETF = {
    "Semiconductors": "SMH",
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Industrials": "XLI",
}


class WaseemV2ContextEngine:
    """Best-effort free-data context used only by Waseem V2.

    Missing public data is explicit (UNAVAILABLE/STALE) and never fabricated.
    Context is a soft modifier; stale underlying/option quotes and untradable
    spreads remain hard execution gates in the existing service/selector.
    """

    def __init__(self, ttl_seconds: int = 60):
        self.ttl_seconds = max(30, int(ttl_seconds))
        self._cache: dict[str, tuple[float, dict]] = {}

    @staticmethod
    def _safe_float(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _status(ts) -> tuple[str, float | None, str | None]:
        try:
            stamp = pd.Timestamp(ts)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            age = max(0.0, (pd.Timestamp.now(tz="UTC") - stamp).total_seconds() / 60.0)
            if age <= 20:
                state = "AVAILABLE"
            elif age <= 60:
                state = "DELAYED"
            else:
                state = "STALE"
            return state, round(age, 1), stamp.isoformat()
        except Exception:
            return "UNAVAILABLE", None, None

    @staticmethod
    def _load_history(ticker: str, period: str = "5d", interval: str = "5m", prepost: bool = True) -> pd.DataFrame:
        try:
            import yfinance as yf
            df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False, actions=False, prepost=prepost)
            return df if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    async def _series(self, label: str, ticker: str) -> dict:
        key = f"{label}:{ticker}"
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] <= self.ttl_seconds:
            return dict(cached[1])
        df = await asyncio.to_thread(self._load_history, ticker)
        if df.empty or "Close" not in df:
            out = {"label": label, "ticker": ticker, "status": "UNAVAILABLE", "value": None, "change_pct": None, "trend": "UNAVAILABLE", "age_minutes": None, "timestamp": None}
            self._cache[key] = (time.monotonic(), out)
            return dict(out)
        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if close.empty:
            out = {"label": label, "ticker": ticker, "status": "UNAVAILABLE", "value": None, "change_pct": None, "trend": "UNAVAILABLE", "age_minutes": None, "timestamp": None}
            self._cache[key] = (time.monotonic(), out)
            return dict(out)
        last = float(close.iloc[-1])
        ref = float(close.iloc[-7]) if len(close) >= 7 else float(close.iloc[0])
        change = ((last / ref) - 1.0) * 100.0 if ref else 0.0
        trend = "BULLISH" if change >= 0.12 else "BEARISH" if change <= -0.12 else "NEUTRAL"
        state, age, iso = self._status(df.index[-1])
        out = {"label": label, "ticker": ticker, "status": state, "value": round(last, 4), "change_pct": round(change, 3), "trend": trend, "age_minutes": age, "timestamp": iso}
        self._cache[key] = (time.monotonic(), out)
        return dict(out)

    @staticmethod
    def _direction_sign(direction: str) -> int:
        return 1 if str(direction).upper() == "LONG" else -1

    @staticmethod
    def _transition_modifier(analysis: dict, direction: str) -> tuple[float, list[str]]:
        sign = 1 if str(direction).upper() == "LONG" else -1
        scores = analysis.get("scores") or {}
        momentum = float(scores.get("Momentum", 50) or 50)
        vwap = float(scores.get("VWAP", 50) or 50)
        structure = float(scores.get("Structure", 50) or 50)
        reasons = " | ".join(str(x) for x in (analysis.get("reasons") or []))
        bonus = 0.0
        notes: list[str] = []
        if sign > 0:
            if "Liquidity Sweep صاعد" in reasons: bonus += 1.5; notes.append("bullish liquidity sweep")
            if momentum >= 62: bonus += 1.5; notes.append("momentum flipped bullish")
            if vwap >= 62: bonus += 1.0; notes.append("VWAP bullish/reclaim")
            if structure >= 62: bonus += 1.0; notes.append("structure improving")
        else:
            if "Liquidity Sweep هابط" in reasons: bonus += 1.5; notes.append("bearish liquidity sweep")
            if momentum <= 38: bonus += 1.5; notes.append("momentum flipped bearish")
            if vwap <= 38: bonus += 1.0; notes.append("VWAP bearish/reject")
            if structure <= 38: bonus += 1.0; notes.append("structure weakening")
        return min(5.0, bonus), notes

    async def index_context(self, analysis: dict, direction: str) -> dict:
        rows = await asyncio.gather(*[self._series(k, v) for k, v in {**FUTURES, **MACRO}.items()])
        data = {r["label"]: r for r in rows}
        sign = self._direction_sign(direction)
        modifier = 0.0
        notes: list[str] = []
        # Futures: strongest free pre/overnight confirmation layer.
        for label, weight in (("ES", 1.8), ("NQ", 1.5), ("YM", 0.6), ("RTY", 0.6)):
            r = data.get(label, {})
            if r.get("status") in {"AVAILABLE", "DELAYED"}:
                t = r.get("trend")
                aligned = (t == "BULLISH" and sign > 0) or (t == "BEARISH" and sign < 0)
                opposed = (t == "BEARISH" and sign > 0) or (t == "BULLISH" and sign < 0)
                if aligned: modifier += weight; notes.append(f"{label} confirms")
                elif opposed: modifier -= weight; notes.append(f"{label} diverges")
        # VIX normally moves opposite risk assets.
        vix = data.get("VIX", {})
        if vix.get("status") in {"AVAILABLE", "DELAYED"}:
            vt = vix.get("trend")
            aligned = (vt == "BEARISH" and sign > 0) or (vt == "BULLISH" and sign < 0)
            opposed = (vt == "BULLISH" and sign > 0) or (vt == "BEARISH" and sign < 0)
            if aligned: modifier += 1.5; notes.append("VIX confirms")
            elif opposed: modifier -= 1.5; notes.append("VIX divergence")
        # DXY/10Y are deliberately small soft context, never hard vetoes.
        for label in ("DXY", "US10Y"):
            r = data.get(label, {})
            if r.get("status") in {"AVAILABLE", "DELAYED"}:
                if sign > 0 and r.get("trend") == "BEARISH": modifier += 0.4
                elif sign > 0 and r.get("trend") == "BULLISH": modifier -= 0.4
                elif sign < 0 and r.get("trend") == "BULLISH": modifier += 0.4
                elif sign < 0 and r.get("trend") == "BEARISH": modifier -= 0.4
        trans, trans_notes = self._transition_modifier(analysis, direction)
        modifier += trans
        notes.extend(trans_notes)
        modifier = max(-7.0, min(9.0, modifier))
        lines = []
        for label in ("ES", "NQ", "YM", "RTY", "VIX", "DXY", "US10Y"):
            r = data.get(label, {})
            if r.get("status") == "UNAVAILABLE":
                lines.append(f"{label}: UNAVAILABLE")
            else:
                age = r.get("age_minutes")
                age_txt = f" | age {age}m" if age is not None else ""
                lines.append(f"{label}: {r.get('trend','N/A')} {r.get('change_pct',0):+.2f}% | {r.get('status')}{age_txt}")
        return {"modifier": round(modifier, 2), "notes": notes[:8], "data": data, "lines": lines, "source": "FREE/BEST_EFFORT yfinance + existing Alpaca"}

    async def equity_context(self, symbol: str, sector: str | None, analysis: dict, direction: str) -> dict:
        sector_ticker = SECTOR_ETF.get(str(sector or ""))
        targets = {"SPY": "SPY", "QQQ": "QQQ", "VIX": "^VIX"}
        if sector_ticker:
            targets["SECTOR"] = sector_ticker
        rows = await asyncio.gather(*[self._series(k, v) for k, v in targets.items()])
        data = {r["label"]: r for r in rows}
        sign = self._direction_sign(direction)
        modifier = 0.0
        notes: list[str] = []
        for label, weight in (("SPY", 1.0), ("QQQ", 0.8), ("SECTOR", 1.6)):
            r = data.get(label, {})
            if r.get("status") in {"AVAILABLE", "DELAYED"}:
                t = r.get("trend")
                aligned = (t == "BULLISH" and sign > 0) or (t == "BEARISH" and sign < 0)
                opposed = (t == "BEARISH" and sign > 0) or (t == "BULLISH" and sign < 0)
                if aligned: modifier += weight; notes.append(f"{label} confirms")
                elif opposed: modifier -= weight; notes.append(f"{label} diverges")
        vix = data.get("VIX", {})
        if vix.get("status") in {"AVAILABLE", "DELAYED"}:
            if sign > 0 and vix.get("trend") == "BEARISH": modifier += 0.7
            elif sign > 0 and vix.get("trend") == "BULLISH": modifier -= 0.7
            elif sign < 0 and vix.get("trend") == "BULLISH": modifier += 0.7
            elif sign < 0 and vix.get("trend") == "BEARISH": modifier -= 0.7
        trans, trans_notes = self._transition_modifier(analysis, direction)
        modifier += trans
        notes.extend(trans_notes)
        # Existing per-symbol relative strength is already computed in _analyze.
        rs = analysis.get("relative_strength")
        if rs is not None:
            rs = self._safe_float(rs)
            aligned_rs = rs if sign > 0 else -rs
            rs_mod = max(-2.0, min(2.0, aligned_rs * 0.25))
            modifier += rs_mod
            if abs(rs_mod) >= 0.5:
                notes.append(f"relative strength {rs_mod:+.1f}")
        modifier = max(-6.0, min(8.0, modifier))
        lines = []
        for label in ("SPY", "QQQ", "SECTOR", "VIX"):
            r = data.get(label)
            if not r:
                lines.append(f"{label}: UNAVAILABLE")
            elif r.get("status") == "UNAVAILABLE":
                lines.append(f"{label}: UNAVAILABLE")
            else:
                age = r.get("age_minutes")
                age_txt = f" | age {age}m" if age is not None else ""
                extra = f" ({r.get('ticker')})" if label == "SECTOR" else ""
                lines.append(f"{label}{extra}: {r.get('trend','N/A')} {r.get('change_pct',0):+.2f}% | {r.get('status')}{age_txt}")
        return {"modifier": round(modifier, 2), "notes": notes[:8], "data": data, "lines": lines, "source": "FREE/BEST_EFFORT yfinance + existing Alpaca"}
