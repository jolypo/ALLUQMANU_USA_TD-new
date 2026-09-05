from app.telegram.signal_delivery_policy import SignalDeliveryPolicy


def sig(symbol, score, *, direction="CALL", decision="READY", engine="WASEEM_V2", contract=None, required=90):
    return {
        "symbol": symbol,
        "score": score,
        "required_score": required,
        "direction": "LONG" if direction == "CALL" else "SHORT",
        "decision": decision,
        "option": {
            "symbol": contract or f"{symbol}-OPT-{score}",
            "type": direction,
            "strategy_mode": engine,
        },
    }


def test_unique_symbol_ranking_allows_fourth_symbol_to_surface():
    p = SignalDeliveryPolicy(cooldown_seconds=1200)
    rows = [
        sig("NVDA", 96, contract="NVDA-A"),
        sig("NVDA", 95, contract="NVDA-B"),
        sig("INTC", 94),
        sig("EVGO", 93),
        sig("AAPL", 92),
    ]
    selected, suppressed = p.select_unique_symbols(rows, 3)
    assert [x["symbol"] for x in selected] == ["NVDA", "INTC", "EVGO"]
    assert any(x["reason"] == "DUPLICATE_SYMBOL_LOWER_SCORE" for x in suppressed)


def test_global_symbol_cooldown_cross_engine():
    p = SignalDeliveryPolicy(cooldown_seconds=1200, upgrade_score_delta=3)
    first = sig("NVDA", 92, engine="WASEEM_V2")
    assert p.evaluate(first, now=100).allowed
    p.record_sent(first, now=100)
    same_symbol_other_engine = sig("NVDA", 93, engine="WASEEM_V4")
    d = p.evaluate(same_symbol_other_engine, now=400)
    assert not d.allowed
    assert d.reason == "GLOBAL_SYMBOL_COOLDOWN"


def test_symbol_can_return_after_twenty_minutes():
    p = SignalDeliveryPolicy(cooldown_seconds=1200)
    row = sig("NVDA", 92)
    p.record_sent(row, now=100)
    assert not p.evaluate(row, now=1299).allowed
    assert p.evaluate(row, now=1300).allowed


def test_watch_to_ready_bypasses_cooldown():
    p = SignalDeliveryPolicy(cooldown_seconds=1200)
    watch = sig("NVDA", 91, decision="WATCH", engine="WASEEM_V6")
    p.record_sent(watch, now=100)
    ready = sig("NVDA", 92, decision="READY", engine="WASEEM_V6")
    ready["v6_watch_transition"] = "WATCH_TO_READY"
    d = p.evaluate(ready, now=200)
    assert d.allowed and d.material_override
    assert d.reason == "WATCH_TO_READY_OVERRIDE"


def test_confirmed_direction_reversal_bypasses_cooldown():
    p = SignalDeliveryPolicy(cooldown_seconds=1200)
    call = sig("NVDA", 92, direction="CALL")
    p.record_sent(call, now=100)
    put = sig("NVDA", 94, direction="PUT", required=91)
    d = p.evaluate(put, now=250)
    assert d.allowed and d.material_override
    assert d.reason == "CONFIRMED_DIRECTION_REVERSAL_OVERRIDE"


def test_material_score_upgrade_bypasses_cooldown_but_small_upgrade_does_not():
    p = SignalDeliveryPolicy(cooldown_seconds=1200, upgrade_score_delta=3)
    first = sig("NVDA", 91, engine="WASEEM_V2")
    p.record_sent(first, now=100)
    assert not p.evaluate(sig("NVDA", 93, engine="WASEEM_V4"), now=200).allowed
    d = p.evaluate(sig("NVDA", 94, engine="WASEEM_V6"), now=210)
    assert d.allowed and d.reason == "MATERIAL_SCORE_UPGRADE_OVERRIDE"
