from datetime import datetime, timezone

from app.telegram.message_templates import (
    regular_option_caption,
    candidate_full_text,
    success_message,
    entry_message,
    profit_update_message,
)


def sample_trade():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "symbol": "NVDA",
        "trade_id": "OPT-24906001",
        "score": 92.1,
        "required_score": 91.0,
        "entry_low": 2.62,
        "entry_high": 2.64,
        "stop": 2.04,
        "tp1": 3.51,
        "tp2": 3.80,
        "tp3": 4.27,
        "rr": 2.0,
        "published_at": now,
        "option": {
            "strategy_mode": "WASEEM_V5",
            "engine_source": "Waseem V5",
            "type": "CALL",
            "strike": 235,
            "expiration": "2026-09-09",
            "dte": 5,
            "horizon": "WEEKLY",
            "bid": 2.60,
            "ask": 2.64,
            "mid": 2.62,
            "underlying_current_price": 233.50,
            "underlying_entry_low": 233.45,
            "underlying_entry_high": 233.67,
            "quote_timestamp": now,
            "entry_quality": 89,
            "contract_score": 92,
            "v4_liquidity_score": 90,
            "v4_pre_move_score": 91,
            "v5_score": 93,
            "v5_order_flow_score": 91,
            "v5_flow_confidence": "HIGH",
        },
    }


def test_regular_opportunity_matches_approved_compact_layout():
    text = regular_option_caption(sample_trade())
    assert "فرصة عقود جديدة" in text
    assert text.index("قوة الإشارة") < text.index("المحرك") < text.index("التقييم")
    assert "السبريد" not in text
    assert "العائد مقابل المخاطرة" not in text
    assert "Greeks" not in text
    assert "إلغاء فكرة" not in text
    assert "---" in text
    assert "رقم الفرصة" in text
    assert len(text) <= 1024


def test_ready_engine_above_symbol_and_signal_strength_under_engine():
    text = candidate_full_text(sample_trade(), 180)
    assert text.index("المحرك") < text.index("قوة الإشارة") < text.index("السهم")
    assert "تحليل OHLCV والسيولة" in text
    assert "Order Flow — وسيم V5" in text
    assert "اكتشاف العقد — نيويورك" in text
    assert "اكتشاف العقد — الرياض" in text


def test_success_layout_entry_and_current_same_line():
    trade = sample_trade()
    text = success_message(trade, 3.35, 3.99, 50.0, 64.45, 241.69, "🟢", "قوي", "استمرار مع حماية الربح")
    assert "الدخول:</b> $3.35 | 📈 <b>العقد الآن:</b> $3.99" in text
    assert "الربح الحالي بالدولار" in text
    assert "بالريال السعودي" in text


def test_entry_layout_is_spaced_and_compact():
    text = entry_message(sample_trade(), 3.35)
    assert "تم الدخول في الصفقة" in text
    assert "NVDA   |   CALL   |   Strike 235" in text
    assert "سعر الدخول" in text


def test_profit_update_layout():
    text = profit_update_message(sample_trade(), 3.35, 3.96, 60.50, 226.88)
    assert "تحديث الأرباح" in text
    assert "الدخول:</b> $3.35   |   <b>العقد الآن:</b> $3.96" in text
    assert "السعودية" in text and "نيويورك" in text
