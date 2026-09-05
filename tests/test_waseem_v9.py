from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from app.options.waseem_v2_selector import WaseemV2ContractSelector
from app.telegram.messages import signal_caption


def occ(root: str, cp: str, strike: float) -> str:
    d = datetime.now(ZoneInfo("America/New_York")).strftime("%y%m%d")
    return f"{root}{d}{cp}{int(round(strike*1000)):08d}"


def snap(delta=0.45, gamma=0.08, theta=-0.05, bid=2.95, ask=3.05, volume=500):
    now = datetime.now(ZoneInfo("UTC")).isoformat()
    return {
        "latestQuote": {"bp": bid, "ap": ask, "bs": 25, "as": 25, "t": now},
        "greeks": {"delta": delta, "gamma": gamma, "theta": theta, "vega": 0.12},
        "dailyBar": {"v": volume},
        "impliedVolatility": 0.48,
    }


def test_waseem_v2_selector_marks_engine_and_efficiency():
    selector = WaseemV2ContractSelector()
    a = occ("SPXW", "C", 7710)
    b = occ("SPXW", "C", 7720)
    rows, diag = selector.rank(
        {"snapshots": {a: snap(delta=.48, gamma=.09), b: snap(delta=.38, gamma=.07, bid=2.4, ask=2.55)}},
        "LONG", "SPX", 7700, min_dte=0, max_dte=0, horizon="DAILY",
        expected_move=30, is_index=True, max_results=2,
    )
    assert rows
    assert all(r["selection_engine"] == "WASEEM_V2" for r in rows)
    assert all("strike_efficiency" in r for r in rows)
    assert any("Strike Efficiency" in x for x in diag)


def test_v2_buttons_are_separate_from_v1_and_continuous():
    source = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()
    assert 'menu:horizon:option:waseem_v2' in source
    assert 'menu:horizon:index:waseem_v2' in source
    assert 'continuous_v2 = key.endswith(":waseem_v2")' in source
    assert 'option:waseem": "both"' in source  # V1 preserved
    assert 'index:waseem": "both"' in source


def test_auto_time_exit_and_stop_loss_route_private_admin_only():
    source = (Path(__file__).resolve().parents[1] / "app" / "scheduler" / "monitor.py").read_text()
    time_block = source[source.index('f"🟠 إغلاق زمني'):source.index('distance_to_stop')]
    stop_block = source[source.index('f"🔴 وقف الخسارة'):source.index('for n in (1, 2, 3)')]
    assert "chat_id=settings.telegram_admin_user_id" in time_block
    assert "reply_to_message_id=None" in time_block
    assert "chat_id=settings.telegram_admin_user_id" in stop_block
    assert "reply_to_message_id=None" in stop_block


def test_v2_caption_identifies_engine_horizon_and_date_fields():
    s = {
        "symbol": "SPX", "trade_type": "INDEX_OPTION_INTRADAY", "direction": "LONG",
        "score": 92.0, "entry_low": 3.0, "entry_high": 3.1, "stop": 2.4,
        "tp1": 4.0, "tp2": 4.8, "tp3": 5.6, "rr": 2.0, "risk_pct": .0035,
        "market_state": "WASEEM_V2_SOFT_CONTEXT", "required_score": 90,
        "liquidity_state": "HIGH", "data_quality": "LIMITED", "reasons": ["test"],
        "created_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "option": {
            "type": "CALL", "strike": 7710, "expiration": datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            "dte": 0, "horizon": "DAILY", "mid": 3.0, "bid": 2.95, "ask": 3.05, "spread_pct": 3.3,
            "strategy_mode": "WASEEM_V2", "engine_source": "Waseem V2", "strike_efficiency": 88,
            "market_context_modifier": 4.2, "market_context_lines": ["ES: BULLISH", "NQ: BULLISH", "VIX: BEARISH"],
            "underlying_direction": "LONG", "underlying_current_price": 7700,
            "underlying_entry_low": 7698, "underlying_entry_high": 7702, "underlying_stop": 7688,
        },
    }
    text = signal_caption(s)
    assert "وسيم V2" in text
    assert "يومي 0DTE" in text
    assert "كفاءة Strike" not in text
    assert len(text) <= 1024
