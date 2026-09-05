from pathlib import Path

from app.config import settings
from app.learning import LearningStore
from app.models.domain import Decision, Signal, TradeType
from app.strategies.judge import JudgeEngine


class HistoryRepo:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return list(self.rows)


def closed_trade(i, outcome, *, mode="CONFIRMED_SETUP", direction="LONG", market="NORMAL", liquidity="HIGH"):
    return {
        "trade_id": f"T-{i}",
        "symbol": "NVDA",
        "trade_type": "EQUITY_OPTION_INTRADAY",
        "direction": "LONG",
        "score": 94,
        "status": "CLOSED",
        "final_result": outcome,
        "market_state": market,
        "liquidity_state": liquidity,
        "volatility_state": "NORMAL",
        "closed_at": f"2026-08-29T12:{i%60:02d}:00+00:00",
        "option": {
            "strategy_mode": mode,
            "underlying_direction": direction,
            "horizon": "WEEKLY",
            "dte": 3,
            "judge_score": 93,
        },
    }


def signal(score=94, contract=94):
    return Signal(
        symbol="NVDA",
        trade_type=TradeType.EQUITY_OPTION_INTRADAY,
        direction="LONG",
        decision=Decision.READY,
        score=score,
        entry_low=2.0,
        entry_high=2.05,
        stop=1.7,
        tp1=2.4,
        tp2=2.6,
        tp3=2.9,
        rr=2.0,
        risk_pct=0.005,
        sector="Semiconductors",
        market_state="NORMAL",
        liquidity_state="HIGH",
        volatility_state="NORMAL",
        option={
            "strategy_mode": "CONFIRMED_SETUP",
            "underlying_direction": "LONG",
            "horizon": "WEEKLY",
            "contract_score": contract,
            "bid": 2.00,
            "ask": 2.04,
        },
    )


def test_learning_ignores_non_confirmed_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "store_dir", str(tmp_path))
    rows = [closed_trade(i, "WIN") for i in range(5)]
    rows.append(closed_trade(99, "WIN", mode="SPX_CORE"))
    store = LearningStore(HistoryRepo(rows))
    assert store.refresh_from_history() == 5
    summary = store.summary()
    assert summary["samples"] == 5
    assert summary["status"] == "COLLECTING"
    assert Path(summary["memory_file"]).exists()


def test_learning_activates_after_minimum_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "store_dir", str(tmp_path))
    monkeypatch.setattr(settings, "learning_min_global_samples", 12)
    monkeypatch.setattr(settings, "learning_min_bucket_samples", 5)
    rows = [closed_trade(i, "LOSS" if i < 10 else "WIN") for i in range(12)]
    store = LearningStore(HistoryRepo(rows))
    learned = store.adjustment_for_signal(signal())
    assert learned.status == "ACTIVE"
    assert learned.samples == 12
    assert learned.adjustment < 0


def test_learning_does_not_rescue_raw_judge_below_90(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "store_dir", str(tmp_path))
    monkeypatch.setattr(settings, "learning_min_global_samples", 12)
    rows = [closed_trade(i, "WIN") for i in range(20)]
    judge = JudgeEngine(LearningStore(HistoryRepo(rows)))
    s = signal(score=70, contract=75)
    raw, _ = judge._raw_score(s)
    assert raw < 90
    final, _ = judge.score(s)
    assert final == raw
    assert judge.rank([s]) == []


def test_learning_can_penalize_weak_historical_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "store_dir", str(tmp_path))
    monkeypatch.setattr(settings, "learning_min_global_samples", 12)
    monkeypatch.setattr(settings, "learning_min_bucket_samples", 5)
    rows = [closed_trade(i, "LOSS") for i in range(20)]
    judge = JudgeEngine(LearningStore(HistoryRepo(rows)))
    s = signal(score=90, contract=90)
    raw, _ = judge._raw_score(s)
    assert raw >= 90
    final, _ = judge.score(s)
    assert final < raw
    assert s.option["learning_status"] == "ACTIVE"


def test_success_threshold_is_learned_as_statistical_win(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "store_dir", str(tmp_path))
    row = closed_trade(1, "LOSS")
    row["success_reached"] = True
    store = LearningStore(HistoryRepo([row]))
    store.refresh_from_history()
    data = store._read_unlocked()
    assert data["samples"]["T-1"]["outcome"] == "WIN"
