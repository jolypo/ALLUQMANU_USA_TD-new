from datetime import datetime, timedelta, timezone
import pandas as pd

from app.market.waseem_v6_engine import WaseemV6Engine
from app.models.domain import Signal, TradeType, Decision
from app.telegram.message_templates import candidate_full_text
from app.config import settings


def bars(up=True, n=80):
    rows=[]
    start=datetime(2026,9,1,13,30,tzinfo=timezone.utc)
    px=100.0
    for i in range(n):
        drift=(0.10 if up else -0.10)
        px += drift + (0.02 if i%3==0 else -0.005)
        rows.append({
            'timestamp':start+timedelta(minutes=15*i),
            'open':px-drift*0.5,'high':px+0.25,'low':px-0.25,'close':px,
            'volume':100000+(i%7)*9000,
        })
    return pd.DataFrame(rows)


def signal():
    return Signal(
        symbol='NVDA', trade_type=TradeType.EQUITY_OPTION_SWING, direction='LONG',
        decision=Decision.WATCH, score=90, entry_low=3.0, entry_high=3.2, stop=2.4,
        tp1=3.8,tp2=4.2,tp3=4.8,rr=2.0,risk_pct=0.005,required_score=88,
        option={'strategy_mode':'WASEEM_V5','engine_source':'Waseem V5','type':'CALL','strike':110,
                'expiration':'2026-09-09','dte':5,'horizon':'WEEKLY','mid':3.1,
                'underlying_current_price':108.0,'v5_order_flow_score':70,'v5_flow_confidence':'LOW',
                'v4_liquidity_score':80,'v4_pre_move_score':78,'entry_quality':82,'contract_score':88}
    )


def intelligence():
    return {
        'ok':True,'current':108.0,'nearest_support':106.5,'nearest_resistance':109.0,'target':111.5,
        'atr':3.0,'rvol':1.3,'vwap':107.2,'momentum5_pct':0.8,
        'frames':[
            {'key':'15m','available':True,'supports':[106.8],'resistances':[109.0]},
            {'key':'1h','available':True,'supports':[106.0],'resistances':[110.0]},
            {'key':'4h','available':True,'supports':[104.0],'resistances':[112.0]},
            {'key':'1d','available':True,'supports':[102.0],'resistances':[114.0]},
            {'key':'1w','available':True,'supports':[98.0],'resistances':[118.0]},
            {'key':'1mo','available':True,'supports':[90.0],'resistances':[125.0]},
        ],
        'ict':{'bullish_ob':(106.5,107.0),'bullish_fvg':(107.1,107.5),'bearish_ob':None,'bearish_fvg':None},
        'fib':{'direction':'UP','retracements':{0.5:106.0},'extensions':{1.272:112.0}},
    }


def test_v6_delayed_feed_downweights_flow_and_stays_independent():
    eng=WaseemV6Engine()
    row=eng.evaluate(signal=signal(), intelligence=intelligence(), bars15=bars(True),
                     data_age_minutes=15, delayed_threshold=5, flow_score=95, flow_confidence='HIGH', ready_floor=88)
    assert row.delayed_data is True
    assert row.session in {'PREMARKET','RTH','AFTER_HOURS','CLOSED'}
    assert row.room_to_target_score > 50
    assert any('order_flow_weight=0.03' in x for x in row.diagnostics)
    assert row.cross_state in {'POSITIVE_CROSS','POSITIVE','NEGATIVE_CROSS','NEGATIVE','NEUTRAL'}


def test_v6_message_has_v6_only_explainability_block():
    t=signal().to_dict()
    t['score']=91
    t['market_state']='WASEEM_V6_WATCH'
    t['option'].update({
        'strategy_mode':'WASEEM_V6','engine_source':'Waseem V6','v6_score':91,
        'v6_session':'RTH','v6_delayed_data':True,'v6_multi_timeframe_score':82,
        'v6_room_to_target_score':79,'v6_momentum_decay_score':74,'v6_late_entry_score':69,
        'v6_breakout_quality_score':76,'v6_reversal_risk_score':31,'v6_ict_score':80,
        'v6_fibonacci_score':72,'v6_cross_state':'POSITIVE_CROSS','v6_cross_score':88,
        'v6_nearest_support':106.5,'v6_nearest_resistance':109,'v6_next_target':111.5,
        'v6_watch_reason':'WATCH: delayed-data safety gate','v5_order_flow_score':70,'v5_flow_confidence':'LOW',
    })
    text=candidate_full_text(t,180)
    assert 'وسيم V6' in text
    assert 'منع نهاية الزخم' in text
    assert 'المساحة حتى الهدف' in text
    assert 'خطر الانعكاس' in text
    assert 'تقاطع موجب' in text
    assert 'Order Flow — طبقة مساندة' in text


def test_new_default_watchlist_symbols_exist():
    assert {'MRVL','MSTR','GOOGL'}.issubset(set(settings.stocks))
