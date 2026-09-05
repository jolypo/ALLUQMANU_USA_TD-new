from datetime import datetime
from zoneinfo import ZoneInfo

from app.market.waseem_v6_engine import WaseemV6Engine
from app.telegram.message_templates import candidate_full_text

NY = ZoneInfo('America/New_York')


def test_v6_session_lifecycle_is_time_aware():
    eng = WaseemV6Engine()
    assert eng.session(datetime(2026, 9, 8, 8, 0, tzinfo=NY)) == 'PREMARKET'
    assert eng.session(datetime(2026, 9, 8, 9, 35, tzinfo=NY)) == 'OPENING'
    assert eng.session(datetime(2026, 9, 8, 10, 30, tzinfo=NY)) == 'RTH_MORNING'
    assert eng.session(datetime(2026, 9, 8, 12, 30, tzinfo=NY)) == 'MIDDAY'
    assert eng.session(datetime(2026, 9, 8, 15, 30, tzinfo=NY)) == 'POWER_HOUR'


def test_v6_premarket_plan_is_underlying_only():
    eng = WaseemV6Engine()
    intel = {
        'current': 100.0, 'trend_score': 72.0, 'nearest_support': 98.5,
        'nearest_resistance': 101.0, 'target': 103.0, 'target_horizon': 'يومي',
        'target_confirmation': 'إغلاق شمعة 15 دقيقة', 'atr': 2.5,
        'rvol': 1.4, 'vwap': 99.4, 'momentum5_pct': 0.7, 'trend': 'صاعد',
        'ict': {'buy_side': 101.0, 'sell_side': 98.5}, 'fib': {'direction': 'UP'},
        'frames': [],
    }
    plan = eng.plan_from_intelligence(intel, None)
    assert plan['direction'] == 'CALL'
    assert plan['trigger'] == 101.0
    assert plan['target'] == 103.0
    assert 'Re-scan option chain after RTH open' in plan['note']


def test_v6_contract_message_has_execution_details():
    trade = {
        'symbol':'NVDA','direction':'LONG','decision':'WATCH','score':91.0,'required_score':88.0,
        'entry_low':3.0,'entry_high':3.2,'stop':2.5,'tp1':3.8,'tp2':4.2,'tp3':4.8,'rr':2.0,
        'current_price':108.0,'market_state':'WASEEM_V6_WATCH','liquidity_state':'HIGH','volatility_state':'NORMAL',
        'option':{
            'strategy_mode':'WASEEM_V6','engine_source':'Waseem V6','type':'CALL','strike':110,
            'expiration':'2026-09-11','dte':3,'horizon':'WEEKLY','current_contract_price':3.1,
            'bid':3.0,'ask':3.2,'mid':3.1,'spread_pct':6.45,'volume':1200,'open_interest':5400,
            'delta':0.44,'gamma':0.05,'theta':-0.19,'vega':0.11,'iv':0.41,'contract_score':90,
            'underlying_current_price':108.0,'v6_score':91,'v6_session':'RTH_MORNING',
            'v6_delayed_data':True,'v6_multi_timeframe_score':82,'v6_room_to_target_score':78,
            'v6_momentum_decay_score':74,'v6_late_entry_score':70,'v6_breakout_quality_score':75,
            'v6_reversal_risk_score':30,'v6_ict_score':80,'v6_fibonacci_score':72,
            'v6_cross_state':'POSITIVE_CROSS','v6_cross_score':88,'v6_nearest_support':106.5,
            'v6_nearest_resistance':109.0,'v6_next_target':111.5,'v6_projected_underlying_move':3.5,
            'v6_projected_contract_price':4.64,'v6_projected_contract_gain_pct':49.7,
            'v6_contract_confirmed_after_open':True,'v6_phase':'CONTRACT_CONFIRMATION',
            'v6_watch_reason':'WATCH: delayed-data safety gate','v5_order_flow_score':65,'v5_flow_confidence':'LOW',
        }
    }
    text = candidate_full_text(trade, 180)
    assert 'فحص العقد التنفيذي — V6' in text
    assert 'Delta' in text and 'Theta' in text and 'OI' in text
    assert 'الحركة المتوقعة للسهم نحو الهدف' in text
