from __future__ import annotations
import math
import pandas as pd
from app.config import settings
from app.utils.indicators import add_indicators


def _clip(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


class StrategyEngine:
    """Weighted technical engine. Indicators are grouped to reduce double counting."""

    def analyze(self, raw: pd.DataFrame) -> dict:
        df = add_indicators(raw).dropna(subset=["close", "ema20", "ema50", "atr"])
        if len(df) < 3:
            raise ValueError("insufficient indicator rows")
        r = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(r.close)
        atr = float(r.atr) if pd.notna(r.atr) and float(r.atr) > 0 else max(close * 0.02, 0.01)
        scores: dict[str, float] = {}
        reasons: list[str] = []

        # Trend: EMA stack + ADX strength.
        trend = 50.0
        bullish_stack = close > r.ema9 > r.ema20 > r.ema50
        bearish_stack = close < r.ema9 < r.ema20 < r.ema50
        if bullish_stack:
            trend += 28; reasons.append("EMA 9/20/50 صاعدة")
        elif close > r.ema20 > r.ema50:
            trend += 18
        if bearish_stack:
            trend -= 28; reasons.append("EMA 9/20/50 هابطة")
        elif close < r.ema20 < r.ema50:
            trend -= 18
        if pd.notna(r.ema200):
            if r.ema50 > r.ema200 and close > r.ema200: trend += 10
            elif r.ema50 < r.ema200 and close < r.ema200: trend -= 10
        adx_val = float(r.adx) if pd.notna(r.adx) else 0.0
        if adx_val >= settings.strong_adx:
            trend += 12 if close > r.ema20 else -12
            reasons.append(f"ADX قوي {adx_val:.1f}")
        elif adx_val < settings.min_adx_trend:
            trend += -8 if close > r.ema20 else 8
        scores["Trend"] = _clip(trend)

        # Momentum: RSI location+slope, MACD histogram slope, 5-bar ROC.
        momentum = 50.0
        rsi_val = float(r.rsi)
        rsi_slope = float(r.rsi_slope) if pd.notna(r.rsi_slope) else 0.0
        macd_hist = float(r.macd_hist) if pd.notna(r.macd_hist) else 0.0
        macd_slope = float(r.macd_hist_slope) if pd.notna(r.macd_hist_slope) else 0.0
        mom5 = float(r.momentum5_pct) if pd.notna(r.momentum5_pct) else 0.0
        if 52 <= rsi_val <= 72: momentum += 13
        elif 28 <= rsi_val <= 48: momentum -= 13
        if rsi_slope > 0: momentum += 7
        elif rsi_slope < 0: momentum -= 7
        if macd_hist > 0: momentum += 12
        else: momentum -= 12
        if macd_slope > 0: momentum += 8
        elif macd_slope < 0: momentum -= 8
        momentum += max(-10, min(10, mom5 * 2.0))
        scores["Momentum"] = _clip(momentum)
        if macd_hist > 0 and macd_slope > 0:
            reasons.append("MACD Histogram يتسارع صعودًا")
        elif macd_hist < 0 and macd_slope < 0:
            reasons.append("MACD Histogram يتسارع هبوطًا")

        # Participation/volume.
        rvol = float(r.rvol) if pd.notna(r.rvol) else 0.0
        volume_slope = float(r.volume_slope) if pd.notna(r.volume_slope) else 0.0
        volume = 45.0
        if rvol >= 1.5: volume += 35; reasons.append(f"Relative Volume قوي {rvol:.2f}x")
        elif rvol >= settings.min_rvol_breakout: volume += 22; reasons.append(f"Relative Volume {rvol:.2f}x")
        elif rvol < 0.8: volume -= 15
        if volume_slope > 0: volume += 10
        scores["Volume"] = _clip(volume)

        high20 = float(r.high20) if pd.notna(r.high20) else float(df.high.iloc[-21:-1].max())
        low20 = float(r.low20) if pd.notna(r.low20) else float(df.low.iloc[-21:-1].min())
        structure = 50.0
        if close > high20:
            structure += 30
            reasons.append("اختراق مقاومة حديثة بإغلاق")
            if rvol >= settings.min_rvol_breakout: structure += 10
        elif close < low20:
            structure -= 30
            reasons.append("كسر دعم حديث بإغلاق")
            if rvol >= settings.min_rvol_breakout: structure -= 10
        else:
            midpoint = (high20 + low20) / 2
            structure += 12 if close > midpoint else -12
        # HH/HL or LH/LL sequence.
        highs = df.high.iloc[-4:].astype(float).tolist()
        lows = df.low.iloc[-4:].astype(float).tolist()
        if all(a < b for a, b in zip(highs, highs[1:])) and all(a < b for a, b in zip(lows, lows[1:])):
            structure += 12; reasons.append("HH/HL صاعد")
        if all(a > b for a, b in zip(highs, highs[1:])) and all(a > b for a, b in zip(lows, lows[1:])):
            structure -= 12; reasons.append("LH/LL هابط")
        scores["Structure"] = _clip(structure)

        # VWAP: useful intraday, bounded contribution.
        vwap_score = 50.0
        if pd.notna(r.vwap) and float(r.vwap) > 0:
            dist = float(r.vwap_distance_pct) if pd.notna(r.vwap_distance_pct) else 0.0
            vwap_score += 22 if close > r.vwap else -22
            if abs(dist) > 3.5:  # avoid chasing far from VWAP
                vwap_score -= 8 if close > r.vwap else -8
        scores["VWAP"] = _clip(vwap_score)

        # Volatility quality: ATR% too low means dead tape; too high means unstable.
        atr_pct = float(r.atr_pct) if pd.notna(r.atr_pct) else 0.0
        atr_hist = pd.to_numeric(df.get("atr_pct"), errors="coerce").dropna().tail(60)
        atr_median = float(atr_hist.median()) if not atr_hist.empty else 0.0
        atr_regime_ratio = (atr_pct / atr_median) if atr_median > 0 else 1.0
        volq = 65.0
        if atr_pct < 0.35: volq -= 20
        elif atr_pct > 8.0: volq -= 18
        elif 0.7 <= atr_pct <= 4.5: volq += 15
        scores["Volatility"] = _clip(volq)

        # ICT-style features remain secondary, not decisive.
        ict = 50.0
        recent_low = float(df.low.iloc[-6:-1].min())
        recent_high = float(df.high.iloc[-6:-1].max())
        bullish_liquidity_sweep = float(r.low) < recent_low and close > recent_low
        bearish_liquidity_sweep = float(r.high) > recent_high and close < recent_high
        bos_up = close > recent_high
        bos_down = close < recent_low
        bullish_fvg = len(df) >= 3 and float(df.low.iloc[-1]) > float(df.high.iloc[-3])
        bearish_fvg = len(df) >= 3 and float(df.high.iloc[-1]) < float(df.low.iloc[-3])
        if bullish_liquidity_sweep:
            ict += 15; reasons.append("Liquidity Sweep صاعد")
        if bearish_liquidity_sweep:
            ict -= 15; reasons.append("Liquidity Sweep هابط")
        if bos_up: ict += 15; reasons.append("BOS صاعد")
        if bos_down: ict -= 15; reasons.append("BOS هابط")
        if bullish_fvg: ict += 7; reasons.append("Bullish FVG")
        if bearish_fvg: ict -= 7; reasons.append("Bearish FVG")
        scores["ICT"] = _clip(ict)

        # Direction and quality are intentionally separated. Volume and ATR
        # quality describe whether a move is tradable; they must not push the
        # engine toward CALL merely because participation is strong.
        directional_weights = {
            "Trend": 0.30,
            "Structure": 0.24,
            "Momentum": 0.22,
            "VWAP": 0.12,
            "ICT": 0.12,
        }
        bull_directional = sum(
            directional_weights[name] * scores[name]
            for name in directional_weights
        )
        bear_directional = sum(
            directional_weights[name] * (100.0 - scores[name])
            for name in directional_weights
        )

        quality_score = 0.55 * scores["Volume"] + 0.45 * scores["Volatility"]
        # Quality is a modifier around a neutral 50, not a directional vote.
        # Good liquidity/volatility can strengthen either CALL or PUT equally;
        # poor quality can weaken either side equally.
        quality_adjustment = 0.35 * (quality_score - 50.0)
        bull_score = _clip(bull_directional + quality_adjustment)
        bear_score = _clip(bear_directional + quality_adjustment)
        directional_gap = abs(bull_directional - bear_directional)

        # Require both directional conviction and a meaningful advantage over
        # the opposite thesis. Tiny EMA stacking inside dead/ranging tape must
        # not become a fake CALL/PUT. A low-ADX setup needs a genuine structural
        # break confirmed by participation before direction is activated.
        min_directional = 60.0
        min_gap = 8.0
        structural_impulse = (
            (scores["Structure"] >= 70.0 or scores["Structure"] <= 30.0)
            and rvol >= settings.min_rvol_breakout
        )
        trend_active = adx_val >= settings.min_adx_trend or abs(mom5) >= 0.50 or structural_impulse
        if (
            trend_active
            and bull_directional >= min_directional
            and bull_directional > bear_directional
            and directional_gap >= min_gap
        ):
            direction = "LONG"
            unified = bull_score
        elif (
            trend_active
            and bear_directional >= min_directional
            and bear_directional > bull_directional
            and directional_gap >= min_gap
        ):
            direction = "SHORT"
            unified = bear_score
        else:
            direction = "NEUTRAL"
            unified = max(bull_score, bear_score)

        # Hard quality guard: weak trend + weak participation should not masquerade as READY trend signal.
        weak_trend = adx_val < settings.min_adx_trend
        weak_volume = rvol < 0.85
        quality_flags = []
        if weak_trend: quality_flags.append("WEAK_ADX")
        if weak_volume: quality_flags.append("WEAK_VOLUME")

        if direction == "LONG":
            entry_low, entry_high = close - 0.12 * atr, close + 0.08 * atr
            stop = min(low20, close - 1.15 * atr)
            risk = max(entry_high - stop, atr * 0.5)
            tp1, tp2, tp3 = entry_high + risk * 1.5, entry_high + risk * 2.0, entry_high + risk * 2.8
        elif direction == "SHORT":
            entry_low, entry_high = close - 0.08 * atr, close + 0.12 * atr
            stop = max(high20, close + 1.15 * atr)
            risk = max(stop - entry_low, atr * 0.5)
            tp1, tp2, tp3 = entry_low - risk * 1.5, entry_low - risk * 2.0, entry_low - risk * 2.8
        else:
            entry_low = entry_high = stop = tp1 = tp2 = tp3 = close
            risk = 0.0

        return {
            "score": round(unified, 1),
            "direction": direction,
            "bull_score": round(bull_score, 1),
            "bear_score": round(bear_score, 1),
            "quality_score": round(quality_score, 1),
            "directional_gap": round(directional_gap, 1),
            "trend_active": bool(trend_active),
            "scores": {k: round(v, 1) for k, v in scores.items()},
            "reasons": reasons[:8],
            "entry_low": round(entry_low, 2),
            "entry_high": round(entry_high, 2),
            "stop": round(stop, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "tp3": round(tp3, 2),
            "rr": 2.0 if risk > 0 else 0.0,
            "atr": round(atr, 4),
            "atr_pct": round(atr_pct, 4),
            "atr_regime_ratio": round(atr_regime_ratio, 3),
            "adx": round(adx_val, 2),
            "rvol": round(rvol, 2),
            "return20_pct": round(float(r.return20_pct), 2) if pd.notna(r.return20_pct) else 0.0,
            "quality_flags": quality_flags,
            "last_close": round(close, 4),
        }
