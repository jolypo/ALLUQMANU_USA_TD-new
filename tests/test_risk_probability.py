from app.risk.engine import RiskEngine
from app.probability.engine import ProbabilityEngine

def test_risk_rejects_low_rr():
    ok,_,_=RiskEngine().assess(90,"GOOD",1.0)
    assert not ok

def test_probability_unvalidated_small_sample():
    r=ProbabilityEngine().summarize([],"STOCK_SWING")
    assert r["status"]=="UNVALIDATED" and r["probability"] is None
