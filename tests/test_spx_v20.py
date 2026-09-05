from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import pytest

from app.strategies.spx_v20 import SPXV20Engine
from app.trading.service import SignalService

NY = ZoneInfo("America/New_York")


class _History:
    def all(self):
        return []


class _Provider:
    def __init__(self):
        self.calls = []

    async def bars(self, *args, **kwargs):
        raise RuntimeError("not used by monkeypatched V20 analysis")

    async def index_option_chain(self, index, min_dte, max_dte, opt_type):
        self.calls.append((index, min_dte, max_dte, opt_type))
        return {"snapshots": {"dummy": {}}, "_chain_source": "test"}


def _bars(count: int, start: datetime, step: timedelta, base: float = 500.0) -> pd.DataFrame:
    rows = []
    price = base
    t = start
    for i in range(count):
        price += 0.12
        rows.append({
            "timestamp": t.astimezone(ZoneInfo("UTC")).isoformat(),
            "open": price - 0.08,
            "high": price + 0.25,
            "low": price - 0.25,
            "close": price,
            "volume": 1_000_000 + i * 1000,
        })
        t += step
    return pd.DataFrame(rows)


def _rth_15m(days: int = 30) -> pd.DataFrame:
    rows = []
    d = datetime(2026, 7, 20, tzinfo=NY)
    price = 500.0
    built = 0
    while built < days:
        if d.weekday() < 5:
            t = d.replace(hour=9, minute=30)
            for j in range(26):
                price += 0.08
                rows.append({
                    "timestamp": t.astimezone(ZoneInfo("UTC")).isoformat(),
                    "open": price - 0.05,
                    "high": price + 0.18,
                    "low": price - 0.18,
                    "close": price,
                    "volume": 1_000_000 + len(rows) * 1000,
                })
                t += timedelta(minutes=15)
            built += 1
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def test_v20_engine_returns_structured_decision_without_future_bar_use():
    primary = _rth_15m(30)
    now = pd.to_datetime(primary.iloc[-1]["timestamp"], utc=True).to_pydatetime().astimezone(NY) + timedelta(minutes=20)
    mtf = {
        "5": _bars(260, now - timedelta(minutes=5 * 260), timedelta(minutes=5)),
        "15": primary,
        "60": _bars(260, now - timedelta(hours=260), timedelta(hours=1)),
        "240": _bars(260, now - timedelta(hours=4 * 260), timedelta(hours=4)),
    }
    daily = _bars(80, now - timedelta(days=80), timedelta(days=1))
    result = SPXV20Engine().analyze(primary, mtf, daily, now=now)
    assert result["strategy_id"] == "SPX_V20"
    assert result["direction"] in {"LONG", "SHORT", "NEUTRAL"}
    assert 0 <= result["score"] <= 100
    assert "V20_CALL" in result["scores"]
    assert "V20_PUT" in result["scores"]
    assert result["rr"] >= 1.5


@pytest.mark.asyncio
async def test_v20_index_scan_routes_to_v20_and_keeps_zero_dte_plus_swing(monkeypatch):
    provider = _Provider()
    service = SignalService(provider, _History())

    async def fake_v20(proxy):
        return ({
            "direction": "LONG", "score": 90.0, "rr": 1.75,
            "entry_low": 650.0, "entry_high": 651.0, "stop": 648.0,
            "tp1": 653.0, "tp2": 655.0, "tp3": 658.0,
            "reasons": ["SPX V20 test"], "scores": {"V20_CALL": 90.0, "V20_PUT": 20.0},
            "market_regime": "BULL TREND", "adx": 30.0, "rvol": 1.4, "v20": {"bull_tf": 4, "bear_tf": 0},
        }, "GOOD")

    monkeypatch.setattr(service, "_analyze_spx_v20", fake_v20)
    monkeypatch.setattr(service.risk, "assess", lambda *a, **k: (True, 0.005, "OK"))
    monkeypatch.setattr(service.selector, "select", lambda payload, direction, expected_underlying, underlying_price, **kwargs: {
        "symbol": f"SPXW_V20_{kwargs['min_dte']}", "type": "CALL", "strike": 7000,
        "expiration": "2026-08-28", "dte": kwargs["min_dte"], "bid": 1.70, "ask": 1.80,
        "mid": 1.75, "spread_pct": 5.7, "delta": 0.55, "gamma": 0.01, "theta": -0.1,
        "vega": 0.1, "rho": 0.01, "iv": 0.2, "volume": 100, "contract_score": 95.0,
    })

    rows, rejects = await service.best_index_options(3, strategy_mode="v20")
    assert len(rows) == 2
    assert {row.option["dte_mode"] for row in rows} == {"0DTE", "SWING"}
    assert all(row.option["strategy_mode"] == "SPX_V20" for row in rows)
    assert all("SPX_V20" in row.strategies for row in rows)
    assert ("SPX", 0, 0, "call") in provider.calls
    assert ("SPX", 7, 35, "call") in provider.calls


def test_index_options_menu_requires_strategy_choice():
    source = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()
    assert 'callback_data="menu:index_strategy"' in source
    assert 'callback_data="menu:horizon:index:v20"' in source
    assert 'callback_data="menu:horizon:index:core"' in source

@pytest.mark.asyncio
async def test_v20_explicit_daily_is_only_zero_dte(monkeypatch):
    provider = _Provider()
    service = SignalService(provider, _History())

    async def fake_v20(proxy):
        return ({
            "direction": "LONG", "score": 90.0, "rr": 1.75,
            "entry_low": 650.0, "entry_high": 651.0, "stop": 648.0,
            "tp1": 653.0, "tp2": 655.0, "tp3": 658.0,
            "reasons": ["SPX V20 test"], "scores": {"V20_CALL": 90.0, "V20_PUT": 20.0},
            "market_regime": "BULL TREND", "adx": 30.0, "rvol": 1.4, "v20": {"bull_tf": 4, "bear_tf": 0},
        }, "GOOD")

    monkeypatch.setattr(service, "_analyze_spx_v20", fake_v20)
    monkeypatch.setattr(service.risk, "assess", lambda *a, **k: (True, 0.005, "OK"))
    monkeypatch.setattr(service.selector, "select", lambda payload, direction, expected_underlying, underlying_price, **kwargs: {
        "symbol": "SPXW_V20_0", "type": "CALL", "strike": 7000,
        "expiration": "2026-08-28", "dte": 0, "bid": 1.70, "ask": 1.80,
        "mid": 1.75, "spread_pct": 5.7, "delta": 0.55, "gamma": 0.01, "theta": -0.1,
        "vega": 0.1, "rho": 0.01, "iv": 0.2, "volume": 100, "contract_score": 95.0,
    })

    rows, rejects = await service.best_index_options(3, strategy_mode="v20", horizon="daily")
    assert provider.calls == [("SPX", 0, 0, "call")]
    assert len(rows) == 1
    assert rows[0].option["horizon"] == "DAILY"
