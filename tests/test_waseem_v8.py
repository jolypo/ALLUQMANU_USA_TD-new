from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.options.waseem_selector import WaseemContractSelector
from app.scheduler.profit_watcher import OpenOptionProfitWatcher


def occ(root: str, cp: str, strike: float) -> str:
    d = datetime.now(ZoneInfo("America/New_York")).strftime("%y%m%d")
    return f"{root}{d}{cp}{int(round(strike*1000)):08d}"


def snap(delta=0.5, bid=2.9, ask=3.0, volume=400, bs=20, ass=20):
    now = datetime.now(ZoneInfo("UTC")).isoformat()
    return {
        "latestQuote": {"bp": bid, "ap": ask, "bs": bs, "as": ass, "t": now},
        "greeks": {} if delta is None else {"delta": delta, "theta": -0.04},
        "dailyBar": {"v": volume},
        "impliedVolatility": 0.55,
    }


def test_waseem_daily_allows_missing_greeks_and_near_otm_call():
    selector = WaseemContractSelector()
    symbol = occ("TSLA", "C", 305)
    rows, diag = selector.rank(
        {"snapshots": {symbol: snap(delta=None)}},
        "LONG", "TSLA", 300.0,
        min_dte=0, max_dte=0, horizon="DAILY",
        expected_move=7.0, is_index=False,
    )
    assert rows
    assert rows[0]["type"] == "CALL"
    assert rows[0]["strike"] == 305
    assert rows[0]["delta"] is None
    assert rows[0]["selection_engine"] == "WASEEM_V1"


def test_waseem_call_put_are_directional_mirrors():
    selector = WaseemContractSelector()
    c = occ("TSLA", "C", 305)
    p = occ("TSLA", "P", 295)
    payload = {"snapshots": {c: snap(delta=0.48), p: snap(delta=-0.48)}}
    calls, _ = selector.rank(payload, "LONG", "TSLA", 300, min_dte=0, max_dte=0, horizon="DAILY", expected_move=7)
    puts, _ = selector.rank(payload, "SHORT", "TSLA", 300, min_dte=0, max_dte=0, horizon="DAILY", expected_move=7)
    assert calls[0]["type"] == "CALL" and calls[0]["strike"] == 305
    assert puts[0]["type"] == "PUT" and puts[0]["strike"] == 295


def test_waseem_rejects_far_equity_lottery_strike():
    selector = WaseemContractSelector()
    far = occ("TSLA", "C", 340)
    rows, diag = selector.rank(
        {"snapshots": {far: snap()}}, "LONG", "TSLA", 300,
        min_dte=0, max_dte=0, horizon="DAILY", expected_move=7,
    )
    assert not rows
    assert any("strike_too_far" in x for x in diag)


def test_waseem_spx_near_otm_max_40_points():
    selector = WaseemContractSelector()
    near = occ("SPXW", "P", 7670)
    far = occ("SPXW", "P", 7640)
    rows, diag = selector.rank(
        {"snapshots": {near: snap(delta=-0.45), far: snap(delta=-0.35)}},
        "SHORT", "SPX", 7700,
        min_dte=0, max_dte=0, horizon="DAILY", expected_move=35,
        is_index=True, max_results=3,
    )
    assert rows
    assert rows[0]["strike"] == 7670
    assert all(r["strike_distance"] <= 40 for r in rows)


def test_waseem_buttons_exist_in_both_option_menus():
    source = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()
    assert 'callback_data="menu:horizon:option:waseem"' in source
    assert 'callback_data="menu:horizon:index:waseem"' in source
    assert '"option:waseem": "both"' in source
    assert '"index:waseem": "both"' in source


class FakeRepo:
    def __init__(self, rows):
        self.rows = rows
    def all(self):
        return [dict(r) for r in self.rows]
    def update_trade(self, trade_id, fields):
        for r in self.rows:
            if r.get("trade_id") == trade_id:
                r.update(fields)
                return True
        return False


class FakeProvider:
    def __init__(self, prices):
        self.prices = list(prices)
    async def option_quotes(self, symbols):
        price = self.prices.pop(0)
        return {symbols[0]: {"bp": price-0.01, "ap": price+0.01, "t": datetime.now(ZoneInfo("UTC")).isoformat()}}


class FakeBot:
    def __init__(self):
        self.sent = []
    async def send_photo(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent))


@pytest.mark.asyncio
async def test_profit_watcher_uses_last_alert_price_and_does_not_repeat(monkeypatch, tmp_path):
    trade = {
        "trade_id": "T1", "status": "OPEN", "entry_confirmed": True,
        "filled_entry_price": 8.93, "contracts": 1, "symbol": "TSLA",
        "option": {"symbol": occ("TSLA", "C", 310), "strike": 310, "type": "CALL"},
    }
    repo = FakeRepo([trade])
    provider = FakeProvider([9.04, 9.04, 9.15])
    bot = FakeBot()
    watcher = OpenOptionProfitWatcher(repo, provider, bot, -100, interval=60)
    monkeypatch.setattr("app.scheduler.profit_watcher.profit_alert_rules.get_step", lambda: 0.10)
    monkeypatch.setattr("app.scheduler.profit_watcher.profit_update_card", lambda trade, usd, sar, price, path: Path(path).write_bytes(b"x"))

    await watcher.cycle()  # +0.11 -> send
    await watcher.cycle()  # same price -> no duplicate
    await watcher.cycle()  # +0.11 from last alert -> send
    assert len(bot.sent) == 2
    assert repo.rows[0]["profit_alert_last_price"] == pytest.approx(9.15)
