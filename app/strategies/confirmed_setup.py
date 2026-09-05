from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class SetupConfirmation:
    ready: bool
    state: str
    path: str | None
    direction: str
    breakout_level: float | None
    breakout_index: int | None
    hold_confirmed: bool
    retest_confirmed: bool
    structure_confirmed: bool
    momentum_confirmed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConfirmedSetupEngine:
    """Strict breakout/breakdown confirmation used only by Confirmed Setup mode.

    The legacy Core and SPX V20 paths do not call this engine. A setup becomes
    READY only after a recent structural break plus either hold or retest, and
    directional momentum confirmation. The logic is intentionally symmetric
    for bullish/CALL and bearish/PUT setups.
    """

    def __init__(self, lookback: int = 20, breakout_window: int = 4, hold_bars: int = 2):
        self.lookback = max(8, int(lookback))
        self.breakout_window = max(2, int(breakout_window))
        self.hold_bars = max(1, int(hold_bars))

    @staticmethod
    def _series(df: pd.DataFrame, name: str) -> pd.Series:
        for key in (name, name.lower(), name.upper(), name.capitalize()):
            if key in df.columns:
                return pd.to_numeric(df[key], errors="coerce")
        raise KeyError(name)

    def evaluate(self, df: pd.DataFrame, direction: str, analysis: dict | None = None) -> SetupConfirmation:
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return SetupConfirmation(False, "WAIT_DIRECTION", None, direction, None, None, False, False, False, False, "الاتجاه غير محسوم")
        if df is None or len(df) < self.lookback + self.breakout_window + 3:
            return SetupConfirmation(False, "WAIT_DATA", None, direction, None, None, False, False, False, False, "بيانات غير كافية لتأكيد البنية")

        try:
            high = self._series(df, "high")
            low = self._series(df, "low")
            close = self._series(df, "close")
            volume = self._series(df, "volume") if any(str(c).lower() == "volume" for c in df.columns) else None
        except Exception:
            return SetupConfirmation(False, "WAIT_DATA", None, direction, None, None, False, False, False, False, "أعمدة OHLC غير مكتملة")

        if close.isna().tail(self.lookback + self.breakout_window).any():
            return SetupConfirmation(False, "WAIT_DATA", None, direction, None, None, False, False, False, False, "بيانات سعر غير مكتملة")

        n = len(df)
        recent_start = n - self.breakout_window
        reference_end = recent_start
        reference_start = max(0, reference_end - self.lookback)
        if reference_end - reference_start < 8:
            return SetupConfirmation(False, "WAIT_DATA", None, direction, None, None, False, False, False, False, "سجل البنية قصير")

        if direction == "LONG":
            level = float(high.iloc[reference_start:reference_end].max())
            broken = [i for i in range(recent_start, n) if float(close.iloc[i]) > level]
        else:
            level = float(low.iloc[reference_start:reference_end].min())
            broken = [i for i in range(recent_start, n) if float(close.iloc[i]) < level]

        if not broken:
            state = "WAIT_BREAKOUT" if direction == "LONG" else "WAIT_BREAKDOWN"
            return SetupConfirmation(False, state, None, direction, round(level, 4), None, False, False, False, False, "بانتظار كسر بنيوي حديث")

        bidx = broken[0]
        post = list(range(bidx, n))
        atr = float((high - low).tail(14).mean() or 0.0)
        tolerance = max(abs(level) * 0.0015, atr * 0.18, 1e-9)

        tail_idx = list(range(max(bidx, n - self.hold_bars), n))
        if direction == "LONG":
            hold = len(tail_idx) >= self.hold_bars and all(float(close.iloc[i]) >= level - tolerance for i in tail_idx)
            retest = any(float(low.iloc[i]) <= level + tolerance and float(close.iloc[i]) > level for i in post[1:])
            # Simple higher-low / continuation structure after the break.
            structure = float(close.iloc[-1]) > level and float(low.iloc[-1]) >= min(float(low.iloc[bidx]), level - tolerance)
            mom3 = float(close.iloc[-1]) - float(close.iloc[max(0, n - 4)])
            momentum = mom3 > 0
        else:
            hold = len(tail_idx) >= self.hold_bars and all(float(close.iloc[i]) <= level + tolerance for i in tail_idx)
            retest = any(float(high.iloc[i]) >= level - tolerance and float(close.iloc[i]) < level for i in post[1:])
            structure = float(close.iloc[-1]) < level and float(high.iloc[-1]) <= max(float(high.iloc[bidx]), level + tolerance)
            mom3 = float(close.iloc[-1]) - float(close.iloc[max(0, n - 4)])
            momentum = mom3 < 0

        a = analysis or {}
        adx = float(a.get("adx", 0.0) or 0.0)
        rvol = float(a.get("rvol", 0.0) or 0.0)
        trend_active = bool(a.get("trend_active", False))
        # The setup's own price momentum is mandatory. Existing strategy context
        # can strengthen it but never flip bullish vs bearish asymmetrically.
        momentum = bool(momentum and (trend_active or adx >= 18.0 or rvol >= 1.0 or not a))

        confirmed_path = "RETEST" if retest else "HOLD" if hold else None
        if confirmed_path is None:
            return SetupConfirmation(False, "WAIT_HOLD_OR_RETEST", None, direction, round(level, 4), bidx, hold, retest, structure, momentum, "تم الكسر؛ بانتظار الثبات أو إعادة الاختبار")
        if not structure:
            return SetupConfirmation(False, "WAIT_STRUCTURE", confirmed_path, direction, round(level, 4), bidx, hold, retest, False, momentum, "الكسر موجود لكن البنية لم تتأكد")
        if not momentum:
            return SetupConfirmation(False, "WAIT_MOMENTUM", confirmed_path, direction, round(level, 4), bidx, hold, retest, True, False, "البنية مؤكدة لكن الزخم لم يؤكد الاستمرار")

        return SetupConfirmation(True, "CONFIRMED", confirmed_path, direction, round(level, 4), bidx, hold, retest, True, True, "Breakout/Breakdown + confirmation مكتمل")
