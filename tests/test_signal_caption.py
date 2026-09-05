from app.telegram.messages import signal_caption, signal_text


def _verbose_option_signal():
    return {
        "symbol": "SPX",
        "trade_type": "INDEX_OPTION_INTRADAY",
        "direction": "LONG",
        "entry_low": 1.75,
        "entry_high": 1.80,
        "stop": 1.40,
        "tp1": 2.40,
        "tp2": 2.60,
        "tp3": 3.00,
        "score": 88,
        "rr": 2.0,
        "risk_pct": 0.003,
        "probability_status": "UNVALIDATED",
        "probability": None,
        "probability_samples": 0,
        "market_regime": "BULL",
        "sector": "INDEX",
        "data_quality": "LIMITED",
        "trade_id": "IDX-ABCDEFGH",
        "reasons": [f"سبب فني تفصيلي رقم {i}" for i in range(20)],
        "strategies": ["Trend", "Structure", "Momentum", "Volume", "VWAP", "Volatility", "ICT"],
        "invalidation": ["إبطال بنية SPY عند 650.00"],
        "option": {
            "type": "CALL",
            "strike": 7000,
            "expiration": "2026-08-28",
            "dte": 0,
            "bid": 1.70,
            "ask": 1.80,
            "spread_pct": 5.71,
            "contract_score": 84,
            "delta": 0.55,
            "gamma": 0.01,
            "theta": -0.2,
            "vega": 0.1,
            "iv": 0.2,
            "underlying_direction": "LONG",
            "underlying_entry_low": 650,
            "underlying_entry_high": 651,
            "underlying_stop": 648,
            "underlying_tp1": 653,
            "underlying_tp2": 655,
            "underlying_tp3": 658,
        },
    }


def test_media_caption_never_exceeds_telegram_limit():
    s = _verbose_option_signal()
    assert len(signal_text(s)) > 1024
    caption = signal_caption(s)
    assert len(caption) <= 1024
    assert "IDX-ABCDEFGH" in caption
    assert "Strike:</b> 7000" in caption
    assert "TP1 $2.40 | TP2 $2.60 | TP3 $3.00" in caption
