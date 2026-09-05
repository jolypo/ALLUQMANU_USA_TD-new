from pathlib import Path


def test_waseem_v3_monitor_start_uses_spx_option_session_gate():
    source = Path("app/telegram/bots.py").read_text(encoding="utf-8")
    marker = 'if data.startswith("monitor:start:"):'
    start = source.index(marker)
    end = source.index('if data.startswith("monitor:stop:"):', start)
    block = source[start:end]
    assert 'if key == "index:waseem_v3":' in block
    assert 'self.service.spx_option_session_status()' in block
    assert 'self.service.market_is_open()' in block  # preserved fallback for all other engines


def test_waseem_v3_manual_scan_keeps_spx_option_session_gate():
    source = Path("app/telegram/bots.py").read_text(encoding="utf-8")
    marker = 'async def _run_scan('
    start = source.index(marker)
    end = source.index('requested = self._requested_count', start)
    block = source[start:end]
    assert 'index_strategy).lower() in {"waseem_v3", "waseem3", "v3"}' in block
    assert 'self.service.spx_option_session_status()' in block
