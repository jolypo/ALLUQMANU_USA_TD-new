import pandas as pd

from app.models.domain import Signal, TradeType, Decision
from app.strategies.confirmed_setup import ConfirmedSetupEngine
from app.strategies.judge import JudgeEngine
from pathlib import Path


def _trend_df(long=True):
    base = [95 + i * (4 / 25) for i in range(26)] + [100.5, 101.0, 101.5, 102.0]
    if not long:
        base = [200 - x for x in base]
    return pd.DataFrame({
        "open": base,
        "high": [x + 0.4 for x in base],
        "low": [x - 0.4 for x in base],
        "close": base,
        "volume": [1000] * len(base),
    })


def _signal(symbol, sector, underlying_direction, score=94, contract_score=94, bid=2.00, ask=2.04):
    return Signal(
        symbol=symbol,
        trade_type=TradeType.EQUITY_OPTION_INTRADAY,
        direction="LONG",
        decision=Decision.READY,
        score=score,
        entry_low=2.02,
        entry_high=ask,
        stop=1.70,
        tp1=2.40,
        tp2=2.60,
        tp3=2.90,
        rr=2.0,
        risk_pct=0.005,
        sector=sector,
        market_state="NORMAL",
        liquidity_state="HIGH",
        option={
            "type": "call" if underlying_direction == "LONG" else "put",
            "underlying_direction": underlying_direction,
            "contract_score": contract_score,
            "bid": bid,
            "ask": ask,
        },
    )


def test_confirmed_setup_is_symmetric_for_call_and_put():
    engine = ConfirmedSetupEngine(lookback=20, breakout_window=4, hold_bars=2)
    bull = engine.evaluate(_trend_df(True), "LONG", {})
    bear = engine.evaluate(_trend_df(False), "SHORT", {})
    assert bull.ready and bear.ready
    assert bull.state == bear.state == "CONFIRMED"
    assert bull.path == bear.path == "HOLD"
    assert bull.structure_confirmed and bear.structure_confirmed
    assert bull.momentum_confirmed and bear.momentum_confirmed


def test_confirmed_setup_waits_without_breakout():
    engine = ConfirmedSetupEngine(lookback=20, breakout_window=4, hold_bars=2)
    flat = pd.DataFrame({
        "open": [100.0] * 30,
        "high": [100.5] * 30,
        "low": [99.5] * 30,
        "close": [100.0] * 30,
        "volume": [1000] * 30,
    })
    result = engine.evaluate(flat, "LONG", {})
    assert not result.ready
    assert result.state == "WAIT_BREAKOUT"


def test_judge_ranks_and_applies_same_sector_correlation_guard():
    judge = JudgeEngine()
    a = _signal("NVDA", "Semiconductors", "LONG", score=96, contract_score=95)
    b = _signal("AMD", "Semiconductors", "LONG", score=95, contract_score=94)
    c = _signal("TSLA", "Consumer Discretionary", "SHORT", score=94, contract_score=95)
    ranked = judge.rank([a, b, c], max_results=3)
    symbols = [row.symbol for row in ranked]
    assert "NVDA" in symbols
    assert "AMD" not in symbols
    assert "TSLA" in symbols
    assert ranked[0].option["judge_rank"] == 1
    assert all(row.option["strategy_mode"] == "CONFIRMED_SETUP" for row in ranked)


def test_telegram_has_separate_confirmed_setup_paths_without_replacing_legacy():
    source = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()
    assert 'callback_data="menu:horizon:option"' in source
    assert 'callback_data="menu:horizon:option:confirmed"' in source
    assert 'callback_data="menu:horizon:index:v20"' in source
    assert 'callback_data="menu:horizon:index:core"' in source
    assert 'callback_data="menu:horizon:index:confirmed"' in source
    assert '"option:confirmed": "weekly"' in source
    assert '"index:confirmed": "daily"' in source
