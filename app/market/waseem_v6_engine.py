from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import pandas as pd

from app.utils.indicators import add_indicators

NY = ZoneInfo('America/New_York')


def _f(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _clip(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


@dataclass
class V6Result:
    score: float
    session: str
    delayed_data: bool
    freshness_score: float
    multi_timeframe_score: float
    room_to_target_score: float
    momentum_decay_score: float
    late_entry_score: float
    breakout_quality_score: float
    reversal_risk_score: float
    ict_score: float
    fibonacci_score: float
    cross_state: str
    cross_score: float
    no_trade: bool
    ready: bool
    watch_reason: str
    next_target: float | None
    nearest_support: float | None
    nearest_resistance: float | None
    diagnostics: list[str]

    def to_dict(self):
        return asdict(self)


class WaseemV6Engine:
    """Independent delayed/session-aware structure engine.

    V6 does not alter V2/V3/V4/V5. It consumes their candidate and adds
    structure/ICT/Fibonacci/late-entry/reversal controls. Order-flow evidence
    remains secondary whenever the feed is delayed or indicative.
    """

    @staticmethod
    def session(now: datetime | None = None) -> str:
        now = (now or datetime.now(timezone.utc)).astimezone(NY)
        minute = now.hour * 60 + now.minute
        if 4 * 60 <= minute < 9 * 60 + 30:
            return 'PREMARKET'
        if 9 * 60 + 30 <= minute < 10 * 60:
            return 'OPENING'
        if 10 * 60 <= minute < 12 * 60:
            return 'RTH_MORNING'
        if 12 * 60 <= minute < 14 * 60 + 30:
            return 'MIDDAY'
        if 14 * 60 + 30 <= minute < 15 * 60:
            return 'RTH_AFTERNOON'
        if 15 * 60 <= minute < 16 * 60:
            return 'POWER_HOUR'
        if 16 * 60 <= minute < 20 * 60:
            return 'AFTER_HOURS'
        return 'CLOSED'

    @staticmethod
    def _frame_bias(frame: dict, current: float, long: bool) -> float:
        if not frame or not frame.get('available'):
            return 50.0
        supports = frame.get('supports') or []
        resistances = frame.get('resistances') or []
        nearest_s = max([x for x in supports if x < current], default=None)
        nearest_r = min([x for x in resistances if x > current], default=None)
        if long:
            if nearest_r is None:
                return 60.0
            room = (nearest_r-current)/max(current, 1e-9)*100
            return _clip(48 + room*18)
        if nearest_s is None:
            return 60.0
        room = (current-nearest_s)/max(current, 1e-9)*100
        return _clip(48 + room*18)

    @staticmethod
    def _cross(bars: pd.DataFrame | None, long: bool) -> tuple[str, float]:
        if bars is None or len(bars) < 30:
            return 'UNAVAILABLE', 50.0
        ind = add_indicators(bars.copy())
        row = ind.iloc[-1]
        prev = ind.iloc[-2]
        ema9 = _f(row.get('ema9')); ema20 = _f(row.get('ema20'))
        p9 = _f(prev.get('ema9')); p20 = _f(prev.get('ema20'))
        hist = _f(row.get('macd_hist'), 0.0)
        if None in (ema9, ema20, p9, p20):
            return 'UNAVAILABLE', 50.0
        pos_cross = p9 <= p20 and ema9 > ema20
        neg_cross = p9 >= p20 and ema9 < ema20
        bullish = ema9 > ema20 and hist >= 0
        bearish = ema9 < ema20 and hist <= 0
        if pos_cross:
            state, raw = 'POSITIVE_CROSS', 88.0
        elif neg_cross:
            state, raw = 'NEGATIVE_CROSS', 12.0
        elif bullish:
            state, raw = 'POSITIVE', 72.0
        elif bearish:
            state, raw = 'NEGATIVE', 28.0
        else:
            state, raw = 'NEUTRAL', 50.0
        return state, raw if long else 100.0-raw

    def plan_from_intelligence(self, intelligence: dict, bars15: pd.DataFrame | None = None) -> dict:
        """Build an underlying-first V6 plan without selecting an option contract.

        Used before the regular session. It is intentionally non-executable: the
        option chain is re-checked only after RTH opens.
        """
        current=_f(intelligence.get("current"),0.0) or 0.0
        trend_score=_f(intelligence.get("trend_score"),50.0)
        support=_f(intelligence.get("nearest_support"))
        resistance=_f(intelligence.get("nearest_resistance"))
        target=_f(intelligence.get("target"))
        atr=max(_f(intelligence.get("atr"), max(current*0.01,0.01)) or 0.01,0.01)
        momentum=_f(intelligence.get("momentum5_pct"),0.0)
        rvol=_f(intelligence.get("rvol"),1.0)
        vwap=_f(intelligence.get("vwap"))
        bull_room=((target or resistance or current)-current)/atr if current else 0.0
        bear_room=(current-(support if support is not None else current))/atr if current else 0.0
        bull=trend_score + max(-12,min(12,momentum*4)) + max(-5,min(8,(rvol-1)*8)) + max(-5,min(12,bull_room*8))
        bear=(100-trend_score) + max(-12,min(12,-momentum*4)) + max(-5,min(8,(rvol-1)*8)) + max(-5,min(12,bear_room*8))
        direction='CALL' if bull>=bear else 'PUT'
        confidence=_clip(max(bull,bear))
        trigger=resistance if direction=='CALL' else support
        invalidation=support if direction=='CALL' else resistance
        objective=target if direction=='CALL' else support
        cross_state,cross_score=self._cross(bars15,direction=='CALL')
        ict=intelligence.get('ict') or {}; fib=intelligence.get('fib') or {}
        return {
            'session':self.session(),'direction':direction,'confidence':round(confidence,1),
            'current':current,'support':support,'resistance':resistance,'trigger':trigger,
            'invalidation':invalidation,'target':objective,'target_horizon':intelligence.get('target_horizon'),
            'target_confirmation':intelligence.get('target_confirmation'),'atr':_f(intelligence.get('atr')),
            'rvol':rvol,'vwap':vwap,'momentum5_pct':momentum,'trend':intelligence.get('trend'),
            'ict':ict,'fib':fib,'cross_state':cross_state,'cross_score':round(cross_score,1),
            'frames':intelligence.get('frames') or [],
            'note':'Pre-market plan only. Re-scan option chain after RTH open before any READY contract.'
        }

    def evaluate(self, *, signal, intelligence: dict, bars15: pd.DataFrame | None,
                 data_age_minutes: float | None, delayed_threshold: float = 5.0,
                 flow_score: float | None = None, flow_confidence: str | None = None, ready_floor: float = 88.0, force_delayed: bool = False) -> V6Result:
        option = dict(getattr(signal, 'option', {}) or {})
        direction = str(option.get('option_type') or option.get('type') or getattr(signal, 'direction', '')).upper()
        long = direction in {'CALL','LONG','BUY','C'}
        current = _f(intelligence.get('current'), _f(option.get('underlying_current_price'), _f(getattr(signal, 'current_price', None))))
        current = current or 0.0
        session = self.session()
        delayed = bool(force_delayed or data_age_minutes is None or float(data_age_minutes) > delayed_threshold)
        freshness = 25.0 if data_age_minutes is None else _clip(100 - max(0.0, float(data_age_minutes)-1.0)*4.0)

        frames = intelligence.get('frames') or []
        weights = {'15m':0.22,'1h':0.24,'4h':0.24,'1d':0.18,'1w':0.08,'1mo':0.04}
        mtf_parts=[]
        for fr in frames:
            if fr.get('key') in weights:
                mtf_parts.append((self._frame_bias(fr,current,long), weights[fr.get('key')]))
        mtf = sum(v*w for v,w in mtf_parts)/max(sum(w for _,w in mtf_parts),1e-9) if mtf_parts else 50.0

        support = _f(intelligence.get('nearest_support'))
        resistance = _f(intelligence.get('nearest_resistance'))
        target = _f(intelligence.get('target'))
        atr = _f(intelligence.get('atr'))
        if long:
            room = ((target or resistance or current)-current) if current else 0
        else:
            room = current-((support if support is not None else current))
        room_atr = room/max(atr or max(current*0.01,0.01),1e-9)
        room_score = _clip(35 + room_atr*45)

        momentum = _f(intelligence.get('momentum5_pct'), 0.0)
        rvol = _f(intelligence.get('rvol'), 1.0)
        vwap = _f(intelligence.get('vwap'))
        if bars15 is not None and len(bars15) >= 30:
            ind = add_indicators(bars15.copy())
            row = ind.iloc[-1]
            prev = ind.iloc[-4] if len(ind) >= 4 else ind.iloc[-2]
            hist_now = _f(row.get('macd_hist'),0.0); hist_prev = _f(prev.get('macd_hist'),0.0)
            mom_now = _f(row.get('momentum5_pct'),0.0); mom_prev = _f(prev.get('momentum5_pct'),0.0)
            signed_decay = ((mom_now-mom_prev) + (hist_now-hist_prev)*100.0) * (1 if long else -1)
            momentum_decay = _clip(50 + signed_decay*8)
            vwap_dist = abs(_f(row.get('vwap_distance_pct'),0.0))
            atr_pct = max(_f(row.get('atr_pct'),1.0),0.01)
            extension = vwap_dist/atr_pct
            late_entry = _clip(100-extension*28)
            adx = _f(row.get('adx'),20.0)
            rvol_weight = 32.0 if session in {'OPENING','RTH_MORNING','RTH_AFTERNOON','POWER_HOUR'} else 20.0 if session=='MIDDAY' else 12.0
            breakout = _clip(45 + (rvol-1.0)*rvol_weight + max(0,adx-18)*1.3 + max(0,momentum_decay-50)*0.25)
        else:
            momentum_decay=50.0; late_entry=55.0; breakout=50.0

        # Reversal risk rises when extended, room is small, momentum is decaying,
        # and price is pressing the next opposing structure.
        reversal = _clip(100 - (0.36*late_entry + 0.30*room_score + 0.34*momentum_decay))

        ict = intelligence.get('ict') or {}
        ict_points = 50.0
        if long:
            if ict.get('bullish_ob'): ict_points += 10
            if ict.get('bullish_fvg'): ict_points += 8
            if resistance and current and resistance > current: ict_points += 7
            if support and current and support < current: ict_points += 7
        else:
            if ict.get('bearish_ob'): ict_points += 10
            if ict.get('bearish_fvg'): ict_points += 8
            if support and current and support < current: ict_points += 7
            if resistance and current and resistance > current: ict_points += 7
        ict_score=_clip(ict_points)

        fib=intelligence.get('fib') or {}
        fib_score=50.0
        if fib:
            fib_dir=str(fib.get('direction') or '')
            fib_score = 72.0 if (long and fib_dir=='UP') or ((not long) and fib_dir=='DOWN') else 38.0
        cross_state,cross_score=self._cross(bars15,long)

        # Session penalty: extended sessions can inform context, but READY is more conservative.
        session_penalty = 0.0 if session in {'RTH_MORNING','RTH_AFTERNOON','POWER_HOUR'} else 2.0 if session=='OPENING' else 4.0 if session=='MIDDAY' else 6.0 if session in {'PREMARKET','AFTER_HOURS'} else 10.0
        delay_penalty = 10.0 if delayed else 0.0
        # Flow is deliberately low-weight under delayed data.
        fs=_f(flow_score,50.0)
        flow_weight = 0.03 if delayed else 0.08
        core_weights = [
            (mtf,0.17),(room_score,0.16),(momentum_decay,0.17),(late_entry,0.13),
            (breakout,0.10),(100-reversal,0.09),(ict_score,0.07),(fib_score,0.04),(cross_score,0.04),
        ]
        base=sum(v*w for v,w in core_weights)+fs*flow_weight
        total=sum(w for _,w in core_weights)+flow_weight
        score=_clip(base/max(total,1e-9)-session_penalty-delay_penalty)

        # NO TRADE when structure is boxed-in/mixed or reversal risk is too high.
        no_trade = bool(room_score < 42 or (47 <= mtf <= 53 and breakout < 55) or reversal > 72)
        ready_session = session in {'OPENING','RTH_MORNING','MIDDAY','RTH_AFTERNOON','POWER_HOUR'}
        ready = bool(ready_session and not no_trade and score >= float(ready_floor) and room_score >= 55 and momentum_decay >= 52 and late_entry >= 48 and reversal <= 55 and freshness >= 35)
        if session == 'PREMARKET':
            reason='PREMARKET_WATCH: build underlying plan now; confirm contract only after RTH open'
            ready=False
        elif session == 'AFTER_HOURS':
            reason='AFTER_HOURS_WATCH: context only; rebuild executable contract after RTH open'
            ready=False
        elif ready:
            reason='READY: structure + room + momentum + entry are aligned'
        elif no_trade:
            reason='NO_TRADE: insufficient room / mixed structure / elevated reversal risk'
        elif delayed:
            reason='WATCH: delayed-data safety gate; wait for stronger structure/entry'
        elif late_entry < 48:
            reason='WATCH: late-entry/chase risk'
        elif momentum_decay < 52:
            reason='WATCH: momentum is not accelerating enough'
        else:
            reason='WATCH: V6 confirmation incomplete'

        diagnostics=[
            f'session={session}',f'data_age_minutes={data_age_minutes}',f'delayed={delayed}',
            f'mtf={mtf:.1f}',f'room_to_target={room_score:.1f}',f'momentum_decay={momentum_decay:.1f}',
            f'late_entry={late_entry:.1f}',f'breakout_quality={breakout:.1f}',f'reversal_risk={reversal:.1f}',
            f'ict={ict_score:.1f}',f'fibonacci={fib_score:.1f}',f'cross={cross_state}/{cross_score:.1f}',
            f'order_flow_weight={flow_weight:.2f}; confidence={flow_confidence or "UNAVAILABLE"}',
        ]
        return V6Result(round(score,1),session,delayed,round(freshness,1),round(mtf,1),round(room_score,1),
                        round(momentum_decay,1),round(late_entry,1),round(breakout,1),round(reversal,1),
                        round(ict_score,1),round(fib_score,1),cross_state,round(cross_score,1),no_trade,ready,
                        reason,target,support,resistance,diagnostics)
