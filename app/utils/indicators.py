from __future__ import annotations
import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    a = atr(df, period).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)


def macd(s: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    m = ema(s, 12) - ema(s, 26)
    sig = ema(m, 9)
    return m, sig, m - sig


def session_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, np.nan)
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        session = ts.dt.tz_convert("America/New_York").dt.date
        pv = typical * vol
        return pv.groupby(session).cumsum() / vol.groupby(session).cumsum()
    return (typical * vol).cumsum() / vol.cumsum()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    for p in (9, 20, 50, 200):
        x[f"ema{p}"] = ema(x["close"], p)
    x["rsi"] = rsi(x["close"], 14)
    x["rsi_slope"] = x["rsi"].diff(3)
    x["atr"] = atr(x, 14)
    x["atr_pct"] = (x["atr"] / x["close"].replace(0, np.nan)) * 100
    x["adx"] = adx(x, 14)
    m, s, h = macd(x["close"])
    x["macd"], x["macd_signal"], x["macd_hist"] = m, s, h
    x["macd_hist_slope"] = h.diff(3)
    x["vwap"] = session_vwap(x)
    x["vwap_distance_pct"] = ((x["close"] - x["vwap"]) / x["vwap"].replace(0, np.nan)) * 100
    vol_mean = x["volume"].rolling(20).mean().replace(0, np.nan)
    x["rvol"] = x["volume"] / vol_mean
    x["volume_slope"] = x["volume"].rolling(5).mean().pct_change(3) * 100
    x["momentum5_pct"] = x["close"].pct_change(5) * 100
    x["return20_pct"] = x["close"].pct_change(20) * 100
    x["high20"] = x["high"].shift(1).rolling(20).max()
    x["low20"] = x["low"].shift(1).rolling(20).min()
    return x
