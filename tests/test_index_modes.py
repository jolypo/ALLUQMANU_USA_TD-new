import pytest

from app.trading.service import SignalService


class _History:
    def all(self):
        return []


class _Provider:
    def __init__(self):
        self.calls = []

    async def bars(self, *args, **kwargs):
        raise RuntimeError("not used by monkeypatched analysis")

    async def news(self, *args, **kwargs):
        return []

    async def index_option_chain(self, index, min_dte, max_dte, opt_type):
        self.calls.append((index, min_dte, max_dte, opt_type))
        return {"snapshots": {"dummy": {}}, "_chain_source": "test"}


@pytest.mark.asyncio
async def test_spx_index_scan_checks_zero_dte_and_swing(monkeypatch):
    provider = _Provider()
    service = SignalService(provider, _History())

    async def fake_analyze(*args, **kwargs):
        return ({
            "direction": "LONG",
            "score": 90.0,
            "rr": 2.0,
            "entry_low": 650.0,
            "entry_high": 651.0,
            "stop": 648.0,
            "tp1": 653.0,
            "tp2": 655.0,
            "tp3": 658.0,
            "reasons": [],
            "scores": {"Trend": 90.0},
            "market_regime": "BULL", "adx": 30.0, "rvol": 1.4, "directional_gap": 20.0, "trend_active": True,
        }, "GOOD")

    monkeypatch.setattr(service, "_analyze", fake_analyze)
    monkeypatch.setattr(service.risk, "assess", lambda *a, **k: (True, 0.005, "OK"))
    monkeypatch.setattr(
        service.selector,
        "select",
        lambda payload, direction, expected_underlying, underlying_price, **kwargs: {
            "symbol": f"SPXW_TEST_{kwargs['min_dte']}",
            "type": "CALL",
            "strike": 7000,
            "expiration": "2026-08-28",
            "dte": kwargs["min_dte"],
            "bid": 1.70,
            "ask": 1.80,
            "mid": 1.75,
            "spread_pct": 5.7,
            "delta": 0.55,
            "gamma": 0.01,
            "theta": -0.1,
            "vega": 0.1,
            "rho": 0.01,
            "iv": 0.2,
            "volume": 100,
            "contract_score": 95.0,
        },
    )

    rows, rejects = await service.best_index_options(3)
    assert len(rows) == 2
    assert ("SPX", 0, 0, "call") in provider.calls
    assert ("SPX", 7, 35, "call") in provider.calls
    assert {row.option["dte_mode"] for row in rows} == {"0DTE", "SWING"}

@pytest.mark.asyncio
async def test_spx_core_explicit_weekly_is_only_1_to_7(monkeypatch):
    provider = _Provider()
    service = SignalService(provider, _History())

    async def fake_analyze(*args, **kwargs):
        return ({
            "direction": "LONG", "score": 90.0, "rr": 2.0,
            "entry_low": 650.0, "entry_high": 651.0, "stop": 648.0,
            "tp1": 653.0, "tp2": 655.0, "tp3": 658.0,
            "reasons": [], "scores": {"Trend": 90.0}, "market_regime": "BULL", "adx": 30.0, "rvol": 1.4, "directional_gap": 20.0, "trend_active": True,
        }, "GOOD")

    monkeypatch.setattr(service, "_analyze", fake_analyze)
    monkeypatch.setattr(service.risk, "assess", lambda *a, **k: (True, 0.005, "OK"))
    monkeypatch.setattr(
        service.selector, "select",
        lambda payload, direction, expected_underlying, underlying_price, **kwargs: {
            "symbol": "SPXW_WEEKLY", "type": "CALL", "strike": 7000,
            "expiration": "2026-09-04", "dte": 5, "bid": 1.70, "ask": 1.80,
            "mid": 1.75, "spread_pct": 5.7, "delta": 0.55, "gamma": 0.01,
            "theta": -0.1, "vega": 0.1, "rho": 0.01, "iv": 0.2,
            "volume": 100, "contract_score": 95.0,
        },
    )
    rows, rejects = await service.best_index_options(3, horizon="weekly")
    assert provider.calls == [("SPX", 1, 7, "call")]
    assert len(rows) == 1
    assert rows[0].option["horizon"] == "WEEKLY"


@pytest.mark.asyncio
async def test_spx_core_short_routes_to_put_chain(monkeypatch):
    provider = _Provider()
    service = SignalService(provider, _History())

    async def fake_analyze(*args, **kwargs):
        return ({
            "direction": "SHORT", "score": 90.0, "rr": 2.0,
            "entry_low": 650.0, "entry_high": 651.0, "stop": 654.0,
            "tp1": 647.0, "tp2": 645.0, "tp3": 642.0,
            "reasons": ["EMA 9/20/50 هابطة"],
            "scores": {"Trend": 10.0}, "market_regime": "BEAR", "adx": 30.0, "rvol": 1.4, "directional_gap": 20.0, "trend_active": True,
        }, "GOOD")

    monkeypatch.setattr(service, "_analyze", fake_analyze)
    monkeypatch.setattr(service.risk, "assess", lambda *a, **k: (True, 0.005, "OK"))
    monkeypatch.setattr(
        service.selector, "select",
        lambda payload, direction, expected_underlying, underlying_price, **kwargs: {
            "symbol": "SPXW_PUT_TEST", "type": "PUT", "strike": 6400,
            "expiration": "2026-09-04", "dte": 5, "bid": 2.0, "ask": 2.1,
            "mid": 2.05, "spread_pct": 4.9, "delta": -0.55, "gamma": 0.01,
            "theta": -0.1, "vega": 0.1, "rho": -0.01, "iv": 0.2,
            "volume": 100, "contract_score": 95.0,
        },
    )
    rows, rejects = await service.best_index_options(1, horizon="weekly")
    assert provider.calls == [("SPX", 1, 7, "put")]
    assert len(rows) == 1
    assert rows[0].option["type"] == "PUT"
    assert rows[0].option["underlying_direction"] == "SHORT"
