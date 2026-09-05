import pandas as pd
from app.market.waseem_v4_liquidity import WaseemV4LiquidityEngine


def _bars():
    rows=[]
    px=100.0
    for i in range(30):
        px += 0.10 if i < 24 else 0.18
        rows.append({"open":px-0.08,"high":px+0.20,"low":px-0.20,"close":px,"volume":1000+i*30+(500 if i>=27 else 0)})
    return pd.DataFrame(rows)

def test_v4_liquidity_engine_outputs_scores():
    r=WaseemV4LiquidityEngine().evaluate(_bars(), "LONG", session="RTH")
    assert 0 <= r.score <= 100
    assert 0 <= r.pre_move_score <= 100
    assert r.flow_confidence == "HIGH"
    assert r.external_target is not None
    assert len(r.diagnostics) >= 6

def test_v4_gth_confidence_is_not_fake_level2():
    r=WaseemV4LiquidityEngine().evaluate(_bars(), "SHORT", session="GTH")
    assert r.flow_confidence == "MEDIUM"
    assert any("Level2/DOM" in x for x in r.diagnostics)
