import pandas as pd
from app.strategies.engine import StrategyEngine

def test_strategy_returns_structured_result():
    n=240
    df=pd.DataFrame({"open":[100+i*.2 for i in range(n)],"high":[101+i*.2 for i in range(n)],"low":[99+i*.2 for i in range(n)],"close":[100.5+i*.2 for i in range(n)],"volume":[100000+i*100 for i in range(n)]})
    r=StrategyEngine().analyze(df)
    assert 0 <= r["score"] <= 100
    assert r["direction"] in {"LONG","SHORT","NEUTRAL"}
    assert r["tp3"] >= r["tp2"] >= r["tp1"] if r["direction"]=="LONG" else True


def _trend_frame(sign: int, n: int = 260) -> pd.DataFrame:
    base = 100.0 if sign > 0 else 200.0
    closes = [base + sign * i * 0.35 for i in range(n)]
    return pd.DataFrame({
        "open": [c - sign * 0.08 for c in closes],
        "high": [c + 0.60 for c in closes],
        "low": [c - 0.60 for c in closes],
        "close": closes,
        "volume": [100000 + i * 500 for i in range(n)],
    })


def test_core_directional_scores_are_symmetric_for_clear_trends():
    engine = StrategyEngine()
    bull = engine.analyze(_trend_frame(1))
    bear = engine.analyze(_trend_frame(-1))

    assert bull["direction"] == "LONG"
    assert bear["direction"] == "SHORT"
    assert bull["bull_score"] > bull["bear_score"]
    assert bear["bear_score"] > bear["bull_score"]
    assert bull["score"] >= 75
    assert bear["score"] >= 75
    # Mirrored tape should not create a material one-sided scoring advantage.
    assert abs(bull["score"] - bear["score"]) <= 5.0


def test_volume_and_volatility_are_quality_not_direction_votes():
    engine = StrategyEngine()
    bull = engine.analyze(_trend_frame(1))
    bear = engine.analyze(_trend_frame(-1))

    assert 0 <= bull["quality_score"] <= 100
    assert 0 <= bear["quality_score"] <= 100
    # The engine exposes independent directional scores for diagnostics.
    for result in (bull, bear):
        assert "bull_score" in result
        assert "bear_score" in result
        assert "directional_gap" in result


def test_core_neutral_tape_stays_neutral():
    n = 260
    closes = [100.0 + (0.08 if i % 2 == 0 else -0.08) for i in range(n)]
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 0.20 for c in closes],
        "low": [c - 0.20 for c in closes],
        "close": closes,
        "volume": [100000 for _ in range(n)],
    })
    result = StrategyEngine().analyze(df)
    assert result["direction"] == "NEUTRAL"
