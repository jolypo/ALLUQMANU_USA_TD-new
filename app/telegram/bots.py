from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from app.config import settings
from app.reports.card import option_card
from app.reports.profit_card import profit_update_card
from app.reports.performance import (
    performance,
    category_period_report,
    comprehensive_options_report,
)
from app.reports.weekly_card import (
    weekly_performance_card,
)
from app.telegram.messages import signal_text, signal_caption
from app.telegram.signal_delivery_policy import SignalDeliveryPolicy
from app.telegram.message_templates import candidate_full_text, entry_message, profit_update_message, success_message
from app.runtime_settings import success_rules, contract_search_rules, profit_alert_rules


class TelegramHub:
    """
    Telegram control layer.

    IMPORTANT:
    - Admin commands are private-only.
    - Scanning does NOT create Trades.
    - Scanning does NOT publish to the channel.
    - Admin scans -> chooses /pick -> confirms /publish.
    - Only /publish creates the Trade.
    """

    def __init__(
        self,
        service,
        open_repo,
        history_repo,
        state_repo,
    ):
        self.service = service
        self.open_repo = open_repo
        self.history_repo = history_repo
        self.state_repo = state_repo

        self.app = (
            Application.builder()
            .token(settings.signal_bot_token)
            .updater(None)
            .build()
        )

        self.profit = Bot(settings.profit_bot_token)
        self.report = Bot(settings.report_bot_token)

        # -----------------------------------------------------
        # Pending scan selections
        #
        # user_id -> {
        #     candidates: [...],
        #     scan_type: stock / option / index,
        #     created_monotonic: float,
        #     picked_index: int | None,
        #     published_indexes: set[int],
        # }
        # -----------------------------------------------------
        self.pending_scans: dict[int, dict] = {}

        # Continuous opportunity monitors. Each key is one independent scanner:
        # stock / option / index:v20 / index:core. They never publish by themselves.
        self.opportunity_monitor_tasks: dict[str, asyncio.Task] = {}
        self.opportunity_monitor_sessions: dict[str, dict] = {}
        self.watch_candidates: dict[int, dict[str, dict]] = {}
        self._monitor_scan_locks: dict[str, asyncio.Lock] = {}
        self._global_scan_lock = asyncio.Lock()
        # Cross-engine delivery policy: strategy logic remains untouched.
        self.signal_delivery_policy = SignalDeliveryPolicy(
            cooldown_seconds=settings.monitor_symbol_cooldown_seconds,
            upgrade_score_delta=settings.monitor_symbol_upgrade_score_delta,
        )
        # Expiration horizon selected from Telegram before option scans.
        # One active horizon per independent monitor.
        self.search_horizons: dict[str, str] = {
            "option": "weekly",
            "option:confirmed": "weekly",
            "option:waseem": "both",
            "option:waseem_v2": "both",
            "option:waseem_v3": "both",
            "option:waseem_v4": "both",
            "option:waseem_v5": "both",
            "option:waseem_v6": "both",
            "index:v20": "daily",
            "index:core": "daily",
            "index:confirmed": "daily",
            "index:waseem": "both",
            "index:waseem_v2": "both",
            "index:waseem_v3": "both",
            "index:waseem_v4": "both",
            "index:waseem_v5": "both",
            "index:waseem_v6": "both",
        }

        # Pending manual closes
        #
        # user_id -> {
        #     trade_id: "...",
        #     created_monotonic: float,
        # }
        self.pending_closes: dict[int, dict] = {}

        # Pending close-all confirmations
        self.pending_close_all: dict[int, float] = {}

        handlers = {
            "start": self.start,
            "help": self.help,
            "myid": self.myid,

            # Scan
            "stock": self.stock,
            "option": self.option,
            "indexoption": self.indexoption,

            # Manual approval
            "pick": self.pick,
            "pic1k": self.pick,
            "pic2k": self.pick,
            "pic3k": self.pick,
            "publish": self.publish,
            "cancel": self.cancel,

            # Manual close
            "close_stock": self.close_stock,
            "close_option": self.close_option,
            "close_index": self.close_index,
            "close_trade": self.close_trade,
            "confirm_close": self.confirm_close,
            "close_all": self.close_all,
            "confirm_close_all": self.confirm_close_all,

            # Existing commands
            "open": self.open_trades,
            "status": self.status,
            "health": self.status,
            "risk": self.risk,
            "performance": self.performance,
            "report": self.report_cmd,
            "settings": self.settings_cmd,
            "pause": self.pause,
            "resume": self.resume,
            "market": self.market,
            "testprofit": self.test_profit_alert,
        }

        for command, handler in handlers.items():
            self.app.add_handler(
                CommandHandler(command, handler)
            )

        # Telegram inline-menu navigation.
        # Existing slash commands stay available as a fallback, but the
        # normal admin workflow can now be completed entirely by buttons.
        self.app.add_handler(
            CallbackQueryHandler(self.menu_callback)
        )
        self.app.add_handler(
            MessageHandler(filters.Document.ALL, self.document_input)
        )
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_input)
        )

    # =========================================================
    # Inline Telegram Menus
    # =========================================================

    @staticmethod
    def _main_menu_markup() -> ReplyKeyboardMarkup:
        """Persistent private-admin home keyboard.

        Only the top-level menu uses ReplyKeyboardMarkup. All nested menus,
        candidate selection, and trade confirmation intentionally remain
        InlineKeyboardMarkup so callbacks stay bound to the exact workflow.
        """
        return ReplyKeyboardMarkup(
            [
                ["🔍 Trading", "📊 الأسهم"],
                ["📂 Open Trades", "📊 Reports"],
                ["🎯 Success Rules", "🧪 اختبارات الرسائل"],
                ["🛡️ Risk", "⚙️ System"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            one_time_keyboard=False,
            input_field_placeholder="اختر من القائمة الرئيسية",
        )

    @staticmethod
    def _back_main_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")]]
        )

    @staticmethod
    def _trading_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📈 Stocks", callback_data="menu:monitor:stock")],
                [InlineKeyboardButton("🟢 Equity Options", callback_data="menu:equity_strategy")],
                [InlineKeyboardButton("📊 Index Options", callback_data="menu:index_strategy")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:main")],
            ]
        )

    @staticmethod
    def _stocks_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 تحليل السهم", callback_data="stocks:analysis:list")],
            [InlineKeyboardButton("⚙️ إدارة الأسهم", callback_data="menu:watchlist")],
            [InlineKeyboardButton("📰 أخبار الأسهم", callback_data="stocks:news:list")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:main")],
        ])

    async def _stock_symbols_markup(self, mode: str) -> InlineKeyboardMarkup:
        repo = getattr(self.service, "equity_watchlist", None)
        rows = await repo.list(include_disabled=True) if repo is not None else [{"symbol":s,"enabled":True} for s in await self.service.equity_symbols()]
        buttons = []
        active = [r for r in rows if r.get("enabled", True)]
        for i in range(0, len(active), 3):
            row=[]
            for r in active[i:i+3]:
                sym=str(r.get("symbol","")).upper()
                row.append(InlineKeyboardButton(sym, callback_data=f"stocks:{mode}:{sym}"))
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu:stocks")])
        return InlineKeyboardMarkup(buttons)

    @staticmethod
    def _equity_strategy_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🧠 Core", callback_data="menu:horizon:option")],
                [InlineKeyboardButton("✅ Confirmed Setup", callback_data="menu:horizon:option:confirmed")],
                [InlineKeyboardButton("⚡ وسيم V1", callback_data="menu:horizon:option:waseem")],
                [InlineKeyboardButton("🚀 وسيم V2", callback_data="menu:horizon:option:waseem_v2")],
                [InlineKeyboardButton("🧪 وسيم V3", callback_data="menu:horizon:option:waseem_v3")],
                [InlineKeyboardButton("🌊 وسيم V4 · Liquidity", callback_data="menu:horizon:option:waseem_v4")],
                [InlineKeyboardButton("🧭 وسيم V5 · Order Flow", callback_data="menu:horizon:option:waseem_v5")],
                [InlineKeyboardButton("🛡️ وسيم V6 · Delayed-Aware", callback_data="menu:horizon:option:waseem_v6")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:trading")],
            ]
        )

    @staticmethod
    def _index_strategy_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🛰️ SPX V20", callback_data="menu:horizon:index:v20")],
                [InlineKeyboardButton("🧠 SPX Core", callback_data="menu:horizon:index:core")],
                [InlineKeyboardButton("✅ Confirmed Setup", callback_data="menu:horizon:index:confirmed")],
                [InlineKeyboardButton("⚡ وسيم V1", callback_data="menu:horizon:index:waseem")],
                [InlineKeyboardButton("🚀 وسيم V2", callback_data="menu:horizon:index:waseem_v2")],
                [InlineKeyboardButton("🧪 وسيم V3 · GTH", callback_data="menu:horizon:index:waseem_v3")],
                [InlineKeyboardButton("🌊 وسيم V4 · Liquidity", callback_data="menu:horizon:index:waseem_v4")],
                [InlineKeyboardButton("🧭 وسيم V5 · Order Flow", callback_data="menu:horizon:index:waseem_v5")],
                [InlineKeyboardButton("🛡️ وسيم V6 · SPX", callback_data="menu:horizon:index:waseem_v6")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:trading")],
            ]
        )

    @staticmethod
    def _horizon_markup(key: str) -> InlineKeyboardMarkup:
        back = "menu:index_strategy" if key.startswith("index:") else ("menu:equity_strategy" if key.startswith("option") else "menu:trading")
        rows = []
        if key.endswith(":waseem") or key.endswith(":waseem_v2") or key.endswith(":waseem_v3") or key.endswith(":waseem_v4") or key.endswith(":waseem_v5") or key.endswith(":waseem_v6"):
            rows.append([InlineKeyboardButton("⚡ Daily + Weekly معًا", callback_data=f"horizon:select:{key}:both")])
        rows.extend([
            [InlineKeyboardButton("⚡ Daily 0DTE only", callback_data=f"horizon:select:{key}:daily")],
            [InlineKeyboardButton("📅 Weekly 1–7 DTE", callback_data=f"horizon:select:{key}:weekly")],
            [InlineKeyboardButton("🗓️ Monthly 8–35 DTE", callback_data=f"horizon:select:{key}:monthly")],
            [InlineKeyboardButton("🔙 Back", callback_data=back)],
        ])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _horizon_label(value: str | None) -> str:
        return {
            "daily": "Daily · 0DTE only",
            "weekly": "Weekly · 1–7 DTE",
            "monthly": "Monthly · 8–35 DTE",
            "both": "Daily 0DTE + Weekly 1–7 DTE",
        }.get(str(value or "").lower(), "Default")

    @staticmethod
    def _monitor_control_markup(key: str, running: bool) -> InlineKeyboardMarkup:
        start_stop = (
            InlineKeyboardButton("⏹️ Stop Monitoring", callback_data=f"monitor:stop:{key}")
            if running
            else InlineKeyboardButton("▶️ Start Monitoring", callback_data=f"monitor:start:{key}")
        )
        back = "menu:index_strategy" if key.startswith("index:") else ("menu:equity_strategy" if key.startswith("option") else "menu:trading")
        rows = [
            [start_stop],
            [InlineKeyboardButton("🔎 Scan Now", callback_data=f"monitor:scan:{key}")],
        ]
        if key != "stock":
            rows.append([InlineKeyboardButton("📅 Change Expiration", callback_data=f"menu:horizon:{key}")])
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=back)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _settings_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Show Settings", callback_data="cmd:settings")],
            [InlineKeyboardButton("💵 Contract Search Price", callback_data="menu:contract_search")],
            [InlineKeyboardButton("📈 Profit Alert Step", callback_data="menu:profit_alert_step")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu:system")],
        ])

    @staticmethod
    def _contract_search_menu_markup() -> InlineKeyboardMarkup:
        rules = contract_search_rules.all()

        def fmt(category: str, horizon: str) -> str:
            value = float((rules.get(category) or {}).get(horizon, 0) or 0)
            return "Unlimited" if value <= 0 else f"≤ ${value:,.2f}"

        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"🟢 Equity 0DTE: {fmt('equity_option', 'daily')}",
                callback_data="contract:set:equity_option:daily",
            )],
            [InlineKeyboardButton(
                f"🟢 Equity 1–7 DTE: {fmt('equity_option', 'weekly')}",
                callback_data="contract:set:equity_option:weekly",
            )],
            [InlineKeyboardButton(
                f"🟢 Equity 8–35 DTE: {fmt('equity_option', 'monthly')}",
                callback_data="contract:set:equity_option:monthly",
            )],
            [InlineKeyboardButton(
                f"📊 SPX 0DTE: {fmt('index_option', 'daily')}",
                callback_data="contract:set:index_option:daily",
            )],
            [InlineKeyboardButton(
                f"📊 SPX 1–7 DTE: {fmt('index_option', 'weekly')}",
                callback_data="contract:set:index_option:weekly",
            )],
            [InlineKeyboardButton(
                f"📊 SPX 8–35 DTE: {fmt('index_option', 'monthly')}",
                callback_data="contract:set:index_option:monthly",
            )],
            [InlineKeyboardButton("♻️ Restore All Defaults", callback_data="contract:reset:all")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu:settings")],
        ])

    @staticmethod
    def _reports_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📈 Performance", callback_data="menu:performance")],
                [InlineKeyboardButton("🗓️ Daily Report", callback_data="menu:daily")],
                [InlineKeyboardButton("📊 Weekly Report", callback_data="menu:weekly")],
                [InlineKeyboardButton("🌎 Market Status", callback_data="cmd:market")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:main")],
            ]
        )

    @staticmethod
    def _performance_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📈 Stocks Performance", callback_data="perf:stock")],
                [InlineKeyboardButton("🟢 Equity Options Performance", callback_data="perf:equity_option")],
                [InlineKeyboardButton("📊 Index Options Performance", callback_data="perf:index_option")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:reports")],
            ]
        )

    @staticmethod
    def _daily_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🌐 جميع العقود — يومي شامل", callback_data="daily:options_all:all")],
                [InlineKeyboardButton("📈 Stocks Daily", callback_data="daily:stock:all")],
                [InlineKeyboardButton("🟢 Equity 0DTE", callback_data="daily:equity_option:daily"),
                 InlineKeyboardButton("📊 SPX 0DTE", callback_data="daily:index_option:daily")],
                [InlineKeyboardButton("🟢 Equity 1–7 DTE", callback_data="daily:equity_option:weekly"),
                 InlineKeyboardButton("📊 SPX 1–7 DTE", callback_data="daily:index_option:weekly")],
                [InlineKeyboardButton("🟢 Equity 8–35 DTE", callback_data="daily:equity_option:monthly"),
                 InlineKeyboardButton("📊 SPX 8–35 DTE", callback_data="daily:index_option:monthly")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:reports")],
            ]
        )

    @staticmethod
    def _weekly_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🌐 جميع العقود — أسبوعي شامل", callback_data="weekly:options_all:all")],
                [InlineKeyboardButton("📈 Stocks Weekly", callback_data="weekly:stock:all")],
                [InlineKeyboardButton("🟢 Equity 0DTE", callback_data="weekly:equity_option:daily"),
                 InlineKeyboardButton("📊 SPX 0DTE", callback_data="weekly:index_option:daily")],
                [InlineKeyboardButton("🟢 Equity 1–7 DTE", callback_data="weekly:equity_option:weekly"),
                 InlineKeyboardButton("📊 SPX 1–7 DTE", callback_data="weekly:index_option:weekly")],
                [InlineKeyboardButton("🟢 Equity 8–35 DTE", callback_data="weekly:equity_option:monthly"),
                 InlineKeyboardButton("📊 SPX 8–35 DTE", callback_data="weekly:index_option:monthly")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:reports")],
            ]
        )

    @staticmethod
    def _success_rules_menu_markup() -> InlineKeyboardMarkup:
        rules = success_rules.all()

        def fmt(category: str) -> str:
            row = rules[category]
            value = float(row.get("threshold", 0) or 0)
            if value <= 0:
                return "OFF"
            if row.get("unit") == "USD":
                return f"+${value:,.2f}"
            return f"+{value:.2f}%"

        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(
                    "📈 Stocks: حسب الأهداف",
                    callback_data="success:stocks_info",
                )],
                [InlineKeyboardButton(
                    f"🟢 Equity Options: {fmt('equity_option')}",
                    callback_data="success:set:equity_option",
                )],
                [InlineKeyboardButton(
                    f"📊 Index Options: {fmt('index_option')}",
                    callback_data="success:set:index_option",
                )],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:main")],
            ]
        )

    @staticmethod
    def _message_tests_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚨 اختبار فرصة عقود", callback_data="test:template:opportunity")],
                [InlineKeyboardButton("🔥 اختبار READY", callback_data="test:template:ready"), InlineKeyboardButton("👁 اختبار WATCH", callback_data="test:template:watch")],
                [InlineKeyboardButton("🛡️ اختبار وسيم V6", callback_data="test:template:v6")],
                [InlineKeyboardButton("✅ اختبار نجاح الإشارة", callback_data="test:template:success")],
                [InlineKeyboardButton("✅ اختبار الدخول", callback_data="test:template:entry")],
                [InlineKeyboardButton("📈 اختبار تحديث الأرباح", callback_data="test:template:profit")],
                [InlineKeyboardButton("📊 اختبار تحليل السهم", callback_data="test:template:stock_analysis"), InlineKeyboardButton("📰 اختبار أخبار السهم", callback_data="test:template:stock_news")],
                [InlineKeyboardButton("📊 اختبار فرصة SPX", callback_data="test:signal:index_option")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:main")],
            ]
        )

    @staticmethod
    def _risk_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🛡️ Risk Status", callback_data="cmd:risk")],
                [InlineKeyboardButton("📂 Open Risk", callback_data="cmd:open")],
                [InlineKeyboardButton("⚙️ Risk Settings", callback_data="cmd:settings")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:main")],
            ]
        )

    @staticmethod
    def _system_menu_markup(paused: bool) -> InlineKeyboardMarkup:
        toggle = (
            InlineKeyboardButton("▶️ Resume Scanning", callback_data="cmd:resume")
            if paused
            else InlineKeyboardButton("⏸ Pause Scanning", callback_data="cmd:pause")
        )
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("❤️ Health", callback_data="cmd:health")],
                [InlineKeyboardButton("📡 Status", callback_data="cmd:status")],
                [InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings")],
                [InlineKeyboardButton("🧠 Learning", callback_data="menu:learning")],
                [toggle],
                [InlineKeyboardButton("👤 My ID", callback_data="cmd:myid")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:main")],
            ]
        )

    @staticmethod
    def _learning_menu_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📊 Learning Status", callback_data="learning:status")],
                [InlineKeyboardButton("📤 Export Learning File", callback_data="learning:export")],
                [InlineKeyboardButton("📥 Import Learning File", callback_data="learning:import")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:system")],
            ]
        )

    def _learning_status_text(self) -> str:
        info = self.service.learning.summary()
        rate = info.get("bayesian_win_rate")
        rate_text = "N/A" if rate is None else f"{float(rate):.1f}%"
        return (
            "🧠 Learning Memory\n\n"
            f"Status: {info.get('status', 'COLLECTING')}\n"
            f"Samples: {info.get('samples', 0)}/{info.get('required_samples', 12)}\n"
            f"Wins: {info.get('wins', 0)} | Losses: {info.get('losses', 0)} | BE: {info.get('breakeven', 0)}\n"
            f"Bayesian Win Rate: {rate_text}\n\n"
            "التعلّم طبقة إضافية فقط. إذا حُذف الملف يبقى Judge يعمل بالقواعد الأساسية، "
            "والـHard Floor 90 وبوابات السوق والسيولة لا تتعطل."
        )

    def _open_menu_markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📈 Stock Trades", callback_data="open:stock")],
                [InlineKeyboardButton("🟢 Equity Options", callback_data="open:option")],
                [InlineKeyboardButton("📊 Index Options", callback_data="open:index")],
                [InlineKeyboardButton("❌ Close Trade", callback_data="close:list:all")],
                [InlineKeyboardButton("❌ Close All", callback_data="close:all")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu:main")],
            ]
        )

    async def _watchlist_text(self) -> str:
        repo = getattr(self.service, "equity_watchlist", None)
        if repo is None:
            return "⚙️ إدارة الأسهم\n\nغير متاح"
        rows = await repo.list(include_disabled=True)
        status = repo.status()
        storage = "قاعدة بيانات دائمة" if status.persistent else "ذاكرة مؤقتة"
        body = ["⚙️ <b>إدارة الأسهم</b>", f"💾 <b>التخزين:</b> {storage}", ""]
        body.extend([f"{r['symbol']} {'✅ مفعّل' if r['enabled'] else '⏸ معطّل'}" for r in rows])
        if not status.persistent:
            body.extend(["", "⚠️ الإضافات الحالية تعمل مباشرة في الفحص، لكنها قد تختفي بعد Restart أو Redeploy."])
        return "\n".join(body)

    @staticmethod
    def _watchlist_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة", callback_data="watchlist:add"), InlineKeyboardButton("➖ حذف", callback_data="watchlist:remove")],
            [InlineKeyboardButton("⏸ تعطيل", callback_data="watchlist:disable"), InlineKeyboardButton("▶️ تفعيل", callback_data="watchlist:enable")],
            [InlineKeyboardButton("🔄 تحديث", callback_data="menu:watchlist")],
            [InlineKeyboardButton("🔙 الأسهم", callback_data="menu:stocks")],
        ])

    @staticmethod
    def _monitor_label(key: str) -> str:
        return {
            "stock": "Stock Monitor",
            "option": "Equity Options Core Monitor",
            "option:confirmed": "Equity Confirmed Setup Monitor",
            "option:waseem": "Equity Waseem V1 Monitor",
            "option:waseem_v2": "Equity Waseem V2 Monitor",
            "option:waseem_v3": "Equity Waseem V3 Monitor",
            "option:waseem_v4": "Equity Waseem V4 Monitor",
            "option:waseem_v5": "Equity Waseem V5 Order Flow Monitor",
            "option:waseem_v6": "Equity Waseem V6 Delayed-Aware Monitor",
            "index:v20": "SPX V20 Monitor",
            "index:core": "SPX Core Monitor",
            "index:confirmed": "SPX Confirmed Setup Monitor",
            "index:waseem": "SPX Waseem V1 Monitor",
            "index:waseem_v2": "SPX Waseem V2 Monitor",
            "index:waseem_v3": "SPX Waseem V3 GTH/RTH Monitor",
            "index:waseem_v4": "SPX Waseem V4 GTH/RTH Monitor",
            "index:waseem_v5": "SPX Waseem V5 Order Flow GTH/RTH Monitor",
            "index:waseem_v6": "SPX Waseem V6 Delayed-Aware GTH/RTH Monitor",
        }.get(key, key)

    @staticmethod
    def _monitor_parts(key: str) -> tuple[str, str]:
        if key.startswith("index:"):
            return "index", key.split(":", 1)[1]
        if key.startswith("option:"):
            return "option", key.split(":", 1)[1]
        return key, "core"

    def _monitor_running(self, key: str) -> bool:
        task = self.opportunity_monitor_tasks.get(key)
        return bool(task and not task.done())

    def _monitor_status_text(self, key: str) -> str:
        session = self.opportunity_monitor_sessions.get(key) or {}
        count = int(session.get("count", 0) or 0)
        status = "RUNNING ✅" if self._monitor_running(key) else "STOPPED ⏹️"
        kind, _ = self._monitor_parts(key)
        interval = self._monitor_interval(kind)
        horizon = self.search_horizons.get(key) if key != "stock" else None
        horizon_line = f"Expiration: {self._horizon_label(horizon)}\n" if horizon else ""
        return (
            f"🔎 {self._monitor_label(key)}\n\n"
            f"Status: {status}\n"
            f"{horizon_line}"
            f"Detected: {count}{' (continuous until close/Stop)' if (key.endswith(':waseem_v2') or key.endswith(':waseem_v3') or key.endswith(':waseem_v4') or key.endswith(':waseem_v5') or key.endswith(':waseem_v6')) else f'/{settings.monitor_max_opportunities}'}\n"
            f"Candidate TTL: {settings.candidate_ttl_seconds // 60} minutes\n"
            f"Scan interval: {interval} seconds\n\n"
            "READY opportunities only. Nothing is published until you approve."
        )

    @staticmethod
    def _monitor_interval(kind: str) -> int:
        if kind == "stock":
            return int(settings.stock_monitor_interval_seconds)
        if kind == "option":
            return int(settings.equity_option_monitor_interval_seconds)
        return int(settings.index_option_monitor_interval_seconds)

    async def _fetch_candidates(self, kind: str, index_strategy: str = "core", max_results: int = 3, horizon: str | None = None):
        if kind == "stock":
            return await self.service.best_stocks(max_results)
        if kind == "option":
            mode = str(index_strategy).lower()
            if mode in {"waseem_v6", "waseem6", "v6"}:
                return await self.service.best_equity_options_waseem_v6(max_results, horizon=horizon)
            if mode in {"waseem_v5", "waseem5", "v5"}:
                return await self.service.best_equity_options_waseem_v5(max_results, horizon=horizon)
            if mode in {"waseem_v4", "waseem4", "v4"}:
                return await self.service.best_equity_options_waseem_v4(max_results, horizon=horizon)
            if mode in {"waseem_v3", "waseem3", "v3"}:
                return await self.service.best_equity_options_waseem_v3(max_results, horizon=horizon)
            if mode in {"waseem_v2", "waseem2", "v2"}:
                return await self.service.best_equity_options_waseem_v2(max_results, horizon=horizon)
            if mode in {"waseem", "waseem_v1"}:
                return await self.service.best_equity_options_waseem(max_results, horizon=horizon)
            if mode in {"confirmed", "confirmed_setup"}:
                return await self.service.best_equity_options_confirmed(max_results, horizon=horizon)
            return await self.service.best_equity_options(max_results, horizon=horizon)
        return await self.service.best_index_options(max_results, strategy_mode=index_strategy, horizon=horizon)

    @staticmethod
    def _candidate_fingerprint(trade: dict) -> str:
        option = trade.get("option") or {}
        contract = str(option.get("symbol") or "").upper()
        if contract:
            return f"OPTION|{contract}|{option.get('strategy_mode','CORE')}"
        return "|".join([
            "STOCK",
            str(trade.get("symbol", "")).upper(),
            str(trade.get("trade_type", "")),
            str(trade.get("direction", "")),
        ])

    @staticmethod
    def _signal_strength(score) -> str:
        try:
            value = float(score)
        except (TypeError, ValueError):
            return "N/A"
        if value >= 90:
            return "🔥 استثنائية"
        if value >= 85:
            return "🟢 قوية جدًا"
        if value >= 80:
            return "🟢 قوية"
        if value >= 75:
            return "✅ جيدة"
        return "⚪ محدودة"

    @staticmethod
    def _fmt_money(value) -> str:
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return "N/A"

    @staticmethod
    def _detection_lag_text(data_timestamp, detected_at: datetime | None = None) -> str:
        if not data_timestamp:
            return "UNAVAILABLE"
        try:
            dt = datetime.fromisoformat(str(data_timestamp).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            detected = detected_at or datetime.now(timezone.utc)
            if detected.tzinfo is None:
                detected = detected.replace(tzinfo=timezone.utc)
            seconds = max(0, int((detected.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
            return f"{seconds // 60}m {seconds % 60}s"
        except Exception:
            return "UNAVAILABLE"

    def _candidate_details_text(self, trade: dict, detected_at: datetime | None = None) -> str:
        return candidate_full_text(
            trade,
            candidate_ttl_seconds=settings.candidate_ttl_seconds,
            detected_at=detected_at,
        )

    async def _send_watch_candidate(self, key: str, trade: dict) -> None:
        user_id = int(settings.telegram_admin_user_id)
        now_mono = time.monotonic()
        bucket = self.watch_candidates.setdefault(user_id, {})
        for old_id, old in list(bucket.items()):
            if now_mono - float(old.get("created_monotonic", 0.0) or 0.0) > settings.candidate_ttl_seconds:
                bucket.pop(old_id, None)
        candidate_id = uuid.uuid4().hex[:12]
        now_utc = datetime.now(timezone.utc)
        trade = dict(trade)
        trade["_candidate_detected_at"] = now_utc.isoformat()
        if str(trade.get("decision", "READY")).upper() == "WATCH":
            trade.setdefault("watch_added_at", now_utc.isoformat())
        bucket[candidate_id] = {
            "trade": trade,
            "key": key,
            "created_monotonic": time.monotonic(),
            "detected_at": now_utc.isoformat(),
        }
        if str(trade.get("decision", "READY")).upper() == "WATCH":
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("👁 Keep Watching", callback_data=f"watch:keep:{candidate_id}")],
                [InlineKeyboardButton("❌ Remove Watch", callback_data=f"watch:reject:{candidate_id}")],
            ])
        else:
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"watch:approve:{candidate_id}")],
                [InlineKeyboardButton("❌ Reject", callback_data=f"watch:reject:{candidate_id}")],
            ])
        await self.app.bot.send_message(
            chat_id=user_id,
            text=self._candidate_details_text(trade, now_utc),
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    async def _opportunity_monitor_loop(self, key: str) -> None:
        session = self.opportunity_monitor_sessions[key]
        kind, index_strategy = self._monitor_parts(key)
        interval = self._monitor_interval(kind)
        horizon = session.get("horizon")
        continuous_v2 = key.endswith(":waseem_v2")
        continuous_v3 = key.endswith(":waseem_v3") or key.endswith(":waseem_v4") or key.endswith(":waseem_v5") or key.endswith(":waseem_v6")
        is_v4 = key.endswith(":waseem_v4")
        is_v5 = key.endswith(":waseem_v5")
        is_v6 = key.endswith(":waseem_v6")
        is_auto_watch = is_v4 or is_v5 or is_v6
        continuous_engine = continuous_v2 or continuous_v3
        try:
            while continuous_engine or int(session.get("count", 0)) < int(settings.monitor_max_opportunities):
                if self._paused():
                    await asyncio.sleep(min(interval, 30))
                    continue
                if key in {"index:waseem_v3", "index:waseem_v4", "index:waseem_v5", "index:waseem_v6"}:
                    state = self.service.spx_option_session_status()
                    is_open = bool(state.get("open"))
                    clock = f"{state.get('session')} | {state.get('timestamp')}"
                else:
                    is_open, clock = await self.service.market_is_open()
                if not is_open:
                    await self.app.bot.send_message(
                        chat_id=settings.telegram_admin_user_id,
                        text=f"⏰ {self._monitor_label(key)} stopped: trading session is closed.\n{clock}",
                    )
                    break
                async with self._global_scan_lock:
                    # V4 scans a wider internal pool so contracts already placed on
                    # KEEP WATCH remain eligible for automatic re-evaluation even if
                    # they temporarily fall outside the three new opportunities shown.
                    # Scan a wider internal pool for every continuous engine.  The
                    # Telegram layer later chooses the best unique underlyings, so a
                    # fourth-ranked EVGO is not hidden merely because the top three
                    # rows belong to NVDA/INTC contracts.
                    fetch_limit = max(int(settings.max_signals_per_scan), 20) if continuous_engine else int(settings.max_signals_per_scan)
                    scanned_candidates, _ = await self._fetch_candidates(
                        kind,
                        index_strategy=index_strategy,
                        max_results=fetch_limit,
                        horizon=horizon,
                    )

                # Telegram discovery slots are unique by underlying symbol.
                # Engines still scan every candidate; this only prevents NVDA/INTC
                # from consuming multiple delivery slots with different contracts.
                unique_new, diversity_suppressed = self.signal_delivery_policy.select_unique_symbols(
                    scanned_candidates, int(settings.max_signals_per_scan)
                )
                if diversity_suppressed:
                    session.setdefault("delivery_suppressed", []).extend(diversity_suppressed[-50:])
                    session["delivery_suppressed"] = session["delivery_suppressed"][-200:]
                candidates = list(unique_new)
                if is_auto_watch:
                    registry_name = "v6_watch_registry" if is_v6 else ("v5_watch_registry" if is_v5 else "v4_watch_registry")
                    registry = session.setdefault(registry_name, {})
                    by_contract = {}
                    for row in scanned_candidates:
                        tr = row.to_dict() if hasattr(row, "to_dict") else dict(row)
                        op = tr.get("option") or {}
                        ck = str(op.get("symbol") or f"{tr.get('symbol','')}|{op.get('strike','')}|{op.get('type','')}|{op.get('expiration','')}")
                        if ck:
                            by_contract[ck] = row
                    # New discovery is already capped to unique underlyings, but every
                    # tracked WATCH contract is appended for state-transition checks.
                    selected = list(unique_new)
                    selected_keys = set()
                    for row in selected:
                        tr = row.to_dict() if hasattr(row, "to_dict") else dict(row)
                        op = tr.get("option") or {}
                        selected_keys.add(str(op.get("symbol") or f"{tr.get('symbol','')}|{op.get('strike','')}|{op.get('type','')}|{op.get('expiration','')}"))
                    for ck in list(registry):
                        if ck in by_contract:
                            registry[ck]["misses"] = 0
                            if ck not in selected_keys:
                                selected.append(by_contract[ck])
                        else:
                            registry[ck]["misses"] = int(registry[ck].get("misses", 0)) + 1
                            # Do not keep a vanished/invalid setup forever. Ten missed
                            # wide scans expires only the automatic watch state.
                            if registry[ck]["misses"] >= 10:
                                registry.pop(ck, None)
                    candidates = selected

                for signal in candidates:
                    if (not continuous_engine) and int(session.get("count", 0)) >= int(settings.monitor_max_opportunities):
                        break
                    trade = signal.to_dict() if hasattr(signal, "to_dict") else dict(signal)
                    fp = self._candidate_fingerprint(trade)
                    if (not continuous_engine) and fp in session.setdefault("seen", set()):
                        continue
                    option_row = trade.get("option") or {}
                    strategy_mode = option_row.get("strategy_mode", "CORE")
                    decision_state = str(trade.get("decision", "READY")).upper()
                    symbol_key = f"{trade.get('symbol','')}|{strategy_mode}|{decision_state}"
                    v4_was_tracked = False
                    if str(strategy_mode).upper() in {"WASEEM_V3", "WASEEM_V4", "WASEEM_V5", "WASEEM_V6"}:
                        contract_key = str(option_row.get("symbol") or f"{trade.get('symbol','')}|{option_row.get('strike','')}|{option_row.get('type','')}|{option_row.get('expiration','')}")
                        first_map = session.setdefault("v3_first_detected", {})
                        first = first_map.setdefault(contract_key, datetime.now(timezone.utc).isoformat())
                        trade["first_detected_at"] = first
                        if str(strategy_mode).upper() in {"WASEEM_V4", "WASEEM_V5", "WASEEM_V6"}:
                            v5_mode = str(strategy_mode).upper() == "WASEEM_V5"
                            v6_mode = str(strategy_mode).upper() == "WASEEM_V6"
                            registry_name = "v6_watch_registry" if v6_mode else ("v5_watch_registry" if v5_mode else "v4_watch_registry")
                            registry = session.setdefault(registry_name, {})
                            v4_was_tracked = contract_key in registry
                            if v4_was_tracked:
                                saved = registry[contract_key]
                                trade["first_detected_at"] = saved.get("first_detected_at", first)
                                trade["watch_added_at"] = saved.get("watch_added_at", first)
                            if decision_state == "WATCH":
                                if not v4_was_tracked:
                                    registry[contract_key] = {
                                        "first_detected_at": first,
                                        "watch_added_at": datetime.now(timezone.utc).isoformat(),
                                        "misses": 0,
                                    }
                                    trade["watch_added_at"] = registry[contract_key]["watch_added_at"]
                                else:
                                    # KEEP WATCH is automatic in V4. Do not spam the
                                    # same WATCH message every cooldown interval.
                                    continue
                            elif decision_state == "READY":
                                trade["entry_ready_at"] = datetime.now(timezone.utc).isoformat()
                                if v4_was_tracked:
                                    trade["v6_watch_transition" if v6_mode else ("v5_watch_transition" if v5_mode else "v4_watch_transition")] = "WATCH_TO_READY"
                                    registry.pop(contract_key, None)
                        else:
                            if decision_state == "WATCH":
                                trade["watch_added_at"] = first
                            elif decision_state == "READY":
                                trade["entry_ready_at"] = datetime.now(timezone.utc).isoformat()
                    now_mono = time.monotonic()
                    last_sent = float(session.setdefault("symbol_last_sent", {}).get(symbol_key, 0.0) or 0.0)
                    force_auto_transition = bool(trade.get("v4_watch_transition") == "WATCH_TO_READY" or trade.get("v5_watch_transition") == "WATCH_TO_READY" or trade.get("v6_watch_transition") == "WATCH_TO_READY")
                    if (not force_auto_transition) and last_sent and now_mono - last_sent < settings.monitor_duplicate_cooldown_seconds:
                        continue

                    # Global cross-engine cooldown is symbol-based, not engine-based.
                    # Scanning/re-evaluation continues normally; only duplicate Telegram
                    # delivery is suppressed. Material thesis changes can bypass it.
                    delivery = self.signal_delivery_policy.evaluate(trade, now=now_mono)
                    if not delivery.allowed:
                        self.signal_delivery_policy.record_suppressed(
                            trade,
                            reason=delivery.reason,
                            extra={"remaining_seconds": round(delivery.remaining_seconds, 1), "monitor": key},
                        )
                        session.setdefault("delivery_suppressed", []).append({
                            "symbol": trade.get("symbol"),
                            "score": trade.get("score"),
                            "reason": delivery.reason,
                            "remaining_seconds": round(delivery.remaining_seconds, 1),
                        })
                        session["delivery_suppressed"] = session["delivery_suppressed"][-200:]
                        continue
                    if not continuous_engine:
                        session["seen"].add(fp)
                    await self._send_watch_candidate(key, trade)
                    self.signal_delivery_policy.record_sent(trade, now=now_mono)
                    session["symbol_last_sent"][symbol_key] = now_mono
                    session["count"] = int(session.get("count", 0)) + 1
                if (not continuous_engine) and int(session.get("count", 0)) >= int(settings.monitor_max_opportunities):
                    await self.app.bot.send_message(
                        chat_id=settings.telegram_admin_user_id,
                        text=(
                            f"✅ {self._monitor_label(key)} detected "
                            f"{settings.monitor_max_opportunities}/{settings.monitor_max_opportunities} opportunities.\n"
                            "⏹️ Monitoring stopped automatically.\n"
                            "Start Monitoring again to begin a new 0/3 session."
                        ),
                    )
                    break
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.app.bot.send_message(
                chat_id=settings.telegram_admin_user_id,
                text=f"⚠️ {self._monitor_label(key)} stopped because of {type(exc).__name__}.",
            )
        finally:
            session["stopped_at"] = datetime.now(timezone.utc).isoformat()

    async def _start_opportunity_monitor(self, key: str) -> bool:
        if self._monitor_running(key):
            return False
        # Every explicit restart is a fresh 0/3 session.
        self.opportunity_monitor_sessions[key] = {
            "count": 0,
            "seen": set(),
            "symbol_last_sent": {},
            "started_at": datetime.now(timezone.utc).isoformat(),
            "horizon": self.search_horizons.get(key) if key != "stock" else None,
        }
        task = asyncio.create_task(
            self._opportunity_monitor_loop(key),
            name=f"opportunity-monitor:{key}",
        )
        self.opportunity_monitor_tasks[key] = task
        return True

    async def _stop_opportunity_monitor(self, key: str) -> bool:
        task = self.opportunity_monitor_tasks.get(key)
        if not task or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True

    async def stop_background_monitors(self) -> None:
        keys = list(self.opportunity_monitor_tasks)
        for key in keys:
            await self._stop_opportunity_monitor(key)

    @staticmethod
    def _candidate_markup(rows: list[dict], kind: str) -> InlineKeyboardMarkup:
        buttons = []
        for index, trade in enumerate(rows[:3], start=1):
            option = trade.get("option") or {}
            suffix = ""
            if option:
                opt_type = str(option.get("type", "")).upper()
                if opt_type:
                    suffix = f" {opt_type}"
                mode = str(option.get("dte_mode", "")).upper()
                if mode in {"0DTE", "SWING"}:
                    suffix += f" {mode}"
                strategy_mode = str(option.get("strategy_mode", "")).upper()
                if strategy_mode == "SPX_V20":
                    suffix += " V20"
            label = f"{index}️⃣ {trade.get('symbol', 'N/A')}{suffix}"
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"pick:{index}")]
            )
        buttons.append(
            [InlineKeyboardButton("🔄 Rescan", callback_data=f"scan:{kind}")]
        )
        buttons.append(
            [InlineKeyboardButton("🔙 Back", callback_data="menu:trading")]
        )
        return InlineKeyboardMarkup(buttons)

    @staticmethod
    def _approval_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Approve", callback_data="trade:publish")],
                [InlineKeyboardButton("❌ Cancel", callback_data="trade:cancel")],
                [InlineKeyboardButton("🔙 Back", callback_data="trade:results")],
            ]
        )

    async def _edit_menu(self, query, text: str, markup: InlineKeyboardMarkup, parse_mode=None):
        try:
            await query.edit_message_text(text=text, reply_markup=markup, parse_mode=parse_mode)
        except Exception:
            await query.message.reply_text(text=text, reply_markup=markup, parse_mode=parse_mode)

    def _filtered_open_rows(self, category: str) -> list[dict]:
        rows = self._open_rows()
        if category == "stock":
            return [r for r in rows if str(r.get("trade_type", "")).startswith("STOCK_")]
        if category == "option":
            return [r for r in rows if str(r.get("trade_type", "")).startswith("EQUITY_OPTION_")]
        if category == "index":
            return [r for r in rows if str(r.get("trade_type", "")).startswith("INDEX_OPTION_")]
        return rows

    async def _show_open_rows(self, query, category: str, close_mode: bool = False):
        rows = self._filtered_open_rows(category)
        if not rows:
            return await self._edit_menu(
                query,
                "📂 Open Trades\nNo open trades in this category.",
                self._open_menu_markup(),
            )

        title_map = {
            "stock": "📈 Stock Trades",
            "option": "🟢 Equity Options",
            "index": "📊 Index Options",
            "all": "📂 Open Trades",
        }
        lines = [title_map.get(category, "📂 Open Trades")]
        buttons = []
        for idx, trade in enumerate(rows[:20], start=1):
            label = self._contract_label(trade)
            trade_id = str(trade.get("trade_id", ""))
            lines.append(f"{idx}. {label} — {trade_id}")
            if close_mode and trade_id:
                buttons.append(
                    [InlineKeyboardButton(f"❌ {label}", callback_data=f"close:trade:{trade_id}")]
                )
        if close_mode:
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="menu:open")])
            markup = InlineKeyboardMarkup(buttons)
        else:
            markup = self._open_menu_markup()
        await self._edit_menu(query, "\n".join(lines), markup)

    async def _show_close_confirmation(self, query, trade_id: str):
        trade = self._find_open_trade(trade_id)
        if not trade:
            return await self._edit_menu(query, "❌ Trade not found.", self._open_menu_markup())

        user_id = query.from_user.id
        self.pending_closes[user_id] = {
            "trade_id": trade_id,
            "created_monotonic": time.monotonic(),
        }
        last_price = await self._latest_trade_price(trade)
        entry = self._entry_reference(trade)
        pnl_text = "N/A"
        if last_price is not None and entry > 0:
            pnl_text = f"{self._trade_pnl_pct(trade, last_price):+.2f}%"

        text = (
            "⚠️ Confirm Close\n"
            f"{self._contract_label(trade)}\n"
            f"Trade ID: {trade_id}\n"
            f"Entry: {entry}\n"
            f"Last Price: {last_price if last_price is not None else 'N/A'}\n"
            f"P&L: {pnl_text}"
        )
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Confirm Close", callback_data=f"close:confirm:{trade_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu:open")],
            ]
        )
        await self._edit_menu(query, text, markup)

    async def menu_callback(self, update: Update, context):
        query = update.callback_query
        if not query:
            return
        # A callback can arrive after Telegram's short answer window (for
        # example when an old menu button is tapped).  Acknowledge immediately
        # when possible, but do not let an expired callback break the menu.
        try:
            await query.answer()
        except BadRequest as exc:
            if "query is too old" not in str(exc).lower() and "query id is invalid" not in str(exc).lower():
                raise

        if not self.allowed(update):
            return await self._deny(update)
        if not await self._require_private(update):
            return

        data = str(query.data or "")

        if data == "menu:main":
            context.user_data.pop("success_rule_pending", None)
            # Telegram editMessageText accepts only inline keyboards. The
            # persistent ReplyKeyboard is already attached by /start.
            try:
                await query.edit_message_text(
                    text=(
                        "✅ ALLUQMANU_USA_TD Ready\n\n"
                        "⌨️ استخدم القائمة الرئيسية الثابتة أسفل المحادثة."
                    )
                )
            except Exception:
                await query.message.reply_text(
                    "✅ ALLUQMANU_USA_TD Ready",
                    reply_markup=self._main_menu_markup(),
                )
            return
        if data == "menu:trading":
            return await self._edit_menu(query, "🔍 Trading Menu", self._trading_menu_markup())
        if data == "menu:stocks":
            return await self._edit_menu(query, "📊 <b>الأسهم</b>\n\nاختر الخدمة المطلوبة:", self._stocks_menu_markup(), parse_mode=ParseMode.HTML)
        if data == "stocks:analysis:list":
            return await self._edit_menu(query, "📊 <b>تحليل السهم</b>\n\nاختر السهم:", await self._stock_symbols_markup("analysis"), parse_mode=ParseMode.HTML)
        if data == "stocks:news:list":
            return await self._edit_menu(query, "📰 <b>أخبار الأسهم</b>\n\nاختر السهم:", await self._stock_symbols_markup("news"), parse_mode=ParseMode.HTML)
        if data.startswith("stocks:analysis:") and data != "stocks:analysis:list":
            symbol = data.rsplit(":", 1)[-1].upper()
            result = await self.service.stock_analysis(symbol)
            text = self.service.stock_intelligence.render_ar(result)
            return await self._edit_menu(query, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث التحليل", callback_data=f"stocks:analysis:{symbol}")],[InlineKeyboardButton("🔙 قائمة الأسهم", callback_data="stocks:analysis:list")]]), parse_mode=ParseMode.HTML)
        if data.startswith("stocks:news:") and data != "stocks:news:list":
            symbol = data.rsplit(":", 1)[-1].upper()
            result = await self.service.stock_news_analysis(symbol)
            text = self.service.stock_news.render_ar(result)
            return await self._edit_menu(query, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث الأخبار", callback_data=f"stocks:news:{symbol}")],[InlineKeyboardButton("🔙 قائمة الأسهم", callback_data="stocks:news:list")]]), parse_mode=ParseMode.HTML)
        if data == "menu:watchlist":
            context.user_data.pop("watchlist_action", None)
            return await self._edit_menu(query, await self._watchlist_text(), self._watchlist_markup(), parse_mode=ParseMode.HTML)
        if data in {"watchlist:add", "watchlist:remove", "watchlist:disable", "watchlist:enable"}:
            action = data.split(":", 1)[1]
            repo = getattr(self.service, "equity_watchlist", None)
            if repo is None:
                return await self._edit_menu(query, "❌ إدارة الأسهم غير متاحة حاليًا.", self._watchlist_markup())
            context.user_data["watchlist_action"] = action
            labels={"add":"إضافة سهم","remove":"حذف سهم","disable":"تعطيل سهم","enable":"تفعيل سهم"}
            return await self._edit_menu(query, f"⚙️ <b>{labels.get(action, action)}</b>\n\nأرسل رمز سهم واحد مثل: MRVL", InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="menu:watchlist")]]), parse_mode=ParseMode.HTML)
        if data == "menu:equity_strategy":
            return await self._edit_menu(
                query,
                "🟢 Equity Options\n\nاختر محرك التحليل:\n🧠 Core — النظام الحالي بدون تغيير\n✅ Confirmed Setup — Hunter + Structure Confirmation + Judge\n⚡ وسيم V1 — CALL/PUT متوازن + Near-OTM + Diagnostics",
                self._equity_strategy_markup(),
            )
        if data == "menu:index_strategy":
            return await self._edit_menu(
                query,
                "📊 SPX Index Options\n\nاختر محرك التحليل:\n🛰️ SPX V20 — ALLUQMANI Radar V2.1\n🧠 SPX Core — استراتيجية المشروع الحالية\n✅ Confirmed Setup — Hunter + Structure Confirmation + Judge\n⚡ وسيم V1 — Near-OTM + Expected Move + Diagnostics",
                self._index_strategy_markup(),
            )
        if data == "menu:open":
            return await self._edit_menu(query, "📂 Open Trades", self._open_menu_markup())
        if data == "menu:reports":
            return await self._edit_menu(query, "📊 Reports", self._reports_menu_markup())
        if data == "menu:performance":
            return await self._edit_menu(
                query,
                "📈 Performance — Select Category",
                self._performance_menu_markup(),
            )
        if data == "menu:daily":
            return await self._edit_menu(
                query,
                "🗓️ Daily Report — Select Category",
                self._daily_menu_markup(),
            )
        if data == "menu:weekly":
            return await self._edit_menu(
                query,
                "📊 Weekly Report — Select Category",
                self._weekly_menu_markup(),
            )
        if data == "menu:success_rules":
            context.user_data.pop("success_rule_pending", None)
            return await self._edit_menu(
                query,
                self._success_rules_text(),
                self._success_rules_menu_markup(),
            )
        if data == "menu:message_tests":
            return await self._edit_menu(
                query,
                "🧪 اختبارات الرسائل — خاص فقط\n\nهذه الاختبارات لا تنشئ صفقات ولا تغيّر الإحصائيات.",
                self._message_tests_menu_markup(),
            )
        if data == "menu:risk":
            return await self._edit_menu(query, "🛡️ Risk Management", self._risk_menu_markup())
        if data == "menu:system":
            context.user_data.pop("learning_import_pending", None)
            return await self._edit_menu(query, "⚙️ System", self._system_menu_markup(self._paused()))
        if data == "menu:learning":
            context.user_data.pop("learning_import_pending", None)
            return await self._edit_menu(query, self._learning_status_text(), self._learning_menu_markup())
        if data == "menu:settings":
            context.user_data.pop("contract_price_pending", None)
            return await self._edit_menu(query, "⚙️ Settings", self._settings_menu_markup())
        if data == "menu:contract_search":
            context.user_data.pop("contract_price_pending", None)
            return await self._edit_menu(
                query,
                "💵 Contract Search Price\n\nحدد الحد الأعلى لسعر العقد لكل مدة بشكل مستقل.\nDaily = 0DTE | Weekly = 1–7 | Monthly = 8–35\n0 = Default / Unlimited.",
                self._contract_search_menu_markup(),
            )
        if data == "menu:profit_alert_step":
            context.user_data.pop("profit_alert_step_pending", None)
            step = profit_alert_rules.get_step()
            return await self._edit_menu(
                query,
                (
                    "📈 Profit Alert Step\n\n"
                    f"Current: ${step:.2f}\n\n"
                    "هذا الإعداد يحدد مقدار ارتفاع سعر العقد المطلوب لكل تحديث أرباح جديد.\n"
                    "مثال: دخول 7.00 وإعداد 0.10 → تنبيهات عند 7.10 ثم 7.20 ثم 7.30...\n\n"
                    "اختر قيمة جاهزة أو أدخل قيمة مخصصة."
                ),
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("$0.05", callback_data="profitstep:set:0.05"), InlineKeyboardButton("$0.10", callback_data="profitstep:set:0.10")],
                    [InlineKeyboardButton("$0.25", callback_data="profitstep:set:0.25"), InlineKeyboardButton("✏️ Custom", callback_data="profitstep:custom")],
                    [InlineKeyboardButton("♻️ Restore Default ($0.10)", callback_data="profitstep:reset")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu:settings")],
                ]),
            )

        if data == "learning:status":
            return await self._edit_menu(query, self._learning_status_text(), self._learning_menu_markup())

        if data == "learning:export":
            if not self.allowed(update) or not self._is_private(update):
                return
            path = self.service.learning.export_snapshot()
            with open(path, "rb") as fh:
                await query.message.reply_document(
                    document=fh,
                    filename="learning_memory.json",
                    caption=(
                        "📤 Learning Memory Backup\n"
                        "احتفظ بهذا الملف لاسترجاع ذاكرة التعلّم بعد Deploy/Restart عند الحاجة."
                    ),
                )
            return await self._edit_menu(query, self._learning_status_text(), self._learning_menu_markup())

        if data == "learning:import":
            if not self.allowed(update) or not self._is_private(update):
                return
            context.user_data["learning_import_pending"] = True
            return await self._edit_menu(
                query,
                "📥 Import Learning File\n\nأرسل الآن ملف learning_memory.json كـ Document.\n"
                "الحد الأقصى 5MB، وسيتم الدمج حسب trade_id بدون تكرار.",
                InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="learning:cancel")]]),
            )

        if data == "learning:cancel":
            context.user_data.pop("learning_import_pending", None)
            return await self._edit_menu(query, self._learning_status_text(), self._learning_menu_markup())

        if data.startswith("menu:horizon:"):
            key = data.split("menu:horizon:", 1)[1]
            if key not in {"option", "option:confirmed", "index:v20", "index:core", "index:confirmed", "option:waseem", "index:waseem", "option:waseem_v2", "index:waseem_v2", "option:waseem_v3", "index:waseem_v3", "option:waseem_v4", "index:waseem_v4", "option:waseem_v5", "index:waseem_v5", "option:waseem_v6", "index:waseem_v6"}:
                return await self._edit_menu(query, "❌ Unknown option search.", self._trading_menu_markup())
            current = self.search_horizons.get(key, "weekly")
            return await self._edit_menu(
                query,
                f"📅 اختر مدة انتهاء العقد\n\nCurrent: {self._horizon_label(current)}\n\nالاختيار يغيّر نطاق DTE المستخدم فعليًا في البحث.",
                self._horizon_markup(key),
            )

        if data.startswith("horizon:select:"):
            payload = data.split(":")
            if len(payload) < 4:
                return
            key = ":".join(payload[2:-1])
            horizon = payload[-1].lower()
            if key not in {"option", "option:confirmed", "index:v20", "index:core", "index:confirmed", "option:waseem", "index:waseem", "option:waseem_v2", "index:waseem_v2", "option:waseem_v3", "index:waseem_v3", "option:waseem_v4", "index:waseem_v4", "option:waseem_v5", "index:waseem_v5", "option:waseem_v6", "index:waseem_v6"}:
                return
            allowed_horizons = {"daily", "weekly", "monthly", "both"} if (key.endswith(":waseem") or key.endswith(":waseem_v2") or key.endswith(":waseem_v3") or key.endswith(":waseem_v4") or key.endswith(":waseem_v5")) else {"daily", "weekly", "monthly"}
            if horizon not in allowed_horizons:
                return
            if self._monitor_running(key):
                return await self._edit_menu(
                    query,
                    "⏹️ أوقف المراقبة الحالية أولًا قبل تغيير مدة العقد.",
                    self._monitor_control_markup(key, True),
                )
            self.search_horizons[key] = horizon
            return await self._edit_menu(
                query,
                self._monitor_status_text(key),
                self._monitor_control_markup(key, False),
            )

        if data.startswith("menu:monitor:"):
            key = data.split("menu:monitor:", 1)[1]
            if key not in {"stock", "option", "option:confirmed", "index:v20", "index:core", "index:confirmed", "option:waseem", "index:waseem", "option:waseem_v2", "index:waseem_v2", "option:waseem_v3", "index:waseem_v3", "option:waseem_v4", "index:waseem_v4", "option:waseem_v5", "index:waseem_v5", "option:waseem_v6", "index:waseem_v6"}:
                return await self._edit_menu(query, "❌ Unknown monitor.", self._trading_menu_markup())
            return await self._edit_menu(
                query,
                self._monitor_status_text(key),
                self._monitor_control_markup(key, self._monitor_running(key)),
            )

        if data.startswith("monitor:start:"):
            key = data.split("monitor:start:", 1)[1]
            if key not in {"stock", "option", "option:confirmed", "index:v20", "index:core", "index:confirmed", "option:waseem", "index:waseem", "option:waseem_v2", "index:waseem_v2", "option:waseem_v3", "index:waseem_v3", "option:waseem_v4", "index:waseem_v4", "option:waseem_v5", "index:waseem_v5", "option:waseem_v6", "index:waseem_v6"}:
                return
            if self._paused():
                return await self._edit_menu(
                    query,
                    "⏸️ النظام Paused. استخدم Resume أولًا.",
                    self._monitor_control_markup(key, False),
                )
            if key == "index:waseem_v3":
                state = self.service.spx_option_session_status()
                is_open = bool(state.get("open"))
                clock = (
                    f"SPX Options Session: {state.get('session')} | "
                    f"{state.get('timestamp')}"
                )
            elif key == "index:waseem_v4":
                state = self.service.spx_option_session_status()
                is_open = bool(state.get("open"))
                clock = (
                    f"SPX Options Session: {state.get('session')} | "
                    f"{state.get('timestamp')}"
                )
            elif key == "index:waseem_v6":
                index_strategy = "waseem_v6"
            elif key == "index:waseem_v5":
                state = self.service.spx_option_session_status()
                is_open = bool(state.get("open"))
                clock = f"SPX Options Session: {state.get('session')} | {state.get('timestamp')}"
            else:
                is_open, clock = await self.service.market_is_open()
            if not is_open:
                return await self._edit_menu(
                    query,
                    f"⏰ جلسة التداول المطلوبة مغلقة أو تعذر تأكيد أنها مفتوحة.\n{clock}",
                    self._monitor_control_markup(key, False),
                )
            await self._start_opportunity_monitor(key)
            return await self._edit_menu(
                query,
                self._monitor_status_text(key),
                self._monitor_control_markup(key, True),
            )

        if data.startswith("monitor:stop:"):
            key = data.split("monitor:stop:", 1)[1]
            if key not in {"stock", "option", "option:confirmed", "index:v20", "index:core", "index:confirmed", "option:waseem", "index:waseem", "option:waseem_v2", "index:waseem_v2", "option:waseem_v3", "index:waseem_v3", "option:waseem_v4", "index:waseem_v4", "option:waseem_v5", "index:waseem_v5", "option:waseem_v6", "index:waseem_v6"}:
                return
            await self._stop_opportunity_monitor(key)
            return await self._edit_menu(
                query,
                self._monitor_status_text(key),
                self._monitor_control_markup(key, False),
            )

        if data.startswith("monitor:scan:"):
            key = data.split("monitor:scan:", 1)[1]
            if key not in {"stock", "option", "option:confirmed", "index:v20", "index:core", "index:confirmed", "option:waseem", "index:waseem", "option:waseem_v2", "index:waseem_v2", "option:waseem_v3", "index:waseem_v3", "option:waseem_v4", "index:waseem_v4", "option:waseem_v5", "index:waseem_v5", "option:waseem_v6", "index:waseem_v6"}:
                return
            kind, strategy = self._monitor_parts(key)
            context.args = [str(settings.max_signals_per_scan)]
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            await self._edit_menu(query, f"🔎 Scanning {self._monitor_label(key)}...", self._monitor_control_markup(key, self._monitor_running(key)))
            return await self._run_scan(
                update, context, kind, index_strategy=strategy,
                horizon=self.search_horizons.get(key) if key != "stock" else None,
            )

        if data.startswith("watch:reject:"):
            candidate_id = data.split("watch:reject:", 1)[1]
            rows = self.watch_candidates.get(update.effective_user.id, {})
            rows.pop(candidate_id, None)
            return await self._edit_menu(query, "❌ Opportunity rejected. Monitoring continues if active.", self._trading_menu_markup())

        if data.startswith("watch:approve:"):
            candidate_id = data.split("watch:approve:", 1)[1]
            rows = self.watch_candidates.get(update.effective_user.id, {})
            candidate = rows.get(candidate_id)
            if not candidate:
                return await self._edit_menu(query, "⚠️ Candidate expired\nPlease rescan.", self._trading_menu_markup())
            age = time.monotonic() - float(candidate.get("created_monotonic", 0.0))
            if age > settings.candidate_ttl_seconds:
                rows.pop(candidate_id, None)
                return await self._edit_menu(query, "⚠️ Candidate expired\nPlease rescan.", self._trading_menu_markup())
            trade = dict(candidate["trade"])
            self.pending_scans[update.effective_user.id] = {
                "candidates": [trade],
                "scan_type": candidate.get("key", "option"),
                "created_monotonic": float(candidate["created_monotonic"]),
                "picked_index": 0,
                "published_indexes": set(),
            }
            rows.pop(candidate_id, None)
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            return await self.publish(update, context)

        if data.startswith("contract:set:"):
            parts = data.split(":")
            if len(parts) != 4:
                return
            category, horizon = parts[2], parts[3]
            if category not in contract_search_rules.CATEGORIES or horizon not in contract_search_rules.HORIZONS:
                return
            context.user_data["contract_price_pending"] = {"category": category, "horizon": horizon}
            current = contract_search_rules.get_max_price(category, horizon)
            current_text = "Default / Unlimited" if current <= 0 else f"${current:,.2f} or less"
            category_text = "Equity Options" if category == "equity_option" else "SPX Options"
            horizon_text = {"daily": "0DTE", "weekly": "1–7 DTE", "monthly": "8–35 DTE"}[horizon]
            return await self._edit_menu(
                query,
                f"💵 Max Contract Price\n\n{category_text} — {horizon_text}\nCurrent: {current_text}\n\nأرسل رقمًا مثل 5 أو 10.\n0 = Default / Unlimited.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="menu:contract_search")],
                ]),
            )

        if data == "contract:reset:all":
            contract_search_rules.reset()
            context.user_data.pop("contract_price_pending", None)
            return await self._edit_menu(
                query,
                "♻️ Contract search prices restored to defaults.",
                self._contract_search_menu_markup(),
            )

        if data.startswith("profitstep:set:"):
            value = float(data.split("profitstep:set:", 1)[1])
            saved = profit_alert_rules.set_step(value)
            context.user_data.pop("profit_alert_step_pending", None)
            return await self._edit_menu(
                query,
                f"✅ Profit Alert Step = ${saved:.2f}\nسيطبق فورًا على عقود الأسهم وSPX.",
                self._settings_menu_markup(),
            )

        if data == "profitstep:custom":
            context.user_data["profit_alert_step_pending"] = True
            return await self._edit_menu(
                query,
                "✏️ أرسل قيمة بالسنت/الدولار مثل 0.05 أو 0.10 أو 0.25.",
                InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="menu:profit_alert_step")]]),
            )

        if data == "profitstep:reset":
            saved = profit_alert_rules.reset()
            context.user_data.pop("profit_alert_step_pending", None)
            return await self._edit_menu(
                query,
                f"♻️ رجعنا الإعداد الافتراضي: ${saved:.2f}",
                self._settings_menu_markup(),
            )

        if data.startswith("test:template:"):
            template_name = data.split(":", 2)[2]
            return await self._send_template_test(update, context, query, template_name)

        if data.startswith("test:signal:"):
            category = data.split(":", 2)[2]
            return await self._send_test_signal(update, context, query, category)

        if data.startswith("test:profit:"):
            category = data.split(":", 2)[2]
            return await self._send_test_profit(update, context, query, category)

        if data == "test:profit":
            return await self._send_test_profit(update, context, query, "equity_option")

        if data == "success:stocks_info":
            return await self._edit_menu(
                query,
                "📈 Stocks Success Rule\n\n"
                "الأسهم لا تستخدم حد +$50 أو +$100.\n"
                "✅ تعتبر ناجحة عند تحقق أحد الأهداف TP1/TP2/TP3.\n"
                "🔴 إذا أغلقت بدون تحقق أي هدف تسجل كغير ناجحة/خاسرة في تقييم الأداء.\n\n"
                "هذا الإعداد ثابت حسب طلبك.",
                self._success_rules_menu_markup(),
            )

        if data.startswith("success:set:"):
            category = data.split(":", 2)[2]
            if category == "stock":
                return await self._edit_menu(
                    query,
                    "📈 الأسهم تعتمد على الأهداف TP1/TP2/TP3 ولا تستخدم حد ربح نقدي.",
                    self._success_rules_menu_markup(),
                )
            if category not in success_rules.CATEGORIES:
                return await self._edit_menu(
                    query,
                    "❌ Invalid success-rule category.",
                    self._success_rules_menu_markup(),
                )
            context.user_data["success_rule_pending"] = category
            rule = success_rules.get(category)
            unit = "$ USD" if rule.get("unit") == "USD" else "%"
            label = self._success_category_label(category)
            return await self._edit_menu(
                query,
                f"🎯 {label} Success Rule\n\n"
                f"Current: {self._format_success_rule(rule)}\n\n"
                f"أرسل القيمة الجديدة الآن برسالة واحدة ({unit}).\n"
                "مثال: 50\n"
                "أرسل 0 لتعطيل معيار النجاح لهذه الفئة.\n\n"
                "📌 هذا المعيار هو نتيجة الأداء للعقود، لكنه لا يغلق الصفقة ولا يغيّر P&L المحقق فعليًا.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="success:cancel")],
                    [InlineKeyboardButton("🔙 Success Rules", callback_data="menu:success_rules")],
                ]),
            )

        if data == "success:cancel":
            context.user_data.pop("success_rule_pending", None)
            return await self._edit_menu(
                query,
                self._success_rules_text(),
                self._success_rules_menu_markup(),
            )

        if data.startswith("scan:index:"):
            strategy_mode = data.split(":", 2)[2].strip().lower()
            if strategy_mode not in {"core", "v20"}:
                strategy_mode = "core"
            context.args = ["3"]
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            title = "SPX V20" if strategy_mode == "v20" else "SPX Core"
            await self._edit_menu(query, f"🔎 Scanning {title}...", self._index_strategy_markup())
            return await self._run_scan(update, context, "index", index_strategy=strategy_mode)

        if data.startswith("scan:"):
            kind = data.split(":", 1)[1]
            context.args = ["3"]
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            await self._edit_menu(query, "🔎 Scanning...", self._trading_menu_markup())
            return await self._run_scan(update, context, kind)

        if data.startswith("pick:"):
            number = data.split(":", 1)[1]
            context.args = [number]
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            return await self.pick(update, context)

        if data == "trade:publish":
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            return await self.publish(update, context)

        if data == "trade:cancel":
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            await self.cancel(update, context)
            return await self._edit_menu(query, "❌ Trade Cancelled", self._trading_menu_markup())

        if data == "trade:results":
            user_id = update.effective_user.id
            session = self.pending_scans.get(user_id)
            if not session:
                return await self._edit_menu(query, "No active scan results.", self._trading_menu_markup())
            return await self._edit_menu(
                query,
                "Top Opportunities",
                self._candidate_markup(session.get("candidates", []), session.get("scan_type", "option")),
            )

        if data.startswith("open:"):
            category = data.split(":", 1)[1]
            return await self._show_open_rows(query, category, close_mode=False)

        if data.startswith("close:list:"):
            category = data.split(":", 2)[2]
            return await self._show_open_rows(query, category, close_mode=True)

        if data.startswith("close:trade:"):
            trade_id = data.split(":", 2)[2]
            return await self._show_close_confirmation(query, trade_id)

        if data.startswith("close:confirm:"):
            trade_id = data.split(":", 2)[2]
            context.args = [trade_id]
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            await self.confirm_close(update, context)
            return await self._edit_menu(query, "✅ Trade Closed", self._open_menu_markup())

        if data == "close:all":
            self.pending_close_all[update.effective_user.id] = time.monotonic()
            markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Confirm Close All", callback_data="close:confirm_all")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="menu:open")],
                ]
            )
            return await self._edit_menu(query, "⚠️ Confirm closing ALL open trades?", markup)

        if data == "close:confirm_all":
            context.user_data["_menu_callback"] = True
            context.user_data["_menu_query"] = query
            await self.confirm_close_all(update, context)
            return await self._edit_menu(query, "✅ Close All completed", self._open_menu_markup())

        if data.startswith("perf:"):
            category = data.split(":", 1)[1]
            return await self._show_category_performance(query, category)

        if data.startswith("daily:"):
            parts = data.split(":")
            category = parts[1] if len(parts) > 1 else "options_all"
            horizon = parts[2] if len(parts) > 2 else "all"
            return await self._send_category_daily(update, query, category, horizon=horizon)

        if data.startswith("weekly:"):
            parts = data.split(":")
            category = parts[1] if len(parts) > 1 else "options_all"
            horizon = parts[2] if len(parts) > 2 else "all"
            return await self._send_category_weekly(update, query, category, horizon=horizon)

        command_map = {
            "cmd:performance": self.performance,
            "cmd:report": self.report_cmd,
            "cmd:market": self.market,
            "cmd:risk": self.risk,
            "cmd:open": self.open_trades,
            "cmd:settings": self.settings_cmd,
            "cmd:health": self.status,
            "cmd:status": self.status,
            "cmd:pause": self.pause,
            "cmd:resume": self.resume,
            "cmd:myid": self.myid,
        }
        handler = command_map.get(data)
        if handler:
            await handler(update, context)
            if data in {"cmd:pause", "cmd:resume"}:
                return await self._edit_menu(query, "⚙️ System", self._system_menu_markup(self._paused()))
            return

    @staticmethod
    def _success_category_label(category: str) -> str:
        return {
            "stock": "Stocks",
            "equity_option": "Equity Options",
            "index_option": "Index Options",
        }.get(category, category)

    @staticmethod
    def _format_success_rule(rule: dict) -> str:
        value = float(rule.get("threshold", 0) or 0)
        if value <= 0:
            return "OFF"
        if rule.get("unit") == "USD":
            return f"+${value:,.2f}"
        return f"+{value:.2f}%"

    @classmethod
    def _success_rules_text(cls) -> str:
        rules = success_rules.all()
        return (
            "🎯 Success Rules\n\n"
            "📈 Stocks: النجاح حسب تحقق الأهداف TP1/TP2/TP3\n"
            f"🟢 Equity Options: {cls._format_success_rule(rules['equity_option'])}\n"
            f"📊 Index Options: {cls._format_success_rule(rules['index_option'])}\n\n"
            "🎯 العقود: إذا وصل الربح النقدي للحد المحدد تُسجل ناجحة فورًا.\n"
            "🔴 إذا انتهت جلسة نيويورك بدون بلوغ الحد تُسجل خاسرة في الأداء.\n"
            "📈 الأسهم: لا تستخدم حدًا نقديًا؛ النجاح يعتمد على الأهداف.\n"
            "0 = تعطيل معيار العقود لذلك النوع."
        )

    async def document_input(self, update: Update, context):
        """Import a learning-memory JSON document only after explicit admin request."""
        if not self.allowed(update):
            return
        if not self._is_private(update):
            return
        if not context.user_data.get("learning_import_pending"):
            return

        document = update.effective_message.document
        if document is None:
            return
        if int(document.file_size or 0) > 5 * 1024 * 1024:
            return await update.effective_message.reply_text(
                "❌ الملف أكبر من 5MB.", reply_markup=self._learning_menu_markup()
            )
        name = str(document.file_name or "").lower()
        if name and not name.endswith(".json"):
            return await update.effective_message.reply_text(
                "❌ أرسل ملف JSON فقط.", reply_markup=self._learning_menu_markup()
            )

        fd, temp_path = tempfile.mkstemp(prefix="learning_import_", suffix=".json")
        os.close(fd)
        try:
            telegram_file = await document.get_file()
            await telegram_file.download_to_drive(custom_path=temp_path)
            result = self.service.learning.import_memory_file(temp_path)
            context.user_data.pop("learning_import_pending", None)
            return await update.effective_message.reply_text(
                (
                    "✅ تم استيراد ذاكرة التعلّم ودمجها.\n\n"
                    f"Received: {result['received']}\n"
                    f"New: {result['added']}\n"
                    f"Total: {result['total']}"
                ),
                reply_markup=self._learning_menu_markup(),
            )
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return await update.effective_message.reply_text(
                "❌ ملف التعلم غير صالح أو إصدار غير مدعوم. لم يتم تغيير الذاكرة الحالية.",
                reply_markup=self._learning_menu_markup(),
            )
        except Exception:
            return await update.effective_message.reply_text(
                "❌ تعذر استيراد الملف. لم يتم تغيير الذاكرة الحالية.",
                reply_markup=self._learning_menu_markup(),
            )
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    async def text_input(self, update: Update, context):
        """Handle persistent main-menu buttons and numeric admin settings input."""
        if not self.allowed(update):
            return
        if not self._is_private(update):
            return

        raw = str(update.effective_message.text or "").strip()

        # Persistent ReplyKeyboard home menu. Nested workflows remain inline.
        main_actions = {
            "🔍 Trading": ("🔍 Trading Menu", self._trading_menu_markup),
            "📊 الأسهم": ("📊 الأسهم\n\nاختر الخدمة المطلوبة:", self._stocks_menu_markup),
            "📂 Open Trades": ("📂 Open Trades", self._open_menu_markup),
            "📊 Reports": ("📊 Reports", self._reports_menu_markup),
            "🎯 Success Rules": (self._success_rules_text(), self._success_rules_menu_markup),
            "🧪 اختبارات الرسائل": (
                "🧪 اختبارات الرسائل — خاص فقط\n\n"
                "هذه الاختبارات لا تنشئ صفقات ولا تغيّر الإحصائيات.",
                self._message_tests_menu_markup,
            ),
            "🛡️ Risk": ("🛡️ Risk Management", self._risk_menu_markup),
            "⚙️ System": ("⚙️ System", lambda: self._system_menu_markup(self._paused())),
        }
        action = main_actions.get(raw)
        if action is not None:
            context.user_data.pop("success_rule_pending", None)
            context.user_data.pop("contract_price_pending", None)
            context.user_data.pop("profit_alert_step_pending", None)
            text, markup_factory = action
            return await update.effective_message.reply_text(
                text,
                reply_markup=markup_factory(),
            )

        watch_action = context.user_data.get("watchlist_action")
        if watch_action in {"add", "remove", "disable", "enable"}:
            symbol = raw.upper().strip()
            repo = getattr(self.service, "equity_watchlist", None)
            if repo is None:
                context.user_data.pop("watchlist_action", None)
                return await update.effective_message.reply_text("❌ إدارة الأسهم غير متاحة حاليًا.", reply_markup=self._stocks_menu_markup())
            try:
                if watch_action == "add":
                    validation = await self.service.validate_equity_watchlist_symbol(symbol)
                    if not validation.get("ok"):
                        return await update.effective_message.reply_text(
                            f"❌ {symbol}: validation failed — {validation.get('reason')}",
                            reply_markup=self._watchlist_markup(),
                        )
                    await repo.upsert(symbol, True)
                    msg = f"✅ تم إضافة {symbol} وتفعيله. سيدخل في عمليات فحص الأسهم القادمة مباشرة."
                elif watch_action == "remove":
                    changed = await repo.remove(symbol)
                    msg = f"✅ تم حذف {symbol}." if changed else f"⚠️ {symbol} غير موجود في القائمة."
                else:
                    changed = await repo.set_enabled(symbol, watch_action == "enable")
                    status_word = "تم تفعيله" if watch_action == "enable" else "تم تعطيله"
                    msg = f"✅ {symbol} {status_word}." if changed else f"⚠️ {symbol} غير موجود في القائمة."
                context.user_data.pop("watchlist_action", None)
                return await update.effective_message.reply_text(
                    msg + "\n\n" + await self._watchlist_text(),
                    reply_markup=self._watchlist_markup(),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                context.user_data.pop("watchlist_action", None)
                return await update.effective_message.reply_text(
                    f"❌ Watchlist update failed: {type(exc).__name__}",
                    reply_markup=self._watchlist_markup(),
                )

        contract_pending = context.user_data.get("contract_price_pending")
        if isinstance(contract_pending, dict):
            contract_category = contract_pending.get("category")
            contract_horizon = contract_pending.get("horizon")
            if contract_category in contract_search_rules.CATEGORIES and contract_horizon in contract_search_rules.HORIZONS:
                arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789..")
                cleaned = raw.translate(arabic_digits).replace(",", "").replace("$", "").strip()
                try:
                    value = float(cleaned)
                    if value < 0:
                        raise ValueError
                    row = contract_search_rules.set_max_price(contract_category, contract_horizon, value)
                except ValueError:
                    return await update.effective_message.reply_text(
                        "❌ أرسل رقمًا فقط مثل 5 أو 10.\n0 = Default / Unlimited.",
                        reply_markup=self._contract_search_menu_markup(),
                    )
                context.user_data.pop("contract_price_pending", None)
                saved = float(row.get("max_contract_price", 0) or 0)
                saved_text = "Default / Unlimited" if saved <= 0 else f"${saved:,.2f} or less"
                horizon_text = {"daily": "0DTE", "weekly": "1–7 DTE", "monthly": "8–35 DTE"}[contract_horizon]
                category_text = "Equity Options" if contract_category == "equity_option" else "SPX Options"
                return await update.effective_message.reply_text(
                    f"✅ تم تحديث {category_text} — {horizon_text}: {saved_text}",
                    reply_markup=self._contract_search_menu_markup(),
                )

        if context.user_data.get("profit_alert_step_pending"):
            arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789..")
            cleaned = raw.translate(arabic_digits).replace(",", "").replace("$", "").strip()
            try:
                value = float(cleaned)
                saved = profit_alert_rules.set_step(value)
            except ValueError:
                return await update.effective_message.reply_text(
                    "❌ أرسل قيمة بين 0.01 و1000. مثال: 0.05 أو 0.10.",
                    reply_markup=self._settings_menu_markup(),
                )
            context.user_data.pop("profit_alert_step_pending", None)
            return await update.effective_message.reply_text(
                f"✅ تم تحديث Profit Alert Step إلى ${saved:.2f}",
                reply_markup=self._settings_menu_markup(),
            )

        category = context.user_data.get("success_rule_pending")
        if category not in {"equity_option", "index_option"}:
            return

        arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789..")
        cleaned = raw.translate(arabic_digits).replace(",", "").replace("$", "").replace("%", "").strip()
        try:
            value = float(cleaned)
            if value < 0:
                raise ValueError
        except ValueError:
            return await update.effective_message.reply_text(
                "❌ أرسل رقمًا فقط.\nمثال: 50\nأو 0 لتعطيل المعيار.",
                reply_markup=self._success_rules_menu_markup(),
            )

        try:
            rule = success_rules.set_threshold(category, value)
        except ValueError:
            return await update.effective_message.reply_text(
                "❌ القيمة غير صالحة أو كبيرة جدًا.",
                reply_markup=self._success_rules_menu_markup(),
            )

        context.user_data.pop("success_rule_pending", None)
        label = self._success_category_label(category)
        await update.effective_message.reply_text(
            f"✅ تم تحديث {label}\n"
            f"Success Rule: {self._format_success_rule(rule)}\n\n"
            "سيُطبق على المراقبة والتقارير الجديدة فورًا.",
            reply_markup=self._success_rules_menu_markup(),
        )

    @staticmethod
    def _sample_option_signal(category: str) -> dict:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        ny_date = now_dt.astimezone(ZoneInfo("America/New_York")).date()
        if category == "index_option":
            symbol = "SPX"
            trade_type = "INDEX_OPTION_INTRADAY"
            strike = 6460.0
            entry_low, entry_high = 3.80, 3.90
            stop, tp1, tp2, tp3 = 3.05, 5.00, 5.75, 6.90
            option_symbol = "SPXW-TEST-0DTE"
            mode = "0DTE"
            option_type = "PUT"
            underlying_direction = "SHORT"
            underlying_low, underlying_high = 6452.0, 6458.0
            underlying_stop = 6472.0
            underlying_targets = (6438.0, 6425.0, 6410.0)
            expiration = ny_date.isoformat()
            dte = 0
            delta = -0.48
        else:
            symbol = "NVDA"
            trade_type = "EQUITY_OPTION_SWING"
            strike = 185.0
            entry_low, entry_high = 1.72, 1.80
            stop, tp1, tp2, tp3 = 1.38, 2.35, 2.70, 3.05
            option_symbol = "NVDA-TEST-SWING"
            mode = "SWING"
            option_type = "CALL"
            underlying_direction = "LONG"
            underlying_low, underlying_high = 184.4, 184.9
            underlying_stop = 182.8
            underlying_targets = (187.2, 189.0, 191.5)
            expiration = (ny_date + timedelta(days=7)).isoformat()
            dte = 7
            delta = 0.52
        return {
            "symbol": symbol,
            "trade_id": f"TEST-{symbol}-SIGNAL",
            "trade_type": trade_type,
            # The option itself is always a long-premium position. The
            # bullish/bearish underlying thesis is stored in the option block.
            "direction": "LONG",
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "score": 92.1,
            "required_score": 91.0,
            "rr": 2.1,
            "risk_pct": 0.005,
            "probability": None,
            "probability_status": "UNVALIDATED",
            "probability_samples": 0,
            "market_regime": "BEAR" if category == "index_option" else "BULL",
            "sector": "Technology" if category != "index_option" else "INDEX",
            "data_quality": "LIMITED",
            "created_at": now,
            "published_at": now,
            "reasons": [
                "Trend + VWAP متوافقان",
                "Momentum وMACD يدعمان الاتجاه",
                "السيولة وSpread ضمن الحدود",
            ],
            "strategies": ["Trend", "Momentum", "VWAP", "Structure"],
            "invalidation": ["كسر مستوى الإبطال الفني يلغي السيناريو"],
            "option": {
                "symbol": option_symbol,
                "type": option_type,
                "strike": strike,
                "expiration": expiration,
                "dte": dte,
                "dte_mode": mode,
                "bid": entry_low,
                "ask": entry_high,
                "mid": round((entry_low + entry_high) / 2, 2),
                "entry_low": entry_low,
                "entry_high": entry_high,
                "spread_pct": 4.4,
                "volume": 1240,
                "open_interest": 5820,
                "delta": delta,
                "gamma": 0.08,
                "theta": -0.16,
                "vega": 0.11,
                "rho": 0.01,
                "iv": 0.34,
                "contract_score": 92.0,
                "strategy_mode": "WASEEM_V2",
                "engine_source": "Waseem V2",
                "horizon": "WEEKLY" if category == "equity_option" else "DAILY",
                "current_contract_price": round((entry_low + entry_high) / 2, 2),
                "underlying_current_price": round((underlying_low + underlying_high) / 2, 2),
                "underlying_direction": underlying_direction,
                "underlying_entry_low": underlying_low,
                "underlying_entry_high": underlying_high,
                "underlying_stop": underlying_stop,
                "underlying_tp1": underlying_targets[0],
                "underlying_tp2": underlying_targets[1],
                "underlying_tp3": underlying_targets[2],
            },
        }

    async def _send_template_test(self, update: Update, context, query, template_name: str):
        if not self.allowed(update):
            return await self._deny(update)
        if not await self._require_private(update):
            return

        trade = self._sample_option_signal("equity_option")
        option = trade.get("option") or {}
        now = datetime.now(timezone.utc)
        trade["trade_id"] = "OPT-TEST-V19"
        trade["created_at"] = now.isoformat()
        trade["published_at"] = now.isoformat()

        # Rich V5-only sample data is used only for private visual testing.
        if template_name in {"ready", "watch"}:
            option.update({
                "strategy_mode": "WASEEM_V5",
                "engine_source": "Waseem V5",
                "entry_state": "KEEP_WATCH" if template_name == "watch" else "ENTRY_READY",
                "entry_quality": 89.0 if template_name == "ready" else 74.0,
                "preferred_entry_low": trade["entry_low"],
                "preferred_entry_high": trade["entry_high"],
                "chase_risk": "LOW" if template_name == "ready" else "MEDIUM",
                "watch_reason": "السعر داخل منطقة الدخول والتدفق مؤيد" if template_name == "ready" else "بانتظار عودة العقد لمنطقة الدخول المفضلة",
                "expected_move": 5.20,
                "strike_distance": 1.10,
                "expected_move_coverage": 0.72,
                "strike_efficiency": 94.0,
                "bid_size": 156,
                "ask_size": 42,
                "quote_age_minutes": 0.1,
                "underlying_data_age_minutes": 1.0,
                "quote_timestamp": now.isoformat(),
                "underlying_data_timestamp": now.isoformat(),
                "v4_liquidity_score": 90.0,
                "v4_pre_move_score": 91.0,
                "v4_internal_liquidity": 184.70,
                "v4_external_liquidity": 187.20,
                "v4_liquidity_density": 88.0,
                "v4_volume_acceleration": 92.0,
                "v4_momentum_acceleration": 89.0,
                "v4_compression": 86.0,
                "ohlcv_levels_above": "$185.20 | $185.60 | $186.10 | $187.20",
                "ohlcv_levels_below": "$184.20 | $183.80 | $183.30 | $182.80",
                "v5_score": 93.0 if template_name == "ready" else 88.0,
                "v5_order_flow_score": 91.0 if template_name == "ready" else 70.0,
                "v5_flow_confidence": "HIGH" if template_name == "ready" else "MEDIUM",
                "v5_bid_ask_pressure": "BUYING",
                "v5_trade_aggression": "TOWARD_ASK",
                "v5_execution_pressure": "POSITIVE",
                "v5_book_imbalance": "UNAVAILABLE",
                "v5_absorption": "UNAVAILABLE",
                "v5_replenishment": "UNAVAILABLE",
                "v5_quote_status": "AVAILABLE",
                "v5_trade_status": "AVAILABLE",
                "market_context_lines": [
                    "SPY: NEUTRAL +0.01% | AVAILABLE",
                    "QQQ: BULLISH +0.06% | AVAILABLE",
                    "SECTOR (SMH): BULLISH +1.02% | AVAILABLE",
                    "VIX: BEARISH -0.92% | AVAILABLE",
                    "Economic Calendar (FRED): CAUTION — Employment Situation; FOMC Press Release",
                    "Earnings (Alpha Vantage): AVAILABLE — none in 3-month calendar",
                    "NEWS: AVAILABLE — Sample market headline for private message test",
                ],
            })
            trade["decision"] = "WATCH" if template_name == "watch" else "READY"
            if template_name == "watch":
                trade["watch_added_at"] = now.isoformat()
            text = self._candidate_details_text(trade, now)
            await self.app.bot.send_message(
                chat_id=settings.telegram_admin_user_id, text=text, parse_mode=ParseMode.HTML
            )

        elif template_name == "opportunity":
            path = os.path.join(tempfile.gettempdir(), "TEST_TEMPLATE_OPPORTUNITY.png")
            try:
                option_card(trade, path)
                with open(path, "rb") as image_file:
                    await self.app.bot.send_photo(
                        chat_id=settings.telegram_admin_user_id,
                        photo=image_file,
                        caption=signal_caption(trade),
                        parse_mode=ParseMode.HTML,
                    )
            finally:
                try: os.remove(path)
                except OSError: pass

        elif template_name == "v6":
            option.update({
                "strategy_mode":"WASEEM_V6", "engine_source":"Waseem V6",
                "entry_state":"ENTRY_READY", "entry_quality":88.0,
                "preferred_entry_low":trade["entry_low"], "preferred_entry_high":trade["entry_high"],
                "chase_risk":"LOW", "watch_reason":"توافق الفريمات والمساحة والزخم يدعم الدخول",
                "expected_move":5.20, "strike_distance":1.10, "expected_move_coverage":0.72, "strike_efficiency":94.0,
                "bid_size":156, "ask_size":42, "quote_age_minutes":15.0, "underlying_data_age_minutes":15.0,
                "quote_timestamp":now.isoformat(), "underlying_data_timestamp":now.isoformat(),
                "v4_liquidity_score":90.0, "v4_pre_move_score":91.0, "v4_internal_liquidity":184.70,
                "v4_external_liquidity":187.20, "v4_liquidity_density":88.0, "v4_volume_acceleration":92.0,
                "v4_momentum_acceleration":89.0, "v4_compression":86.0,
                "v5_order_flow_score":70.0, "v5_flow_confidence":"LOW",
                "v6_score":91.0, "v6_session":"RTH", "v6_delayed_data":True,
                "v6_multi_timeframe_score":84.0, "v6_room_to_target_score":80.0,
                "v6_momentum_decay_score":76.0, "v6_late_entry_score":74.0,
                "v6_breakout_quality_score":79.0, "v6_reversal_risk_score":32.0,
                "v6_ict_score":82.0, "v6_fibonacci_score":75.0,
                "v6_cross_state":"POSITIVE_CROSS", "v6_cross_score":88.0,
                "v6_nearest_support":183.80, "v6_nearest_resistance":185.60, "v6_next_target":187.20,
                "v6_watch_reason":"READY: structure + room + momentum + entry are aligned",
            })
            trade["decision"]="READY"
            trade["market_state"]="WASEEM_V6_READY"
            trade["score"]=91.0
            text=self._candidate_details_text(trade,now)
            await self.app.bot.send_message(chat_id=settings.telegram_admin_user_id,text=text,parse_mode=ParseMode.HTML)

        elif template_name == "success":
            entry = 3.35
            current = 3.99
            usd = 64.45
            sar = 241.69
            trade["filled_entry_price"] = entry
            option["strike"] = 235
            path = os.path.join(tempfile.gettempdir(), "TEST_TEMPLATE_SUCCESS.png")
            try:
                profit_update_card(trade, usd, sar, current, path)
                caption = success_message(trade, entry, current, 50.0, usd, sar, "🟢", "قوي", "استمرار مع حماية الربح")
                with open(path, "rb") as image_file:
                    await self.profit.send_photo(
                        chat_id=settings.telegram_admin_user_id, photo=image_file, caption=caption, parse_mode=ParseMode.HTML
                    )
            finally:
                try: os.remove(path)
                except OSError: pass

        elif template_name == "entry":
            option["strike"] = 235
            await self.app.bot.send_message(
                chat_id=settings.telegram_admin_user_id,
                text=entry_message(trade, 3.35),
                parse_mode=ParseMode.HTML,
            )

        elif template_name == "stock_analysis":
            result = await self.service.stock_analysis("AAPL")
            await self.app.bot.send_message(
                chat_id=settings.telegram_admin_user_id,
                text=self.service.stock_intelligence.render_ar(result),
                parse_mode=ParseMode.HTML,
            )

        elif template_name == "stock_news":
            result = await self.service.stock_news_analysis("AAPL")
            await self.app.bot.send_message(
                chat_id=settings.telegram_admin_user_id,
                text=self.service.stock_news.render_ar(result),
                parse_mode=ParseMode.HTML,
            )

        elif template_name == "profit":
            entry = 3.35
            current = 3.96
            usd = 60.50
            sar = 226.88
            trade["filled_entry_price"] = entry
            option["strike"] = 235
            path = os.path.join(tempfile.gettempdir(), "TEST_TEMPLATE_PROFIT.png")
            try:
                profit_update_card(trade, usd, sar, current, path)
                caption = profit_update_message(trade, entry, current, usd, sar, now=now)
                with open(path, "rb") as image_file:
                    await self.profit.send_photo(
                        chat_id=settings.telegram_admin_user_id, photo=image_file, caption=caption, parse_mode=ParseMode.HTML
                    )
            finally:
                try: os.remove(path)
                except OSError: pass
        else:
            return await self._edit_menu(query, "❌ نموذج اختبار غير معروف.", self._message_tests_menu_markup())

        return await self._edit_menu(
            query,
            "✅ تم إرسال نموذج الرسالة في الخاص.\nاختبار عرض فقط — لم يتم إنشاء أو تعديل أي صفقة.",
            self._message_tests_menu_markup(),
        )

    async def _send_test_signal(self, update: Update, context, query, category: str):
        if category not in {"equity_option", "index_option"}:
            return await self._edit_menu(query, "❌ نوع اختبار غير صالح.", self._message_tests_menu_markup())
        if not self.allowed(update):
            return await self._deny(update)
        if not await self._require_private(update):
            return

        trade = self._sample_option_signal(category)
        path = os.path.join(tempfile.gettempdir(), f"TEST_{category.upper()}_SIGNAL.png")
        try:
            option_card(trade, path)
            caption = "🧪 اختبار فقط — لا توجد صفقة حقيقية\n" + signal_caption(trade, max_chars=970)
            with open(path, "rb") as image_file:
                await self.app.bot.send_photo(
                    chat_id=settings.telegram_admin_user_id,
                    photo=image_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            return await self._edit_menu(
                query,
                "✅ تم إرسال نموذج رسالة الصفقة والصورة في الخاص.\nلم يتم إنشاء أو تسجيل أي صفقة.",
                self._message_tests_menu_markup(),
            )
        except Exception as exc:
            return await self._edit_menu(
                query,
                f"❌ فشل اختبار رسالة الصفقة: {type(exc).__name__}",
                self._message_tests_menu_markup(),
            )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def _send_test_profit(self, update: Update, context, query, category: str):
        if category not in {"equity_option", "index_option"}:
            return await self._edit_menu(query, "❌ نوع اختبار غير صالح.", self._message_tests_menu_markup())
        if not self.allowed(update):
            return await self._deny(update)
        if not await self._require_private(update):
            return

        base = self._sample_option_signal(category)
        entry = float(base["entry_high"])
        current = round(entry + 1.25, 2)
        contracts = 1
        usd = (current - entry) * settings.option_multiplier * contracts
        sar = usd * settings.usd_sar_rate
        base["filled_entry_price"] = entry
        base["contracts"] = contracts
        base["trade_id"] = f"TEST-{base['symbol']}-PROFIT"
        path = os.path.join(tempfile.gettempdir(), f"TEST_{category.upper()}_PROFIT.png")
        option = base.get("option") or {}
        label = f"{base.get('symbol')} {option.get('strike')} {option.get('type')}"
        caption = profit_update_message(base, entry, current, usd, sar, now=datetime.now(timezone.utc))
        try:
            profit_update_card(base, usd, sar, current, path)
            with open(path, "rb") as image_file:
                await self.profit.send_photo(
                    chat_id=settings.telegram_admin_user_id,
                    photo=image_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            return await self._edit_menu(
                query,
                "✅ تم إرسال نموذج رسالة الأرباح والصورة في الخاص.\nلم تتغير الصفقات أو الإحصائيات.",
                self._message_tests_menu_markup(),
            )
        except Exception as exc:
            return await self._edit_menu(
                query,
                f"❌ فشل اختبار رسالة الأرباح: {type(exc).__name__}",
                self._message_tests_menu_markup(),
            )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def test_profit_alert(self, update: Update, context, query=None):
        """Backward-compatible slash command; private test only."""
        if query is not None:
            return await self._send_test_profit(update, context, query, "equity_option")
        if not self.allowed(update):
            return await self._deny(update)
        if not await self._require_private(update):
            return

        trade = self._sample_option_signal("equity_option")
        entry = float(trade["entry_high"])
        current = round(entry + 1.25, 2)
        usd = (current - entry) * settings.option_multiplier
        sar = usd * settings.usd_sar_rate
        trade["filled_entry_price"] = entry
        trade["contracts"] = 1
        path = os.path.join(tempfile.gettempdir(), "TEST_EQUITY_OPTION_PROFIT.png")
        try:
            profit_update_card(trade, usd, sar, current, path)
            with open(path, "rb") as image_file:
                await self.profit.send_photo(
                    chat_id=settings.telegram_admin_user_id,
                    photo=image_file,
                    caption=profit_update_message(trade, entry, current, usd, sar, now=datetime.now(timezone.utc)),
                    parse_mode=ParseMode.HTML,
                )
            return await update.effective_message.reply_text("✅ تم إرسال اختبار الأرباح في الخاص. TEST ONLY.")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # =========================================================
    # Authorization
    # =========================================================

    def allowed(self, update: Update) -> bool:
        return bool(
            update.effective_user
            and update.effective_user.id
            == settings.telegram_admin_user_id
        )

    async def _deny(self, update: Update):
        await update.effective_message.reply_text(
            "⛔ غير مصرح لهذا الحساب."
        )

    def _is_private(self, update: Update) -> bool:
        return bool(
            update.effective_chat
            and update.effective_chat.type == "private"
        )

    async def _require_private(self, update: Update) -> bool:
        if self._is_private(update):
            return True

        await update.effective_message.reply_text(
            "🔒 هذا الأمر يعمل في المحادثة الخاصة "
            "مع Signal Bot فقط."
        )
        return False

    # =========================================================
    # Pause State
    # =========================================================

    def _paused(self) -> bool:
        rows = self.state_repo.all()

        return bool(
            rows
            and rows[0].get("paused")
        )

    def _set_paused(self, value: bool):
        self.state_repo.replace(
            [{"paused": value}]
        )

    # =========================================================
    # Common Helpers
    # =========================================================

    @staticmethod
    def _requested_count(context) -> int:
        """
        /stock
        /stock 2
        /stock 3

        Invalid values are safely clamped.
        """

        default = settings.default_signals_per_scan

        if not context.args:
            return default

        try:
            requested = int(context.args[0])
        except (TypeError, ValueError):
            return default

        return max(
            1,
            min(
                requested,
                settings.max_signals_per_scan,
            ),
        )

    @staticmethod
    def _trade_type_ar(value: str) -> str:
        mapping = {
            "STOCK_INTRADAY":
                "سهم أمريكي — مضاربة يومية",

            "STOCK_SWING":
                "سهم أمريكي — سوينغ",

            "EQUITY_OPTION_INTRADAY":
                "خيارات سهم — مضاربة يومية",

            "EQUITY_OPTION_SWING":
                "خيارات سهم — سوينغ",

            "INDEX_OPTION_INTRADAY":
                "خيارات مؤشر — مضاربة يومية",

            "INDEX_OPTION_SWING":
                "خيارات مؤشر — سوينغ",
        }

        return mapping.get(value, value)

    @staticmethod
    def _is_option_trade(trade: dict) -> bool:
        return bool(trade.get("option"))

    @staticmethod
    def _is_index_trade(trade: dict) -> bool:
        return str(
            trade.get("trade_type", "")
        ).startswith("INDEX_OPTION")

    @staticmethod
    def _trade_prefix(trade: dict) -> str:
        if str(
            trade.get("trade_type", "")
        ).startswith("INDEX_OPTION"):
            return "IDX"

        if trade.get("option"):
            return "OPT"

        return "STK"

    @staticmethod
    def _contract_label(trade: dict) -> str:
        option = trade.get("option") or {}

        if not option:
            return trade.get("symbol", "N/A")

        contract_type = str(
            option.get("type", "")
        ).upper()

        strike = option.get("strike", "")

        return (
            f'{trade.get("symbol", "N/A")} '
            f'{strike} {contract_type}'
        ).strip()

    @staticmethod
    def _entry_reference(trade: dict) -> float:
        stored = trade.get(
            "filled_entry_price",
            trade.get("entry_price"),
        )

        if stored is not None:
            try:
                return float(stored)
            except (TypeError, ValueError):
                pass

        direction = str(
            trade.get("direction", "LONG")
        ).upper()

        if trade.get("option"):
            # Long-premium option positions use the conservative Ask/high edge
            # regardless of whether the underlying thesis is bullish or bearish.
            value = trade.get(
                "entry_high",
                trade.get("entry_low", 0),
            )
        elif direction == "SHORT":
            value = trade.get(
                "entry_low",
                trade.get("entry_high", 0),
            )
        else:
            value = trade.get(
                "entry_high",
                trade.get("entry_low", 0),
            )

        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _trade_pnl_pct(
        cls,
        trade: dict,
        current_price: float,
    ) -> float:
        entry = cls._entry_reference(trade)

        if entry <= 0:
            return 0.0

        try:
            price = float(current_price)
        except (TypeError, ValueError):
            return 0.0

        direction = str(
            trade.get("direction", "LONG")
        ).upper()

        multiplier = (
            1.0
            if trade.get("option")
            else (-1.0 if direction == "SHORT" else 1.0)
        )

        return (
            (
                price - entry
            )
            / entry
            * 100.0
            * multiplier
        )

    @classmethod
    def _trade_cash_pnl(cls, trade: dict, current_price: float) -> tuple[float, float]:
        """Realized option cash P&L. Options are long-premium positions."""
        if not trade.get("option"):
            return 0.0, 0.0
        entry = cls._entry_reference(trade)
        try:
            price = float(current_price)
            contracts = max(1, int(float(trade.get("contracts", 1) or 1)))
        except (TypeError, ValueError):
            return 0.0, 0.0
        if entry <= 0 or price <= 0:
            return 0.0, 0.0
        usd = (price - entry) * settings.option_multiplier * contracts
        return round(usd, 2), round(usd * settings.usd_sar_rate, 2)

    async def _delete_trade_channel_messages(self, trade: dict) -> tuple[int, int]:
        """Delete Telegram messages associated with one manually closed trade.

        History/performance data is intentionally preserved. Deletion requires
        the corresponding bot to have Telegram delete-message permission.
        """
        chat_id = settings.telegram_channel_chat_id
        if not chat_id:
            return 0, 0
        refs = list(trade.get("telegram_message_refs") or [])
        if trade.get("channel_message_id"):
            base = {"bot": "signal", "message_id": int(trade["channel_message_id"])}
            if base not in refs:
                refs.insert(0, base)
        deleted = failed = 0
        # Newest first makes reply-thread cleanup more predictable.
        for ref in reversed(refs):
            try:
                message_id = int(ref.get("message_id"))
                bot_name = str(ref.get("bot") or "signal")
                bot = self.profit if bot_name == "profit" else self.app.bot
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
                deleted += 1
            except Exception:
                failed += 1
        return deleted, failed

    # =========================================================
    # Candidate Expiry
    # =========================================================

    def _scan_expired(self, session: dict) -> bool:
        age = (
            time.monotonic()
            - session["created_monotonic"]
        )

        return (
            age
            > settings.candidate_ttl_seconds
        )

    def _clear_expired_scan(
        self,
        user_id: int,
    ) -> bool:
        session = self.pending_scans.get(user_id)

        if not session:
            return False

        if not self._scan_expired(session):
            return False

        self.pending_scans.pop(
            user_id,
            None,
        )

        return True

    # =========================================================
    # Portfolio / Duplicate / Daily Gates
    # =========================================================

    def _open_rows(self) -> list[dict]:
        return [
            row
            for row in self.open_repo.all()
            if row.get("status") == "OPEN"
        ]

    def _exact_duplicate(
        self,
        candidate: dict,
        rows: list[dict],
    ) -> bool:
        """
        Important:
        NVDA Stock + NVDA Option is ALLOWED.

        What is blocked:
        - same stock trade idea
        - exact same option contract
        """

        candidate_option = candidate.get("option")

        for row in rows:
            row_option = row.get("option")

            # Stock vs stock
            if not candidate_option and not row_option:
                if (
                    row.get("symbol")
                    == candidate.get("symbol")
                    and row.get("trade_type")
                    == candidate.get("trade_type")
                    and row.get("direction")
                    == candidate.get("direction")
                ):
                    return True

            # Option vs option
            if candidate_option and row_option:
                candidate_contract = str(
                    candidate_option.get(
                        "symbol",
                        "",
                    )
                )

                row_contract = str(
                    row_option.get(
                        "symbol",
                        "",
                    )
                )

                if (
                    candidate_contract
                    and row_contract
                    and candidate_contract
                    == row_contract
                ):
                    return True

        return False

    def _portfolio_gate(
        self,
        trade: dict,
    ) -> tuple[bool, str]:
        rows = self._open_rows()

        # -----------------------------------------------
        # Open trade count
        # -----------------------------------------------
        if len(rows) >= settings.max_open_trades:
            return (
                False,
                "تم بلوغ الحد الأقصى "
                "للصفقات المفتوحة.",
            )

        # -----------------------------------------------
        # Total risk
        # -----------------------------------------------
        total_risk = sum(
            float(
                row.get(
                    "risk_pct",
                    0,
                )
                or 0
            )
            for row in rows
        )

        new_risk = float(
            trade.get(
                "risk_pct",
                0,
            )
            or 0
        )

        if (
            total_risk + new_risk
            > settings.max_total_open_risk
        ):
            return (
                False,
                "إجمالي المخاطر المفتوحة "
                "سيتجاوز الحد المسموح.",
            )

        # -----------------------------------------------
        # Exact duplicate only
        #
        # Do NOT block NVDA stock merely because
        # NVDA option is already open.
        # -----------------------------------------------
        if (
            settings.prevent_exact_duplicate_trade
            and self._exact_duplicate(
                trade,
                rows,
            )
        ):
            return (
                False,
                "يوجد بالفعل Trade مطابق "
                "أو عقد مطابق مفتوح.",
            )

        # -----------------------------------------------
        # Sector concentration
        #
        # Do not hard reject simply because
        # two same-sector trades exist.
        # Risk ceiling remains the hard protection.
        # -----------------------------------------------

        return True, "ACCEPT"

    def _daily_publish_count(
        self,
        category: str,
    ) -> int:
        """
        Counts trades created/published today
        from open + history.

        category:
        stock
        option
        index
        """

        today = datetime.now(
            timezone.utc
        ).date()

        rows = (
            self.open_repo.all()
            + self.history_repo.all()
        )

        count = 0
        seen_ids: set[str] = set()

        for row in rows:
            trade_id = str(
                row.get(
                    "trade_id",
                    "",
                )
            )

            if trade_id and trade_id in seen_ids:
                continue

            published_at = (
                row.get("published_at")
                or row.get("created_at")
            )

            if not published_at:
                continue

            try:
                created_date = (
                    datetime.fromisoformat(
                        str(
                            published_at
                        ).replace(
                            "Z",
                            "+00:00",
                        )
                    )
                    .astimezone(timezone.utc)
                    .date()
                )
            except Exception:
                continue

            if created_date != today:
                continue

            trade_type = str(
                row.get(
                    "trade_type",
                    "",
                )
            )

            matches = False

            if category == "stock":
                matches = trade_type.startswith(
                    "STOCK_"
                )

            elif category == "option":
                matches = trade_type.startswith(
                    "EQUITY_OPTION_"
                )

            elif category == "index":
                matches = trade_type.startswith(
                    "INDEX_OPTION_"
                )

            if matches:
                count += 1

                if trade_id:
                    seen_ids.add(trade_id)

        return count

    def _daily_gate(
        self,
        trade: dict,
    ) -> tuple[bool, str]:
        trade_type = str(
            trade.get(
                "trade_type",
                "",
            )
        )

        if trade_type.startswith("STOCK_"):
            current = self._daily_publish_count(
                "stock"
            )

            limit = (
                settings.max_daily_stock_signals
            )

            label = "صفقات الأسهم"

        elif trade_type.startswith(
            "EQUITY_OPTION_"
        ):
            current = self._daily_publish_count(
                "option"
            )

            limit = (
                settings
                .max_daily_equity_option_signals
            )

            label = "عقود الأسهم"

        else:
            current = self._daily_publish_count(
                "index"
            )

            limit = (
                settings
                .max_daily_index_option_signals
            )

            label = "عقود المؤشر"

        if current >= limit:
            return (
                False,
                f"تم بلوغ الحد اليومي لـ{label}: "
                f"{current}/{limit}",
            )

        return True, "ACCEPT"

    # =========================================================
    # Start / Help
    # =========================================================

    async def start(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        context.user_data.pop("success_rule_pending", None)
        await update.effective_message.reply_text(
            "✅ ALLUQMANU_USA_TD Ready",
            reply_markup=self._main_menu_markup(),
        )

    async def help(
        self,
        update: Update,
        context,
    ):
        return await self.start(
            update,
            context,
        )

    async def myid(
        self,
        update: Update,
        context,
    ):
        if not update.effective_user:
            return

        await update.effective_message.reply_text(
            "👤 Telegram User ID:\n"
            f"{update.effective_user.id}"
        )

    # =========================================================
    # Scanning
    # =========================================================

    async def _run_scan(
        self,
        update: Update,
        context,
        kind: str,
        index_strategy: str = "core",
        horizon: str | None = None,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        if self._paused():
            return await (
                update.effective_message.reply_text(
                    "⏸️ البحث عن إشارات جديدة موقوف.\n"
                    "استخدم /resume."
                )
            )

        if kind == "index" and str(index_strategy).lower() in {"waseem_v3", "waseem3", "v3"}:
            state = self.service.spx_option_session_status()
            is_open = bool(state.get("open"))
            clock = f"{state.get('session')} | {state.get('timestamp')}"
        else:
            is_open, clock = await self.service.market_is_open()

        if not is_open:
            return await (
                update.effective_message.reply_text(
                    "⏰ السوق الأمريكي مغلق "
                    "أو تعذر تأكيد أنه مفتوح.\n\n"

                    "لن يتم فتح أو نشر أي صفقة.\n"
                    f"{clock}"
                )
            )

        requested = self._requested_count(
            context
        )

        labels = {
            "stock": "الأسهم الأمريكية",
            "option": "خيارات الأسهم",
            "index": "خيارات SPX",
        }

        menu_mode_active = bool(context.user_data.get("_menu_callback"))
        if not menu_mode_active:
            await update.effective_message.reply_text(
                f"🔎 بدأ فحص {labels[kind]}\n\n"
                f"المطلوب: أفضل {requested} "
                "فرصة كحد أقصى\n\n"
                "⚠️ لن يتم نشر أو فتح أي صفقة "
                "قبل اختيارك وموافقتك."
            )

        async with self._global_scan_lock:
            candidates, rejects = await self._fetch_candidates(
                kind,
                index_strategy=index_strategy,
                max_results=requested,
                horizon=horizon,
            )

        if not candidates:
            message = (
                "❌ لا توجد صفقة READY حاليًا."
            )

            if rejects:
                message += (
                    "\n\nأسباب مختصرة:\n"
                    + "\n".join(
                        f"• {item}"
                        for item in rejects[-7:]
                    )
                )

            menu_query = context.user_data.pop("_menu_query", None)
            menu_mode = bool(context.user_data.pop("_menu_callback", False))
            if menu_mode and menu_query is not None:
                return await self._edit_menu(
                    menu_query,
                    "No READY opportunities right now.",
                    self._trading_menu_markup(),
                )
            return await (
                update.effective_message.reply_text(
                    message
                )
            )

        detected_utc = datetime.now(timezone.utc)
        detected_monotonic = time.monotonic()
        rows: list[dict] = [
            signal.to_dict()
            for signal in candidates
        ]
        for row in rows:
            row["_candidate_detected_at"] = detected_utc.isoformat()
            row["_candidate_created_monotonic"] = detected_monotonic

        user_id = update.effective_user.id

        self.pending_scans[user_id] = {
            "candidates": rows,
            "scan_type": (f"index:{index_strategy}" if kind == "index" else kind),
            "created_monotonic": detected_monotonic,
            "picked_index": None,
            "published_indexes": set(),
        }

        scan_title = (
            "✅ اكتمل فحص SPX V20" if kind == "index" and index_strategy == "v20"
            else "✅ اكتمل فحص SPX Confirmed Setup" if kind == "index" and index_strategy == "confirmed"
            else "✅ اكتمل فحص SPX وسيم V3" if kind == "index" and index_strategy == "waseem_v3"
            else "✅ اكتمل فحص SPX وسيم V2" if kind == "index" and index_strategy == "waseem_v2"
            else "✅ اكتمل فحص SPX وسيم V1" if kind == "index" and index_strategy == "waseem"
            else "✅ اكتمل فحص SPX Core" if kind == "index"
            else "✅ اكتمل فحص Equity Confirmed Setup" if kind == "option" and index_strategy == "confirmed"
            else "✅ اكتمل فحص Equity وسيم V3" if kind == "option" and index_strategy == "waseem_v3"
            else "✅ اكتمل فحص Equity وسيم V2" if kind == "option" and index_strategy == "waseem_v2"
            else "✅ اكتمل فحص Equity وسيم V1" if kind == "option" and index_strategy == "waseem"
            else "✅ اكتمل الفحص"
        )
        lines = [
            scan_title,
            "",
            f"تم العثور على {len(rows)} "
            "فرصة READY:",
            "",
        ]

        medals = {
            1: "🥇",
            2: "🥈",
            3: "🥉",
        }

        for index, trade in enumerate(
            rows,
            start=1,
        ):
            medal = medals.get(
                index,
                "🔹",
            )

            lines.append(
                f"{medal} {index}) "
                f"{self._contract_label(trade)}"
            )

            lines.append(
                "النوع: "
                f"{self._trade_type_ar(trade['trade_type'])}"
            )

            lines.append(
                f"Score: {trade['score']}/100"
            )

            lines.append(
                f"R/R: 1 : {trade['rr']}"
            )

            if trade.get("option"):
                option = trade["option"]

                lines.append(
                    "Expiration: "
                    f"{option.get('expiration', 'N/A')}"
                )

                lines.append(
                    "DTE: "
                    f"{option.get('dte', 'N/A')}"
                )

                lines.append(
                    "Bid/Ask: "
                    f"${option.get('bid', 'N/A')} / "
                    f"${option.get('ask', 'N/A')}"
                )

            lines.append("")

        lines.extend(
            [
                "اختر الصفقة برقم:",
                "",
            ]
        )

        for index in range(
            1,
            len(rows) + 1,
        ):
            lines.append(
                f"/pic{index}k"
            )

        lines.extend(
            [
                "",
                "⏳ صلاحية نتائج الفحص: "
                f"{settings.candidate_ttl_seconds // 60} "
                "دقائق.",
                "",
                "⚠️ لا شيء تم نشره حتى الآن.",
            ]
        )

        menu_query = context.user_data.pop("_menu_query", None)
        menu_mode = bool(context.user_data.pop("_menu_callback", False))
        if menu_mode and menu_query is not None:
            compact = [f"Top {len(rows)} Opportunities", ""]
            for index, trade in enumerate(rows, start=1):
                option = trade.get("option") or {}
                side = str(option.get("type", "")).upper()
                label = f"{index}️⃣ {trade.get('symbol', 'N/A')}"
                if side:
                    label += f" {side}"
                compact.append(label)
                compact.append(f"Strength: {self._signal_strength(trade.get('score'))} | Score: {trade['score']}/100 | R/R: 1:{trade['rr']}")
                if option:
                    compact.append(
                        f"Exp: {option.get('expiration','N/A')} | DTE: {option.get('dte','N/A')} | Current: {self._fmt_money(option.get('mid'))}"
                    )
                else:
                    compact.append(f"Current: {self._fmt_money(trade.get('current_price'))}")
            await self._edit_menu(
                menu_query,
                "\n".join(compact),
                self._candidate_markup(rows, kind),
            )
        else:
            await update.effective_message.reply_text(
                "\n".join(lines)
            )

    async def stock(
        self,
        update: Update,
        context,
    ):
        await self._run_scan(
            update,
            context,
            "stock",
        )

    async def option(
        self,
        update: Update,
        context,
    ):
        await self._run_scan(
            update,
            context,
            "option",
        )

    async def indexoption(
        self,
        update: Update,
        context,
    ):
        await self._run_scan(
            update,
            context,
            "index",
        )

    # =========================================================
    # Pick
    # =========================================================

    async def pick(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        user_id = update.effective_user.id

        if self._clear_expired_scan(user_id):
            return await (
                update.effective_message.reply_text(
                    "⚠️ انتهت صلاحية نتائج الفحص.\n"
                    "أعد البحث للحصول على أسعار "
                    "وبيانات محدثة."
                )
            )

        session = self.pending_scans.get(
            user_id
        )

        if not session:
            return await (
                update.effective_message.reply_text(
                    "❌ لا يوجد فحص نشط.\n\n"
                    "استخدم أولًا:\n"
                    "/stock 3\n"
                    "أو /option 3\n"
                    "أو /indexoption 3"
                )
            )

        selected_number = None
        command_text = (update.effective_message.text or "").split()[0]
        match = re.match(r"^/pic([123])k(?:@\w+)?$", command_text, re.IGNORECASE)
        if match:
            selected_number = int(match.group(1))
        elif context.args:
            try:
                selected_number = int(context.args[0])
            except ValueError:
                selected_number = None
        if selected_number is None:
            return await update.effective_message.reply_text(
                "استخدم: /pic1k أو /pic2k أو /pic3k"
            )

        candidates = session["candidates"]

        if not (
            1
            <= selected_number
            <= len(candidates)
        ):
            return await (
                update.effective_message.reply_text(
                    "❌ رقم الصفقة غير موجود.\n"
                    f"اختر من 1 إلى "
                    f"{len(candidates)}."
                )
            )

        index = selected_number - 1

        if index in session[
            "published_indexes"
        ]:
            return await (
                update.effective_message.reply_text(
                    "⚠️ هذه الصفقة سبق نشرها "
                    "من نفس الفحص."
                )
            )

        session["picked_index"] = index

        trade = candidates[index]

        detected_raw = trade.get("_candidate_detected_at")
        try:
            detected_at = (
                datetime.fromisoformat(str(detected_raw).replace("Z", "+00:00"))
                if detected_raw else datetime.now(timezone.utc)
            )
        except Exception:
            detected_at = datetime.now(timezone.utc)
        detail_text = self._candidate_details_text(trade, detected_at)

        menu_query = context.user_data.pop("_menu_query", None)
        menu_mode = bool(context.user_data.pop("_menu_callback", False))
        if menu_mode and menu_query is not None:
            await self._edit_menu(
                menu_query,
                "الصفقة المحددة\n\n" + detail_text,
                self._approval_markup(),
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.effective_message.reply_text(
                detail_text
                + "\n\nللاعتماد والنشر: /publish"
                + "\nللإلغاء: /cancel",
                parse_mode=ParseMode.HTML,
            )

    # =========================================================
    # Publish
    # =========================================================

    async def publish(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        user_id = update.effective_user.id

        if self._clear_expired_scan(user_id):
            return await (
                update.effective_message.reply_text(
                    "⚠️ Candidate expired\nPlease rescan.\n\n"
                    "انتهت صلاحية العرض (3 دقائق)، ولم يتم النشر أو إنشاء Trade."
                )
            )

        session = self.pending_scans.get(
            user_id
        )

        if not session:
            return await (
                update.effective_message.reply_text(
                    "❌ لا توجد صفقة بانتظار النشر."
                )
            )

        picked_index = session.get(
            "picked_index"
        )

        if picked_index is None:
            return await (
                update.effective_message.reply_text(
                    "❌ اختر الصفقة أولًا.\n\n"
                    "استخدم /pic1k أو /pic2k أو /pic3k"
                )
            )

        if picked_index in session[
            "published_indexes"
        ]:
            return await (
                update.effective_message.reply_text(
                    "⚠️ هذه الصفقة سبق نشرها."
                )
            )

        trade = dict(
            session["candidates"][
                picked_index
            ]
        )

        if str(trade.get("decision", "READY")).upper() == "WATCH":
            return await update.effective_message.reply_text(
                "👁 هذه الفرصة ما زالت WATCH وليست READY.\n"
                "سعر العقد الحالي لم يصل بعد إلى منطقة الدخول المفضلة في Waseem V3.\n"
                "لن يتم نشر أو إنشاء Trade حتى تتحول إلى READY."
            )

        # -----------------------------------------------
        # Re-check portfolio at PUBLISH time.
        # Not at scan time.
        # -----------------------------------------------
        ok, reason = self._portfolio_gate(
            trade
        )

        if not ok:
            return await (
                update.effective_message.reply_text(
                    "❌ لم يتم اعتماد الصفقة.\n\n"
                    f"السبب:\n{reason}\n\n"
                    "لم يتم فتح أو نشر شيء."
                )
            )

        daily_ok, daily_reason = (
            self._daily_gate(trade)
        )

        if not daily_ok:
            return await (
                update.effective_message.reply_text(
                    "❌ لم يتم اعتماد الصفقة.\n\n"
                    f"{daily_reason}\n\n"
                    "لم يتم فتح أو نشر شيء."
                )
            )

        if not settings.telegram_channel_chat_id:
            return await (
                update.effective_message.reply_text(
                    "❌ TELEGRAM_CHANNEL_CHAT_ID "
                    "غير مضبوط.\n\n"
                    "لم يتم إنشاء Trade "
                    "لأن النشر في القناة فشل "
                    "قبل أن يبدأ."
                )
            )

        # -----------------------------------------------
        # Assign Trade ID only after admin approval.
        # -----------------------------------------------
        prefix = self._trade_prefix(trade)

        trade["trade_id"] = (
            f"{prefix}-"
            f"{uuid.uuid4().hex[:8].upper()}"
        )

        trade["status"] = "OPEN"

        trade["published_at"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        trade["entry_confirmed"] = False
        trade["filled_entry_price"] = None

        trade.update(
            {
                "tp1_hit": False,
                "tp2_hit": False,
                "tp3_hit": False,
                "near_stop_sent": False,
                "manual_publish": True,
            }
        )

        text = signal_text(trade)

        image_path = None

        try:
            # -------------------------------------------
            # OPTION IMAGE
            #
            # Horizontal card for BOTH:
            # - Equity Options
            # - SPX Index Options
            # -------------------------------------------
            if trade.get("option"):
                image_path = os.path.join(
                    tempfile.gettempdir(),
                    f'{trade["trade_id"]}.png',
                )

                option_card(
                    trade,
                    image_path,
                )

                with open(
                    image_path,
                    "rb",
                ) as photo_file:
                    # One Telegram post: visual card + the existing detailed
                    # signal text as its caption. No second channel message.
                    sent_message = await self.app.bot.send_photo(
                        chat_id=(
                            settings
                            .telegram_channel_chat_id
                        ),
                        photo=photo_file,
                        caption=signal_caption(trade),
                        parse_mode=ParseMode.HTML,
                    )
            else:
                # Stock signals have no option card, so their existing text
                # message behavior stays unchanged.
                sent_message = await self.app.bot.send_message(
                    chat_id=(
                        settings.telegram_channel_chat_id
                    ),
                    text=text,
                )
            trade["channel_message_id"] = sent_message.message_id
            trade["telegram_message_refs"] = [
                {"bot": "signal", "message_id": int(sent_message.message_id)}
            ]

        except Exception as exc:
            # Critical:
            # Do NOT create open trade if Telegram
            # publishing fails.
            return await (
                update.effective_message.reply_text(
                    "❌ فشل نشر الصفقة في القناة.\n\n"
                    "لم يتم إنشاء Trade.\n\n"
                    f"الخطأ: "
                    f"{type(exc).__name__}"
                )
            )

        finally:
            if image_path:
                try:
                    os.remove(image_path)
                except OSError:
                    pass

        # -----------------------------------------------
        # Only NOW persist as OPEN.
        # -----------------------------------------------
        self.open_repo.append(trade)

        session[
            "published_indexes"
        ].add(picked_index)

        session["picked_index"] = None

        menu_query = context.user_data.pop("_menu_query", None)
        menu_mode = bool(context.user_data.pop("_menu_callback", False))
        if menu_mode and menu_query is not None:
            await self._edit_menu(
                menu_query,
                "✅ Trade Approved\n"
                "📡 Published to Channel\n"
                f"🆔 {trade['trade_id']}",
                InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("📂 Open Trades", callback_data="menu:open")],
                        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")],
                    ]
                ),
            )
        else:
            await update.effective_message.reply_text(
                "✅ تم اعتماد ونشر الصفقة بنجاح.\n\n"
                f"🆔 Trade ID:\n"
                f"{trade['trade_id']}\n\n"
                "📡 تم نشرها في القناة.\n"
                "📂 أصبحت الآن ضمن الصفقات المفتوحة."
            )

    # =========================================================
    # Cancel Selection
    # =========================================================

    async def cancel(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        user_id = update.effective_user.id

        session = self.pending_scans.get(
            user_id
        )

        if not session:
            return await (
                update.effective_message.reply_text(
                    "ℹ️ لا يوجد اختيار نشط لإلغائه."
                )
            )

        session["picked_index"] = None

        await update.effective_message.reply_text(
            "✅ تم إلغاء الاختيار.\n"
            "لم يتم فتح أو نشر أي صفقة.\n\n"
            "تستطيع اختيار فرصة أخرى من "
            "نفس نتائج الفحص قبل انتهاء صلاحيتها."
        )

    # =========================================================
    # Latest Price Helpers
    # =========================================================

    async def _latest_trade_price(
        self,
        trade: dict,
    ) -> float | None:
        """
        Used for manual Paper close.

        Stock:
        latest IEX bar close.

        Options:
        indicative latest quote midpoint.
        """

        option = trade.get("option")

        try:
            if option:
                contract_symbol = option.get(
                    "symbol"
                )

                if not contract_symbol:
                    return trade.get(
                        "last_price"
                    )

                quotes = (
                    await self.service.provider
                    .option_quotes(
                        [contract_symbol]
                    )
                )

                quote = quotes.get(
                    contract_symbol,
                    {},
                )

                bid = quote.get("bp")
                ask = quote.get("ap")

                if (
                    bid is not None
                    and ask is not None
                ):
                    bid = float(bid)
                    ask = float(ask)

                    if bid > 0 and ask > 0:
                        return round(
                            (
                                bid + ask
                            )
                            / 2,
                            4,
                        )

                if bid is not None:
                    return float(bid)

                if ask is not None:
                    return float(ask)

            else:
                symbol = trade.get("symbol")

                bars = (
                    await self.service.provider
                    .latest_bars(
                        [symbol]
                    )
                )

                bar = bars.get(
                    symbol,
                    {},
                )

                close = bar.get("c")

                if close is not None:
                    return float(close)

        except Exception:
            pass

        fallback = trade.get(
            "last_price"
        )

        if fallback is None:
            return None

        try:
            return float(fallback)
        except Exception:
            return None

    # =========================================================
    # Manual Close Helpers
    # =========================================================

    def _find_open_trade(
        self,
        trade_id: str,
    ) -> dict | None:
        trade_id = trade_id.upper()

        for trade in self._open_rows():
            if str(
                trade.get(
                    "trade_id",
                    "",
                )
            ).upper() == trade_id:
                return trade

        return None

    async def _prepare_close(
        self,
        update: Update,
        trade: dict,
    ):
        user_id = update.effective_user.id

        trade_id = trade["trade_id"]

        last_price = (
            await self._latest_trade_price(
                trade
            )
        )

        self.pending_closes[user_id] = {
            "trade_id": trade_id,
            "created_monotonic":
                time.monotonic(),
        }

        entry = self._entry_reference(
            trade
        )

        pnl_text = "N/A"

        if (
            last_price is not None
            and entry > 0
        ):
            pnl = self._trade_pnl_pct(
                trade,
                last_price,
            )

            pnl_text = f"{pnl:+.2f}%"

        await update.effective_message.reply_text(
            "🔎 تم العثور على الصفقة\n\n"

            f"الأصل:\n"
            f"{self._contract_label(trade)}\n\n"

            f"النوع:\n"
            f"{self._trade_type_ar(trade['trade_type'])}\n\n"

            f"Trade ID:\n"
            f"{trade_id}\n\n"

            f"سعر الدخول المرجعي:\n"
            f"{entry}\n\n"

            f"آخر سعر متاح:\n"
            f"{last_price if last_price is not None else 'N/A'}\n\n"

            f"P&L التقريبي:\n"
            f"{pnl_text}\n\n"

            "⚠️ هل تريد إغلاقها ًا؟\n\n"

            "للتأكيد:\n"
            f"/confirm_close {trade_id}"
        )

    # =========================================================
    # Close Stock
    # =========================================================

    async def close_stock(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        if not context.args:
            return await (
                update.effective_message.reply_text(
                    "استخدم:\n"
                    "/close_stock NVDA"
                )
            )

        symbol = context.args[0].upper()

        rows = [
            trade
            for trade in self._open_rows()
            if (
                trade.get("symbol") == symbol
                and str(
                    trade.get(
                        "trade_type",
                        "",
                    )
                ).startswith("STOCK_")
            )
        ]

        if not rows:
            return await (
                update.effective_message.reply_text(
                    f"📂 لا توجد صفقة سهم "
                    f"مفتوحة على {symbol}."
                )
            )

        if len(rows) > 1:
            lines = [
                f"⚠️ توجد {len(rows)} "
                f"صفقات سهم مفتوحة على {symbol}.",
                "",
                "اختر Trade ID:",
                "",
            ]

            for trade in rows:
                lines.append(
                    f"/close_trade "
                    f"{trade['trade_id']}"
                )

            return await (
                update.effective_message.reply_text(
                    "\n".join(lines)
                )
            )

        await self._prepare_close(
            update,
            rows[0],
        )

    # =========================================================
    # Close Equity Option
    # =========================================================

    async def close_option(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        if not context.args:
            return await (
                update.effective_message.reply_text(
                    "استخدم:\n"
                    "/close_option NVDA"
                )
            )

        symbol = context.args[0].upper()

        rows = [
            trade
            for trade in self._open_rows()
            if (
                trade.get("symbol") == symbol
                and str(
                    trade.get(
                        "trade_type",
                        "",
                    )
                ).startswith(
                    "EQUITY_OPTION_"
                )
            )
        ]

        if not rows:
            return await (
                update.effective_message.reply_text(
                    f"📂 لا توجد عقود أسهم "
                    f"مفتوحة على {symbol}."
                )
            )

        if len(rows) > 1:
            lines = [
                f"📄 يوجد {len(rows)} "
                f"عقد مفتوح على {symbol}:",
                "",
            ]

            for trade in rows:
                lines.extend(
                    [
                        self._contract_label(
                            trade
                        ),
                        f"Trade ID: "
                        f"{trade['trade_id']}",
                        "",
                    ]
                )

            lines.append(
                "اختر العقد باستخدام:"
            )

            for trade in rows:
                lines.append(
                    f"/close_trade "
                    f"{trade['trade_id']}"
                )

            return await (
                update.effective_message.reply_text(
                    "\n".join(lines)
                )
            )

        await self._prepare_close(
            update,
            rows[0],
        )

    # =========================================================
    # Close Index Option
    # =========================================================

    async def close_index(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        symbol = (
            context.args[0].upper()
            if context.args
            else "SPX"
        )

        rows = [
            trade
            for trade in self._open_rows()
            if (
                trade.get("symbol") == symbol
                and str(
                    trade.get(
                        "trade_type",
                        "",
                    )
                ).startswith(
                    "INDEX_OPTION_"
                )
            )
        ]

        if not rows:
            return await (
                update.effective_message.reply_text(
                    f"📂 لا توجد عقود مؤشر "
                    f"مفتوحة على {symbol}."
                )
            )

        if len(rows) > 1:
            lines = [
                f"📊 يوجد {len(rows)} "
                f"عقد مؤشر مفتوح على {symbol}:",
                "",
            ]

            for trade in rows:
                lines.extend(
                    [
                        self._contract_label(
                            trade
                        ),
                        f"Trade ID: "
                        f"{trade['trade_id']}",
                        "",
                    ]
                )

            lines.append(
                "اختر باستخدام:"
            )

            for trade in rows:
                lines.append(
                    f"/close_trade "
                    f"{trade['trade_id']}"
                )

            return await (
                update.effective_message.reply_text(
                    "\n".join(lines)
                )
            )

        await self._prepare_close(
            update,
            rows[0],
        )

    # =========================================================
    # Close by Trade ID
    # =========================================================

    async def close_trade(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        if not context.args:
            return await (
                update.effective_message.reply_text(
                    "استخدم:\n"
                    "/close_trade OPT-XXXXXXXX"
                )
            )

        trade_id = context.args[0].upper()

        trade = self._find_open_trade(
            trade_id
        )

        if not trade:
            return await (
                update.effective_message.reply_text(
                    "❌ لم يتم العثور على "
                    "Trade مفتوح بهذا ID."
                )
            )

        await self._prepare_close(
            update,
            trade,
        )

    # =========================================================
    # Confirm Close
    # =========================================================

    async def confirm_close(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        if not context.args:
            return await (
                update.effective_message.reply_text(
                    "استخدم:\n"
                    "/confirm_close TRADE_ID"
                )
            )

        user_id = update.effective_user.id
        trade_id = context.args[0].upper()

        pending = self.pending_closes.get(
            user_id
        )

        if not pending:
            return await (
                update.effective_message.reply_text(
                    "❌ لا يوجد طلب إغلاق "
                    "بانتظار التأكيد."
                )
            )

        age = (
            time.monotonic()
            - pending["created_monotonic"]
        )

        if age > 300:
            self.pending_closes.pop(
                user_id,
                None,
            )

            return await (
                update.effective_message.reply_text(
                    "⚠️ انتهت صلاحية تأكيد الإغلاق.\n"
                    "أعد طلب الإغلاق."
                )
            )

        if (
            pending["trade_id"].upper()
            != trade_id
        ):
            return await (
                update.effective_message.reply_text(
                    "❌ Trade ID لا يطابق "
                    "الصفقة المنتظرة للتأكيد."
                )
            )

        trade = self._find_open_trade(
            trade_id
        )

        if not trade:
            self.pending_closes.pop(
                user_id,
                None,
            )

            return await (
                update.effective_message.reply_text(
                    "ℹ️ الصفقة لم تعد مفتوحة."
                )
            )

        exit_price = (
            await self._latest_trade_price(
                trade
            )
        )

        if exit_price is None:
            return await (
                update.effective_message.reply_text(
                    "❌ تعذر الحصول على سعر "
                    "حالي موثوق للإغلاق ال.\n\n"
                    "لم يتم إغلاق الصفقة."
                )
            )

        entry = self._entry_reference(
            trade
        )

        pnl_pct = self._trade_pnl_pct(
            trade,
            exit_price,
        )

        closed_trade = dict(trade)

        final_result = "WIN" if pnl_pct > 0.01 else "LOSS" if pnl_pct < -0.01 else "BREAKEVEN"
        cash_usd, cash_sar = self._trade_cash_pnl(closed_trade, exit_price)
        closed_trade.update(
            {
                "status": "CLOSED",
                "final_result": final_result,
                "exit_price": exit_price,
                "last_price": exit_price,
                "pnl_pct": round(
                    pnl_pct,
                    4,
                ),
                "cash_pnl_usd": cash_usd,
                "cash_pnl_sar": cash_sar,
                "exit_reason":
                    "MANUAL_CLOSE",
                "closed_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
            }
        )

        # Remove from open repository.
        open_rows = [
            row
            for row in self.open_repo.all()
            if row.get("trade_id") != trade_id
        ]

        self.open_repo.replace(
            open_rows
        )

        # Add to permanent history.
        self.history_repo.append(
            closed_trade
        )

        deleted_count, delete_failed = await self._delete_trade_channel_messages(closed_trade)

        self.pending_closes.pop(
            user_id,
            None,
        )

        result_icon = (
            "🟢"
            if pnl_pct >= 0
            else "🔴"
        )

        cleanup_note = (
            f"🧹 تم حذف {deleted_count} رسالة مرتبطة بالصفقة من القروب."
            if delete_failed == 0
            else f"🧹 تم حذف {deleted_count} رسالة، وتعذر حذف {delete_failed} رسالة (تحقق من صلاحية Delete Messages)."
        )
        private_message = (
            f"{result_icon} تم الإغلاق اليدوي بنجاح\n\n"
            f"{self._contract_label(closed_trade)}\n"
            f"🆔 {trade_id}\n\n"
            f"💵 الدخول: ${entry:.2f} | الخروج: ${exit_price:.2f}\n"
            f"📊 العائد: {pnl_pct:+.2f}%\n"
            + (f"💰 الربح/الخسارة: {cash_usd:+.2f}$ | {cash_sar:+.2f} ريال\n" if closed_trade.get("option") else "")
            + "Exit Reason: MANUAL_CLOSE\n\n"
            + cleanup_note
        )

        await update.effective_message.reply_text(
            private_message
        )

        # Manual close is private-only by design. No new close message is
        # published to the group; the trade's existing Telegram messages were
        # removed above while history/performance remains intact.

    # =========================================================
    # Close All
    # =========================================================

    async def close_all(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        rows = self._open_rows()

        if not rows:
            return await (
                update.effective_message.reply_text(
                    "📂 لا توجد صفقات مفتوحة."
                )
            )

        user_id = update.effective_user.id

        self.pending_close_all[
            user_id
        ] = time.monotonic()

        stock_count = sum(
            1
            for trade in rows
            if str(
                trade.get(
                    "trade_type",
                    "",
                )
            ).startswith("STOCK_")
        )

        option_count = sum(
            1
            for trade in rows
            if str(
                trade.get(
                    "trade_type",
                    "",
                )
            ).startswith(
                "EQUITY_OPTION_"
            )
        )

        index_count = sum(
            1
            for trade in rows
            if str(
                trade.get(
                    "trade_type",
                    "",
                )
            ).startswith(
                "INDEX_OPTION_"
            )
        )

        await update.effective_message.reply_text(
            "⚠️ طلب إغلاق جميع الصفقات\n\n"

            f"إجمالي الصفقات المفتوحة:\n"
            f"{len(rows)}\n\n"

            f"Stocks: {stock_count}\n"
            f"Equity Options: {option_count}\n"
            f"Index Options: {index_count}\n\n"

            "لن يتم الإغلاق حتى تؤكد.\n\n"

            "للتأكيد:\n"
            "/confirm_close_all"
        )

    async def confirm_close_all(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        if not await self._require_private(update):
            return

        user_id = update.effective_user.id

        started = self.pending_close_all.get(
            user_id
        )

        if started is None:
            return await (
                update.effective_message.reply_text(
                    "❌ لا يوجد طلب Close All "
                    "بانتظار التأكيد."
                )
            )

        if (
            time.monotonic() - started
            > 300
        ):
            self.pending_close_all.pop(
                user_id,
                None,
            )

            return await (
                update.effective_message.reply_text(
                    "⚠️ انتهت صلاحية التأكيد.\n"
                    "استخدم /close_all من جديد."
                )
            )

        rows = self._open_rows()

        if not rows:
            self.pending_close_all.pop(
                user_id,
                None,
            )

            return await (
                update.effective_message.reply_text(
                    "📂 لا توجد صفقات مفتوحة."
                )
            )

        closed = []
        failed = []

        for trade in rows:
            try:
                exit_price = (
                    await self._latest_trade_price(
                        trade
                    )
                )

                if exit_price is None:
                    failed.append(
                        trade["trade_id"]
                    )
                    continue

                entry = self._entry_reference(
                    trade
                )

                pnl_pct = self._trade_pnl_pct(
                    trade,
                    exit_price,
                )

                result = dict(trade)

                final_result = "WIN" if pnl_pct > 0.01 else "LOSS" if pnl_pct < -0.01 else "BREAKEVEN"
                cash_usd, cash_sar = self._trade_cash_pnl(result, exit_price)
                result.update(
                    {
                        "status": "CLOSED",
                        "final_result": final_result,
                        "exit_price":
                            exit_price,
                        "last_price":
                            exit_price,
                        "pnl_pct":
                            round(
                                pnl_pct,
                                4,
                            ),
                        "cash_pnl_usd": cash_usd,
                        "cash_pnl_sar": cash_sar,
                        "exit_reason":
                            "MANUAL_CLOSE_ALL",
                        "closed_at":
                            datetime.now(
                                timezone.utc
                            ).isoformat(),
                    }
                )

                self.history_repo.append(
                    result
                )
                await self._delete_trade_channel_messages(result)

                closed.append(result)

            except Exception:
                failed.append(
                    trade.get(
                        "trade_id",
                        "UNKNOWN",
                    )
                )

        closed_ids = {
            trade["trade_id"]
            for trade in closed
        }

        remaining = [
            trade
            for trade in self.open_repo.all()
            if trade.get("trade_id")
            not in closed_ids
        ]

        self.open_repo.replace(
            remaining
        )

        self.pending_close_all.pop(
            user_id,
            None,
        )

        message = (
            "✅ انتهى طلب Close All\n\n"

            f"تم إغلاق:\n"
            f"{len(closed)} صفقة\n\n"

            f"تعذر إغلاق:\n"
            f"{len(failed)} صفقة\n\n"

            "Exit Reason:\n"
            "MANUAL_CLOSE_ALL"
        )

        if failed:
            message += (
                "\n\n⚠️ الصفقات التي بقيت مفتوحة:\n"
                + "\n".join(
                    f"• {trade_id}"
                    for trade_id in failed
                )
            )

        await update.effective_message.reply_text(
            message
        )

        # Close All is also private-only. Existing trade messages are deleted
        # per trade and no new group close message is posted.

    # =========================================================
    # Open Trades
    # =========================================================

    async def open_trades(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        rows = self._open_rows()

        if not rows:
            return await (
                update.effective_message.reply_text(
                    "📂 لا توجد صفقات مفتوحة."
                )
            )

        lines = [
            "📂 الصفقات المفتوحة",
            "",
            f"العدد: {len(rows)} / "
            f"{settings.max_open_trades}",
            "",
        ]

        for index, trade in enumerate(
            rows,
            start=1,
        ):
            lines.extend(
                [
                    f"{index}) "
                    f"{self._contract_label(trade)}",
                    f"🆔 "
                    f"{trade.get('trade_id', 'N/A')}",
                    "النوع: "
                    f"{self._trade_type_ar(trade.get('trade_type', ''))}",
                    f"Entry: "
                    f"{trade.get('entry_low')} – "
                    f"{trade.get('entry_high')}",
                    f"Last: "
                    f"{trade.get('last_price', 'N/A')}",
                    f"Status: "
                    f"{trade.get('status', 'OPEN')}",
                    "",
                ]
            )

        await update.effective_message.reply_text(
            "\n".join(lines)
        )

    # =========================================================
    # Status
    # =========================================================

    async def _data_sources_status_text(self) -> str:
        """Live diagnostics for configured/free market-data sources.

        A source is marked AVAILABLE only after a real fetch succeeds. Missing or
        failed sources are reported explicitly and never treated as bearish/zero.
        """
        lines = ["📡 DATA SOURCES"]

        # Alpaca: prove the connection with a real market-clock request, then try
        # one latest stock bar to confirm the market-data API itself.
        try:
            clock = await self.service.provider.market_clock()
            stamp = str(clock.get("timestamp") or "N/A")
            lines.append(f"Alpaca API: ✅ AVAILABLE | clock {stamp}")
        except Exception as exc:
            lines.append(f"Alpaca API: ⚪ UNAVAILABLE | {type(exc).__name__}")

        probe_symbol = (settings.stocks[0] if settings.stocks else "SPY")
        try:
            bars = await self.service.provider.latest_bars([probe_symbol])
            bar = (bars or {}).get(probe_symbol) or {}
            ts = bar.get("t") or bar.get("timestamp") or "N/A"
            if bar:
                lines.append(f"Alpaca Stocks ({settings.alpaca_stock_feed.upper()}): ✅ AVAILABLE | {probe_symbol} | {ts}")
            else:
                lines.append(f"Alpaca Stocks ({settings.alpaca_stock_feed.upper()}): ⚪ UNAVAILABLE | no latest bar")
        except Exception as exc:
            lines.append(f"Alpaca Stocks ({settings.alpaca_stock_feed.upper()}): ⚪ UNAVAILABLE | {type(exc).__name__}")
        lines.append(f"Alpaca Options feed: {settings.alpaca_options_feed.upper()} | configured")

        # FRED: real calendar + Treasury calls.
        try:
            cal, tsy = await asyncio.gather(
                self.service.economic_context.fred_release_calendar(3),
                self.service.economic_context.fred_treasury_snapshot(),
            )
            if cal.get("status") == "AVAILABLE":
                lines.append(f"FRED Calendar: ✅ AVAILABLE | events {len(cal.get('events') or [])}")
            else:
                reason = cal.get("reason") or "no data"
                state = "NOT CONFIGURED" if reason == "API_KEY_NOT_CONFIGURED" else "UNAVAILABLE"
                lines.append(f"FRED Calendar: ⚪ {state} | {reason}")
            if tsy.get("status") == "AVAILABLE":
                parts = []
                for label in ("US2Y", "US10Y_FRED", "US30Y"):
                    row = (tsy.get("series") or {}).get(label) or {}
                    val = row.get("value")
                    if val is not None:
                        display = "US10Y" if label == "US10Y_FRED" else label
                        parts.append(f"{display} {float(val):.3f}%")
                lines.append("FRED Treasuries: ✅ AVAILABLE | " + (" | ".join(parts) if parts else "no usable series"))
            else:
                lines.append(f"FRED Treasuries: ⚪ UNAVAILABLE | {tsy.get('reason','no data')}")
        except Exception as exc:
            lines.append(f"FRED: ⚪ UNAVAILABLE | {type(exc).__name__}")

        # Alpha Vantage: use the cached 3-month earnings calendar through a real
        # lookup. This is intentionally one probe only to protect free quotas.
        try:
            av = await self.service.economic_context.alpha_vantage_earnings(probe_symbol)
            if av.get("status") == "AVAILABLE":
                nxt = av.get("next_earnings")
                when = (nxt or {}).get("report_date") if isinstance(nxt, dict) else None
                lines.append(f"Alpha Vantage Earnings: ✅ AVAILABLE | {probe_symbol} | next {when or 'none in window'}")
            else:
                reason = av.get("reason") or "no data"
                state = "NOT CONFIGURED" if reason == "API_KEY_NOT_CONFIGURED" else "UNAVAILABLE"
                lines.append(f"Alpha Vantage Earnings: ⚪ {state} | {reason}")
        except Exception as exc:
            lines.append(f"Alpha Vantage Earnings: ⚪ UNAVAILABLE | {type(exc).__name__}")

        # Free/best-effort context used by Waseem V2.
        for label, ticker in (("ES", "ES=F"), ("NQ", "NQ=F"), ("YM", "YM=F"), ("RTY", "RTY=F"), ("VIX", "^VIX"), ("DXY", "DX-Y.NYB")):
            try:
                row = await self.service.waseem_v2_context._series(label, ticker)
                state = str(row.get("status") or "UNAVAILABLE")
                if state in {"AVAILABLE", "DELAYED", "STALE"}:
                    age = row.get("age_minutes")
                    age_txt = f" | age {age}m" if age is not None else ""
                    lines.append(f"{label}: {'✅' if state == 'AVAILABLE' else '🟡'} {state} | {row.get('value','N/A')}{age_txt}")
                else:
                    lines.append(f"{label}: ⚪ UNAVAILABLE")
            except Exception as exc:
                lines.append(f"{label}: ⚪ UNAVAILABLE | {type(exc).__name__}")

        v3_session = self.service.spx_option_session_status()
        try:
            gth = await self.service.spx_gth_data_diagnostics()
            state = str(gth.get("option_data_status") or "UNAVAILABLE")
            icon = "✅" if state == "AVAILABLE" else ("🟡" if state in {"DELAYED", "PARTIAL", "STALE"} else "⚪")
            lines.extend([
                f"SPXW GTH Data: {icon} {state} | feed={gth.get('options_feed','N/A')} | session={gth.get('session','N/A')} | trade_date={gth.get('trade_date','N/A')}",
                f"SPXW Quote Status: {gth.get('latest_quote_status','UNAVAILABLE')} | Trade Status: {gth.get('latest_trade_status','UNAVAILABLE')}",
                f"SPXW Session ET: {gth.get('session_start_et','N/A')} -> {gth.get('session_end_et','N/A')}",
                f"SPXW Session KSA: {gth.get('session_start_ksa','N/A')} -> {gth.get('session_end_ksa','N/A')}",
                f"SPXW GTH Snapshots: {gth.get('snapshot_count',0)} | quotes={gth.get('quote_count',0)} | trades={gth.get('trade_count',0)} | source={gth.get('chain_source','unavailable')}",
                f"SPXW Latest Quote: {gth.get('latest_quote_contract','N/A')} | {gth.get('latest_quote_time','N/A')} | age {gth.get('latest_quote_age_minutes','N/A')}m | bid {gth.get('latest_quote_bid','N/A')} / ask {gth.get('latest_quote_ask','N/A')}",
                f"SPXW Latest Trade: {gth.get('latest_trade_contract','N/A')} | {gth.get('latest_trade_time','N/A')} | age {gth.get('latest_trade_age_minutes','N/A')}m | price {gth.get('latest_trade_price','N/A')}",
                f"SPX Cash Last Point: {gth.get('cash_last_point_price','N/A')} | {gth.get('cash_last_point_time','N/A')} | {gth.get('cash_last_point_session','UNAVAILABLE')} | age {gth.get('cash_last_point_age_minutes','N/A')}m",
                f"GTH Diagnostics Checked: {gth.get('checked_at','N/A')} | errors={','.join(gth.get('errors') or []) or 'NONE'}",
            ])
        except Exception as exc:
            lines.append(f"SPXW GTH Data: ⚪ UNAVAILABLE | diagnostics {type(exc).__name__}")
        lines.extend([
            f"Waseem V3 SPX Session: {'✅ OPEN' if v3_session.get('open') else '⚪ CLOSED'} | {v3_session.get('session')} | Cash SPX={v3_session.get('cash_spx_state')}",
            "Waseem V3 Entry Engine: ✅ AVAILABLE | Equity + SPX",
            "Waseem V4 Engine: ✅ AVAILABLE | V2 Setup + V3 Entry + Liquidity/Pre-Move | Equity + SPX",
            "Waseem V5 Engine: ✅ AVAILABLE | V4 + observable Order Flow/Execution | Equity + SPX",
            "Waseem V6 Engine: ✅ AVAILABLE | delayed/session-aware + MTF + ICT/Fibonacci + anti-late-entry | Equity + SPX",
            "NYSE TICK: ⚪ UNAVAILABLE",
            "Institutional GEX: ⚪ UNAVAILABLE",
            "Institutional Options Flow: ⚪ UNAVAILABLE",
            "Full Level 2 / DOM: ⚪ UNAVAILABLE",
        ])
        return "\n".join(lines)

    async def status(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        try:
            is_open, stamp = await self.service.market_is_open()
        except Exception:
            is_open = False
            stamp = "N/A"

        data_text = await self._data_sources_status_text()
        monitor_keys = (
            "stock",
            "option", "option:confirmed", "option:waseem", "option:waseem_v2", "option:waseem_v3", "option:waseem_v4", "option:waseem_v5", "option:waseem_v6",
            "index:v20", "index:core", "index:confirmed", "index:waseem", "index:waseem_v2", "index:waseem_v3", "index:waseem_v4", "index:waseem_v5", "index:waseem_v6",
        )
        monitor_lines = ["🔍 MONITORS"]
        for key in monitor_keys:
            monitor_lines.append(
                f"{self._monitor_label(key)}: {'RUNNING ✅' if self._monitor_running(key) else 'STOPPED'}"
            )

        text = (
            "🤖 RUNNING ✅\n\n"
            f"Paper Mode: {settings.paper_mode}\n"
            f"Live Trading: {settings.live_trading}\n"
            f"Paused: {self._paused()}\n"
            f"US Market Open: {is_open}\n"
            f"Market Clock: {stamp}\n"
            f"Stocks Universe: {len(settings.stocks)}\n"
            f"Index: {','.join(settings.indices)}\n"
            f"Manual Publish: {settings.require_manual_publish}\n"
            f"Max Per Scan: {settings.max_signals_per_scan}\n"
            f"Max Open Trades: {settings.max_open_trades}\n"
            f"SPX 0DTE: {'ON' if settings.enable_spx_0dte else 'OFF'}\n\n"
            + data_text + "\n\n"
            + "\n".join(monitor_lines) + "\n\n"
            + f"Channel ID: {'SET' if settings.telegram_channel_chat_id else 'PENDING'}"
        )
        await update.effective_message.reply_text(text)

    # =========================================================
    # Risk
    # =========================================================

    async def risk(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        rows = self._open_rows()

        total = sum(
            float(
                trade.get(
                    "risk_pct",
                    0,
                )
                or 0
            )
            for trade in rows
        )

        await update.effective_message.reply_text(
            "🛡️ حالة المخاطر\n\n"

            f"Open Trades:\n"
            f"{len(rows)} / "
            f"{settings.max_open_trades}\n\n"

            f"Max Risk / Trade:\n"
            f"{settings.max_risk_per_trade * 100:.2f}%\n\n"

            f"Max Total Open Risk:\n"
            f"{settings.max_total_open_risk * 100:.2f}%\n\n"

            f"Current Open Risk:\n"
            f"{total * 100:.2f}%\n\n"

            f"MIN R/R:\n"
            f"1 : {settings.min_rr}\n\n"

            "📌 يسمح النظام بسهم وعقد Option "
            "على نفس الأصل إذا لم يكونا "
            "Trade مكررًا وكان Risk Engine يسمح."
        )

    # =========================================================
    # Category Reports Helpers
    # =========================================================

    @staticmethod
    def _report_category(trade: dict) -> str:
        trade_type = str(trade.get("trade_type", "")).upper()
        if trade_type.startswith("STOCK_"):
            return "stock"
        if trade_type.startswith("EQUITY_OPTION_"):
            return "equity_option"
        if trade_type.startswith("INDEX_OPTION_"):
            return "index_option"
        return "other"

    @staticmethod
    def _report_category_label(category: str) -> str:
        return {
            "stock": "Stocks",
            "equity_option": "Equity Options",
            "index_option": "Index Options",
        }.get(category, category)

    def _filter_report_rows(self, rows, category: str) -> list[dict]:
        return [
            trade
            for trade in rows
            if self._report_category(trade) == category
        ]

    @staticmethod
    def _closed_report_rows(rows: list[dict]) -> list[dict]:
        closed_states = {"WIN", "LOSS", "BREAKEVEN", "CLOSED"}
        return [
            trade
            for trade in rows
            if str(trade.get("status", "")).upper() in closed_states
        ]

    def _option_cash_totals(self, rows: list[dict]) -> tuple[float, float]:
        usd = 0.0
        sar = 0.0
        for trade in rows:
            try:
                cash_usd = float(trade.get("cash_pnl_usd") or 0.0)
            except (TypeError, ValueError):
                cash_usd = 0.0
            try:
                cash_sar = float(trade.get("cash_pnl_sar") or 0.0)
            except (TypeError, ValueError):
                cash_sar = cash_usd * float(settings.usd_sar_rate)
            usd += cash_usd
            sar += cash_sar
        return round(usd, 2), round(sar, 2)

    async def _show_category_performance(self, query, category: str):
        if category not in {"stock", "equity_option", "index_option"}:
            return await self._edit_menu(
                query,
                "Invalid report category.",
                self._performance_menu_markup(),
            )

        rows = self._filter_report_rows(self.history_repo.all(), category)
        open_rows = self._filter_report_rows(self.open_repo.all(), category)
        result = performance(rows, open_rows)
        label = self._report_category_label(category)

        lines = [
            f"📈 {label} Performance",
            "",
            f"📊 Activity: {result['activity']}",
            f"✅ Successful: {result['wins']}",
            f"🔴 Losing: {result['losses']}",
            f"⏳ Pending: {result['pending']}",
            f"🎯 Success Rate: {result['win_rate']}%",
            "",
            f"📁 Actual Closed Trades: {result['final_closed_trades']}",
            f"🟢 Actual Closed Profit: {result['final_wins']}",
            f"🔴 Actual Closed Loss: {result['final_losses']}",
            f"⚪ Actual Breakeven: {result['final_breakeven']}",
            f"📊 Realized Profit Factor: {result['profit_factor']}",
            f"💹 Realized Net P&L: {result['net_pnl_pct']:+.2f}%",
            f"📉 Realized Max Drawdown: {result['max_drawdown_pct']:.2f}%",
        ]

        if category != "stock":
            closed = self._closed_report_rows(rows)
            usd, sar = self._option_cash_totals(closed)
            lines.extend([
                f"💵 Net Cash P&L: {usd:+.2f}$",
                f"🇸🇦 Net Cash P&L: {sar:+.2f} SAR",
            ])

        return await self._edit_menu(
            query,
            "\n".join(lines),
            self._performance_menu_markup(),
        )

    @staticmethod
    def _report_title_ar(category: str, period: str, horizon: str | None = None) -> str:
        base = {
            "stock": "تقرير الأسهم",
            "equity_option": "تقرير عقود الأسهم",
            "index_option": "تقرير عقود المؤشر SPX",
            "options_all": "تقرير جميع العقود",
        }.get(category, "تقرير الأداء")
        horizon_label = {
            "daily": " — 0DTE",
            "weekly": " — 1–7 DTE",
            "monthly": " — 8–35 DTE",
        }.get(str(horizon or "all").lower(), "")
        return f"{base} {'اليومي' if period == 'daily' else 'الأسبوعي'}{horizon_label}"

    @staticmethod
    def _report_date_ar(report: dict) -> str:
        try:
            d = datetime.fromisoformat(str(report.get("report_date_ny"))).date()
        except Exception:
            d = datetime.now(ZoneInfo("America/New_York")).date()
        days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
        months = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
        if report.get("period") == "daily":
            return f"{days[d.weekday()]} {d.day} {months[d.month - 1]}"
        try:
            start = datetime.fromisoformat(str(report.get("period_start")).replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()
            if start.month == d.month:
                return f"{start.day}–{d.day} {months[d.month - 1]}"
            return f"{start.day} {months[start.month - 1]} – {d.day} {months[d.month - 1]}"
        except Exception:
            return f"الأسبوع المنتهي {d.day} {months[d.month - 1]}"

    @classmethod
    def _category_report_caption(cls, report: dict) -> str:
        category = str(report.get("category"))
        period = str(report.get("period"))
        financial = report.get("financial") or {}
        summary = report.get("summary") or {}
        rule = report.get("success_rule") or {}
        threshold = float(rule.get("threshold", 0) or 0)
        period_word = "اليوم" if period == "daily" else "الأسبوع"
        lines = [
            f"✨ نتائج {settings.watermark_name} ✨",
            f"📊 {cls._report_title_ar(category, period, report.get('horizon'))}",
            f"▫️ {cls._report_date_ar(report)} ▫️",
            "",
        ]
        if category == "stock":
            lines.extend([
                f"✅ إجمالي العائد الرابح {period_word}: +{float(financial.get('gross_profit',0)):.2f}%",
                f"❌ إجمالي العائد الخاسر {period_word}: -{float(financial.get('gross_loss',0)):.2f}%",
                f"📈 صافي العائد: {float(financial.get('net',0)):+.2f}%",
            ])
            rule_text = "حسب الأهداف TP1/TP2/TP3"
        else:
            net = float(financial.get("net", 0) or 0)
            lines.extend([
                f"✅ أرباح {period_word}: {float(financial.get('gross_profit',0)):,.2f} $",
                f"❌ خسائر {period_word}: {float(financial.get('gross_loss',0)):,.2f} $",
                f"📈 صافي الربح: {net:,.2f} $ ({float(financial.get('net_sar',0)):,.2f} ﷼)",
            ])
            rule_text = "حسب إعداد كل فئة" if category == "options_all" else ("OFF" if threshold <= 0 else f"+${threshold:,.2f}")
            if category == "options_all":
                breakdown = report.get("breakdown") or {}
                labels = (("daily", "0DTE"), ("weekly", "1–7 DTE"), ("monthly", "8–35 DTE"))
                lines.append("")
                lines.append("📂 التقسيم حسب مدة العقد:")
                for key, label in labels:
                    row = breakdown.get(key) or {}
                    lines.append(
                        f"• {label}: {row.get('trades',0)} صفقة | "
                        f"W {row.get('wins',0)} / L {row.get('losses',0)} / P {row.get('pending',0)} | "
                        f"Net {float(row.get('net',0) or 0):+.2f}$"
                    )
        lines.extend([
            "",
            f"🎯 معيار نجاح الإشارة: {rule_text}",
            f"✅ إشارات وصلت للمعيار: {summary.get('successful_signals', 0)}",
            f"🟢 الصفقات الناجحة: {summary.get('wins', 0)}",
            f"🔴 الصفقات الخاسرة: {summary.get('losses', 0)}",
            f"⏳ قيد الانتظار: {summary.get('pending', 0)}",
            f"📊 نسبة النجاح: {summary.get('win_rate', 0)}%",
            "",
            "📌 العقود: النجاح عند بلوغ الحد المحدد، والخسارة لا تُحسم إلا بعد انتهاء جلسة نيويورك إذا لم يصل العقد للحد. الأسهم: النجاح حسب الأهداف.",
        ])
        return "\n".join(lines)

    async def _send_category_report(
        self,
        update: Update,
        query,
        category: str,
        period: str,
        horizon: str | None = "all",
    ):
        if category not in {"stock", "equity_option", "index_option", "options_all"}:
            markup = self._daily_menu_markup() if period == "daily" else self._weekly_menu_markup()
            return await self._edit_menu(query, "Invalid report category.", markup)
        if horizon not in {None, "all", "daily", "weekly", "monthly"}:
            markup = self._daily_menu_markup() if period == "daily" else self._weekly_menu_markup()
            return await self._edit_menu(query, "Invalid report horizon.", markup)

        markup = self._daily_menu_markup() if period == "daily" else self._weekly_menu_markup()
        label = "All Options" if category == "options_all" else self._report_category_label(category)
        horizon_label = {
            "daily": "0DTE",
            "weekly": "1–7 DTE",
            "monthly": "8–35 DTE",
            "all": "All DTE",
            None: "All DTE",
        }[horizon]
        await self._edit_menu(
            query,
            f"📊 Preparing {label} · {horizon_label} · {'Daily' if period == 'daily' else 'Weekly'} Report...",
            markup,
        )
        report = category_period_report(
            history=self.history_repo.all(),
            open_trades=self.open_repo.all(),
            category=category,
            period=period,
            horizon=horizon,
        )
        safe_label = category.upper()
        safe_horizon = str(horizon or "all").upper()
        image_path = os.path.join(
            tempfile.gettempdir(),
            f"ALLUQMANU_USA_TD_{safe_label}_{safe_horizon}_{period.upper()}_REPORT.png",
        )
        try:
            weekly_performance_card(report, image_path)
            with open(image_path, "rb") as image_file:
                await update.effective_message.reply_photo(
                    photo=image_file,
                    caption=self._category_report_caption(report),
                )
            return await self._edit_menu(
                query,
                f"✅ {self._report_title_ar(category, period, horizon)} تم إرساله في الخاص.",
                markup,
            )
        except Exception as exc:
            return await self._edit_menu(
                query,
                f"❌ Could not create report. Error: {type(exc).__name__}",
                markup,
            )
        finally:
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except OSError:
                pass

    async def _send_category_daily(self, update: Update, query, category: str, horizon: str | None = "all"):
        return await self._send_category_report(update, query, category, "daily", horizon=horizon)

    async def _send_category_weekly(self, update: Update, query, category: str, horizon: str | None = "all"):
        return await self._send_category_report(update, query, category, "weekly", horizon=horizon)

    # =========================================================
    # Performance
    # =========================================================

    async def performance(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        result = performance(
            self.history_repo.all(),
            self.open_repo.all(),
        )

        await update.effective_message.reply_text(
            "📊 الأداء\n\n"

            f"الإشارات الناجحة إحصائيًا: "
            f"{result['successful_signals']}\n"

            f"الناجحة وما زالت مفتوحة: "
            f"{result['successful_open']}\n"

            f"نجحت ثم أغلقت بخسارة: "
            f"{result['final_losses_after_success']}\n\n"

            f"الصفقات المحسومة في الأداء: "
            f"{result['trades']}\n"

            f"الصفقات الناجحة: "
            f"{result['wins']}\n"

            f"الصفقات الخاسرة: "
            f"{result['losses']}\n"

            f"قيد الانتظار: "
            f"{result['pending']}\n"

            f"نسبة النجاح: "
            f"{result['win_rate']}%\n"

            f"Profit Factor: "
            f"{result['profit_factor']}\n"

            f"Net P&L: "
            f"{result['net_pnl_pct']}%"
        )

    async def report_cmd(
        self,
        update: Update,
        context,
    ):
        """Manual private comprehensive weekly report.

        The options report includes every option trade whose confirmed
        `entered_at` falls inside the current New York trading week, regardless
        of whether it is 0DTE, 1–7 DTE or 8–35 DTE. Older still-open positions
        are intentionally excluded from the new week's cohort.
        """
        if not self.allowed(update):
            return await self._deny(update)
        if not await self._require_private(update):
            return

        await update.effective_message.reply_text(
            "📊 جاري تجهيز التقرير الأسبوعي الشامل في الخاص..."
        )

        reports = [
            ("options_all", "all"),
            ("stock", "all"),
        ]
        sent = 0
        for category, horizon in reports:
            report = category_period_report(
                history=self.history_repo.all(),
                open_trades=self.open_repo.all(),
                category=category,
                period="weekly",
                horizon=horizon,
            )
            image_path = os.path.join(
                tempfile.gettempdir(),
                f"ALLUQMANU_USA_TD_{category.upper()}_MANUAL_WEEKLY.png",
            )
            try:
                weekly_performance_card(report, image_path)
                with open(image_path, "rb") as image_file:
                    await update.effective_message.reply_photo(
                        photo=image_file,
                        caption=self._category_report_caption(report),
                    )
                sent += 1
            except Exception as exc:
                await update.effective_message.reply_text(
                    f"❌ تعذر إنشاء {self._report_title_ar(category, 'weekly', horizon)}: {type(exc).__name__}"
                )
            finally:
                try:
                    os.remove(image_path)
                except OSError:
                    pass

        if sent:
            await update.effective_message.reply_text(
                f"✅ تم إرسال {sent} تقرير/تقارير أسبوعية في الخاص."
            )

    # =========================================================
    # Settings
    # =========================================================

    async def settings_cmd(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        contract_rules = contract_search_rules.all()

        def _price_text(category: str, horizon: str) -> str:
            value = float((contract_rules.get(category) or {}).get(horizon, 0) or 0)
            return "Unlimited" if value <= 0 else f"≤ ${value:,.2f}"

        await update.effective_message.reply_text(
            "⚙️ الإعدادات\n\n"

            f"Stock Feed: "
            f"{settings.alpaca_stock_feed}\n"

            f"Options Feed: "
            f"{settings.alpaca_options_feed}\n\n"

            f"Min Score: "
            f"{settings.ready_score_floor} (effective)\n"
            f"Dynamic Gate: Healthy/Normal {settings.ready_score_floor:.0f} | "
            f"Caution {settings.dynamic_caution_min_score:.0f} | "
            f"Range {settings.dynamic_range_min_score:.0f} | "
            f"High Vol {settings.dynamic_high_vol_min_score:.0f}\n"
            "Low liquidity + unclear direction: NO TRADE\n"

            f"Min R/R: "
            f"{settings.min_rr}\n\n"

            f"Default Scan Count: "
            f"{settings.default_signals_per_scan}\n"

            f"Max Scan Count: "
            f"{settings.max_signals_per_scan}\n\n"

            f"Candidate TTL: {settings.candidate_ttl_seconds // 60} minutes\n"
            f"Monitor Max Opportunities: {settings.monitor_max_opportunities}\n\n"
            "Contract Search Price:\n"
            f"Equity 0DTE: {_price_text('equity_option', 'daily')} | "
            f"1–7: {_price_text('equity_option', 'weekly')} | "
            f"8–35: {_price_text('equity_option', 'monthly')}\n"
            f"SPX 0DTE: {_price_text('index_option', 'daily')} | "
            f"1–7: {_price_text('index_option', 'weekly')} | "
            f"8–35: {_price_text('index_option', 'monthly')}\n\n"

            f"Watermark: "
            f"{settings.watermark_name}\n"

            f"Option Card: "
            f"{settings.option_card_orientation}\n\n"

            f"SPX 0DTE: {'ON' if settings.enable_spx_0dte else 'OFF'}"
        )

    # =========================================================
    # Pause / Resume
    # =========================================================

    async def pause(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        self._set_paused(True)

        await update.effective_message.reply_text(
            "⏸️ تم إيقاف إنشاء الإشارات اليدوية.\n"
            "متابعة الصفقات المفتوحة تبقى فعالة."
        )

    async def resume(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        self._set_paused(False)

        await update.effective_message.reply_text(
            "▶️ تم استئناف البحث اليدوي "
            "عن الإشارات."
        )

    # =========================================================
    # Market
    # =========================================================

    async def market(
        self,
        update: Update,
        context,
    ):
        if not self.allowed(update):
            return await self._deny(update)

        from app.market.regime import (
            MarketRegimeEngine,
        )

        regime = await MarketRegimeEngine(
            self.service.provider
        ).get()

        is_open, stamp = (
            await self.service.market_is_open()
        )

        await update.effective_message.reply_text(
            "🌎 حالة السوق الأمريكي\n\n"

            f"Market Regime:\n"
            f"{regime}\n\n"

            f"US Market Open:\n"
            f"{is_open}\n\n"

            "المرجع الأساسي:\n"
            "SPY / IEX"
        )

# Backward-compatible name retained for older tests/integrations.
TelegramBots = TelegramHub
