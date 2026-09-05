from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.utils.indicators import ema, rsi, atr, macd

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SPXV20Config:
    ema_fast: int = 9
    ema_mid: int = 20
    ema_slow: int = 50
    ema_long: int = 200
    rsi_len: int = 14
    adx_len: int = 14
    atr_len: int = 14
    rvol_len: int = 20
    min_score: float = 70.0
    min_adx: float = 18.0
    min_rvol: float = 1.05
    required_tf: int = 3
    stop_atr: float = 1.25
    tp1_r: float = 1.0
    tp2_r: float = 1.75
    tp3_r: float = 2.5
    minimum_rr: float = 1.5
    pivot_len: int = 5
    breakout_buffer_atr: float = 0.10
    retest_tolerance_atr: float = 0.20
    max_retest_bars: int = 12
    max_chase_atr: float = 0.75
    max_atr_budget: float = 1.10
    soft_vwap_distance_atr: float = 1.20
    hard_vwap_distance_atr: float = 2.00
    adx_rise_bars: int = 3


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    for c in ("open", "high", "low", "close", "volume"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
    x = x.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    x["ny_time"] = x["timestamp"].dt.tz_convert(NY)
    x["ny_date"] = x["ny_time"].dt.date
    x["ny_minute"] = x["ny_time"].dt.hour * 60 + x["ny_time"].dt.minute
    return x.reset_index(drop=True)


def _dmi(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    prev_close = close.shift(1)
    tr = pd.concat([(high-low), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    smooth_tr = tr.ewm(alpha=1/period, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / smooth_tr
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / smooth_tr
    dx = 100 * (plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, adjust=False).mean().fillna(0.0)
    return plus_di.fillna(0.0), minus_di.fillna(0.0), adx


def _trend_state(raw: pd.DataFrame, cfg: SPXV20Config) -> int:
    if raw is None or len(raw) < cfg.ema_long + 5:
        return 0
    x = _numeric(raw)
    c = x["close"]
    e20, e50, e200 = ema(c, cfg.ema_mid), ema(c, cfg.ema_slow), ema(c, cfg.ema_long)
    last = float(c.iloc[-1])
    if last > e20.iloc[-1] > e50.iloc[-1] > e200.iloc[-1]:
        return 1
    if last < e20.iloc[-1] < e50.iloc[-1] < e200.iloc[-1]:
        return -1
    return 0


def _last_confirmed_pivot(values: pd.Series, strength: int, high: bool) -> float | None:
    arr = values.to_numpy(dtype=float)
    if len(arr) < 2 * strength + 1:
        return None
    for center in range(len(arr) - strength - 1, strength - 1, -1):
        window = arr[center-strength:center+strength+1]
        value = arr[center]
        if not np.isfinite(value):
            continue
        if high and value >= np.nanmax(window):
            return float(value)
        if not high and value <= np.nanmin(window):
            return float(value)
    return None


def _daily_atr_previous(daily: pd.DataFrame, period: int = 14) -> float | None:
    if daily is None or len(daily) < period + 2:
        return None
    x = _numeric(daily)
    a = atr(x, period)
    # Pine: request.security(..., ta.atr(14)[1]) => previous completed daily ATR.
    value = a.iloc[-2] if len(a) >= 2 else np.nan
    return float(value) if pd.notna(value) and float(value) > 0 else None


class SPXV20Engine:
    """Python port of ALLUQMANI SPX Radar V2.1 READY CALL/PUT logic.

    TradingView drawing-only features are intentionally excluded. The signal gates,
    score, MTF veto, RTH VWAP/volume, breakout/retest, chase and ATR-budget controls
    are preserved for server-side scanning.
    """

    def __init__(self, cfg: SPXV20Config | None = None):
        self.cfg = cfg or SPXV20Config()

    def analyze(
        self,
        primary_15m: pd.DataFrame,
        mtf: dict[str, pd.DataFrame],
        daily: pd.DataFrame,
        now: datetime | None = None,
    ) -> dict:
        cfg = self.cfg
        x = _numeric(primary_15m)
        if len(x) < max(220, cfg.ema_long + 20):
            raise ValueError("SPX V20 insufficient 15m history")

        now_ny = (now or datetime.now(NY)).astimezone(NY)
        # Use only bars that have actually started by now; Alpaca timestamps are bar starts.
        now_utc = pd.Timestamp(now_ny).tz_convert("UTC")
        x = x[x["timestamp"] <= now_utc].copy()
        # Pine uses barstate.isconfirmed=true by default. Alpaca may expose the
        # currently-forming 15m candle, so never let an unfinished candle create READY.
        while len(x) and x.iloc[-1]["timestamp"] + pd.Timedelta(minutes=15) > now_utc:
            x = x.iloc[:-1].copy()
        if len(x) < 220:
            raise ValueError("SPX V20 insufficient current history")

        c = x["close"]
        x["ema9"] = ema(c, cfg.ema_fast)
        x["ema20"] = ema(c, cfg.ema_mid)
        x["ema50"] = ema(c, cfg.ema_slow)
        x["ema200"] = ema(c, cfg.ema_long)
        x["rsi"] = rsi(c, cfg.rsi_len)
        macd_line, macd_signal, macd_hist = macd(c)
        x["macd"] = macd_line
        x["macd_signal"] = macd_signal
        x["macd_hist"] = macd_hist
        plus_di, minus_di, adx_v = _dmi(x, cfg.adx_len)
        x["plus_di"] = plus_di
        x["minus_di"] = minus_di
        x["adx"] = adx_v
        x["atr"] = atr(x, cfg.atr_len)
        x["rvol"] = x["volume"] / x["volume"].rolling(cfg.rvol_len).mean().replace(0, np.nan)

        # RTH session state, SPY volume proxy is naturally the primary proxy volume here.
        x["in_cash"] = (x["ny_minute"] >= 570) & (x["ny_minute"] < 960)
        x["in_or"] = (x["ny_minute"] >= 570) & (x["ny_minute"] < 585)
        rth = x[x["in_cash"]].copy()
        if rth.empty:
            return self._not_ready("MARKET CLOSED", x, mtf)

        current_date = rth.iloc[-1]["ny_date"]
        current = rth[rth["ny_date"] == current_date].copy()
        if current.empty:
            return self._not_ready("NO CURRENT RTH SESSION", x, mtf)

        last = current.iloc[-1]
        close = float(last["close"])
        atr_value = float(last["atr"])
        if not np.isfinite(atr_value) or atr_value <= 0:
            raise ValueError("SPX V20 ATR unavailable")

        # Cash VWAP using the same SPY proxy volume configured by the Pine script.
        typical = (current["high"] + current["low"] + current["close"]) / 3.0
        vol = current["volume"].replace(0, np.nan)
        cash_vwap = float((typical * vol).sum() / vol.sum()) if vol.notna().any() and float(vol.sum()) > 0 else np.nan
        relative_volume = float(last["rvol"]) if pd.notna(last["rvol"]) else np.nan
        volume_ok = np.isfinite(relative_volume) and relative_volume >= cfg.min_rvol

        trend_states = {
            "5": _trend_state(mtf.get("5"), cfg),
            "15": _trend_state(mtf.get("15", primary_15m), cfg),
            "60": _trend_state(mtf.get("60"), cfg),
            "240": _trend_state(mtf.get("240"), cfg),
        }
        bull_tf = sum(1 for v in trend_states.values() if v == 1)
        bear_tf = sum(1 for v in trend_states.values() if v == -1)
        bull_veto_ok = bull_tf >= cfg.required_tf
        bear_veto_ok = bear_tf >= cfg.required_tf

        adx_value = float(last["adx"])
        adx_back = current["adx"].iloc[-1-cfg.adx_rise_bars] if len(current) > cfg.adx_rise_bars else np.nan
        adx_rising = pd.notna(adx_back) and adx_value > float(adx_back)
        rsi_value = float(last["rsi"])
        macd_l = float(last["macd"])
        macd_s = float(last["macd_signal"])
        macd_h = float(last["macd_hist"])

        bull_score = 0.0
        bull_score += 12 if float(last["ema9"]) > float(last["ema20"]) else 0
        bull_score += 10 if float(last["ema20"]) > float(last["ema50"]) else 0
        bull_score += 8 if close > float(last["ema200"]) else 0
        bull_score += 10 if np.isfinite(cash_vwap) and close > cash_vwap else 0
        bull_score += 10 if 52 <= rsi_value <= 72 else (5 if rsi_value > 50 else 0)
        bull_score += 10 if macd_l > macd_s and macd_h > 0 else 0
        bull_score += 8 if float(last["plus_di"]) > float(last["minus_di"]) else 0
        bull_score += 8 if adx_value >= cfg.min_adx else (4 if adx_rising else 0)
        bull_score += 8 if volume_ok else 0
        bull_score += bull_tf * 4

        bear_score = 0.0
        bear_score += 12 if float(last["ema9"]) < float(last["ema20"]) else 0
        bear_score += 10 if float(last["ema20"]) < float(last["ema50"]) else 0
        bear_score += 8 if close < float(last["ema200"]) else 0
        bear_score += 10 if np.isfinite(cash_vwap) and close < cash_vwap else 0
        bear_score += 10 if 28 <= rsi_value <= 48 else (5 if rsi_value < 50 else 0)
        bear_score += 10 if macd_l < macd_s and macd_h < 0 else 0
        bear_score += 8 if float(last["minus_di"]) > float(last["plus_di"]) else 0
        bear_score += 8 if adx_value >= cfg.min_adx else (4 if adx_rising else 0)
        bear_score += 8 if volume_ok else 0
        bear_score += bear_tf * 4
        bull_score, bear_score = min(bull_score, 100.0), min(bear_score, 100.0)

        previous_days = rth[rth["ny_date"] < current_date]
        previous_high = previous_low = previous_close = None
        if not previous_days.empty:
            prev_date = previous_days.iloc[-1]["ny_date"]
            prev = previous_days[previous_days["ny_date"] == prev_date]
            previous_high = float(prev["high"].max())
            previous_low = float(prev["low"].min())
            previous_close = float(prev.iloc[-1]["close"])

        or_rows = current[current["in_or"]]
        or_high = float(or_rows["high"].max()) if not or_rows.empty else None
        or_low = float(or_rows["low"].min()) if not or_rows.empty else None
        last_high = _last_confirmed_pivot(x["high"], cfg.pivot_len, True)
        last_low = _last_confirmed_pivot(x["low"], cfg.pivot_len, False)
        cash_high, cash_low = float(current["high"].max()), float(current["low"].min())

        call_levels = [v for v in (last_high, previous_high, or_high) if v is not None and v > close]
        call_resistance = min(call_levels) if call_levels else cash_high
        put_levels = [v for v in (last_low, previous_low, or_low) if v is not None and v < close]
        put_support = max(put_levels) if put_levels else cash_low
        call_activation = call_resistance + atr_value * cfg.breakout_buffer_atr
        put_activation = put_support - atr_value * cfg.breakout_buffer_atr

        plan_is_call = bull_score >= bear_score
        activation = call_activation if plan_is_call else put_activation
        entry_low = activation if plan_is_call else activation - atr_value * 0.20
        entry_high = activation + atr_value * 0.20 if plan_is_call else activation
        stop = activation - atr_value * cfg.stop_atr if plan_is_call else activation + atr_value * cfg.stop_atr
        risk_distance = atr_value * cfg.stop_atr
        tp1 = activation + risk_distance * cfg.tp1_r if plan_is_call else activation - risk_distance * cfg.tp1_r
        tp2 = activation + risk_distance * cfg.tp2_r if plan_is_call else activation - risk_distance * cfg.tp2_r
        tp3 = activation + risk_distance * cfg.tp3_r if plan_is_call else activation - risk_distance * cfg.tp3_r
        rr = abs(tp2-activation) / max(abs(activation-stop), 1e-9)
        rr_ok = rr >= cfg.minimum_rr

        daily_atr = _daily_atr_previous(daily, cfg.atr_len)
        cash_range = cash_high - cash_low
        atr_budget = cash_range / daily_atr if daily_atr and daily_atr > 0 else None
        atr_budget_exceeded = atr_budget is not None and atr_budget >= cfg.max_atr_budget

        # Pine uses 15m ATR for distance. Primary series is 15m in this port.
        vwap_distance_atr = abs(close-cash_vwap)/atr_value if np.isfinite(cash_vwap) else None
        soft_extended = vwap_distance_atr is not None and vwap_distance_atr >= cfg.soft_vwap_distance_atr
        hard_extended = vwap_distance_atr is not None and vwap_distance_atr >= cfg.hard_vwap_distance_atr
        hard_no_trade = hard_extended or atr_budget_exceeded

        # Reconstruct Pine's dynamic breakout/retest state. Activation levels are
        # series values in Pine, not one frozen latest level, so calculate them bar by bar.
        cur = current.reset_index(drop=True).copy()
        # Last confirmed pivot values as they became knowable (pivotLen bars later).
        all_rth = rth.reset_index(drop=True)
        all_bars = x.reset_index(drop=True)
        last_ph = last_pl = None
        ph_by_timestamp: dict[pd.Timestamp, float | None] = {}
        pl_by_timestamp: dict[pd.Timestamp, float | None] = {}
        highs_all_bars = all_bars["high"].to_numpy(dtype=float)
        lows_all_bars = all_bars["low"].to_numpy(dtype=float)
        p = cfg.pivot_len
        for i in range(len(all_bars)):
            center = i - p
            if center >= p:
                wh = highs_all_bars[center-p:center+p+1]
                wl = lows_all_bars[center-p:center+p+1]
                if len(wh) == 2*p+1 and highs_all_bars[center] >= np.nanmax(wh):
                    last_ph = float(highs_all_bars[center])
                if len(wl) == 2*p+1 and lows_all_bars[center] <= np.nanmin(wl):
                    last_pl = float(lows_all_bars[center])
            ph_by_timestamp[all_bars.iloc[i]["timestamp"]] = last_ph
            pl_by_timestamp[all_bars.iloc[i]["timestamp"]] = last_pl

        prev_sessions: dict[object, tuple[float | None, float | None, float | None]] = {}
        dates = list(dict.fromkeys(all_rth["ny_date"].tolist()))
        for j, d in enumerate(dates):
            if j == 0:
                prev_sessions[d] = (None, None, None)
            else:
                prev_d = dates[j-1]
                prev_rows = all_rth[all_rth["ny_date"] == prev_d]
                prev_sessions[d] = (
                    float(prev_rows["high"].max()), float(prev_rows["low"].min()), float(prev_rows.iloc[-1]["close"])
                )

        call_acts: list[float] = []
        put_acts: list[float] = []
        running_high = running_low = None
        running_or_high = running_or_low = None
        for i, row in cur.iterrows():
            h, l, cl, av = float(row["high"]), float(row["low"]), float(row["close"]), float(row["atr"])
            running_high = h if running_high is None else max(running_high, h)
            running_low = l if running_low is None else min(running_low, l)
            if bool(row["in_or"]):
                running_or_high = h if running_or_high is None else max(running_or_high, h)
                running_or_low = l if running_or_low is None else min(running_or_low, l)
            ph = ph_by_timestamp.get(row["timestamp"])
            pl = pl_by_timestamp.get(row["timestamp"])
            prev_h, prev_l, _ = prev_sessions.get(row["ny_date"], (None, None, None))
            call_candidates = [v for v in (ph, prev_h, running_or_high) if v is not None and v > cl]
            put_candidates = [v for v in (pl, prev_l, running_or_low) if v is not None and v < cl]
            resistance_i = min(call_candidates) if call_candidates else float(running_high)
            support_i = max(put_candidates) if put_candidates else float(running_low)
            call_acts.append(resistance_i + av * cfg.breakout_buffer_atr)
            put_acts.append(support_i - av * cfg.breakout_buffer_atr)

        breakout_direction = 0
        breakout_level = None
        breakout_pos = None
        retested = False
        failed = False
        # State only matters within the active cash session; Pine resets stale pending
        # breakout state naturally via failure/expiry, and this avoids carrying yesterday.
        for i in range(1, len(cur)):
            row = cur.iloc[i]
            prev = cur.iloc[i-1]
            row_atr = float(row["atr"])
            row_rvol = float(row["rvol"]) if pd.notna(row["rvol"]) else np.nan
            row_volume_ok = np.isfinite(row_rvol) and row_rvol >= cfg.min_rvol
            new_call = float(row["close"]) > call_acts[i] and float(prev["close"]) <= call_acts[i-1] and row_volume_ok
            new_put = float(row["close"]) < put_acts[i] and float(prev["close"]) >= put_acts[i-1] and row_volume_ok
            if new_call:
                breakout_direction, breakout_level, breakout_pos = 1, call_acts[i], i
                retested = failed = False
            if new_put:
                breakout_direction, breakout_level, breakout_pos = -1, put_acts[i], i
                retested = failed = False

            if breakout_direction == 1 and not retested and breakout_pos is not None and i > breakout_pos:
                if float(row["low"]) <= breakout_level + row_atr*cfg.retest_tolerance_atr and float(row["close"]) >= breakout_level:
                    retested = True
            if breakout_direction == -1 and not retested and breakout_pos is not None and i > breakout_pos:
                if float(row["high"]) >= breakout_level - row_atr*cfg.retest_tolerance_atr and float(row["close"]) <= breakout_level:
                    retested = True

            if breakout_direction == 1 and float(row["close"]) < breakout_level - row_atr*cfg.retest_tolerance_atr:
                failed, breakout_direction = True, 0
            elif breakout_direction == -1 and float(row["close"]) > breakout_level + row_atr*cfg.retest_tolerance_atr:
                failed, breakout_direction = True, 0

            if breakout_direction != 0 and not retested and breakout_pos is not None and i-breakout_pos > cfg.max_retest_bars:
                breakout_direction = 0

        chase_atr = 0.0
        if breakout_direction == 1 and breakout_level is not None:
            chase_atr = (close-breakout_level)/atr_value
        elif breakout_direction == -1 and breakout_level is not None:
            chase_atr = (breakout_level-close)/atr_value
        chase_too_far = chase_atr > cfg.max_chase_atr

        # Pine's confirmed15Close = prior completed 15m close.
        confirmed15 = float(cur.iloc[-2]["close"]) if len(cur) >= 2 else close
        live15_confirmed = confirmed15 > activation if plan_is_call else confirmed15 < activation
        bull_ready = bull_score >= cfg.min_score and bull_veto_ok and adx_value >= cfg.min_adx and volume_ok and rr_ok and not hard_no_trade and close > float(last["ema20"])
        bear_ready = bear_score >= cfg.min_score and bear_veto_ok and adx_value >= cfg.min_adx and volume_ok and rr_ok and not hard_no_trade and close < float(last["ema20"])
        raw_call = bull_ready and breakout_direction == 1 and retested and not chase_too_far and not soft_extended and live15_confirmed
        raw_put = bear_ready and breakout_direction == -1 and retested and not chase_too_far and not soft_extended and live15_confirmed

        direction = "LONG" if raw_call else "SHORT" if raw_put else "NEUTRAL"
        score = bull_score if direction == "LONG" else bear_score if direction == "SHORT" else max(bull_score, bear_score)
        reasons = [
            f"SPX V20 CALL/PUT Score {bull_score:.0f}/{bear_score:.0f}",
            f"MTF 5/15/60/240 = {trend_states['5']}/{trend_states['15']}/{trend_states['60']}/{trend_states['240']}",
            f"ADX {adx_value:.1f} {'RISING' if adx_rising else 'NOT RISING'}",
            f"SPY RVOL {relative_volume:.2f}x" if np.isfinite(relative_volume) else "SPY RVOL N/A",
            f"Cash VWAP {cash_vwap:.2f}" if np.isfinite(cash_vwap) else "Cash VWAP N/A",
            "Breakout + Retest confirmed" if retested else "Retest not confirmed",
        ]
        if atr_budget_exceeded:
            reasons.append("RTH ATR budget exceeded")
        if soft_extended:
            reasons.append("VWAP extension: wait retest")
        if chase_too_far:
            reasons.append("Price too far: do not chase")

        return {
            "strategy_id": "SPX_V20",
            "strategy_name": "SPX V20",
            "score": round(score, 1),
            "direction": direction,
            "scores": {"V20_CALL": round(bull_score, 1), "V20_PUT": round(bear_score, 1)},
            "reasons": reasons[:8],
            "entry_low": round(entry_low, 2),
            "entry_high": round(entry_high, 2),
            "stop": round(stop, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "tp3": round(tp3, 2),
            "rr": round(rr, 2),
            "atr": round(atr_value, 4),
            "adx": round(adx_value, 2),
            "rvol": round(relative_volume, 2) if np.isfinite(relative_volume) else 0.0,
            "market_regime": "BULL TREND" if bull_tf >= 3 and adx_value >= 25 else "BEAR TREND" if bear_tf >= 3 and adx_value >= 25 else "RANGE/MIXED",
            "quality_flags": [x for x, yes in (("HARD_NO_TRADE", hard_no_trade), ("VWAP_EXTENDED", soft_extended), ("CHASE", chase_too_far)) if yes],
            "last_close": round(close, 4),
            "v20": {
                "bull_score": round(bull_score, 1), "bear_score": round(bear_score, 1),
                "bull_tf": bull_tf, "bear_tf": bear_tf,
                "breakout_direction": breakout_direction, "retested": retested, "failed": failed,
                "atr_budget": round(atr_budget, 3) if atr_budget is not None else None,
                "vwap_distance_atr": round(vwap_distance_atr, 3) if vwap_distance_atr is not None else None,
                "previous_cash_high": previous_high, "previous_cash_low": previous_low, "previous_cash_close": previous_close,
                "opening_range_high": or_high, "opening_range_low": or_low,
            },
        }

    @staticmethod
    def _not_ready(reason: str, x: pd.DataFrame, mtf: dict[str, pd.DataFrame]) -> dict:
        close = float(x.iloc[-1]["close"]) if not x.empty else 0.0
        return {
            "strategy_id": "SPX_V20", "strategy_name": "SPX V20", "score": 0.0,
            "direction": "NEUTRAL", "scores": {"V20_CALL": 0.0, "V20_PUT": 0.0},
            "reasons": [reason], "entry_low": close, "entry_high": close, "stop": close,
            "tp1": close, "tp2": close, "tp3": close, "rr": 0.0, "atr": 0.0,
            "adx": 0.0, "rvol": 0.0, "market_regime": "CLOSED", "quality_flags": ["NO_TRADE"],
            "last_close": close,
        }
