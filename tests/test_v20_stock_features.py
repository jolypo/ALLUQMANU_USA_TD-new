import asyncio
from datetime import datetime, timedelta, timezone
import pandas as pd

from app.market.stock_intelligence import StockIntelligenceEngine
from app.market.stock_news import StockNewsEngine
from app.repositories.equity_watchlist import EquityWatchlistRepository


def bars(n=160, start=100.0, step=0.12, freq='15min'):
    ts=pd.date_range(end=pd.Timestamp.now(tz='UTC'), periods=n, freq=freq)
    rows=[]
    p=start
    for i,t in enumerate(ts):
        wave=((i%12)-6)*0.08
        o=p; c=p+step+wave*0.08
        rows.append({'timestamp':t,'open':o,'high':max(o,c)+0.45+(i%7)*0.02,'low':min(o,c)-0.40-(i%5)*0.02,'close':c,'volume':100000+(i%20)*5000})
        p=c
    return pd.DataFrame(rows)

class Provider:
    async def bars(self, symbol, timeframe, lookback_days):
        if timeframe=='15Min': return bars(180,100,0.06,'15min')
        if timeframe=='1Hour': return bars(180,96,0.08,'1h')
        if timeframe=='4Hour': return bars(180,90,0.12,'4h')
        if timeframe=='1Day': return bars(260,75,0.16,'1D')
        if timeframe=='1Week': return bars(150,55,0.35,'7D')
        if timeframe=='1Month': return bars(100,40,0.8,'30D')
        return pd.DataFrame()
    async def news(self, symbol, lookback_hours=24, limit=8):
        return [{'headline':f'{symbol} wins $2.5 billion contract and raises guidance','summary':'Strong demand and new agreement support revenue outlook.','created_at':datetime.now(timezone.utc).isoformat()}]


def test_ephemeral_watchlist_mutations_without_database(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'database_url', None, raising=False)
    repo=EquityWatchlistRepository()
    assert repo.status().backend=='MEMORY_EPHEMERAL'
    asyncio.run(repo.upsert('MRVL', True))
    assert 'MRVL' in asyncio.run(repo.enabled_symbols())
    assert asyncio.run(repo.set_enabled('MRVL', False)) is True
    assert 'MRVL' not in asyncio.run(repo.enabled_symbols())
    assert asyncio.run(repo.remove('MRVL')) is True


def test_stock_analysis_has_multiframe_confirmation_and_target():
    r=asyncio.run(StockIntelligenceEngine().analyze(Provider(),'AAPL'))
    assert r['ok'] is True
    names={f['name'] for f in r['frames']}
    assert {'15 دقيقة','ساعة','4 ساعات','يومي','أسبوعي','شهري'} <= names
    assert r['atr'] is not None
    text=StockIntelligenceEngine.render_ar(r)
    assert 'تحليل السهم' in text and 'ICT والسيولة' in text and 'Fibonacci' in text
    assert 'وقت اكتشاف التحليل' in text and 'وقت إرسال الرسالة' in text
    assert 'رأي فريق المتداولين الخبراء' in text
    assert text.count('---') >= 5
    assert len(text) < 4096


def test_stock_news_is_volatility_aware_and_has_risk_layer(monkeypatch):
    monkeypatch.setenv('NEWS_TRANSLATION_DISABLE','1')
    r=asyncio.run(StockNewsEngine().analyze(Provider(),'NVDA'))
    assert r['available'] is True
    assert r['direction']=='إيجابي'
    assert r['impact'] > 50
    text=StockNewsEngine.render_ar(r)
    assert 'عامل الخطر' in text and 'الأثر السعري المحتمل' in text
    assert '$2.5 billion' in text
    assert 'وقت إرسال الرسالة' in text
    assert 'رأي فريق المتداولين الخبراء' in text
    assert len(text) < 4096
