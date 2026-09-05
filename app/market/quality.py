from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd

NY = ZoneInfo("America/New_York")


def parse_market_timestamp(value) -> datetime | None:
    """Parse Alpaca/yfinance timestamps into an aware UTC datetime."""
    if value is None or value == "":
        return None
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        if hasattr(ts, "to_pydatetime"):
            dt = ts.to_pydatetime()
        else:
            dt = ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def freshness_info(
    timestamp,
    *,
    now: datetime | None = None,
    max_age_minutes: float | None = None,
    require_same_ny_date: bool = False,
) -> tuple[bool, str, float | None, str | None]:
    """Validate freshness and return (ok, reason, age_minutes, normalized_iso)."""
    dt = parse_market_timestamp(timestamp)
    if dt is None:
        return False, "وقت آخر تحديث غير متوفر", None, None
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    age_seconds = (now_utc - dt).total_seconds()
    # A small future skew can happen between provider clocks; larger skew is invalid.
    if age_seconds < -120:
        return False, "وقت البيانات في المستقبل بشكل غير صالح", None, dt.isoformat()
    age_minutes = max(0.0, age_seconds / 60.0)
    if require_same_ny_date and dt.astimezone(NY).date() != now_utc.astimezone(NY).date():
        return False, "البيانات من جلسة سابقة", age_minutes, dt.isoformat()
    if max_age_minutes is not None and age_minutes > float(max_age_minutes):
        return False, f"بيانات STALE بعمر {age_minutes:.1f} دقيقة", age_minutes, dt.isoformat()
    return True, "FRESH", age_minutes, dt.isoformat()


def latest_bar_timestamp(df: pd.DataFrame) -> datetime | None:
    if df is None or df.empty or "timestamp" not in df.columns:
        return None
    values = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dropna()
    if values.empty:
        return None
    return values.max().to_pydatetime()


def validate_bars(
    df: pd.DataFrame,
    min_bars: int,
    *,
    max_age_minutes: float | None = None,
    require_same_ny_date: bool = False,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if df is None or df.empty:
        return False, "لا توجد بيانات تاريخية"
    if len(df) < min_bars:
        return False, f"عدد الشموع غير كافٍ: {len(df)}/{min_bars}"
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        return False, "أعمدة OHLCV ناقصة"
    if df[list(needed)].isna().any().any():
        return False, "بيانات OHLCV تحتوي قيماً فارغة"
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        return False, "سعر غير صالح"
    if (df["volume"] < 0).any():
        return False, "حجم تداول غير صالح"
    if max_age_minutes is not None or require_same_ny_date:
        ts = latest_bar_timestamp(df)
        ok, reason, _, _ = freshness_info(
            ts,
            now=now,
            max_age_minutes=max_age_minutes,
            require_same_ny_date=require_same_ny_date,
        )
        if not ok:
            return False, reason
    return True, "GOOD"
