from pathlib import Path

from app.runtime_settings import ContractSearchStore


def test_contract_price_store_migrates_legacy_category_price(tmp_path):
    path = tmp_path / "contract_search_settings.json"
    path.write_text(
        '{"equity_option":{"max_contract_price":5},"index_option":{"max_contract_price":10}}',
        encoding="utf-8",
    )
    store = ContractSearchStore(filename="unused.json")
    store.path = path
    store._ensure()
    for horizon in store.HORIZONS:
        assert store.get_max_price("equity_option", horizon) == 5
        assert store.get_max_price("index_option", horizon) == 10


def test_contract_price_menu_exposes_six_independent_controls():
    source = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()
    for callback in (
        "contract:set:equity_option:daily",
        "contract:set:equity_option:weekly",
        "contract:set:equity_option:monthly",
        "contract:set:index_option:daily",
        "contract:set:index_option:weekly",
        "contract:set:index_option:monthly",
    ):
        assert callback in source


def test_service_uses_horizon_specific_max_price():
    source = (Path(__file__).resolve().parents[1] / "app" / "trading" / "service.py").read_text()
    assert 'get_max_price("equity_option", price_horizon)' in source
    assert source.count('get_max_price(') >= 3
