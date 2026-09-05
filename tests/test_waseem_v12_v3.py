from datetime import datetime
from zoneinfo import ZoneInfo

from app.options.waseem_v3_entry import WaseemV3EntryEngine
from app.trading.service import SignalService


def _contract(**overrides):
    row = {
        "bid": 4.00,
        "ask": 4.10,
        "mid": 4.05,
        "spread_pct": 2.47,
        "contract_score": 92.0,
        "quote_age_minutes": 2.0,
        "theta": -0.08,
    }
    row.update(overrides)
    return row


def test_v3_entry_ready_when_premium_is_not_extended():
    engine = WaseemV3EntryEngine()
    snap = {"dailyBar": {"o": 3.90, "l": 3.80, "h": 4.60, "v": 4200}}
    plan = engine.evaluate(_contract(), snap, horizon="WEEKLY")
    assert plan.state == "READY"
    assert plan.entry_quality >= 80
    assert plan.entry_low <= 4.05 <= plan.entry_high + 0.10
    assert plan.chase_risk is False


def test_v3_entry_watch_when_premium_is_near_session_high():
    engine = WaseemV3EntryEngine()
    c = _contract(bid=5.05, ask=5.20, mid=5.125, spread_pct=2.93)
    snap = {"dailyBar": {"o": 3.50, "l": 3.20, "h": 5.25, "v": 9000}}
    plan = engine.evaluate(c, snap, horizon="DAILY")
    assert plan.state == "WATCH"
    assert plan.chase_risk is True
    assert plan.entry_high < plan.current_price
    assert "avoid chasing" in plan.reason


def test_v3_session_is_gth_at_2am_et():
    ny = ZoneInfo("America/New_York")
    state = SignalService.spx_option_session_status(datetime(2026, 9, 2, 2, 0, tzinfo=ny))
    assert state["open"] is True
    assert state["session"] == "GTH"
    assert state["cash_spx_state"] == "PREVIOUS_CLOSE"


def test_v3_session_rth_at_10am_et():
    ny = ZoneInfo("America/New_York")
    state = SignalService.spx_option_session_status(datetime(2026, 9, 2, 10, 0, tzinfo=ny))
    assert state["open"] is True
    assert state["session"] == "RTH"
    assert state["cash_spx_state"] == "LIVE"


def test_v3_session_break_between_gth_and_rth():
    ny = ZoneInfo("America/New_York")
    state = SignalService.spx_option_session_status(datetime(2026, 9, 2, 9, 27, tzinfo=ny))
    assert state["open"] is False
    assert state["session"] == "SESSION_BREAK"


def test_v3_session_friday_evening_is_closed():
    ny = ZoneInfo("America/New_York")
    state = SignalService.spx_option_session_status(datetime(2026, 9, 4, 21, 0, tzinfo=ny))
    assert state["open"] is False
    assert state["session"] == "CLOSED"


def test_v3_gth_session_exposes_et_and_ksa_boundaries():
    ny = ZoneInfo("America/New_York")
    state = SignalService.spx_option_session_status(datetime(2026, 9, 2, 2, 0, tzinfo=ny))
    assert state["trade_date"] == "2026-09-02"
    assert "20:15:00" in state["session_start_et"]
    assert "09:25:00" in state["session_end_et"]
    assert state["session_start_ksa"]
    assert state["session_end_ksa"]


class _History:
    def all(self):
        return []


class _GTHProvider:
    async def index_option_chain(self, underlying, min_dte, max_dte, opt_type=None):
        # Use a current-ish timestamp so the diagnostic proves it can classify
        # actual snapshot freshness rather than merely checking configuration.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "_chain_source": "contracts_snapshots",
            "snapshots": {
                "SPXW260902C07800000": {
                    "latestQuote": {"t": now, "bp": 3.9, "ap": 4.1},
                    "latestTrade": {"t": now, "p": 4.0},
                }
            },
        }

    async def public_index_bars(self, symbol, timeframe, lookback_days):
        import pandas as pd
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return pd.DataFrame([{
            "timestamp": now,
            "open": 7790.0,
            "high": 7810.0,
            "low": 7780.0,
            "close": 7800.0,
            "volume": 0,
        }])


import pytest


@pytest.mark.asyncio
async def test_v3_gth_data_diagnostics_reports_actual_quote_and_trade_details():
    service = SignalService(_GTHProvider(), _History())
    d = await service.spx_gth_data_diagnostics(force=True)
    assert d["option_data_status"] == "AVAILABLE"
    assert d["snapshot_count"] >= 1
    assert d["quote_count"] >= 1
    assert d["trade_count"] >= 1
    assert d["latest_quote_contract"] == "SPXW260902C07800000"
    assert d["latest_quote_bid"] == 3.9
    assert d["latest_quote_ask"] == 4.1
    assert d["latest_trade_price"] == 4.0
    assert d["cash_last_point_price"] == 7800.0
    assert d["checked_at"]


@pytest.mark.asyncio
async def test_v3_gth_diagnostics_can_be_partial_when_trade_is_fresh_but_quote_is_stale():
    service = SignalService(_GTHProvider(), _History())
    original = service.provider.index_option_chain

    async def mixed_chain(*args, **kwargs):
        chain = await original(*args, **kwargs)
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        for snap in (chain.get("snapshots") or {}).values():
            if snap.get("latestQuote"):
                snap["latestQuote"]["t"] = (now - timedelta(hours=6)).isoformat()
            if snap.get("latestTrade"):
                snap["latestTrade"]["t"] = (now - timedelta(minutes=15)).isoformat()
        return chain

    service.provider.index_option_chain = mixed_chain
    d = await service.spx_gth_data_diagnostics(force=True)
    assert d["latest_quote_status"] == "STALE"
    assert d["latest_trade_status"] == "DELAYED"
    assert d["option_data_status"] == "PARTIAL"
