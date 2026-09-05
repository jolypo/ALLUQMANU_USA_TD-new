from app.config import settings
from app.market.dynamic_quality import DynamicMarketGate
from app.risk.engine import RiskEngine


def _analysis(direction="LONG", regime="BULL", **overrides):
    row = {
        "direction": direction,
        "market_regime": regime,
        "adx": 31.0,
        "rvol": 1.40,
        "atr_pct": 1.2,
        "atr_regime_ratio": 1.0,
        "directional_gap": 22.0,
        "trend_active": True,
        "quality_flags": [],
        "scores": {"Volatility": 80.0},
    }
    row.update(overrides)
    return row


def _contract(**overrides):
    row = {"spread_pct": 2.0, "contract_score": 92.0, "volume": 500}
    row.update(overrides)
    return row


def test_base_floor_is_90_and_healthy_trend_uses_it():
    gate = DynamicMarketGate().evaluate(_analysis(), _contract())
    assert settings.ready_score_floor == 90.0
    assert gate.blocked is False
    assert gate.state == "HEALTHY_TREND"
    assert gate.required_score == 90.0


def test_call_and_put_are_symmetric_under_same_market_quality():
    gate = DynamicMarketGate()
    call = gate.evaluate(_analysis("LONG", "BULL"), _contract())
    put = gate.evaluate(_analysis("SHORT", "BEAR"), _contract())
    assert call.blocked is False and put.blocked is False
    assert call.required_score == put.required_score == 90.0
    assert call.risk_cap == put.risk_cap


def test_range_market_raises_threshold():
    gate = DynamicMarketGate().evaluate(_analysis(regime="RANGE"), _contract())
    assert gate.blocked is False
    assert gate.state == "RANGE_MIXED"
    assert gate.required_score == 93.0


def test_high_volatility_raises_threshold_to_94_and_caps_risk():
    gate = DynamicMarketGate().evaluate(
        _analysis(atr_regime_ratio=1.9, atr_pct=7.0, scores={"Volatility": 50.0}),
        _contract(),
    )
    assert gate.blocked is False
    assert gate.state == "HIGH_VOLATILITY"
    assert gate.required_score == 94.0
    assert gate.risk_cap <= 0.005


def test_low_liquidity_and_unclear_direction_is_no_trade():
    gate = DynamicMarketGate().evaluate(
        _analysis(rvol=0.55, adx=12.0, directional_gap=3.0, trend_active=False),
        _contract(),
    )
    assert gate.blocked is True
    assert gate.state == "LOW_LIQUIDITY_UNCLEAR"


def test_low_underlying_liquidity_with_clear_direction_requires_94():
    gate = DynamicMarketGate().evaluate(
        _analysis(rvol=0.60, adx=32.0, directional_gap=25.0, trend_active=True),
        _contract(),
    )
    assert gate.blocked is False
    assert gate.state == "LOW_LIQUIDITY_CLEAR"
    assert gate.required_score == 94.0


def test_wide_option_spread_is_no_trade_even_with_high_score_thesis():
    gate = DynamicMarketGate().evaluate(_analysis(), _contract(spread_pct=8.1, contract_score=98.0))
    assert gate.blocked is True
    assert gate.state == "LOW_LIQUIDITY_CONTRACT"


def test_caution_market_requires_92():
    gate = DynamicMarketGate().evaluate(
        _analysis(adx=21.0, rvol=0.95, directional_gap=15.0),
        _contract(),
    )
    assert gate.blocked is False
    assert gate.state == "CAUTION"
    assert gate.required_score == 92.0


def test_risk_engine_respects_dynamic_threshold_and_cap():
    engine = RiskEngine()
    ok, risk, _ = engine.assess(93.0, "GOOD", 2.0, required_score=94.0, risk_cap=0.005)
    assert ok is False
    ok, risk, _ = engine.assess(94.0, "GOOD", 2.0, required_score=94.0, risk_cap=0.005)
    assert ok is True
    assert risk <= 0.005


def test_counter_trend_requires_93():
    gate = DynamicMarketGate().evaluate(_analysis(direction="SHORT", regime="BULL"), _contract())
    assert gate.blocked is False
    assert gate.state == "COUNTER_TREND"
    assert gate.required_score == 93.0


def test_old_env_score_cannot_lower_hard_floor(monkeypatch):
    monkeypatch.setattr(settings, "min_score", 75.0)
    assert settings.ready_score_floor == 90.0
    gate = DynamicMarketGate().evaluate(_analysis(), _contract())
    assert gate.required_score == 90.0
