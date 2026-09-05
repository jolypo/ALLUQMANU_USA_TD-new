from PIL import Image
from app.reports.card import option_card


def test_option_card_is_horizontal_and_dynamic(tmp_path):
    path = tmp_path / "card.png"
    signal = {
        "symbol": "SPCX",
        "created_at": "2026-08-28T15:00:00+00:00",
        "entry_high": 1.80,
        "option": {"type": "CALL", "strike": 140, "entry_high": 1.80},
    }
    option_card(signal, str(path))
    with Image.open(path) as im:
        assert im.size == (2048, 680)


from app.reports.card import _option_subtitle


def test_option_card_prefers_real_expiration_subtitle():
    signal = {
        "symbol": "MSFT",
        "created_at": "2026-08-28T15:00:00+00:00",
        "option": {"expiration": "2026-09-25", "dte": 28},
    }
    assert _option_subtitle(signal) == "EXP 2026-09-25 • DTE 28"
