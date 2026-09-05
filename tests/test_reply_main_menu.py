from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()


def test_main_menu_uses_reply_keyboard():
    assert "def _main_menu_markup() -> ReplyKeyboardMarkup" in SOURCE
    assert "return ReplyKeyboardMarkup(" in SOURCE
    assert "is_persistent=True" in SOURCE
    assert "one_time_keyboard=False" in SOURCE
    for label in (
        "🔍 Trading",
        "📂 Open Trades",
        "📊 Reports",
        "🎯 Success Rules",
        "🧪 اختبارات الرسائل",
        "🛡️ Risk",
        "⚙️ System",
    ):
        assert label in SOURCE


def test_approval_remains_inline():
    start = SOURCE.index("def _approval_markup()")
    chunk = SOURCE[start:start + 700]
    assert "-> InlineKeyboardMarkup" in chunk
    assert "InlineKeyboardButton(\"✅ Approve\"" in chunk
    assert "InlineKeyboardButton(\"❌ Cancel\"" in chunk


def test_main_reply_buttons_route_to_inline_submenus():
    start = SOURCE.index("async def text_input")
    chunk = SOURCE[start:start + 2600]
    assert '"🔍 Trading": ("🔍 Trading Menu", self._trading_menu_markup)' in chunk
    assert '"📊 Reports": ("📊 Reports", self._reports_menu_markup)' in chunk
    assert '"⚙️ System": ("⚙️ System", lambda: self._system_menu_markup(self._paused()))' in chunk
