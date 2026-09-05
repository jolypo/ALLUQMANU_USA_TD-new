from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ALLUQMANU_USA_TD"
    environment: str = "production"
    host: str = "0.0.0.0"
    port: int = 10000
    public_base_url: str | None = None

    # Safety: analysis + simulated positions only.
    paper_mode: bool = True
    live_trading: bool = False

    signal_bot_name: str = "KSA_USA_signal_bot"
    signal_bot_token: str
    profit_bot_name: str = "KSA_USA_profit88_bot"
    profit_bot_token: str
    report_bot_name: str = "KSA_USA_report88_bot"
    report_bot_token: str
    telegram_admin_user_id: int = 1280090240
    telegram_channel_chat_id: int | None = None
    telegram_webhook_secret: str | None = None

    alpaca_trading_base_url: str = "https://paper-api.alpaca.markets/v2"
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_api_key: str
    alpaca_api_secret: str
    alpaca_stock_feed: str = "iex"
    alpaca_options_feed: str = "indicative"
    database_url: str | None = None

    # Waseem V5 independent order-flow/entry policy.
    waseem_v5_ready_floor: float = 88.0
    waseem_v5_min_flow_score: float = 55.0

    # Waseem V6 independent delayed/session-aware policy.
    waseem_v6_ready_floor: float = 88.0
    waseem_v6_delayed_threshold_minutes: float = 5.0

    # Free external context feeds used only by Waseem V2. Keys stay in Render/.env.
    fred_enabled: bool = True
    fred_api_key: str | None = None
    fred_base_url: str = "https://api.stlouisfed.org"
    fred_calendar_cache_seconds: int = 21600
    fred_series_cache_seconds: int = 21600
    alpha_vantage_enabled: bool = True
    alpha_vantage_api_key: str | None = None
    alpha_vantage_base_url: str = "https://www.alphavantage.co/query"
    alpha_vantage_earnings_cache_seconds: int = 21600

    stock_symbols: str = "AMD,UBER,MSFT,MU,META,INTC,ORCL,RKLB,AMZN,AVGO,TSLA,IBM,AAPL,NVDA,SPCX,MRVL,MSTR,GOOGL"
    index_option_symbols: str = "SPX"
    index_analysis_proxy_spx: str = "SPY"

    enable_stock_intraday: bool = True
    enable_stock_swing: bool = True
    enable_equity_options_intraday: bool = True
    enable_equity_options_swing: bool = True
    enable_index_options_intraday: bool = True
    enable_index_options_swing: bool = True
    enable_0dte: bool = False
    # SPX-specific 0DTE path. Kept separate so existing ENABLE_0DTE=false
    # deployments still receive the requested SPX 0DTE behavior by default.
    enable_spx_0dte: bool = True
    allow_off_hours_scan: bool = False

    # Final READY floor. Dynamic market-quality rules may raise this to 92-94,
    # but never lower it.
    min_score: float = 90.0
    min_rr: float = 1.5
    max_risk_per_trade: float = 0.01
    max_total_open_risk: float = 0.03
    max_open_trades: int = 5
    probability_min_samples: int = 50

    # Manual ranking / approval workflow.
    default_signals_per_scan: int = 3
    max_signals_per_scan: int = 3
    candidate_ttl_seconds: int = 180
    monitor_max_opportunities: int = 3
    stock_monitor_interval_seconds: int = 120
    equity_option_monitor_interval_seconds: int = 120
    index_option_monitor_interval_seconds: int = 60
    monitor_duplicate_cooldown_seconds: int = 180
    # Cross-engine Telegram anti-spam. Engines still scan continuously; only
    # repeat delivery of the same underlying is cooled down.
    monitor_symbol_cooldown_seconds: int = 1200
    monitor_symbol_upgrade_score_delta: float = 3.0

    # Freshness hard gates. The user accepts delayed data, but never prior-session data.
    intraday_max_data_age_minutes: float = 20.0
    option_quote_max_age_minutes: float = 20.0
    spx_reference_max_age_minutes: float = 20.0
    monitor_price_max_age_minutes: float = 20.0
    require_manual_publish: bool = True
    prevent_exact_duplicate_trade: bool = True
    max_daily_stock_signals: int = 6
    max_daily_equity_option_signals: int = 6
    max_daily_index_option_signals: int = 4

    intraday_timeframe: str = "15Min"
    intraday_lookback_days: int = 25
    intraday_min_bars: int = 60
    swing_timeframe: str = "1Day"
    swing_lookback_days: int = 320
    swing_min_bars: int = 120
    confirmation_timeframe: str = "1Day"
    confirmation_lookback_days: int = 260

    # Technical quality gates.
    min_adx_trend: float = 18.0
    strong_adx: float = 25.0
    min_rvol_breakout: float = 1.10
    range_regime_penalty: float = 5.0
    relative_strength_weight: float = 6.0

    # Dynamic market-quality gate. Same policy for bullish/CALL and bearish/PUT.
    dynamic_caution_min_score: float = 92.0
    dynamic_range_min_score: float = 93.0
    dynamic_countertrend_min_score: float = 93.0
    dynamic_high_vol_min_score: float = 94.0
    dynamic_low_liquidity_min_score: float = 94.0
    dynamic_min_directional_gap: float = 8.0
    dynamic_low_liquidity_rvol: float = 0.75
    dynamic_high_liquidity_rvol: float = 1.25
    dynamic_high_vol_atr_ratio: float = 1.60
    dynamic_low_vol_atr_ratio: float = 0.60
    dynamic_high_vol_atr_pct: float = 6.0
    dynamic_contract_no_trade_spread_pct: float = 7.5
    dynamic_contract_high_liquidity_spread_pct: float = 3.0
    dynamic_contract_min_quality_score: float = 70.0
    dynamic_option_prefilter_margin: float = 4.0
    dynamic_normal_risk_cap: float = 0.0075
    dynamic_caution_risk_cap: float = 0.005
    dynamic_range_risk_cap: float = 0.005
    dynamic_countertrend_risk_cap: float = 0.005
    dynamic_high_vol_risk_cap: float = 0.005
    dynamic_low_liquidity_risk_cap: float = 0.003

    # Confirmed Setup learning/calibration (v6).
    learning_enabled: bool = True
    learning_memory_filename: str = "learning_memory.json"
    learning_min_global_samples: int = 12
    learning_min_bucket_samples: int = 5
    learning_max_bonus: float = 2.0
    learning_max_penalty: float = -4.0
    # Optional durable GitHub memory. Keep token in Render Environment only.
    # A separate branch prevents learning writes from redeploying `main`.
    learning_github_token: str | None = None
    learning_github_repo: str = "jolypo/ALLUQMANU_USA_TD-new"
    learning_github_branch: str = "learning-data"
    learning_github_path: str = "data/learning_memory.json"
    learning_github_sync_seconds: int = 300

    # News/catalyst layer. News is a modest modifier, not a trade generator.
    news_enabled: bool = True
    news_lookback_hours: int = 6
    news_max_items: int = 8
    news_score_cap: float = 5.0

    option_intraday_min_dte: int = 1
    option_intraday_max_dte: int = 7
    option_swing_min_dte: int = 7
    option_swing_max_dte: int = 35
    option_max_spread_pct: float = 10.0
    option_min_abs_delta: float = 0.35
    option_max_abs_delta: float = 0.75
    option_min_contract_score: float = 65.0
    option_max_strike_distance_pct: float = 40.0
    equity_option_max_contract_price_default: float = 0.0
    index_option_max_contract_price_default: float = 0.0

    # Stricter contract gates for SPX 0DTE.
    spx_0dte_max_spread_pct: float = 8.0
    spx_0dte_min_abs_delta: float = 0.40
    spx_0dte_max_abs_delta: float = 0.65
    spx_0dte_min_contract_score: float = 72.0

    # Monitoring and milestone rules.
    trade_monitor_seconds: int = 60
    # Legacy global milestone kept for compatibility. New per-category
    # thresholds are persisted in data/success_rules.json and editable in Telegram.
    option_profit_success_usd: float = 50.0
    stock_success_pct_default: float = 0.0
    equity_option_success_usd_default: float = 50.0
    index_option_success_usd_default: float = 50.0
    option_profit_alert_step_default: float = 0.10
    usd_sar_rate: float = 3.75
    option_multiplier: int = 100
    trailing_stop_enabled: bool = False
    trailing_after_tp1_to_entry: bool = True
    trailing_after_tp2_atr: float = 1.0
    near_stop_fraction: float = 0.25

    daily_report_enabled: bool = True
    weekly_report_enabled: bool = True
    weekly_report_image_enabled: bool = True
    report_hour_riyadh: int = 23
    option_card_orientation: str = "horizontal"

    store_dir: str = "data"
    watermark_name: str = "ALLUQMANI_USA_TD"
    message_timezones: str = "America/New_York,Asia/Riyadh"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def ready_score_floor(self) -> float:
        """Hard production floor requested for every published READY signal.

        Old Render environments may still contain MIN_SCORE=75. The effective
        floor therefore never falls below 90, while a stricter configured value
        above 90 is still respected.
        """
        return max(90.0, float(self.min_score))

    @property
    def stocks(self) -> list[str]:
        values = [x.strip().upper() for x in self.stock_symbols.split(",") if x.strip()]
        # V22 requested additions remain available even when Render still has an
        # older STOCK_SYMBOLS environment value. Runtime watchlist controls can
        # still disable/remove them for the current server session.
        for symbol in ("MRVL", "MSTR", "GOOGL"):
            if symbol not in values:
                values.append(symbol)
        return values

    @property
    def indices(self) -> list[str]:
        return [x.strip().upper() for x in self.index_option_symbols.split(",") if x.strip()]

    @property
    def data_path(self) -> Path:
        p = Path(self.store_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
