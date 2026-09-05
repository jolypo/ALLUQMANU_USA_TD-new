from __future__ import annotations

import asyncio
import os
import tempfile
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.market.quality import freshness_info
from app.reports.profit_card import profit_update_card
from app.runtime_settings import profit_alert_rules
from app.telegram.message_templates import profit_update_message


class OpenOptionProfitWatcher:
    """Dedicated 60-second watcher for confirmed OPEN option trades.

    It is intentionally separate from the heavier trade monitor. Alerts are
    measured from the last price that actually produced a profit alert, not
    from synthetic entry-anchored step indexes.
    """

    def __init__(self, open_repo, provider, profit_bot, channel_id, interval: int = 60):
        self.open_repo = open_repo
        self.provider = provider
        self.profit_bot = profit_bot
        self.channel_id = channel_id
        self.interval = max(10, int(interval))
        self._task = None
        self._last_alert_price: dict[str, float] = {}
        self._highest_alert_price: dict[str, float] = {}

    @staticmethod
    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _entry(trade: dict) -> float:
        try:
            filled = float(trade.get("filled_entry_price") or 0)
        except (TypeError, ValueError):
            filled = 0.0
        if filled > 0:
            return filled
        try:
            lo, hi = float(trade.get("entry_low") or 0), float(trade.get("entry_high") or 0)
        except (TypeError, ValueError):
            return 0.0
        return (lo + hi) / 2 if lo > 0 and hi > 0 else max(lo, hi, 0.0)

    @staticmethod
    def _label(trade: dict) -> str:
        option = trade.get("option") or {}
        typ = str(option.get("type") or option.get("option_type") or "OPTION").upper()
        if typ == "C": typ = "CALL"
        if typ == "P": typ = "PUT"
        return f"{trade.get('symbol','')} {option.get('strike','')} {typ}".strip()

    async def _send_profit(self, trade: dict, price: float) -> None:
        entry = self._entry(trade)
        qty = max(1, int(self._f(trade.get("contracts", 1), 1)))
        usd = (price - entry) * settings.option_multiplier * qty
        sar = usd * settings.usd_sar_rate
        pnl = ((price - entry) / entry * 100) if entry > 0 else 0.0
        now = datetime.now(timezone.utc)
        sa = now.astimezone(ZoneInfo("Asia/Riyadh"))
        ny = now.astimezone(ZoneInfo("America/New_York"))
        path = os.path.join(tempfile.gettempdir(), f"profit_watcher_{trade.get('trade_id','trade')}.png")
        profit_update_card(trade, usd, sar, price, path)
        caption = profit_update_message(trade, entry, price, usd, sar, now=now)
        sent = None
        try:
            with open(path, "rb") as fh:
                sent = await self.profit_bot.send_photo(
                    chat_id=self.channel_id, photo=fh, caption=caption,
                    reply_to_message_id=int(trade.get("channel_message_id")) if trade.get("channel_message_id") else None,
                    allow_sending_without_reply=True,
                    parse_mode="HTML",
                )
        except Exception as exc:
            print(f"[profit-watcher-send] {type(exc).__name__}: {exc}")
        finally:
            try: os.remove(path)
            except OSError: pass
        if sent is not None:
            refs = list(trade.get("telegram_message_refs") or [])
            refs.append({"bot": "profit", "message_id": getattr(sent, "message_id", None)})
            refs = [x for x in refs if x.get("message_id")]
            self.open_repo.update_trade(str(trade.get("trade_id", "")), {
                "profit_alert_sent": True,
                "profit_alert_last_at": now.isoformat(),
                "profit_alert_last_price": round(price, 4),
                "profit_alert_highest_price": round(price, 4),
                "profit_alert_last_usd": round(usd, 2),
                "telegram_message_refs": refs,
                "last_profit_watcher_at": now.isoformat(),
            })

    async def cycle(self):
        rows = self.open_repo.all()
        eligible = [
            r for r in rows
            if r.get("status") == "OPEN" and r.get("option")
            and bool(r.get("entry_confirmed", r.get("filled_entry_price") is not None))
        ]
        contracts = sorted({str((r.get("option") or {}).get("symbol") or "") for r in eligible if (r.get("option") or {}).get("symbol")})
        if not contracts:
            return
        try:
            quotes = await self.provider.option_quotes(contracts)
        except Exception as exc:
            print(f"[profit-watcher-quotes] {type(exc).__name__}: {exc}")
            return
        step = float(profit_alert_rules.get_step())
        if step <= 0:
            return
        for trade in eligible:
            tid = str(trade.get("trade_id", ""))
            sym = str((trade.get("option") or {}).get("symbol") or "")
            q = quotes.get(sym, {}) or {}
            qts = q.get("t") or q.get("timestamp") or q.get("time")
            fresh, reason, age, iso = freshness_info(
                qts, max_age_minutes=settings.monitor_price_max_age_minutes, require_same_ny_date=True
            )
            if not fresh:
                print(f"[profit-watcher-stale] {tid} {reason}")
                continue
            bid = self._f(q.get("bp", q.get("bid_price")), 0.0)
            ask = self._f(q.get("ap", q.get("ask_price")), 0.0)
            price = (bid + ask) / 2 if bid > 0 and ask > 0 else bid if bid > 0 else ask if ask > 0 else 0.0
            if price <= 0:
                continue
            price = round(price, 4)
            entry = self._entry(trade)
            if price <= entry:
                continue
            saved_last = self._f(trade.get("profit_alert_last_price"), 0.0)
            last = self._last_alert_price.get(tid, saved_last if saved_last > 0 else entry)
            saved_high = self._f(trade.get("profit_alert_highest_price"), 0.0)
            highest = self._highest_alert_price.get(tid, max(saved_high, last))
            self.open_repo.update_trade(tid, {
                "profit_watcher_price": price,
                "profit_watcher_quote_timestamp": iso,
                "profit_watcher_quote_age_minutes": round(float(age or 0), 2),
                "profit_watcher_bid": round(bid, 4), "profit_watcher_ask": round(ask, 4),
                "last_profit_watcher_at": datetime.now(timezone.utc).isoformat(),
            })
            # No duplicate old highs. A new alert requires the configured gain
            # from the last price that actually generated an alert.
            if price + 1e-9 < last + step or price <= highest + 1e-9:
                continue
            await self._send_profit(trade, price)
            self._last_alert_price[tid] = price
            self._highest_alert_price[tid] = max(highest, price)

    async def loop(self):
        # Cadence is anchored to loop start, not cycle-duration + 60 seconds.
        next_run = time.monotonic()
        while True:
            next_run += self.interval
            try:
                await self.cycle()
            except Exception as exc:
                print(f"[profit-watcher] {type(exc).__name__}: {exc}")
            await asyncio.sleep(max(0.0, next_run - time.monotonic()))

    def start(self):
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self.loop(), name="open-option-profit-watcher")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
