from datetime import datetime, timezone
from app.options.selector import parse_occ,ContractSelector

def test_occ_parser():
    d=parse_occ("AAPL260918C00185000")
    assert d["type"]=="CALL" and d["strike"]==185

def test_selector():
    p={"snapshots":{"AAPL260918C00185000":{"latestQuote":{"bp":6.1,"ap":6.3,"t":datetime.now(timezone.utc).isoformat()},"greeks":{"delta":0.58,"gamma":0.03,"theta":-0.1,"vega":0.15},"impliedVolatility":0.42}}}
    c=ContractSelector().select(p,"LONG")
    assert c and c["type"]=="CALL"

def test_spxw_selector_accepts_weekly_root_without_spy_price_filter():
    p={"snapshots":{"SPXW260918C06500000":{"latestQuote":{"bp":50.0,"ap":51.0,"t":datetime.now(timezone.utc).isoformat()},"greeks":{"delta":0.55,"gamma":0.01,"theta":-0.5,"vega":0.2},"impliedVolatility":0.20}}}
    c=ContractSelector().select(p,"LONG",expected_underlying="SPX",underlying_price=None)
    assert c and c["root"]=="SPXW" and c["type"]=="CALL"


def test_selector_allows_explicit_zero_dte_for_spx():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    expiry = datetime.now(ZoneInfo("America/New_York")).strftime("%y%m%d")
    symbol = f"SPXW{expiry}C07000000"
    payload = {
        "snapshots": {
            symbol: {
                "latestQuote": {"bp": 1.70, "ap": 1.80, "t": datetime.now(timezone.utc).isoformat()},
                "greeks": {
                    "delta": 0.55,
                    "gamma": 0.01,
                    "theta": -0.10,
                    "vega": 0.05,
                },
                "impliedVolatility": 0.20,
                "dailyBar": {"v": 1000},
            }
        }
    }
    selector = ContractSelector()
    assert selector.select(payload, "LONG", expected_underlying="SPX") is None
    c = selector.select(
        payload,
        "LONG",
        expected_underlying="SPX",
        min_dte=0,
        max_dte=0,
        max_spread_pct=8,
        min_abs_delta=0.40,
        max_abs_delta=0.65,
        min_contract_score=72,
    )
    assert c and c["dte"] == 0 and c["root"] == "SPXW"


def test_selector_chooses_put_for_short_direction():
    from zoneinfo import ZoneInfo
    expiry = datetime.now(ZoneInfo("America/New_York")).date().strftime("%y%m%d")
    symbol = f"AAPL{expiry}P00200000"
    payload = {
        "snapshots": {
            symbol: {
                "latestQuote": {"bp": 2.00, "ap": 2.05, "t": datetime.now(timezone.utc).isoformat()},
                "greeks": {"delta": -0.55, "gamma": 0.02, "theta": -0.05, "vega": 0.10},
                "dailyBar": {"v": 500},
                "impliedVolatility": 0.30,
            }
        }
    }
    c = ContractSelector().select(
        payload, "SHORT", expected_underlying="AAPL", underlying_price=200,
        min_dte=0, max_dte=0,
    )
    assert c is not None
    assert c["type"] == "PUT"
    assert c["delta"] < 0
