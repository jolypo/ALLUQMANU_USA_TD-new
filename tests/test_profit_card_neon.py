from PIL import Image

from app.reports.profit_card import _tier, profit_update_card


def test_profit_card_uses_approved_dynamic_neon_tiers(tmp_path):
    trade = {
        "symbol": "AAPL",
        "trade_id": "TEST-PROFIT",
        "option": {"strike": 320.0, "type": "CALL"},
    }

    green = tmp_path / "green.png"
    gold = tmp_path / "gold.png"
    blue = tmp_path / "blue.png"

    profit_update_card(trade, 50.0, 187.5, 2.81, str(green))
    profit_update_card(trade, 150.0, 562.5, 3.81, str(gold))
    profit_update_card(trade, 350.0, 1312.5, 5.81, str(blue))

    for path in (green, gold, blue):
        assert path.exists()
        with Image.open(path) as im:
            assert im.size == (1200, 900)
            assert im.mode == "RGB"

    assert _tier(50.0)[0] != _tier(150.0)[0]
    assert _tier(150.0)[0] != _tier(350.0)[0]


def test_congratulatory_card_never_renders_negative_profit_amount(tmp_path):
    trade = {"symbol": "MU", "trade_id": "TEST-NEG", "option": {"strike": 935, "type": "CALL"}}
    path = tmp_path / "defensive.png"
    profit_update_card(trade, -25.0, -93.75, 33.35, str(path))
    assert path.exists()
