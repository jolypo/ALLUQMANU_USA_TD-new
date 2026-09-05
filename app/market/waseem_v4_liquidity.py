from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import pandas as pd


@dataclass
class V4LiquidityResult:
    score: float
    internal_liquidity_score: float
    external_liquidity_score: float
    liquidity_density_score: float
    volume_acceleration_score: float
    momentum_acceleration_score: float
    compression_score: float
    pre_move_score: float
    flow_confidence: str
    external_target: float | None
    internal_reference: float | None
    diagnostics: list[str]

    def to_dict(self):
        return asdict(self)


class WaseemV4LiquidityEngine:
    """Independent V4 liquidity/pre-move overlay.

    Uses only observable OHLCV structure. It does not pretend that Level-2,
    institutional flow, sweeps or DOM exist when the current feed does not
    provide them. The execution/anti-chase layer remains the V3 entry engine.
    """

    @staticmethod
    def _f(x, default=0.0):
        try:
            v=float(x)
            return v if math.isfinite(v) else default
        except Exception:
            return default

    def evaluate(self, bars: pd.DataFrame | None, direction: str, *, session: str = "RTH") -> V4LiquidityResult:
        if bars is None or len(bars) < 8:
            return V4LiquidityResult(50,50,50,50,50,50,50,50,"LOW",None,None,["OHLCV structure=UNAVAILABLE/INSUFFICIENT"])
        df=bars.copy().tail(30)
        for col in ("open","high","low","close","volume"):
            if col not in df.columns:
                return V4LiquidityResult(50,50,50,50,50,50,50,50,"LOW",None,None,[f"{col}=UNAVAILABLE"])
        close=self._f(df["close"].iloc[-1])
        highs=df["high"].astype(float); lows=df["low"].astype(float); vols=df["volume"].astype(float)
        tr=(highs-lows).abs(); atr=max(float(tr.tail(14).mean()), close*0.001, 1e-9)
        hi5=float(highs.iloc[:-1].tail(5).max()); lo5=float(lows.iloc[:-1].tail(5).min())
        hi20=float(highs.iloc[:-1].tail(20).max()); lo20=float(lows.iloc[:-1].tail(20).min())
        long=str(direction).upper() in {"LONG","CALL","BUY"}
        internal=hi5 if long else lo5; external=hi20 if long else lo20
        dist_internal=abs(internal-close)/atr; dist_external=abs(external-close)/atr
        internal_score=max(0,min(100,100-dist_internal*24))
        external_score=max(0,min(100,100-dist_external*12))
        # density of repeated highs/lows near the target side
        side=highs.iloc[:-1].tail(20) if long else lows.iloc[:-1].tail(20)
        target=external
        density=sum(abs(float(x)-target) <= 0.30*atr for x in side)
        density_score=max(0,min(100,35+density*13))
        recent_vol=max(float(vols.tail(3).mean()),0); prior_vol=max(float(vols.iloc[:-3].tail(7).mean()),1e-9)
        vol_ratio=recent_vol/prior_vol
        volume_score=max(0,min(100,50+(vol_ratio-1)*55))
        r3=(close/self._f(df["close"].iloc[-4],close)-1) if len(df)>=4 else 0
        prev_base=self._f(df["close"].iloc[-7],close) if len(df)>=7 else close
        prev_end=self._f(df["close"].iloc[-4],close)
        prev3=(prev_end/prev_base-1) if prev_base else 0
        signed=(r3-prev3)*(1 if long else -1)
        momentum_score=max(0,min(100,50+signed*5000))
        range5=max(float(highs.tail(5).max()-lows.tail(5).min()),1e-9)
        range20=max(float(highs.tail(20).max()-lows.tail(20).min()),range5)
        compression_ratio=range5/range20
        compression_score=max(0,min(100,100-compression_ratio*90))
        pre_move=0.22*external_score+0.18*density_score+0.24*volume_score+0.22*momentum_score+0.14*compression_score
        score=0.18*internal_score+0.20*external_score+0.16*density_score+0.18*volume_score+0.16*momentum_score+0.12*compression_score
        confidence="MEDIUM" if str(session).upper()=="GTH" else "HIGH"
        diagnostics=[
            f"internal_liquidity={internal:.2f} ({internal_score:.1f}/100)",
            f"external_liquidity={external:.2f} ({external_score:.1f}/100)",
            f"liquidity_density_hits={density} ({density_score:.1f}/100)",
            f"volume_acceleration={vol_ratio:.2f}x ({volume_score:.1f}/100)",
            f"momentum_acceleration={signed:+.4f} ({momentum_score:.1f}/100)",
            f"range_compression={compression_ratio:.2f} ({compression_score:.1f}/100)",
            f"pre_move={pre_move:.1f}/100",
            f"flow_confidence={confidence}; Level2/DOM/institutional flow not assumed",
        ]
        return V4LiquidityResult(round(score,1),round(internal_score,1),round(external_score,1),round(density_score,1),round(volume_score,1),round(momentum_score,1),round(compression_score,1),round(pre_move,1),confidence,round(external,2),round(internal,2),diagnostics)
