from app.market.waseem_v5_orderflow import WaseemV5OrderFlowEngine


def test_v5_uses_observable_top_book_and_trade_without_fake_depth():
    eng = WaseemV5OrderFlowEngine()
    snap = {
        "latestQuote": {"bp": 4.80, "ap": 4.90, "bs": 1200, "as": 400, "t": "2026-09-04T15:00:00Z"},
        "latestTrade": {"p": 4.89, "s": 25, "t": "2026-09-04T15:00:01Z"},
    }
    row = eng.evaluate("NVDA_TEST", snap, "CALL")
    assert row.score > 50
    assert row.bid_ask_pressure_score is not None
    assert row.trade_aggression_score is not None
    assert row.book_imbalance_score is None
    assert row.absorption_score is None
    assert any("UNAVAILABLE" in x and "depth" in x.lower() for x in row.diagnostics)


def test_v5_cross_scan_pressure_can_strengthen_flow():
    eng = WaseemV5OrderFlowEngine()
    first = {"latestQuote": {"bp": 4.80, "ap": 4.90, "bs": 900, "as": 500}, "latestTrade": {"p": 4.88}}
    second = {"latestQuote": {"bp": 4.90, "ap": 5.00, "bs": 1500, "as": 250}, "latestTrade": {"p": 5.00}}
    eng.evaluate("NVDA_TEST", first, "CALL")
    row = eng.evaluate("NVDA_TEST", second, "CALL")
    assert row.execution_pressure_score is not None
    assert row.score >= 60
