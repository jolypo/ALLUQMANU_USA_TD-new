from pathlib import Path

from app.trading.service import SignalService


def test_expiration_horizon_mapping():
    assert SignalService._expiration_horizon("daily") == ("DAILY", 0, 0)
    assert SignalService._expiration_horizon("weekly") == ("WEEKLY", 1, 7)
    assert SignalService._expiration_horizon("monthly") == ("MONTHLY", 8, 35)
    assert SignalService._expiration_horizon(None) == (None, None, None)


def test_telegram_has_expiration_horizon_controls():
    source = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()
    assert "Daily 0DTE only" in source
    assert "Weekly 1–7 DTE" in source
    assert "Monthly 8–35 DTE" in source
    assert 'callback_data=f"horizon:select:{key}:daily"' in source
    assert 'horizon=self.search_horizons.get(key)' in source


def test_near_stop_is_internal_only_and_reports_are_manual_only():
    source = (Path(__file__).resolve().parents[1] / "app" / "scheduler" / "monitor.py").read_text()
    assert "do not\n                # publish Near Stop Loss" in source
    scheduled = source.split("async def _scheduled_reports(self):", 1)[1].split("async def cycle", 1)[0]
    assert "return None" in scheduled
    assert "await self._send_daily_reports()" not in scheduled
    assert "await self._send_weekly_report()" not in scheduled


def test_profit_caption_has_requested_fields_and_both_times():
    source = (Path(__file__).resolve().parents[1] / "app" / "scheduler" / "monitor.py").read_text()
    for text in (
        "📈 تحديث أرباح",
        "💵 الدخول:",
        "السعر الحالي:",
        "📊 النسبة:",
        "💰 ربح بالدولار:",
        "🇸🇦 ربح بالريال السعودي:",
        "🕒 السعودية:",
        "🕒 نيويورك:",
    ):
        assert text in source


def test_equity_selector_receives_selected_dte_window():
    source = (Path(__file__).resolve().parents[1] / "app" / "trading" / "service.py").read_text()
    block = source.split("async def best_equity_options", 1)[1].split("async def best_equity_option", 1)[0]
    assert "min_dte=int(min_dte)" in block
    assert "max_dte=int(max_dte)" in block


def test_explicit_spx_horizon_suppresses_legacy_swing_bucket():
    source = (Path(__file__).resolve().parents[1] / "app" / "trading" / "service.py").read_text()
    core = source.split("async def _best_index_options_core", 1)[1].split("async def _analyze_spx_v20", 1)[0]
    v20 = source.split("async def _best_index_options_v20", 1)[1].split("async def best_index_options", 1)[0]
    assert "if not horizon_name and settings.enable_index_options_swing" in core
    assert "if not horizon_name and settings.enable_index_options_swing" in v20


import pytest
from types import SimpleNamespace


class _EquityHistory:
    def all(self):
        return []


class _EquityProvider:
    def __init__(self):
        self.calls = []

    async def option_chain(self, symbol, min_dte, max_dte, opt_type):
        self.calls.append((symbol, min_dte, max_dte, opt_type))
        return {"snapshots": {"dummy": {}}}


@pytest.mark.asyncio
async def test_equity_daily_passes_zero_dte_through_provider_and_selector(monkeypatch):
    provider = _EquityProvider()
    service = SignalService(provider, _EquityHistory())
    base = SimpleNamespace(
        symbol="AAPL", trade_type=SimpleNamespace(value="STOCK_INTRADAY"),
        direction="LONG", entry_low=200.0, entry_high=200.2, stop=198.0,
        tp1=202.0, tp2=203.0, tp3=204.0, current_price=200.1,
        market_timestamp=None, market_age_minutes=1.0, score=92.0, risk_pct=0.005,
        reasons=["test"], strategies=["Trend"], market_regime="BULL", sector="Technology",
        market_context={"direction":"LONG","market_regime":"BULL","adx":30.0,"rvol":1.4,"directional_gap":20.0,"trend_active":True,"scores":{"Volatility":80}},
    )

    async def fake_stock_candidates(types, **kwargs):
        return [base], []

    captured = {}
    def fake_select(payload, direction, expected_underlying, underlying_price, **kwargs):
        captured.update(kwargs)
        return {
            "symbol": "AAPL_TEST_0", "type": "CALL", "strike": 200,
            "expiration": "2099-01-01", "dte": 0, "bid": 1.0, "ask": 1.1,
            "mid": 1.05, "spread_pct": 2.0, "delta": 0.55, "gamma": 0.01,
            "theta": -0.1, "vega": 0.1, "rho": 0.01, "iv": 0.2,
            "volume": 100, "contract_score": 95.0,
        }

    monkeypatch.setattr(service, "_stock_candidates", fake_stock_candidates)
    monkeypatch.setattr(service.selector, "select", fake_select)
    rows, rejects = await service.best_equity_options(3, horizon="daily")
    assert provider.calls == [("AAPL", 0, 0, "call")]
    assert captured["min_dte"] == 0
    assert captured["max_dte"] == 0
    assert len(rows) == 1
    assert rows[0].option["horizon"] == "DAILY"
