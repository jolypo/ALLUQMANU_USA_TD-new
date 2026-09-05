from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.market.quality import freshness_info
from app.reports.performance import category_period_report
from app.reports.profit_card import profit_update_card
from app.reports.weekly_card import weekly_performance_card
from app.runtime_settings import success_rules, profit_alert_rules
from app.strategies.engine import StrategyEngine
from app.telegram.message_templates import success_message, entry_message


class TradeMonitor:
    """Monitoring only. Never creates a new signal."""

    def __init__(
        self,
        open_repo,
        history_repo,
        state_repo,
        provider,
        signal_bot,
        profit_bot,
        report_bot,
        channel_id,
        interval: int = 60,
        external_profit_watcher: bool = False,
    ):
        self.open_repo = open_repo
        self.history_repo = history_repo
        self.state_repo = state_repo
        self.provider = provider
        self.signal_bot = signal_bot
        self.profit_bot = profit_bot
        self.report_bot = report_bot
        self.channel_id = channel_id
        self.interval = interval
        self.external_profit_watcher = bool(external_profit_watcher)
        self._task = None
        self._last_daily = None
        self._last_weekly = None

    async def _send(self, bot, text: str, chat_id=None, reply_to_message_id=None, parse_mode=None):
        target = chat_id if chat_id is not None else self.channel_id
        if not target:
            return None
        try:
            return await bot.send_message(
                chat_id=target,
                text=text,
                reply_to_message_id=reply_to_message_id,
                allow_sending_without_reply=True,
                parse_mode=parse_mode,
            )
        except Exception as exc:
            print(f"[monitor-send] {type(exc).__name__}: {exc}")
            return None

    async def _send_photo(self, bot, path: str, caption=None, chat_id=None, reply_to_message_id=None, parse_mode=None):
        target = chat_id if chat_id is not None else self.channel_id
        if not target:
            return None
        try:
            with open(path, "rb") as f:
                return await bot.send_photo(
                    chat_id=target,
                    photo=f,
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                    allow_sending_without_reply=True,
                    parse_mode=parse_mode,
                )
        except Exception as exc:
            print(f"[monitor-photo] {type(exc).__name__}: {exc}")
            return None

    @staticmethod
    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _entry(self, trade: dict) -> float:
        filled = self._f(trade.get("filled_entry_price"), 0.0)
        if filled > 0:
            return filled
        lo = self._f(trade.get("entry_low"), 0.0)
        hi = self._f(trade.get("entry_high"), 0.0)
        return (lo + hi) / 2 if lo > 0 and hi > 0 else max(lo, hi, 0.0)


    def _conservative_fill(self, trade: dict) -> float:
        """Use the less favorable edge of the published entry zone."""
        lo = self._f(trade.get("entry_low"), 0.0)
        hi = self._f(trade.get("entry_high"), 0.0)
        lo, hi = min(lo, hi), max(lo, hi)
        # Bought options (CALL or PUT) are long premium positions.
        if trade.get("option"):
            return hi
        return hi if self._long(trade) else lo

    async def _entry_touched(self, trade: dict, price: float, previous: float | None) -> bool:
        lo = self._f(trade.get("entry_low"), 0.0)
        hi = self._f(trade.get("entry_high"), 0.0)
        lo, hi = min(lo, hi), max(lo, hi)
        if lo <= price <= hi:
            return True

        # A stored reference from publication/earlier monitoring can prove that
        # price crossed the zone between two samples.
        reference = previous
        if reference is None:
            reference = self._f(trade.get("entry_reference_price"), 0.0) or None
        if reference is not None:
            if lo <= reference <= hi:
                return True
            if (reference < lo and price > hi) or (reference > hi and price < lo):
                return True

        # High/low bars catch a brief touch that polling may otherwise miss.
        start = trade.get("entry_check_from") or trade.get("published_at")
        if start and hasattr(self.provider, "entry_price_range_since"):
            option_symbol = (trade.get("option") or {}).get("symbol") if trade.get("option") else None
            observed = await self.provider.entry_price_range_since(
                str(trade.get("symbol", "")), str(start), option_symbol
            )
            trade["entry_check_from"] = datetime.now(timezone.utc).isoformat()
            if observed:
                observed_low, observed_high = observed
                if observed_low <= hi and observed_high >= lo:
                    return True
        return False

    def _long(self, trade: dict) -> bool:
        if trade.get("option"):
            return True
        return str(trade.get("direction", "LONG")).upper() != "SHORT"

    def _pnl_pct(self, trade: dict, price: float) -> float:
        entry = self._entry(trade)
        if entry <= 0:
            return 0.0
        if trade.get("option"):
            diff = price - entry
        else:
            diff = price - entry if self._long(trade) else entry - price
        return diff / entry * 100

    def _cash(self, trade: dict, price: float) -> tuple[float, float]:
        if not trade.get("option"):
            return 0.0, 0.0
        entry = self._entry(trade)
        qty = max(1, int(self._f(trade.get("contracts", 1), 1)))
        # Equity and index options in this project are long-premium positions,
        # including PUT contracts. Underlying direction is stored separately.
        diff = price - entry
        usd = diff * settings.option_multiplier * qty
        return usd, usd * settings.usd_sar_rate

    def _profit_alert_step_index(self, trade: dict, price: float) -> int:
        """Return the highest configured profit increment reached above entry.

        Example with entry=7.00 and step=.10: 7.09 -> 0, 7.10 -> 1,
        7.21 -> 2. Anchoring to entry prevents duplicate alerts after dips.
        """
        entry = self._entry(trade)
        step = float(profit_alert_rules.get_step())
        if entry <= 0 or step <= 0 or price <= entry:
            return 0
        return max(0, int(((float(price) - entry) + 1e-9) // step))

    def _should_send_profit_alert(self, trade: dict, price: float) -> bool:
        idx = self._profit_alert_step_index(trade, price)
        if idx <= 0:
            return False
        last_idx = int(self._f(trade.get("profit_alert_step_index"), 0.0))
        return idx > last_idx

    @staticmethod
    def _final_result_from_pnl(pnl: float) -> str:
        if pnl > 0.01:
            return "WIN"
        if pnl < -0.01:
            return "LOSS"
        return "BREAKEVEN"

    def _label(self, trade: dict) -> str:
        option = trade.get("option") or {}
        typ = str(option.get("type") or option.get("option_type") or "OPTION").upper()
        if typ == "C":
            typ = "CALL"
        if typ == "P":
            typ = "PUT"
        return f"{trade.get('symbol', '')} {option.get('strike', '')} {typ}".strip()

    @staticmethod
    def _reply_id(trade: dict):
        try:
            return int(trade.get("channel_message_id")) if trade.get("channel_message_id") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _record_message(trade: dict, bot_name: str, message) -> None:
        message_id = getattr(message, "message_id", None) if message is not None else None
        if not message_id:
            return
        refs = list(trade.get("telegram_message_refs") or [])
        ref = {"bot": bot_name, "message_id": int(message_id)}
        if ref not in refs:
            refs.append(ref)
        trade["telegram_message_refs"] = refs

    async def _momentum_state(self, trade: dict) -> tuple[str, str, str]:
        """Evaluate momentum on the underlying, not the option premium alone."""
        try:
            df = await self.provider.bars(
                str(trade.get("symbol")),
                settings.intraday_timeframe,
                min(12, settings.intraday_lookback_days),
            )
            if len(df) < 40:
                raise ValueError("insufficient bars")
            analysis = StrategyEngine().analyze(df)
            momentum = float(analysis["scores"].get("Momentum", 50))
            trend = float(analysis["scores"].get("Trend", 50))
            desired = (trade.get("option") or {}).get("underlying_direction") or trade.get("direction", "LONG")
            aligned = analysis.get("direction") == desired
            if aligned and momentum >= 70 and trend >= 65:
                return "🟢", "قوي", "استمرار مع حماية الربح"
            if (not aligned and analysis.get("direction") in {"LONG", "SHORT"}) or momentum <= 42:
                return "🔴", "ضعيف أو انعكاس", "يفضل الخروج من العقد"
            return "🟡", "يتباطأ", "تأمين جزء من الربح / رفع الوقف"
        except Exception:
            return "🟡", "يتباطأ", "تأمين جزء من الربح / رفع الوقف"

    async def _profit_update(self, trade: dict, previous: float, price: float):
        # Never send a profit/price-rise alert while the option is still below
        # its confirmed entry. A higher tick versus the previous quote is not
        # a profit if the position remains negative.
        entry = self._entry(trade)
        if entry <= 0 or price <= entry:
            return False
        usd, sar = self._cash(trade, price)
        pnl = self._pnl_pct(trade, price)
        previous = float(previous if previous is not None else entry)
        observed_utc = datetime.now(timezone.utc)
        observed_sa = observed_utc.astimezone(ZoneInfo("Asia/Riyadh"))
        observed_ny = observed_utc.astimezone(ZoneInfo("America/New_York"))
        path = os.path.join(tempfile.gettempdir(), f"profit_{trade.get('trade_id', 'trade')}.png")
        profit_update_card(trade, usd, sar, price, path)
        # U+200F (RLM) before every line keeps Telegram aligned right even
        # when a line contains Latin symbols, prices and percentages.
        rlm = "\u200f"
        caption = (
            f"{rlm}📈 تحديث أرباح\n"
            f"{rlm}📄 العقد: {self._label(trade)}\n"
            f"{rlm}💵 الدخول: ${entry:.2f} | السعر الحالي: ${price:.2f}\n"
            f"{rlm}📊 النسبة: {pnl:+.2f}%\n"
            f"{rlm}💰 ربح بالدولار: {usd:+.2f}$\n"
            f"{rlm}🇸🇦 ربح بالريال السعودي: {sar:+.2f} ريال\n"
            f"{rlm}🕒 السعودية: {observed_sa.strftime('%I:%M:%S %p')}\n"
            f"{rlm}🕒 نيويورك: {observed_ny.strftime('%I:%M:%S %p')}\n"
            f"{rlm}🆔 الصفقة: {trade.get('trade_id', '')}"
        )
        try:
            sent = await self._send_photo(
                self.profit_bot,
                path,
                caption,
                reply_to_message_id=self._reply_id(trade),
            )
            self._record_message(trade, "profit", sent)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        trade["profit_alert_sent"] = True
        trade["profit_alert_last_at"] = datetime.now(timezone.utc).isoformat()
        trade["profit_alert_last_usd"] = round(usd, 2)
        trade["profit_alert_step_index"] = self._profit_alert_step_index(trade, price)
        trade["max_profit_usd"] = round(
            max(self._f(trade.get("max_profit_usd"), 0.0), usd),
            2,
        )
        return True

    @staticmethod
    def _success_category(trade: dict) -> str:
        tt = str(trade.get("trade_type", "")).upper()
        if tt.startswith("STOCK_"):
            return "stock"
        if tt.startswith("EQUITY_OPTION_"):
            return "equity_option"
        if tt.startswith("INDEX_OPTION_"):
            return "index_option"
        return "other"

    async def _mark_success_if_reached(self, trade: dict, price: float) -> bool:
        """Persist statistical success once, without changing final trade status."""
        if str(trade.get("performance_result", "")).upper() == "LOSS":
            return False
        if trade.get("success_reached") or trade.get("success_100_reached"):
            return False

        category = self._success_category(trade)
        if category not in {"equity_option", "index_option"}:
            return False
        rule = success_rules.get(category)
        threshold = self._f(rule.get("threshold"), 0.0)
        if threshold <= 0:
            return False

        usd, sar = self._cash(trade, price)
        value = usd
        unit = "USD"

        if value < threshold:
            return False

        now = datetime.now(timezone.utc).isoformat()
        trade["success_reached"] = True
        trade["success_reached_at"] = now
        trade["success_threshold_value"] = round(threshold, 4)
        trade["success_threshold_unit"] = unit
        trade["success_value_at_hit"] = round(value, 4)
        trade["performance_result"] = "WIN"
        trade["performance_finalized_at"] = now
        trade["performance_rule"] = "OPTION_CASH_THRESHOLD"

        if category in {"equity_option", "index_option"}:
            icon, state, advice = await self._momentum_state(trade)
            path = os.path.join(
                tempfile.gettempdir(),
                f"milestone_{trade.get('trade_id', 'trade')}.png",
            )
            profit_update_card(trade, usd, sar, price, path)
            msg = success_message(
                trade, self._entry(trade), price, threshold, usd, sar, icon, state, advice
            )
            try:
                sent = await self._send_photo(
                    self.profit_bot,
                    path,
                    msg,
                    reply_to_message_id=self._reply_id(trade),
                    parse_mode="HTML",
                )
                self._record_message(trade, "profit", sent)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        else:
            await self._send(
                self.profit_bot,
                f"✅ تم تسجيل السهم كصفقة ناجحة إحصائيًا\n"
                f"{trade.get('symbol')}\n"
                f"🎯 الحد: +{threshold:.2f}%\n"
                f"📈 أفضل عائد مسجل: +{value:.2f}%\n"
                f"📌 نجاح الإشارة منفصل عن نتيجة الإغلاق النهائية.\n"
                f"🆔 {trade.get('trade_id', '')}",
                reply_to_message_id=self._reply_id(trade),
            )
        return True

    @staticmethod
    def _entered(trade: dict) -> bool:
        return bool(
            trade.get("entry_confirmed")
            or TradeMonitor._f(trade.get("filled_entry_price"), 0.0) > 0
            or trade.get("entered_at")
        )

    @staticmethod
    def _entered_ny_date(trade: dict):
        raw = trade.get("entered_at")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(ZoneInfo("America/New_York")).date()
        except Exception:
            return None

    @staticmethod
    def _option_expiration_ny_date(trade: dict):
        """Return the contract expiration date in New York calendar terms.

        Prefer the actual option expiration persisted with the selected
        contract. Older rows may lack it, so fall back to entered_at + DTE.
        """
        option = trade.get("option") or {}
        raw = option.get("expiration")
        if raw:
            try:
                return datetime.fromisoformat(str(raw)[:10]).date()
            except Exception:
                pass
        entered_day = TradeMonitor._entered_ny_date(trade)
        if entered_day is None:
            return None
        try:
            dte = max(0, int(float(option.get("dte", 0) or 0)))
        except (TypeError, ValueError):
            dte = 0
        from datetime import timedelta
        return entered_day + timedelta(days=dte)

    @staticmethod
    def _option_trade_closed(trade: dict) -> bool:
        status = str(trade.get("status", "")).upper()
        return bool(
            trade.get("closed_at")
            or trade.get("exit_price") is not None
            or status in {"CLOSED", "WIN", "LOSS", "BREAKEVEN"}
        )

    def _finalize_option_performance_rows(self, rows: list[dict], now_ny) -> tuple[list[dict], bool]:
        """Finalize option score results without prematurely judging swing trades.

        Rules:
        - Any option that reaches its configured cash threshold is WIN
          immediately, regardless of DTE.
        - A trade that actually closes before reaching the threshold is LOSS
          immediately because the threshold can no longer be reached.
        - 0DTE unresolved/open trades become LOSS after their expiration
          session closes.
        - 1–7 DTE and 8–35 DTE unresolved/open trades remain PENDING across
          daily reports and are finalized only after their actual expiration
          session closes.
        This changes only performance scoring; it never force-closes a swing.
        """
        changed = False
        today = now_ny.date()
        for trade in rows:
            if self._success_category(trade) not in {"equity_option", "index_option"}:
                continue
            if not self._entered(trade):
                continue
            if str(trade.get("performance_result", "")).upper() in {"WIN", "LOSS"}:
                continue

            if trade.get("success_reached") or trade.get("success_100_reached"):
                trade["performance_result"] = "WIN"
                trade["performance_finalized_at"] = trade.get("success_reached_at") or datetime.now(timezone.utc).isoformat()
                trade["performance_rule"] = "OPTION_CASH_THRESHOLD"
                changed = True
                continue

            rule = success_rules.get(self._success_category(trade))
            threshold = self._f(rule.get("threshold"), 0.0)
            if threshold <= 0:
                continue

            # A real close ends the opportunity to hit the threshold, so the
            # score can be finalized immediately instead of waiting for expiry.
            if self._option_trade_closed(trade):
                trade["performance_result"] = "LOSS"
                trade["performance_finalized_at"] = trade.get("closed_at") or datetime.now(timezone.utc).isoformat()
                trade["performance_rule"] = "OPTION_CASH_THRESHOLD"
                trade["performance_loss_reason"] = "TRADE_CLOSED_BEFORE_THRESHOLD"
                trade["success_threshold_value"] = round(threshold, 4)
                trade["success_threshold_unit"] = "USD"
                changed = True
                continue

            expiry_day = self._option_expiration_ny_date(trade)
            if expiry_day is None:
                # Unknown expiry on a still-open swing is not enough evidence
                # to call it a loss. Keep it PENDING.
                continue

            expiration_finished = today > expiry_day or (today == expiry_day and now_ny.hour >= 16)
            if not expiration_finished:
                continue

            trade["performance_result"] = "LOSS"
            trade["performance_finalized_at"] = datetime.now(timezone.utc).isoformat()
            trade["performance_rule"] = "OPTION_CASH_THRESHOLD"
            trade["performance_loss_reason"] = "THRESHOLD_NOT_REACHED_BY_EXPIRY"
            trade["success_threshold_value"] = round(threshold, 4)
            trade["success_threshold_unit"] = "USD"
            changed = True
        return rows, changed

    def _finalize_option_performance_after_close(self, market_open: bool) -> None:
        now_ny = datetime.now(ZoneInfo("America/New_York"))
        # If the market clock is open we never finalize losses. When closed,
        # current-day losses wait until regular close; previous-day unresolved
        # rows may be finalized at any later time (restart-safe).
        if market_open:
            return
        open_rows, open_changed = self._finalize_option_performance_rows(self.open_repo.all(), now_ny)
        history_rows, history_changed = self._finalize_option_performance_rows(self.history_repo.all(), now_ny)
        if open_changed:
            self.open_repo.replace(open_rows)
        if history_changed:
            self.history_repo.replace(history_rows)

    @staticmethod
    def _report_title_ar(category: str, period: str) -> str:
        base = {
            "stock": "تقرير الأسهم",
            "equity_option": "تقرير عقود الأسهم",
            "index_option": "تقرير عقود المؤشر SPX",
        }.get(category, "تقرير الأداء")
        return f"{base} {'اليومي' if period == 'daily' else 'الأسبوعي'}"

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
    def _report_caption(cls, report: dict) -> str:
        category = str(report.get("category"))
        period = str(report.get("period"))
        financial = report.get("financial") or {}
        summary = report.get("summary") or {}
        rule = report.get("success_rule") or {}
        threshold = float(rule.get("threshold", 0) or 0)
        period_word = "اليوم" if period == "daily" else "الأسبوع"
        lines = [
            f"✨ نتائج {settings.watermark_name} ✨",
            f"📊 {cls._report_title_ar(category, period)}",
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
            lines.extend([
                f"✅ أرباح {period_word}: {float(financial.get('gross_profit',0)):,.2f} $",
                f"❌ خسائر {period_word}: {float(financial.get('gross_loss',0)):,.2f} $",
                f"📈 صافي الربح: {float(financial.get('net',0)):,.2f} $ ({float(financial.get('net_sar',0)):,.2f} ﷼)",
            ])
            rule_text = "OFF" if threshold <= 0 else f"+${threshold:,.2f}"
        lines.extend([
            "",
            f"🎯 معيار نجاح الإشارة: {rule_text}",
            f"✅ إشارات وصلت للمعيار: {summary.get('successful_signals', 0)}",
            f"🟢 الصفقات الناجحة: {summary.get('wins', 0)}",
            f"🔴 الصفقات الخاسرة: {summary.get('losses', 0)}",
            f"⏳ قيد الانتظار: {summary.get('pending', 0)}",
            f"📊 نسبة النجاح: {summary.get('win_rate', 0)}%",
            "",
            "📌 العقود: النجاح عند بلوغ الحد المحدد، والخسارة بعد انتهاء جلسة نيويورك فقط إذا لم يصل العقد للحد. الأسهم: النجاح حسب الأهداف.",
        ])
        return "\n".join(lines)

    async def _send_period_reports(self, period: str, chat_id):
        for category in ("stock", "equity_option", "index_option"):
            report = category_period_report(
                self.history_repo.all(),
                self.open_repo.all(),
                category,
                period,
            )
            path = os.path.join(
                tempfile.gettempdir(),
                f"ALLUQMANU_USA_TD_{category.upper()}_{period.upper()}_REPORT.png",
            )
            try:
                weekly_performance_card(report, path)
                await self._send_photo(
                    self.report_bot,
                    path,
                    self._report_caption(report),
                    chat_id=chat_id,
                )
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def _send_daily_reports(self):
        # Daily reports now behave like the weekly report: automatic channel
        # publication after the US close. Manual menu reports remain private.
        if not self.channel_id:
            return
        await self._send_period_reports("daily", self.channel_id)

    async def _send_weekly_report(self):
        if not self.channel_id:
            return
        # Weekly output is split by Stocks / Equity Options / Index Options.
        await self._send_period_reports("weekly", self.channel_id)

    async def _scheduled_reports(self):
        # Reports are intentionally manual/private-only. They are still fully
        # calculated and available from the admin Reports menu/commands, but
        # nothing is automatically posted to the public Telegram channel.
        return None

    async def cycle(self):
        rows = self.open_repo.all()
        changed = False

        stock_symbols = {
            trade.get("symbol")
            for trade in rows
            if trade.get("status") == "OPEN" and not trade.get("option") and trade.get("symbol")
        }
        stockbars = {}
        try:
            if stock_symbols:
                stockbars = await self.provider.latest_bars(sorted(stock_symbols))
        except Exception:
            pass

        contracts = [
            (trade.get("option") or {}).get("symbol")
            for trade in rows
            if trade.get("status") == "OPEN" and trade.get("option")
        ]
        contracts = [x for x in contracts if x]
        optquotes = {}
        try:
            if contracts:
                optquotes = await self.provider.option_quotes(sorted(set(contracts)))
        except Exception:
            pass

        market_open = True
        try:
            market_open = bool((await self.provider.market_clock()).get("is_open"))
        except Exception:
            pass

        still_open = []
        for trade in rows:
            if trade.get("status") != "OPEN":
                still_open.append(trade)
                continue

            previous = self._f(trade.get("last_price"), 0.0) if trade.get("last_price") is not None else None
            price = None

            if trade.get("option"):
                quote = optquotes.get((trade.get("option") or {}).get("symbol"), {}) or {}
                quote_ts = quote.get("t") or quote.get("timestamp") or quote.get("time")
                quote_fresh, quote_reason, quote_age, quote_iso = freshness_info(
                    quote_ts,
                    max_age_minutes=(settings.monitor_price_max_age_minutes if market_open else None),
                    require_same_ny_date=True,
                )
                if not quote_fresh:
                    trade["monitor_data_status"] = f"STALE: {quote_reason}"
                    still_open.append(trade)
                    continue
                bid = quote.get("bp", quote.get("bid_price"))
                ask = quote.get("ap", quote.get("ask_price"))
                try:
                    bid = float(bid) if bid is not None else 0.0
                    ask = float(ask) if ask is not None else 0.0
                    price = (bid + ask) / 2 if bid > 0 and ask > 0 else bid if bid > 0 else ask if ask > 0 else None
                except (TypeError, ValueError):
                    price = None
                trade["last_market_timestamp"] = quote_iso
                trade["last_market_age_minutes"] = round(float(quote_age or 0.0), 2)
            else:
                bar = stockbars.get(trade.get("symbol"), {}) or {}
                bar_ts = bar.get("t") or bar.get("timestamp")
                bar_fresh, bar_reason, bar_age, bar_iso = freshness_info(
                    bar_ts,
                    max_age_minutes=(settings.monitor_price_max_age_minutes if market_open else None),
                    require_same_ny_date=True,
                )
                if not bar_fresh:
                    trade["monitor_data_status"] = f"STALE: {bar_reason}"
                    still_open.append(trade)
                    continue
                try:
                    price = float(bar.get("c")) if bar.get("c") is not None else None
                except (TypeError, ValueError):
                    price = None
                trade["last_market_timestamp"] = bar_iso
                trade["last_market_age_minutes"] = round(float(bar_age or 0.0), 2)

            if price is None:
                still_open.append(trade)
                continue

            price = round(price, 4)
            trade["last_price"] = price
            trade["last_monitored_at"] = datetime.now(timezone.utc).isoformat()
            changed = True

            # Entry confirmation is shared by stocks, equity options and index
            # options. It detects direct touches, crossings between polls and
            # brief touches visible in 1-minute high/low bars.
            confirmed = bool(trade.get("entry_confirmed", trade.get("filled_entry_price") is not None))
            if not confirmed:
                if await self._entry_touched(trade, price, previous):
                    fill = round(self._conservative_fill(trade), 4)
                    trade["entry_confirmed"] = True
                    trade["filled_entry_price"] = fill
                    trade["entered_at"] = datetime.now(timezone.utc).isoformat()
                    label = self._label(trade) if trade.get("option") else trade.get("symbol")
                    sent = await self._send(
                        self.signal_bot,
                        entry_message(trade, fill),
                        reply_to_message_id=self._reply_id(trade),
                        parse_mode="HTML",
                    )
                    self._record_message(trade, "signal", sent)
                    # If historical high/low proves the entry was touched and
                    # the first observed quote is already profitable, publish
                    # the current profit immediately instead of waiting for one
                    # more upward tick. This also triggers the +$100 milestone.
                    if (not self.external_profit_watcher) and trade.get("option") and self._should_send_profit_alert(trade, price):
                        await self._profit_update(trade, fill, price)
                else:
                    still_open.append(trade)
                    continue

            elif trade.get("option"):
                entry_ref = self._entry(trade)
                if (not self.external_profit_watcher) and self._should_send_profit_alert(trade, price):
                    await self._profit_update(trade, previous or entry_ref, price)

            # Track the best observed result after confirmed entry and apply the
            # admin-configured statistical success rule once. This does not
            # close the trade and never overwrites its eventual WIN/LOSS.
            current_pnl = self._pnl_pct(trade, price)
            trade["max_pnl_pct"] = round(
                max(self._f(trade.get("max_pnl_pct"), current_pnl), current_pnl),
                4,
            )
            if trade.get("option"):
                current_usd, _ = self._cash(trade, price)
                trade["max_profit_usd"] = round(
                    max(self._f(trade.get("max_profit_usd"), current_usd), current_usd),
                    2,
                )
            await self._mark_success_if_reached(trade, price)

            entry = self._entry(trade)
            long_trade = self._long(trade)
            stop = self._f(trade.get("stop"), 0.0)
            initial_risk = abs(entry - stop) or max(entry * 0.01, 0.01)

            if "INTRADAY" in str(trade.get("trade_type", "")) and not market_open:
                pnl = round(self._pnl_pct(trade, price), 2)
                final_result = self._final_result_from_pnl(pnl)
                trade.update(
                    status="CLOSED",
                    final_result=final_result,
                    exit_price=price,
                    exit_reason="TIME_EXIT",
                    closed_at=datetime.now(timezone.utc).isoformat(),
                    pnl_pct=pnl,
                )
                usd, sar = self._cash(trade, price)
                trade["cash_pnl_usd"] = round(usd, 2)
                trade["cash_pnl_sar"] = round(sar, 2)
                self.history_repo.append(trade)
                label = self._label(trade) if trade.get("option") else trade.get("symbol")
                await self._send(
                    self.signal_bot,
                    f"🟠 إغلاق زمني\n{label}\nالخروج: {price:.2f}\nالنتيجة: {pnl:+.2f}%\n🆔 {trade.get('trade_id', '')}",
                    chat_id=settings.telegram_admin_user_id,
                    reply_to_message_id=None,
                )
                continue

            distance_to_stop = price - stop if long_trade else stop - price
            if (
                distance_to_stop <= initial_risk * settings.near_stop_fraction
                and distance_to_stop > 0
                and not trade.get("near_stop_sent")
            ):
                # Keep the state internally to avoid repeated checks, but do not
                # publish Near Stop Loss messages to the Telegram channel.
                trade["near_stop_sent"] = True

            hit_stop = price <= stop if long_trade else price >= stop
            if hit_stop:
                pnl = round(self._pnl_pct(trade, price), 2)
                final_result = self._final_result_from_pnl(pnl)
                trade.update(
                    status=final_result,
                    final_result=final_result,
                    exit_price=price,
                    exit_reason="STOP_LOSS",
                    closed_at=datetime.now(timezone.utc).isoformat(),
                    pnl_pct=pnl,
                )
                usd, sar = self._cash(trade, price)
                trade["cash_pnl_usd"] = round(usd, 2)
                trade["cash_pnl_sar"] = round(sar, 2)
                self.history_repo.append(trade)
                label = self._label(trade) if trade.get("option") else trade.get("symbol")
                await self._send(
                    self.signal_bot,
                    f"🔴 وقف الخسارة\n{label}\nالخروج: {price:.2f}\nالنتيجة: {pnl:+.2f}%\n🆔 {trade.get('trade_id', '')}",
                    chat_id=settings.telegram_admin_user_id,
                    reply_to_message_id=None,
                )
                continue

            for n in (1, 2, 3):
                target = self._f(trade.get(f"tp{n}"), 0.0)
                flag = f"tp{n}_hit"
                target_hit = price >= target if long_trade else price <= target
                if target and target_hit and not trade.get(flag):
                    trade[flag] = True
                    pnl = self._pnl_pct(trade, price)
                    if trade.get("option"):
                        usd, sar = self._cash(trade, price)
                        msg = (
                            f"🟢 تحقق TP{n}\n{self._label(trade)}\n"
                            f"💰 السعر: ${price:.2f} | الهدف: ${target:.2f}\n"
                            f"📈 الربح: {pnl:+.2f}%\n💵 الربح: {usd:+.2f}$\n"
                            f"🇸🇦 الربح بالريال السعودي: {sar:+.2f} ريال\n🆔 {trade.get('trade_id', '')}"
                        )
                    else:
                        msg = (
                            f"🟢 تحقق TP{n}\n{trade.get('symbol')}\n"
                            f"السعر: {price:.2f} | الهدف: {target:.2f}\n"
                            f"النتيجة: {pnl:+.2f}%\n🆔 {trade.get('trade_id', '')}"
                        )
                    sent = await self._send(
                        self.profit_bot,
                        msg,
                        reply_to_message_id=self._reply_id(trade),
                    )
                    self._record_message(trade, "profit", sent)
                    if n == 1 and settings.trailing_after_tp1_to_entry:
                        trade["stop"] = round(entry, 4)

            if trade.get("tp3_hit"):
                pnl = round(self._pnl_pct(trade, price), 2)
                final_result = self._final_result_from_pnl(pnl)
                trade.update(
                    status=final_result,
                    final_result=final_result,
                    exit_price=price,
                    exit_reason="TP3",
                    closed_at=datetime.now(timezone.utc).isoformat(),
                    pnl_pct=pnl,
                )
                usd, sar = self._cash(trade, price)
                trade["cash_pnl_usd"] = round(usd, 2)
                trade["cash_pnl_sar"] = round(sar, 2)
                self.history_repo.append(trade)
                continue

            still_open.append(trade)

        if changed:
            # ProfitWatcher runs independently. Preserve fields it may have
            # written while this heavier cycle was awaiting market/Telegram I/O.
            latest = {str(r.get("trade_id", "")): r for r in self.open_repo.all()}
            preserve = (
                "profit_alert_sent", "profit_alert_last_at", "profit_alert_last_price",
                "profit_alert_highest_price", "profit_alert_last_usd",
                "profit_watcher_price", "profit_watcher_quote_timestamp",
                "profit_watcher_quote_age_minutes", "profit_watcher_bid",
                "profit_watcher_ask", "last_profit_watcher_at", "telegram_message_refs",
            )
            for row in still_open:
                current = latest.get(str(row.get("trade_id", ""))) or {}
                for key in preserve:
                    if key in current:
                        row[key] = current[key]
            self.open_repo.replace(still_open)
        self._finalize_option_performance_after_close(market_open)
        await self._scheduled_reports()

    async def loop(self):
        while True:
            try:
                await self.cycle()
            except Exception as exc:
                print(f"[monitor] {type(exc).__name__}: {exc}")
            await asyncio.sleep(self.interval)

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self.loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
