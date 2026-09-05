from datetime import datetime, timezone

from app.telegram.bots import TelegramBots


def test_v4_watch_transition_title_and_details():
    bot = object.__new__(TelegramBots)
    trade = {
        "symbol": "SPX",
        "direction": "LONG",
        "decision": "READY",
        "score": 92.0,
        "market_state": "WASEEM_V4_READY",
        "first_detected_at": "2026-09-03T10:00:00+00:00",
        "watch_added_at": "2026-09-03T10:00:01+00:00",
        "entry_ready_at": "2026-09-03T10:15:00+00:00",
        "v4_watch_transition": "WATCH_TO_READY",
        "option": {
            "strategy_mode": "WASEEM_V4",
            "engine_source": "Waseem V4",
            "symbol": "SPXW260903C07650000",
            "type": "CALL",
            "strike": 7650,
            "expiration": "2026-09-03",
            "entry_state": "READY",
            "entry_quality": 91.0,
            "current_contract_price": 4.22,
            "preferred_entry_low": 4.10,
            "preferred_entry_high": 4.30,
            "watch_reason": "Premium returned to efficient entry zone",
        },
    }
    text = bot._candidate_details_text(trade, datetime.now(timezone.utc))
    assert "WASEEM V4 WATCH → ENTRY READY" in text
    assert "Watch Transition: WATCH_TO_READY" in text
    assert "Entry Ready At:" in text


def test_v4_keep_watch_title():
    bot = object.__new__(TelegramBots)
    trade = {
        "symbol": "AAPL",
        "direction": "LONG",
        "decision": "WATCH",
        "score": 90.0,
        "option": {
            "strategy_mode": "WASEEM_V4",
            "engine_source": "Waseem V4",
            "symbol": "AAPL260904C00200000",
            "type": "CALL",
            "strike": 200,
            "expiration": "2026-09-04",
            "entry_state": "WATCH",
            "entry_quality": 70.0,
            "current_contract_price": 5.20,
            "preferred_entry_low": 4.45,
            "preferred_entry_high": 4.65,
            "watch_reason": "avoid chasing",
        },
    }
    text = bot._candidate_details_text(trade, datetime.now(timezone.utc))
    assert "WASEEM V4 — KEEP WATCH" in text
    assert "V4 Auto Watch: ACTIVE" in text
