import json
from pathlib import Path

import pytest

from app.learning import LearningStore


class HistoryRepo:
    def all(self):
        return []


def _bots_source():
    return Path('app/telegram/bots.py').read_text(encoding='utf-8')


def test_system_menu_exposes_learning_button():
    source = _bots_source()
    assert 'InlineKeyboardButton("🧠 Learning", callback_data="menu:learning")' in source


def test_learning_menu_has_status_export_import():
    source = _bots_source()
    for callback in ('learning:status', 'learning:export', 'learning:import'):
        assert f'callback_data="{callback}"' in source
    assert 'MessageHandler(filters.Document.ALL, self.document_input)' in source


def test_learning_import_merges_by_trade_id(tmp_path):
    store = LearningStore(HistoryRepo(), path=tmp_path / 'memory.json')
    payload = {
        'version': 1,
        'samples': {
            'T-1': {'trade_id': 'T-1', 'outcome': 'WIN', 'symbol': 'NVDA'},
            'T-2': {'trade_id': 'T-2', 'outcome': 'LOSS', 'symbol': 'TSLA'},
        },
    }
    src = tmp_path / 'import.json'
    src.write_text(json.dumps(payload), encoding='utf-8')
    first = store.import_memory_file(src)
    second = store.import_memory_file(src)
    assert first == {'received': 2, 'added': 2, 'total': 2}
    assert second == {'received': 2, 'added': 0, 'total': 2}


def test_learning_import_rejects_bad_version(tmp_path):
    store = LearningStore(HistoryRepo(), path=tmp_path / 'memory.json')
    src = tmp_path / 'bad.json'
    src.write_text(json.dumps({'version': 999, 'samples': {}}), encoding='utf-8')
    with pytest.raises(ValueError):
        store.import_memory_file(src)
