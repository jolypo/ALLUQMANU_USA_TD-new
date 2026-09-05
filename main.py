from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from telegram import Update

from app.config import settings
from app.providers.alpaca import AlpacaProvider
from app.repositories.json_repo import JsonRepository
from app.repositories.equity_watchlist import EquityWatchlistRepository
from app.scheduler.monitor import TradeMonitor
from app.scheduler.profit_watcher import OpenOptionProfitWatcher
from app.telegram.bots import TelegramHub
from app.trading.service import SignalService
from app.runtime_settings import success_rules

provider = AlpacaProvider()
history = JsonRepository("trade_history.json")
open_repo = JsonRepository("open_trades.json")
state_repo = JsonRepository("state.json")
equity_watchlist = EquityWatchlistRepository()
service = SignalService(provider, history, equity_watchlist)
hub = TelegramHub(service, open_repo, history, state_repo)
monitor = TradeMonitor(
    open_repo,
    history,
    state_repo,
    provider,
    hub.app.bot,
    hub.profit,
    hub.report,
    settings.telegram_channel_chat_id,
    settings.trade_monitor_seconds,
    external_profit_watcher=True,
)
profit_watcher = OpenOptionProfitWatcher(
    open_repo, provider, hub.profit, settings.telegram_channel_chat_id, interval=60
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialized = False
    started = False
    try:
        await equity_watchlist.initialize()
        await hub.app.initialize()
        initialized = True
        await hub.app.start()
        started = True
        monitor.start()
        profit_watcher.start()
        if settings.public_base_url:
            url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
            kwargs = {}
            if settings.telegram_webhook_secret:
                kwargs["secret_token"] = settings.telegram_webhook_secret
            await hub.app.bot.set_webhook(
                url=url,
                allowed_updates=Update.ALL_TYPES,
                **kwargs,
            )
        yield
    finally:
        await hub.stop_background_monitors()
        await profit_watcher.stop()
        await monitor.stop()
        if started:
            await hub.app.stop()
        if initialized:
            await hub.app.shutdown()
        await service.economic_context.close()
        await provider.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": settings.app_name, "status": "ok"}


@app.get("/health")
async def health():
    data_sources = await hub._data_sources_status_text()
    spx_session = service.spx_option_session_status()
    spx_gth_data = await service.spx_gth_data_diagnostics()
    return {
        "ok": True,
        "service": settings.app_name,
        "environment": settings.environment,
        "paper_mode": settings.paper_mode,
        "live_trading": settings.live_trading,
        "channel_configured": bool(settings.telegram_channel_chat_id),
        "webhook_configured": bool(settings.public_base_url),
        "monitoring": True,
        "manual_publish_required": settings.require_manual_publish,
        "max_signals_per_scan": settings.max_signals_per_scan,
        "max_open_trades": settings.max_open_trades,
        "weekly_report_image": settings.weekly_report_image_enabled,
        "option_card_orientation": settings.option_card_orientation,
        "enable_0dte": settings.enable_0dte,
        "enable_spx_0dte": settings.enable_spx_0dte,
        "news_filter": settings.news_enabled,
        "success_rules": success_rules.all(),
        "monitor_seconds": settings.trade_monitor_seconds,
        "profit_watcher_seconds": 60,
        "waseem_v2": {"equity_options": True, "spx_spxw": True, "continuous_until_close": True},
        "waseem_v4": {"equity_options": True, "spx_spxw": True, "daily": True, "weekly": True, "liquidity_pre_move": True, "entry_engine": "V3", "continuous_until_session_close": True, "equity_monitor": ("RUNNING" if hub._monitor_running("option:waseem_v4") else "STOPPED"), "spx_monitor": ("RUNNING" if hub._monitor_running("index:waseem_v4") else "STOPPED")},
        "waseem_v5": {"equity_options": True, "spx_spxw": True, "daily": True, "weekly": True, "order_flow": "top-of-book + latest trade + cross-scan pressure; depth fields unavailable unless feed supports them", "ready_floor": settings.waseem_v5_ready_floor, "equity_monitor": ("RUNNING" if hub._monitor_running("option:waseem_v5") else "STOPPED"), "spx_monitor": ("RUNNING" if hub._monitor_running("index:waseem_v5") else "STOPPED")},
        "equity_watchlist": equity_watchlist.status().__dict__,
        "waseem_v3": {
            "equity_entry_engine": True,
            "spx_entry_engine": True,
            "spx_gth": True,
            "continuous_until_session_close": True,
            "spx_session": spx_session,
            "spx_gth_data": spx_gth_data,
        },
        "spx_gth_data": spx_gth_data,
        "data_sources_live_diagnostics": data_sources.splitlines(),
        "fred_configured": bool(settings.fred_api_key),
        "alpha_vantage_configured": bool(settings.alpha_vantage_api_key),
        "learning": service.learning.summary(),
    }


@app.get("/state")
async def state():
    data_sources = await hub._data_sources_status_text()
    spx_gth_data = await service.spx_gth_data_diagnostics()
    keys = (
        "stock", "option", "option:confirmed", "option:waseem", "option:waseem_v2", "option:waseem_v3", "option:waseem_v4", "option:waseem_v5",
        "index:v20", "index:core", "index:confirmed", "index:waseem", "index:waseem_v2", "index:waseem_v3", "index:waseem_v4", "index:waseem_v5",
    )
    return {
        "ok": True,
        "paused": hub._paused(),
        "spx_options_session": service.spx_option_session_status(),
        "spx_gth_data": spx_gth_data,
        "monitors": {key: ("RUNNING" if hub._monitor_running(key) else "STOPPED") for key in keys},
        "data_sources_live_diagnostics": data_sources.splitlines(),
    }


@app.get("/learning")
async def learning_status():
    if settings.learning_enabled:
        await service.learning.sync_github_if_due(force=True)
    return service.learning.summary()


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if settings.telegram_webhook_secret:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if got != settings.telegram_webhook_secret:
            raise HTTPException(403, "invalid webhook secret")
    data = await request.json()
    update = Update.de_json(data, hub.app.bot)
    await hub.app.process_update(update)
    return {"ok": True}
