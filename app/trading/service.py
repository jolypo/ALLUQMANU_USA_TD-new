from __future__ import annotations
import time
import pandas as pd
from app.config import settings
from app.models.domain import Signal, TradeType, Decision
from app.market.quality import validate_bars, freshness_info, latest_bar_timestamp
from app.market.dynamic_quality import DynamicMarketGate
from app.market.regime import MarketRegimeEngine
from app.strategies.engine import StrategyEngine
from app.strategies.spx_v20 import SPXV20Engine
from app.strategies.confirmed_setup import ConfirmedSetupEngine
from app.strategies.judge import JudgeEngine
from app.learning import LearningStore
from app.risk.engine import RiskEngine
from app.options.selector import ContractSelector
from app.options.waseem_selector import WaseemContractSelector
from app.options.waseem_v2_selector import WaseemV2ContractSelector
from app.options.waseem_v3_entry import WaseemV3EntryEngine
from app.market.waseem_v2_context import WaseemV2ContextEngine
from app.market.waseem_v4_liquidity import WaseemV4LiquidityEngine
from app.market.waseem_v5_orderflow import WaseemV5OrderFlowEngine
from app.market.waseem_v6_engine import WaseemV6Engine
from app.market.stock_intelligence import StockIntelligenceEngine
from app.market.stock_news import StockNewsEngine
from app.providers.economic import EconomicContextProvider
from app.utils.indicators import add_indicators
from app.probability.engine import ProbabilityEngine
from app.runtime_settings import contract_search_rules

SECTOR_MAP = {
    "AMD":"Semiconductors","MU":"Semiconductors","INTC":"Semiconductors","NVDA":"Semiconductors","AVGO":"Semiconductors",
    "MSFT":"Technology","ORCL":"Technology","IBM":"Technology","META":"Communication Services","AAPL":"Technology",
    "AMZN":"Consumer Discretionary","TSLA":"Consumer Discretionary","UBER":"Industrials","RKLB":"Industrials","SPCX":"Industrials",
    "MRVL":"Semiconductors","MSTR":"Technology","GOOGL":"Communication Services",
}

POSITIVE_NEWS = (
    "beats estimates","raises guidance","raised guidance","upgrade","upgraded","approval","approved",
    "record revenue","contract win","wins contract","partnership","buyback","strong demand","outperform",
)
NEGATIVE_NEWS = (
    "misses estimates","cuts guidance","cut guidance","downgrade","downgraded","offering","dilution",
    "investigation","sec probe","fraud","bankruptcy","recall","lawsuit","weak demand","underperform",
)
SEVERE_NEGATIVE = ("bankruptcy","fraud","sec investigation","offering","cuts guidance","cut guidance")


class SignalService:
    def __init__(self, provider, history_repo, equity_watchlist_repo=None):
        self.provider = provider
        self.history = history_repo
        self.equity_watchlist = equity_watchlist_repo
        self.strategy = StrategyEngine()
        self.spx_v20 = SPXV20Engine()
        self.risk = RiskEngine()
        self.selector = ContractSelector()
        self.waseem_selector = WaseemContractSelector()
        self.waseem_v2_selector = WaseemV2ContractSelector()
        self.waseem_v3_entry = WaseemV3EntryEngine()
        self.waseem_v4_liquidity = WaseemV4LiquidityEngine()
        self.waseem_v5_orderflow = WaseemV5OrderFlowEngine()
        self.waseem_v6 = WaseemV6Engine()
        self.stock_intelligence = StockIntelligenceEngine()
        self.stock_news = StockNewsEngine()
        self.waseem_v2_context = WaseemV2ContextEngine(ttl_seconds=60)
        self.economic_context = EconomicContextProvider()
        self.prob = ProbabilityEngine()
        self.market_gate = DynamicMarketGate()
        self.confirmed_setup = ConfirmedSetupEngine()
        self.learning = LearningStore(history_repo)
        self.judge = JudgeEngine(self.learning)
        self._spx_gth_diag_cache = None
        self._spx_gth_diag_cache_at = 0.0


    async def equity_symbols(self) -> list[str]:
        """Return the enabled persistent master watchlist, with safe static fallback."""
        if self.equity_watchlist is None:
            return list(settings.stocks)
        try:
            return await self.equity_watchlist.enabled_symbols()
        except Exception:
            return list(settings.stocks)

    async def stock_analysis(self, symbol: str) -> dict:
        sym = str(symbol or "").strip().upper()
        return await self.stock_intelligence.analyze(self.provider, sym)

    async def stock_news_analysis(self, symbol: str) -> dict:
        sym = str(symbol or "").strip().upper()
        return await self.stock_news.analyze(self.provider, sym, self.economic_context)

    async def validate_equity_watchlist_symbol(self, symbol: str) -> dict:
        sym = str(symbol or "").strip().upper()
        if not sym or len(sym) > 10 or not sym.replace(".", "").replace("-", "").isalnum():
            return {"ok": False, "symbol": sym, "market_data": False, "options": False, "reason": "INVALID_SYMBOL_FORMAT"}
        market_data = False
        options = False
        reasons = []
        try:
            bars = await self.provider.bars(sym, "1Day", 12)
            market_data = bars is not None and not bars.empty
            if not market_data:
                reasons.append("MARKET_DATA_UNAVAILABLE")
        except Exception as exc:
            reasons.append(f"MARKET_DATA_{type(exc).__name__}")
        try:
            contracts = await self.provider.option_contracts(sym, 0, 35)
            options = bool(contracts)
            if not options:
                reasons.append("OPTIONS_UNAVAILABLE")
        except Exception as exc:
            reasons.append(f"OPTIONS_{type(exc).__name__}")
        return {"ok": bool(market_data and options), "symbol": sym, "market_data": market_data, "options": options, "reason": ",".join(reasons) or "OK"}

    @staticmethod
    def _expiration_horizon(horizon: str | None) -> tuple[str | None, int | None, int | None]:
        """Map Telegram search horizon to an actual DTE window.

        DAILY   -> 0 DTE only
        WEEKLY  -> 1-7 DTE
        MONTHLY -> 8-35 DTE
        None keeps the legacy strategy-specific windows.
        """
        h = str(horizon or "").strip().lower()
        if h == "daily":
            return "DAILY", 0, 0
        if h == "weekly":
            return "WEEKLY", 1, 7
        if h == "monthly":
            return "MONTHLY", 8, 35
        return None, None, None

    async def market_is_open(self) -> tuple[bool, str]:
        if settings.allow_off_hours_scan:
            return True, "OVERRIDE"
        try:
            c = await self.provider.market_clock()
            return bool(c.get("is_open")), c.get("timestamp", "")
        except Exception:
            return False, "تعذر التحقق من حالة السوق"

    async def _news_context(self, symbol: str) -> dict:
        if not settings.news_enabled:
            return {"modifier": 0.0, "severe_negative": False, "headline": None}
        try:
            rows = await self.provider.news(symbol, settings.news_lookback_hours, settings.news_max_items)
        except Exception:
            return {"modifier": 0.0, "severe_negative": False, "headline": None}
        raw = 0
        severe = False
        headline = None
        for item in rows:
            text = f"{item.get('headline','')} {item.get('summary','')}".lower()
            if headline is None and item.get("headline"):
                headline = str(item.get("headline"))
            raw += sum(1 for word in POSITIVE_NEWS if word in text)
            raw -= sum(1 for word in NEGATIVE_NEWS if word in text)
            if any(word in text for word in SEVERE_NEGATIVE):
                severe = True
        cap = float(settings.news_score_cap)
        modifier = max(-cap, min(cap, raw * 1.5))
        return {"modifier": modifier, "severe_negative": severe, "headline": headline}

    async def _analyze(self, symbol: str, trade_type: TradeType, benchmark_return: float | None = None, regime: str | None = None, news_context: dict | None = None):
        swing = "SWING" in trade_type.value
        tf = settings.swing_timeframe if swing else settings.intraday_timeframe
        days = settings.swing_lookback_days if swing else settings.intraday_lookback_days
        minbars = settings.swing_min_bars if swing else settings.intraday_min_bars
        df = await self.provider.bars(symbol, tf, days)
        ok, q = validate_bars(
            df, minbars,
            max_age_minutes=(None if swing else settings.intraday_max_data_age_minutes),
            require_same_ny_date=(not swing),
        )
        if not ok:
            return None, q
        a = self.strategy.analyze(df)

        # Multi-timeframe confirmation: intraday entries must respect daily context.
        mtf_modifier = 0.0
        mtf_label = "N/A"
        if not swing:
            try:
                daily = await self.provider.bars(symbol, settings.confirmation_timeframe, settings.confirmation_lookback_days)
                ok2, _ = validate_bars(daily, 60)
                if ok2:
                    h = self.strategy.analyze(daily)
                    mtf_label = h["direction"]
                    if h["direction"] == a["direction"]:
                        mtf_modifier = 5.0
                        a["reasons"].append("توافق 15m مع الاتجاه اليومي")
                    elif h["direction"] in {"LONG", "SHORT"} and h["direction"] != a["direction"]:
                        mtf_modifier = -8.0
                        a["reasons"].append("تعارض مع الاتجاه اليومي")
            except Exception:
                pass

        # Relative strength against benchmark on the same primary timeframe.
        rs_modifier = 0.0
        rs_value = None
        if benchmark_return is not None:
            rs_value = a.get("return20_pct", 0.0) - benchmark_return
            if a["direction"] == "LONG":
                rs_modifier = max(-settings.relative_strength_weight, min(settings.relative_strength_weight, rs_value * 0.6))
            elif a["direction"] == "SHORT":
                rs_modifier = max(-settings.relative_strength_weight, min(settings.relative_strength_weight, -rs_value * 0.6))
            if rs_modifier >= 2:
                a["reasons"].append(f"قوة نسبية أفضل من السوق {rs_value:+.1f}%")
            elif rs_modifier <= -2:
                a["reasons"].append(f"قوة نسبية أضعف من السوق {rs_value:+.1f}%")

        news = news_context if news_context is not None else await self._news_context(symbol)
        news_modifier = news["modifier"] if a["direction"] == "LONG" else -news["modifier"]
        regime = regime or await MarketRegimeEngine(self.provider).get()
        regime_modifier = 0.0
        if regime == "RANGE":
            regime_modifier -= settings.range_regime_penalty
        elif regime == "BULL":
            regime_modifier += 3.0 if a["direction"] == "LONG" else -5.0
        elif regime == "BEAR":
            regime_modifier += 3.0 if a["direction"] == "SHORT" else -5.0

        a["raw_score"] = a["score"]
        a["score"] = round(max(0.0, min(100.0, a["score"] + mtf_modifier + rs_modifier + news_modifier + regime_modifier)), 1)
        a["market_regime"] = regime
        a["mtf_direction"] = mtf_label
        a["relative_strength"] = round(rs_value, 2) if rs_value is not None else None
        a["news_modifier"] = round(news_modifier, 1)
        a["news_headline"] = news["headline"]
        a["severe_negative_news"] = bool(news["severe_negative"] and a["direction"] == "LONG")
        if news["headline"] and abs(news_modifier) >= 1.5:
            a["reasons"].append(f"Catalyst/News {news_modifier:+.1f}")
        return a, q

    async def _benchmark_return(self, trade_type: TradeType) -> float | None:
        swing = "SWING" in trade_type.value
        tf = settings.swing_timeframe if swing else settings.intraday_timeframe
        days = settings.swing_lookback_days if swing else settings.intraday_lookback_days
        try:
            df = await self.provider.bars("SPY", tf, days)
            ok, _ = validate_bars(
                df, 25,
                max_age_minutes=(None if swing else settings.intraday_max_data_age_minutes),
                require_same_ny_date=(not swing),
            )
            if not ok:
                return None
            a = self.strategy.analyze(df)
            return float(a.get("return20_pct", 0.0))
        except Exception:
            return None

    @staticmethod
    def _market_context(a: dict) -> dict:
        keys = (
            "direction", "market_regime", "adx", "rvol", "atr_pct",
            "atr_regime_ratio", "directional_gap", "trend_active",
            "quality_flags", "scores", "strategy_id", "v20",
        )
        return {k: a.get(k) for k in keys if k in a}

    def _make_signal(self, sym: str, t: TradeType, a: dict, q: str, risk: float) -> Signal:
        p = self.prob.summarize(self.history.all(), t.value)
        invalid = [f"كسر/اختراق مستوى الإبطال {a['stop']:.2f}"]
        if a.get("severe_negative_news"):
            invalid.append("خبر سلبي جوهري حديث")
        gate = a.get("_market_gate") or self.market_gate.evaluate(a).to_dict()
        return Signal(
            sym, t, a["direction"], Decision.READY, a["score"],
            a["entry_low"], a["entry_high"], a["stop"], a["tp1"], a["tp2"], a["tp3"], a["rr"], risk,
            a["reasons"][:8], invalid, list(a["scores"].keys()), a.get("market_regime", "UNKNOWN"),
            SECTOR_MAP.get(sym, "N/A"), q, p["status"], p["samples"], p.get("probability"),
            current_price=a.get("current_price", a.get("last_close")),
            market_timestamp=a.get("current_timestamp"),
            market_age_minutes=a.get("current_age_minutes"),
            market_state=str(gate.get("state", "NORMAL")),
            required_score=float(gate.get("required_score", settings.ready_score_floor)),
            liquidity_state=str(gate.get("liquidity_state", "NORMAL")),
            volatility_state=str(gate.get("volatility_state", "NORMAL")),
            market_context=self._market_context(a),
        )

    async def _stock_candidates(self, stock_types: list[TradeType], *, option_prefilter: bool = False):
        candidates, rejects = [], []
        benchmark_cache = {}
        for t in stock_types:
            benchmark_cache[t.value] = await self._benchmark_return(t)
        try:
            regime = await MarketRegimeEngine(self.provider).get()
        except Exception:
            regime = "UNKNOWN"
        news_cache = {}
        try:
            latest = await self.provider.latest_bars(await self.equity_symbols())
        except Exception:
            latest = {}
        for sym in await self.equity_symbols():
            if sym not in news_cache:
                news_cache[sym] = await self._news_context(sym)
            bar = latest.get(sym, {}) or {}
            current_ts = bar.get("t") or bar.get("timestamp")
            fresh, fresh_reason, fresh_age, fresh_iso = freshness_info(
                current_ts,
                max_age_minutes=settings.intraday_max_data_age_minutes,
                require_same_ny_date=True,
            )
            try:
                current_price = float(bar.get("c")) if bar.get("c") is not None else None
            except (TypeError, ValueError):
                current_price = None
            if not fresh or current_price is None or current_price <= 0:
                rejects.append(f"{sym}: STALE/INVALID current market data — {fresh_reason}")
                continue
            for t in stock_types:
                try:
                    a, q = await self._analyze(
                        sym, t, benchmark_cache.get(t.value), regime, news_cache[sym]
                    )
                    if not a:
                        rejects.append(f"{sym}/{t.value}: {q}"); continue
                    if a["direction"] not in {"LONG", "SHORT"}:
                        rejects.append(f"{sym}/{t.value}: اتجاه محايد"); continue
                    if a.get("severe_negative_news"):
                        rejects.append(f"{sym}/{t.value}: خبر سلبي جوهري حديث"); continue
                    # Reject weak-trend + weak-volume combinations, especially inside RANGE.
                    flags = set(a.get("quality_flags", []))
                    if {"WEAK_ADX", "WEAK_VOLUME"}.issubset(flags):
                        rejects.append(f"{sym}/{t.value}: ADX وحجم تداول ضعيفان"); continue
                    # The strategy may use historical bars for context, but a READY
                    # candidate must still be actionable at a fresh market price.
                    atr = max(float(a.get("atr", 0.0) or 0.0), current_price * 0.002)
                    tolerance = max(0.35 * atr, current_price * 0.0025)
                    if current_price < float(a["entry_low"]) - tolerance or current_price > float(a["entry_high"]) + tolerance:
                        rejects.append(
                            f"{sym}/{t.value}: الخطة التاريخية لم تعد قريبة من السعر الحالي "
                            f"({current_price:.2f})"
                        )
                        continue
                    a["current_price"] = round(current_price, 4)
                    a["current_timestamp"] = fresh_iso
                    a["current_age_minutes"] = round(float(fresh_age or 0.0), 2)
                    gate = self.market_gate.evaluate(a)
                    if gate.blocked:
                        rejects.append(f"{sym}/{t.value}: NO TRADE — {gate.reason}")
                        continue
                    a["_market_gate"] = gate.to_dict()
                    required = gate.required_score
                    if option_prefilter:
                        required = max(85.0, required - settings.dynamic_option_prefilter_margin)
                    ok, risk, reason = self.risk.assess(
                        a["score"], q, a["rr"],
                        required_score=required,
                        risk_cap=gate.risk_cap,
                    )
                    if not ok:
                        rejects.append(f"{sym}/{t.value}: {reason}"); continue
                    candidates.append(self._make_signal(sym, t, a, q, risk))
                except Exception as e:
                    rejects.append(f"{sym}/{t.value}: {type(e).__name__}")
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates, rejects

    async def best_stocks(self, max_results: int = 3):
        types = []
        if settings.enable_stock_intraday: types.append(TradeType.STOCK_INTRADAY)
        if settings.enable_stock_swing: types.append(TradeType.STOCK_SWING)
        c, r = await self._stock_candidates(types)
        # Prefer unique symbols in final ranking.
        out, seen = [], set()
        for s in c:
            if s.symbol in seen: continue
            out.append(s); seen.add(s.symbol)
            if len(out) >= max_results: break
        return out, r

    async def best_stock(self):
        c, r = await self.best_stocks(1)
        return (c[0] if c else None), r

    async def best_equity_options(self, max_results: int = 3, horizon: str | None = None):
        stock_types = []
        horizon_name, horizon_min_dte, horizon_max_dte = self._expiration_horizon(horizon)
        if horizon_name == "MONTHLY":
            if settings.enable_equity_options_swing:
                stock_types.append(TradeType.STOCK_SWING)
        elif horizon_name in {"DAILY", "WEEKLY"}:
            if settings.enable_equity_options_intraday:
                stock_types.append(TradeType.STOCK_INTRADAY)
        else:
            if settings.enable_equity_options_intraday: stock_types.append(TradeType.STOCK_INTRADAY)
            if settings.enable_equity_options_swing: stock_types.append(TradeType.STOCK_SWING)
        bases, rejects = await self._stock_candidates(stock_types, option_prefilter=True)
        out, seen = [], set()
        for base in bases[:10]:
            if base.symbol in seen: continue
            swing = "SWING" in base.trade_type.value
            min_dte = horizon_min_dte if horizon_min_dte is not None else (settings.option_swing_min_dte if swing else settings.option_intraday_min_dte)
            max_dte = horizon_max_dte if horizon_max_dte is not None else (settings.option_swing_max_dte if swing else settings.option_intraday_max_dte)
            opt_type = "call" if base.direction == "LONG" else "put"
            try:
                chain = await self.provider.option_chain(base.symbol, min_dte, max_dte, opt_type)
                underlying_price = (float(base.entry_low) + float(base.entry_high)) / 2
                price_horizon = (horizon_name or ("MONTHLY" if swing else "WEEKLY")).lower()
                max_price = contract_search_rules.get_max_price("equity_option", price_horizon)
                c = self.selector.select(
                    chain, base.direction, base.symbol, underlying_price,
                    min_dte=int(min_dte),
                    max_dte=int(max_dte),
                    max_contract_price=max_price,
                )
                if not c:
                    rejects.append(f"{base.symbol}: لا يوجد عقد يحقق شروط العقد/سلامة البيانات"); continue
                t = TradeType.EQUITY_OPTION_SWING if swing else TradeType.EQUITY_OPTION_INTRADAY
                p = self.prob.summarize(self.history.all(), t.value)
                entry_low, entry_high = c["mid"], c["ask"]
                prem = max(entry_high * 0.22, 0.01)
                stop = round(max(0.01, entry_low - prem), 2)
                tp1, tp2, tp3 = round(entry_high + prem*1.5,2), round(entry_high + prem*2,2), round(entry_high + prem*2.8,2)
                c.update({
                    "entry_low": entry_low, "entry_high": entry_high,
                    "underlying_direction": base.direction,
                    "underlying_entry_low": base.entry_low, "underlying_entry_high": base.entry_high,
                    "underlying_stop": base.stop, "underlying_tp1": base.tp1, "underlying_tp2": base.tp2, "underlying_tp3": base.tp3,
                    "underlying_current_price": base.current_price,
                    "underlying_data_timestamp": getattr(base, "market_timestamp", None),
                    "underlying_data_age_minutes": getattr(base, "market_age_minutes", None),
                    "horizon": horizon_name or ("MONTHLY" if swing else "WEEKLY"),
                })
                score = round(0.62 * base.score + 0.38 * c["contract_score"], 1)
                final_gate = self.market_gate.evaluate(getattr(base, "market_context", None) or {}, c)
                if final_gate.blocked:
                    rejects.append(f"{base.symbol}: NO TRADE — {final_gate.reason}"); continue
                if score < final_gate.required_score:
                    rejects.append(
                        f"{base.symbol}: Unified Score {score:.1f} أقل من الحد الديناميكي {final_gate.required_score:.1f}"
                    ); continue
                s = Signal(
                    base.symbol, t, "LONG", Decision.READY, score, entry_low, entry_high, stop, tp1, tp2, tp3,
                    2.0, min(base.risk_pct, 0.005, final_gate.risk_cap), base.reasons,
                    [f"إبطال التحليل الأساسي عند {base.stop:.2f}"], base.strategies, base.market_regime, base.sector,
                    "LIMITED", p["status"], p["samples"], p.get("probability"), c,
                    current_price=base.current_price,
                    market_state=final_gate.state,
                    required_score=final_gate.required_score,
                    liquidity_state=final_gate.liquidity_state,
                    volatility_state=final_gate.volatility_state,
                    market_context=getattr(base, "market_context", None),
                )
                out.append(s); seen.add(base.symbol)
                if len(out) >= max_results: break
            except Exception as e:
                rejects.append(f"{base.symbol} Options API: {type(e).__name__}")
        return out, rejects

    async def best_equity_option(self, horizon: str | None = None):
        c, r = await self.best_equity_options(1, horizon=horizon)
        return (c[0] if c else None), r

    async def _best_index_options_core(self, max_results: int = 3, horizon: str | None = None):
        index = settings.indices[0] if settings.indices else "SPX"
        proxy = settings.index_analysis_proxy_spx if index == "SPX" else index

        # SPX is evaluated on two independent paths requested by the admin:
        #   1) same-day 0DTE (strict contract/risk gates)
        #   2) normal Swing options
        # Other indices retain the legacy intraday DTE window.
        modes = []
        horizon_name, horizon_min_dte, horizon_max_dte = self._expiration_horizon(horizon)
        if horizon_name:
            modes.append({
                "name": horizon_name,
                "trade_type": TradeType.INDEX_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.INDEX_OPTION_INTRADAY,
                "min_dte": int(horizon_min_dte),
                "max_dte": int(horizon_max_dte),
                "max_spread_pct": settings.spx_0dte_max_spread_pct if horizon_name == "DAILY" else None,
                "min_abs_delta": settings.spx_0dte_min_abs_delta if horizon_name == "DAILY" else None,
                "max_abs_delta": settings.spx_0dte_max_abs_delta if horizon_name == "DAILY" else None,
                "min_contract_score": settings.spx_0dte_min_contract_score if horizon_name == "DAILY" else None,
                "risk_cap": 0.003 if horizon_name == "DAILY" else 0.005,
            })
        elif settings.enable_index_options_intraday:
            if index == "SPX" and settings.enable_spx_0dte:
                modes.append({
                    "name": "0DTE",
                    "trade_type": TradeType.INDEX_OPTION_INTRADAY,
                    "min_dte": 0,
                    "max_dte": 0,
                    "max_spread_pct": settings.spx_0dte_max_spread_pct,
                    "min_abs_delta": settings.spx_0dte_min_abs_delta,
                    "max_abs_delta": settings.spx_0dte_max_abs_delta,
                    "min_contract_score": settings.spx_0dte_min_contract_score,
                    "risk_cap": 0.003,
                })
            else:
                modes.append({
                    "name": "INTRADAY",
                    "trade_type": TradeType.INDEX_OPTION_INTRADAY,
                    "min_dte": settings.option_intraday_min_dte,
                    "max_dte": settings.option_intraday_max_dte,
                    "risk_cap": 0.005,
                })
        # A Telegram-selected horizon is authoritative. Do not silently add
        # the legacy SWING bucket when the admin explicitly requested DAILY,
        # WEEKLY, or MONTHLY. Legacy multi-mode behavior is kept only when no
        # horizon was selected.
        if not horizon_name and settings.enable_index_options_swing:
            modes.append({
                "name": "SWING",
                "trade_type": TradeType.INDEX_OPTION_SWING,
                "min_dte": settings.option_swing_min_dte,
                "max_dte": settings.option_swing_max_dte,
                "risk_cap": 0.005,
            })

        out, rejects = [], []
        try:
            regime = await MarketRegimeEngine(self.provider).get()
        except Exception:
            regime = "UNKNOWN"
        news = await self._news_context(proxy)
        index_current_price = None
        index_data_timestamp = None
        index_data_age = None
        if index == "SPX" and hasattr(self.provider, "public_index_bars"):
            try:
                index_bars = await self.provider.public_index_bars("SPX", "15Min", 2)
                ok_ref, ref_reason = validate_bars(
                    index_bars, 1,
                    max_age_minutes=settings.spx_reference_max_age_minutes,
                    require_same_ny_date=True,
                )
                if not ok_ref:
                    return [], [f"SPX: STALE reference data — {ref_reason}"]
                index_current_price = float(index_bars.iloc[-1]["close"])
                ref_ts = latest_bar_timestamp(index_bars)
                _, _, index_data_age, index_data_timestamp = freshness_info(
                    ref_ts, max_age_minutes=settings.spx_reference_max_age_minutes, require_same_ny_date=True
                )
            except Exception as exc:
                return [], [f"SPX: تعذر الحصول على سعر مرجعي حديث — {type(exc).__name__}"]

        for mode in modes:
            t = mode["trade_type"]
            mode_name = mode["name"]
            try:
                a, q = await self._analyze(proxy, t, None, regime, news)
                if not a or a["direction"] not in {"LONG", "SHORT"}:
                    rejects.append(f"{index}/{mode_name}: اتجاه محايد")
                    continue
                pre_gate = self.market_gate.evaluate(a)
                if pre_gate.blocked:
                    rejects.append(f"{index}/{mode_name}: NO TRADE — {pre_gate.reason}")
                    continue
                pre_required = max(85.0, pre_gate.required_score - settings.dynamic_option_prefilter_margin)
                ok, risk, reason = self.risk.assess(
                    a["score"], q, a["rr"],
                    required_score=pre_required,
                    risk_cap=pre_gate.risk_cap,
                )
                if not ok:
                    rejects.append(f"{index}/{mode_name}: {reason}")
                    continue

                min_dte = int(mode["min_dte"])
                max_dte = int(mode["max_dte"])
                opt_type = "call" if a["direction"] == "LONG" else "put"
                chain = await self.provider.index_option_chain(index, min_dte, max_dte, opt_type)

                # SPX is analyzed with SPY as a directional proxy. Never compare
                # SPX option strikes (~index level) with the SPY share price.
                selector_underlying_price = (
                    None
                    if index == "SPX" and proxy != index
                    else (a["entry_low"] + a["entry_high"]) / 2
                )
                c = self.selector.select(
                    chain,
                    a["direction"],
                    index,
                    selector_underlying_price,
                    min_dte=min_dte,
                    max_dte=max_dte,
                    max_spread_pct=mode.get("max_spread_pct"),
                    min_abs_delta=mode.get("min_abs_delta"),
                    max_abs_delta=mode.get("max_abs_delta"),
                    min_contract_score=mode.get("min_contract_score"),
                    max_contract_price=contract_search_rules.get_max_price(
                        "index_option",
                        (horizon_name or ("DAILY" if mode_name == "0DTE" else "MONTHLY")).lower(),
                    ),
                )
                if not c:
                    if not (chain.get("snapshots") or {}):
                        source = chain.get("_chain_source", "unknown")
                        rejects.append(
                            f"{index}/{mode_name}: لا توجد Snapshots لعقود المؤشر "
                            f"من Alpaca (source={source})"
                        )
                    else:
                        rejects.append(
                            f"{index}/{mode_name}: عقود موجودة لكن لا يوجد عقد "
                            "يحقق شروط السيولة/Delta/Spread"
                        )
                    continue

                p = self.prob.summarize(self.history.all(), t.value)
                entry_low, entry_high = c["mid"], c["ask"]
                prem = max(entry_high * .22, .01)
                stop = round(max(.01, entry_low - prem), 2)
                c.update({
                    "entry_low": entry_low,
                    "entry_high": entry_high,
                    "underlying_direction": a["direction"],
                    "underlying_entry_low": a["entry_low"],
                    "underlying_entry_high": a["entry_high"],
                    "underlying_stop": a["stop"],
                    "underlying_tp1": a["tp1"],
                    "underlying_tp2": a["tp2"],
                    "underlying_tp3": a["tp3"],
                    "analysis_proxy": proxy,
                    "chain_source": chain.get("_chain_source", "underlying_chain"),
                    "dte_mode": mode_name,
                    "horizon": horizon_name or mode_name,
                    "strategy_mode": "SPX_CORE",
                    "underlying_current_price": index_current_price if index_current_price is not None else (a.get("last_close") if proxy == index else None),
                    "underlying_data_timestamp": index_data_timestamp,
                    "underlying_data_age_minutes": round(float(index_data_age or 0.0), 2) if index_data_age is not None else None,
                })
                if mode_name == "0DTE":
                    a["reasons"].append("SPX 0DTE مع فلترة عقد ومخاطرة أكثر تشددًا")

                score = round(0.62 * a["score"] + 0.38 * c["contract_score"], 1)
                final_gate = self.market_gate.evaluate(a, c)
                if final_gate.blocked:
                    rejects.append(f"{index}/{mode_name}: NO TRADE — {final_gate.reason}")
                    continue
                if score < final_gate.required_score:
                    rejects.append(
                        f"{index}/{mode_name}: Unified Score {score:.1f} أقل من الحد الديناميكي {final_gate.required_score:.1f}"
                    )
                    continue

                out.append(Signal(
                    index,
                    t,
                    "LONG",
                    Decision.READY,
                    score,
                    entry_low,
                    entry_high,
                    stop,
                    round(entry_high + prem * 1.5, 2),
                    round(entry_high + prem * 2, 2),
                    round(entry_high + prem * 2.8, 2),
                    2.0,
                    min(risk, float(mode.get("risk_cap", 0.005)), final_gate.risk_cap),
                    a["reasons"],
                    [f"إبطال بنية {proxy} عند {a['stop']:.2f}"],
                    list(a["scores"].keys()),
                    a.get("market_regime", "UNKNOWN"),
                    "INDEX",
                    "LIMITED",
                    p["status"],
                    p["samples"],
                    p.get("probability"),
                    c,
                    current_price=index_current_price if index_current_price is not None else (a.get("last_close") if proxy == index else None),
                    market_timestamp=index_data_timestamp,
                    market_age_minutes=round(float(index_data_age or 0.0), 2) if index_data_age is not None else None,
                    market_state=final_gate.state,
                    required_score=final_gate.required_score,
                    liquidity_state=final_gate.liquidity_state,
                    volatility_state=final_gate.volatility_state,
                    market_context=self._market_context(a),
                ))
            except Exception as e:
                rejects.append(f"{index}/{mode_name}: {type(e).__name__}")

        out.sort(key=lambda x: x.score, reverse=True)
        return out[:max_results], rejects

    async def _analyze_spx_v20(self, proxy: str) -> tuple[dict | None, str]:
        """Run the TradingView-derived SPX V20 engine on Alpaca data.

        SPX price bars are not available through the configured stock feed, so the
        existing project convention is preserved: SPY is the directional/volume
        proxy while the selected contract itself is a real SPX/SPXW option.
        """
        try:
            # Price logic comes from SPX itself; volume comes from SPY exactly as
            # in the supplied TradingView script's volumeProxySymbol.
            primary = await self.provider.public_index_bars("SPX", "15Min", 35)
            spy_volume = await self.provider.bars(proxy, "15Min", 35)
            spy_ok, spy_reason = validate_bars(
                spy_volume, 60,
                max_age_minutes=settings.intraday_max_data_age_minutes,
                require_same_ny_date=True,
            )
            if not spy_ok:
                return None, f"SPY volume proxy STALE — {spy_reason}"
            if not primary.empty and not spy_volume.empty:
                left = primary.copy()
                right = spy_volume[["timestamp", "volume"]].copy()
                left["timestamp"] = pd.to_datetime(left["timestamp"], utc=True, errors="coerce")
                right["timestamp"] = pd.to_datetime(right["timestamp"], utc=True, errors="coerce")
                left = left.sort_values("timestamp")
                right = right.sort_values("timestamp").rename(columns={"volume": "proxy_volume"})
                primary = pd.merge_asof(
                    left, right, on="timestamp", direction="nearest", tolerance=pd.Timedelta(minutes=2)
                )
                primary["volume"] = primary["proxy_volume"].where(
                    primary["proxy_volume"].notna(), primary.get("volume", 0)
                )
                primary = primary.drop(columns=["proxy_volume"], errors="ignore")
            ok, q = validate_bars(
                primary, 220,
                max_age_minutes=settings.spx_reference_max_age_minutes,
                require_same_ny_date=True,
            )
            if not ok:
                return None, q
            mtf = {
                "5": await self.provider.public_index_bars("SPX", "5Min", 14),
                "15": primary,
                "60": await self.provider.public_index_bars("SPX", "1Hour", 90),
                "240": await self.provider.public_index_bars("SPX", "4Hour", 260),
            }
            daily = await self.provider.public_index_bars("SPX", "1Day", 60)
            a = self.spx_v20.analyze(primary, mtf, daily)
            ref_ts = latest_bar_timestamp(primary)
            _, _, ref_age, ref_iso = freshness_info(
                ref_ts, max_age_minutes=settings.spx_reference_max_age_minutes, require_same_ny_date=True
            )
            a["current_timestamp"] = ref_iso
            a["current_age_minutes"] = round(float(ref_age or 0.0), 2)
            a.setdefault("reasons", []).append("SPX price + SPY volume proxy (V20 source logic)")
            return a, q
        except Exception as exc:
            return None, f"SPX V20 DATA ERROR: {type(exc).__name__}: {exc}"

    async def _best_index_options_v20(self, max_results: int = 3, horizon: str | None = None):
        index = settings.indices[0] if settings.indices else "SPX"
        proxy = settings.index_analysis_proxy_spx if index == "SPX" else index
        if index != "SPX":
            return [], ["SPX V20 مخصص لمؤشر SPX فقط"]

        a, q = await self._analyze_spx_v20(proxy)
        if not a:
            return [], [f"SPX/V20: {q}"]
        if a.get("direction") not in {"LONG", "SHORT"}:
            reasons = a.get("reasons") or []
            detail = " | ".join(str(x) for x in reasons[:3])
            return [], [f"SPX/V20: لا توجد READY CALL/PUT الآن" + (f" — {detail}" if detail else "")]

        pre_gate = self.market_gate.evaluate(a)
        if pre_gate.blocked:
            return [], [f"SPX/V20: NO TRADE — {pre_gate.reason}"]
        pre_required = max(85.0, pre_gate.required_score - settings.dynamic_option_prefilter_margin)
        ok, risk, reason = self.risk.assess(
            a["score"], q, a["rr"],
            required_score=pre_required,
            risk_cap=pre_gate.risk_cap,
        )
        if not ok:
            return [], [f"SPX/V20: {reason}"]

        modes = []
        horizon_name, horizon_min_dte, horizon_max_dte = self._expiration_horizon(horizon)
        if horizon_name:
            modes.append({
                "name": horizon_name,
                "trade_type": TradeType.INDEX_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.INDEX_OPTION_INTRADAY,
                "min_dte": int(horizon_min_dte), "max_dte": int(horizon_max_dte),
                "max_spread_pct": settings.spx_0dte_max_spread_pct if horizon_name == "DAILY" else None,
                "min_abs_delta": settings.spx_0dte_min_abs_delta if horizon_name == "DAILY" else None,
                "max_abs_delta": settings.spx_0dte_max_abs_delta if horizon_name == "DAILY" else None,
                "min_contract_score": settings.spx_0dte_min_contract_score if horizon_name == "DAILY" else None,
                "risk_cap": 0.003 if horizon_name == "DAILY" else 0.005,
            })
        elif settings.enable_index_options_intraday and settings.enable_spx_0dte:
            modes.append({
                "name": "0DTE", "trade_type": TradeType.INDEX_OPTION_INTRADAY,
                "min_dte": 0, "max_dte": 0,
                "max_spread_pct": settings.spx_0dte_max_spread_pct,
                "min_abs_delta": settings.spx_0dte_min_abs_delta,
                "max_abs_delta": settings.spx_0dte_max_abs_delta,
                "min_contract_score": settings.spx_0dte_min_contract_score,
                "risk_cap": 0.003,
            })
        # Explicit Telegram horizon wins here too. V20 must not mix the
        # selected DTE bucket with an extra SWING search.
        if not horizon_name and settings.enable_index_options_swing:
            modes.append({
                "name": "SWING", "trade_type": TradeType.INDEX_OPTION_SWING,
                "min_dte": settings.option_swing_min_dte,
                "max_dte": settings.option_swing_max_dte,
                "risk_cap": 0.005,
            })

        out, rejects = [], []
        opt_type = "call" if a["direction"] == "LONG" else "put"
        for mode in modes:
            try:
                chain = await self.provider.index_option_chain(
                    index, int(mode["min_dte"]), int(mode["max_dte"]), opt_type
                )
                c = self.selector.select(
                    chain, a["direction"], index, None,
                    min_dte=int(mode["min_dte"]), max_dte=int(mode["max_dte"]),
                    max_spread_pct=mode.get("max_spread_pct"),
                    min_abs_delta=mode.get("min_abs_delta"),
                    max_abs_delta=mode.get("max_abs_delta"),
                    min_contract_score=mode.get("min_contract_score"),
                    max_contract_price=contract_search_rules.get_max_price(
                        "index_option",
                        (horizon_name or ("DAILY" if mode.get("name") == "0DTE" else "MONTHLY")).lower(),
                    ),
                )
                if not c:
                    rejects.append(f"SPX/V20/{mode['name']}: لا يوجد عقد يحقق شروط السيولة/Delta/Spread")
                    continue

                entry_low, entry_high = c["mid"], c["ask"]
                prem = max(entry_high * .22, .01)
                stop = round(max(.01, entry_low - prem), 2)
                c.update({
                    "entry_low": entry_low, "entry_high": entry_high,
                    "underlying_direction": a["direction"],
                    "underlying_entry_low": a["entry_low"], "underlying_entry_high": a["entry_high"],
                    "underlying_stop": a["stop"],
                    "underlying_tp1": a["tp1"], "underlying_tp2": a["tp2"], "underlying_tp3": a["tp3"],
                    "analysis_proxy": proxy, "chain_source": chain.get("_chain_source", "underlying_chain"),
                    "dte_mode": mode["name"], "horizon": horizon_name or mode["name"], "strategy_mode": "SPX_V20",
                    "v20": a.get("v20", {}),
                    "underlying_current_price": a.get("last_close"),
                    "underlying_data_timestamp": a.get("current_timestamp"),
                    "underlying_data_age_minutes": a.get("current_age_minutes"),
                })
                score = round(0.62 * float(a["score"]) + 0.38 * float(c["contract_score"]), 1)
                final_gate = self.market_gate.evaluate(a, c)
                if final_gate.blocked:
                    rejects.append(f"SPX/V20/{mode['name']}: NO TRADE — {final_gate.reason}")
                    continue
                if score < final_gate.required_score:
                    rejects.append(
                        f"SPX/V20/{mode['name']}: Unified Score {score:.1f} أقل من الحد الديناميكي {final_gate.required_score:.1f}"
                    )
                    continue
                p = self.prob.summarize(self.history.all(), mode["trade_type"].value)
                reasons = ["SPX V20 — ALLUQMANI Radar V2.1"] + list(a.get("reasons", []))
                if mode["name"] == "0DTE":
                    reasons.append("SPX V20 0DTE مع فلترة عقد ومخاطرة مشددة")
                out.append(Signal(
                    index, mode["trade_type"], "LONG", Decision.READY, score,
                    entry_low, entry_high, stop,
                    round(entry_high + prem * 1.5, 2), round(entry_high + prem * 2.0, 2), round(entry_high + prem * 2.8, 2),
                    2.0, min(risk, float(mode.get("risk_cap", 0.005)), final_gate.risk_cap),
                    reasons[:8], [f"إبطال SPX V20/بنية {proxy} عند {a['stop']:.2f}"],
                    ["SPX_V20"] + list(a.get("scores", {}).keys()), a.get("market_regime", "UNKNOWN"),
                    "INDEX", q, p["status"], p["samples"], p.get("probability"), c,
                    current_price=a.get("last_close"),
                    market_timestamp=a.get("current_timestamp"),
                    market_age_minutes=a.get("current_age_minutes"),
                    market_state=final_gate.state,
                    required_score=final_gate.required_score,
                    liquidity_state=final_gate.liquidity_state,
                    volatility_state=final_gate.volatility_state,
                    market_context=self._market_context(a),
                ))
            except Exception as exc:
                rejects.append(f"SPX/V20/{mode['name']}: {type(exc).__name__}")

        out.sort(key=lambda x: x.score, reverse=True)
        return out[:max_results], rejects


    async def _confirmed_equity_bars(self, symbol: str, swing: bool) -> pd.DataFrame:
        tf = settings.swing_timeframe if swing else settings.intraday_timeframe
        days = settings.swing_lookback_days if swing else settings.intraday_lookback_days
        return await self.provider.bars(symbol, tf, days)

    async def best_equity_options_confirmed(self, max_results: int = 3, horizon: str | None = None):
        """Confirmed Setup path. Legacy Equity Options remains untouched.

        Hunter = existing production Equity Options engine. Only candidates that
        already pass its market/contract/risk gates enter structural confirmation.
        Judge = second-stage independent ranking/correlation guard.
        """
        hunter_limit = max(8, int(max_results) * 4)
        bases, rejects = await self.best_equity_options(hunter_limit, horizon=horizon)
        confirmed: list[Signal] = []
        for signal in bases:
            try:
                swing = "SWING" in signal.trade_type.value
                df = await self._confirmed_equity_bars(signal.symbol, swing)
                setup_direction = str((signal.option or {}).get("underlying_direction") or signal.direction)
                setup = self.confirmed_setup.evaluate(df, setup_direction, signal.market_context or {})
                if not setup.ready:
                    rejects.append(f"{signal.symbol}/Confirmed Setup: {setup.state} — {setup.reason}")
                    continue
                option = dict(signal.option or {})
                option.update({
                    "strategy_mode": "CONFIRMED_SETUP",
                    "setup_state": setup.state,
                    "setup_path": setup.path,
                    "setup_breakout_level": setup.breakout_level,
                })
                signal.option = option
                ctx = dict(signal.market_context or {})
                ctx["confirmed_setup"] = setup.to_dict()
                signal.market_context = ctx
                signal.reasons = list(signal.reasons) + [
                    f"Confirmed Setup: {setup.path}",
                    "Structure + Momentum confirmed",
                ]
                confirmed.append(signal)
            except Exception as exc:
                rejects.append(f"{signal.symbol}/Confirmed Setup: {type(exc).__name__}")
        if settings.learning_enabled:
            await self.learning.sync_github_if_due()
        ranked = self.judge.rank(confirmed, max_results=max_results)
        if confirmed and not ranked:
            rejects.append("Confirmed Setup Judge: لا توجد فرصة اجتازت Judge Score 90")
        return ranked, rejects

    async def best_equity_option_confirmed(self, horizon: str | None = None):
        rows, rejects = await self.best_equity_options_confirmed(1, horizon=horizon)
        return (rows[0] if rows else None), rejects

    async def best_index_options_confirmed(self, max_results: int = 3, horizon: str | None = None):
        """Confirmed Setup for SPX, built beside (not inside) SPX Core/V20."""
        hunter_limit = max(6, int(max_results) * 3)
        bases, rejects = await self._best_index_options_core(hunter_limit, horizon=horizon)
        confirmed: list[Signal] = []
        for signal in bases:
            try:
                if hasattr(self.provider, "public_index_bars"):
                    df = await self.provider.public_index_bars("SPX", "15Min", 35)
                else:
                    proxy = settings.index_analysis_proxy_spx if signal.symbol == "SPX" else signal.symbol
                    df = await self.provider.bars(proxy, settings.intraday_timeframe, settings.intraday_lookback_days)
                setup_direction = str((signal.option or {}).get("underlying_direction") or signal.direction)
                setup = self.confirmed_setup.evaluate(df, setup_direction, signal.market_context or {})
                if not setup.ready:
                    rejects.append(f"{signal.symbol}/Confirmed Setup: {setup.state} — {setup.reason}")
                    continue
                option = dict(signal.option or {})
                option.update({
                    "strategy_mode": "CONFIRMED_SETUP",
                    "setup_state": setup.state,
                    "setup_path": setup.path,
                    "setup_breakout_level": setup.breakout_level,
                })
                signal.option = option
                ctx = dict(signal.market_context or {})
                ctx["confirmed_setup"] = setup.to_dict()
                signal.market_context = ctx
                signal.reasons = list(signal.reasons) + [
                    f"Confirmed Setup: {setup.path}",
                    "Structure + Momentum confirmed",
                ]
                confirmed.append(signal)
            except Exception as exc:
                rejects.append(f"{signal.symbol}/Confirmed Setup: {type(exc).__name__}")
        if settings.learning_enabled:
            await self.learning.sync_github_if_due()
        ranked = self.judge.rank(confirmed, max_results=max_results)
        if confirmed and not ranked:
            rejects.append("Confirmed Setup Judge: لا توجد فرصة اجتازت Judge Score 90")
        return ranked, rejects

    async def best_index_option_confirmed(self, horizon: str | None = None):
        rows, rejects = await self.best_index_options_confirmed(1, horizon=horizon)
        return (rows[0] if rows else None), rejects

    async def best_equity_options_waseem(self, max_results: int = 3, horizon: str | None = None):
        """Waseem V1 equity-options path. Legacy engines are not modified."""
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_equity_options_waseem(max_results, horizon="daily")
            weekly, rw = await self.best_equity_options_waseem(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: x.score, reverse=True)
            return combined[:max_results], ["Waseem V1 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        horizon_name, min_dte, max_dte = self._expiration_horizon(horizon)
        horizon_name = horizon_name or "WEEKLY"
        min_dte = 1 if min_dte is None else int(min_dte)
        max_dte = 7 if max_dte is None else int(max_dte)
        stock_type = TradeType.STOCK_SWING if horizon_name == "MONTHLY" else TradeType.STOCK_INTRADAY
        bases, rejects = await self._stock_candidates([stock_type], option_prefilter=True)
        out, seen = [], set()
        for base in bases[:16]:
            if base.symbol in seen:
                continue
            try:
                opt_type = "call" if base.direction == "LONG" else "put"
                chain = await self.provider.option_chain(base.symbol, min_dte, max_dte, opt_type)
                spot = float(base.current_price or ((base.entry_low + base.entry_high) / 2))
                ctx = getattr(base, "market_context", None) or {}
                atr_pct = float(ctx.get("atr_pct", 0.0) or 0.0)
                base_move = max(spot * atr_pct / 100.0, spot * 0.005)
                move_mult = 0.85 if horizon_name == "DAILY" else 1.8 if horizon_name == "WEEKLY" else 3.0
                expected_move = base_move * move_mult
                ranked, diag = self.waseem_selector.rank(
                    chain, base.direction, base.symbol, spot, min_dte=min_dte, max_dte=max_dte, horizon=horizon_name,
                    expected_move=expected_move, max_contract_price=contract_search_rules.get_max_price("equity_option", horizon_name.lower()),
                    is_index=False, max_results=3,
                )
                if not ranked:
                    rejects.append(f"{base.symbol}/{horizon_name}/Waseem V1: " + (", ".join(diag[:5]) or "no eligible near-OTM contract"))
                    continue
                c = ranked[0]
                t = TradeType.EQUITY_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.EQUITY_OPTION_INTRADAY
                entry_low, entry_high = c["mid"], c["ask"]
                prem = max(entry_high * 0.22, 0.01)
                stop = round(max(0.01, entry_low - prem), 2)
                c.update({
                    "entry_low": entry_low, "entry_high": entry_high, "underlying_direction": base.direction,
                    "underlying_entry_low": base.entry_low, "underlying_entry_high": base.entry_high, "underlying_stop": base.stop,
                    "underlying_tp1": base.tp1, "underlying_tp2": base.tp2, "underlying_tp3": base.tp3, "underlying_current_price": spot,
                    "horizon": horizon_name, "dte_mode": horizon_name, "strategy_mode": "WASEEM_V1", "engine_source": "Waseem V1",
                    "waseem_diagnostics": diag[:8], "waseem_alternatives": ranked[1:3],
                })
                final_gate = self.market_gate.evaluate(getattr(base, "market_context", None) or {}, c)
                if final_gate.blocked:
                    rejects.append(f"{base.symbol}/{horizon_name}/Waseem V1: NO TRADE — {final_gate.reason}")
                    continue
                score = round(0.60 * float(base.score) + 0.40 * float(c["contract_score"]), 1)
                required = max(90.0, float(final_gate.required_score))
                if score < required:
                    rejects.append(f"{base.symbol}/{horizon_name}/Waseem V1: Final {score:.1f} < Required {required:.1f}; side={base.direction}")
                    continue
                p = self.prob.summarize(self.history.all(), t.value)
                out.append(Signal(
                    base.symbol, t, "LONG", Decision.READY, score, entry_low, entry_high, stop,
                    round(entry_high + prem*1.5, 2), round(entry_high + prem*2, 2), round(entry_high + prem*2.8, 2), 2.0,
                    min(base.risk_pct, 0.0035 if horizon_name == "DAILY" else 0.005, final_gate.risk_cap),
                    ["Waseem V1 — Momentum-Based Near-OTM Selection"] + list(base.reasons[:7]),
                    [f"إبطال التحليل الأساسي عند {base.stop:.2f}"], base.strategies, base.market_regime, base.sector,
                    "LIMITED", p["status"], p["samples"], p.get("probability"), c, current_price=spot,
                    market_state=final_gate.state, required_score=required, liquidity_state=final_gate.liquidity_state,
                    volatility_state=final_gate.volatility_state, market_context=getattr(base, "market_context", None),
                ))
                seen.add(base.symbol)
            except Exception as exc:
                rejects.append(f"{base.symbol}/{horizon_name}/Waseem V1: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: x.score, reverse=True)
        return out[:max_results], rejects

    async def best_index_options_waseem(self, max_results: int = 3, horizon: str | None = None):
        """Waseem V1 SPX path using actual SPX spot for 10-40 point near-OTM ranking."""
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_index_options_waseem(max_results, horizon="daily")
            weekly, rw = await self.best_index_options_waseem(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: x.score, reverse=True)
            return combined[:max_results], ["Waseem V1 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        index = settings.indices[0] if settings.indices else "SPX"
        proxy = settings.index_analysis_proxy_spx if index == "SPX" else index
        horizon_name, min_dte, max_dte = self._expiration_horizon(horizon)
        horizon_name = horizon_name or "DAILY"
        min_dte = 0 if min_dte is None else int(min_dte)
        max_dte = 0 if max_dte is None else int(max_dte)
        trade_type = TradeType.INDEX_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.INDEX_OPTION_INTRADAY
        try:
            try:
                regime = await MarketRegimeEngine(self.provider).get()
            except Exception:
                regime = "UNKNOWN"
            a, q = await self._analyze(proxy, trade_type, None, regime, await self._news_context(proxy))
            if not a or a.get("direction") not in {"LONG", "SHORT"}:
                return [], [f"{index}/{horizon_name}/Waseem V1: اتجاه محايد"]
            pre_gate = self.market_gate.evaluate(a)
            if pre_gate.blocked:
                return [], [f"{index}/{horizon_name}/Waseem V1: NO TRADE — {pre_gate.reason}"]
            ok, risk, reason = self.risk.assess(a["score"], q, a["rr"], required_score=max(85.0, pre_gate.required_score-5), risk_cap=pre_gate.risk_cap)
            if not ok:
                return [], [f"{index}/{horizon_name}/Waseem V1: {reason}"]
            spot = None; expected_move = None; market_ts = None; market_age = None
            if index == "SPX" and hasattr(self.provider, "public_index_bars"):
                bars = await self.provider.public_index_bars("SPX", "15Min", 2)
                valid, why = validate_bars(bars, 14, max_age_minutes=settings.spx_reference_max_age_minutes, require_same_ny_date=True)
                if not valid:
                    return [], [f"SPX/Waseem V1: STALE reference — {why}"]
                spot = float(bars.iloc[-1]["close"])
                atr15 = float(add_indicators(bars).iloc[-1]["atr"])
                now_ny = pd.Timestamp.now(tz="America/New_York")
                close_ny = now_ny.normalize() + pd.Timedelta(hours=16)
                remaining_15m = max(1.0, (close_ny-now_ny).total_seconds()/900.0) if horizon_name == "DAILY" else 26.0 * max(1, max_dte)
                expected_move = atr15 * (remaining_15m ** 0.5)
                ref_ts = latest_bar_timestamp(bars)
                _, _, market_age, market_ts = freshness_info(ref_ts, max_age_minutes=settings.spx_reference_max_age_minutes, require_same_ny_date=True)
            if not spot:
                spot = float(a.get("last_close") or ((a["entry_low"]+a["entry_high"])/2))
                expected_move = max(spot * float(a.get("atr_pct", 0.5))/100.0, 10.0)
            opt_type = "call" if a["direction"] == "LONG" else "put"
            chain = await self.provider.index_option_chain(index, min_dte, max_dte, opt_type)
            ranked, diag = self.waseem_selector.rank(
                chain, a["direction"], index, spot, min_dte=min_dte, max_dte=max_dte, horizon=horizon_name,
                expected_move=expected_move, max_contract_price=contract_search_rules.get_max_price("index_option", horizon_name.lower()),
                is_index=True, max_results=3,
            )
            if not ranked:
                return [], [f"{index}/{horizon_name}/Waseem V1: " + (", ".join(diag[:6]) or "no eligible near-OTM contract")]
            c = ranked[0]
            entry_low, entry_high = c["mid"], c["ask"]
            prem = max(entry_high * 0.22, 0.01)
            stop = round(max(0.01, entry_low-prem), 2)
            c.update({
                "entry_low": entry_low, "entry_high": entry_high, "underlying_direction": a["direction"],
                "underlying_entry_low": a["entry_low"], "underlying_entry_high": a["entry_high"], "underlying_stop": a["stop"],
                "underlying_tp1": a["tp1"], "underlying_tp2": a["tp2"], "underlying_tp3": a["tp3"],
                "underlying_current_price": spot, "underlying_data_timestamp": market_ts,
                "underlying_data_age_minutes": round(float(market_age or 0), 2) if market_age is not None else None,
                "analysis_proxy": proxy, "horizon": horizon_name, "dte_mode": horizon_name, "strategy_mode": "WASEEM_V1",
                "engine_source": "Waseem V1", "waseem_diagnostics": diag[:8], "waseem_alternatives": ranked[1:3],
            })
            gate = self.market_gate.evaluate(a, c)
            if gate.blocked:
                return [], [f"{index}/{horizon_name}/Waseem V1: NO TRADE — {gate.reason}"]
            score = round(0.60*float(a["score"]) + 0.40*float(c["contract_score"]), 1)
            required = max(90.0, float(gate.required_score))
            if score < required:
                return [], [f"{index}/{horizon_name}/Waseem V1: Final {score:.1f} < Required {required:.1f}; side={a['"'"'direction'"'"']}"]
            p = self.prob.summarize(self.history.all(), trade_type.value)
            sig = Signal(
                index, trade_type, "LONG", Decision.READY, score, entry_low, entry_high, stop,
                round(entry_high+prem*1.5,2), round(entry_high+prem*2,2), round(entry_high+prem*2.8,2), 2.0,
                min(risk, 0.0035 if horizon_name=="DAILY" else 0.005, gate.risk_cap),
                ["Waseem V1 — SPX Momentum-Based Near-OTM"] + list(a.get("reasons", [])[:7]),
                [f"إبطال بنية {proxy} عند {a['"'"'stop'"'"']:.2f}"], list(a.get("scores", {}).keys()), a.get("market_regime", "UNKNOWN"),
                "INDEX", "LIMITED", p["status"], p["samples"], p.get("probability"), c, current_price=spot, market_timestamp=market_ts,
                market_age_minutes=round(float(market_age or 0),2) if market_age is not None else None, market_state=gate.state,
                required_score=required, liquidity_state=gate.liquidity_state, volatility_state=gate.volatility_state, market_context=self._market_context(a),
            )
            return [sig][:max_results], []
        except Exception as exc:
            return [], [f"{index}/{horizon_name}/Waseem V1: {type(exc).__name__}: {exc}"]

    @staticmethod
    def _v2_ready_threshold(analysis: dict, horizon_name: str) -> float:
        """V2 keeps a 90 READY floor but avoids stacking old 92-94 soft states."""
        threshold = 90.0
        regime = str(analysis.get("market_regime", "UNKNOWN")).upper()
        if "RANGE" in regime or "MIXED" in regime:
            threshold = 91.0
        if horizon_name == "DAILY" and float(analysis.get("atr_regime_ratio", 1.0) or 1.0) >= 1.8:
            threshold = max(threshold, 91.0)
        return threshold

    async def best_equity_options_waseem_v2(self, max_results: int = 3, horizon: str | None = None):
        """Waseem V2 Equity: isolated soft-context + strike-efficiency engine."""
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_equity_options_waseem_v2(max_results, horizon="daily")
            weekly, rw = await self.best_equity_options_waseem_v2(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: x.score, reverse=True)
            return combined[:max_results], ["Waseem V2 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        horizon_name, min_dte, max_dte = self._expiration_horizon(horizon)
        horizon_name = horizon_name or "WEEKLY"
        min_dte = 1 if min_dte is None else int(min_dte)
        max_dte = 7 if max_dte is None else int(max_dte)
        stock_type = TradeType.STOCK_SWING if horizon_name == "MONTHLY" else TradeType.STOCK_INTRADAY
        benchmark = await self._benchmark_return(stock_type)
        try:
            regime = await MarketRegimeEngine(self.provider).get()
        except Exception:
            regime = "UNKNOWN"
        try:
            latest = await self.provider.latest_bars(await self.equity_symbols())
        except Exception:
            latest = {}
        out, rejects = [], []
        for sym in await self.equity_symbols():
            try:
                bar = latest.get(sym, {}) or {}
                ts = bar.get("t") or bar.get("timestamp")
                fresh, fresh_reason, fresh_age, fresh_iso = freshness_info(
                    ts, max_age_minutes=settings.intraday_max_data_age_minutes, require_same_ny_date=True
                )
                current_price = float(bar.get("c")) if bar.get("c") is not None else 0.0
                if not fresh or current_price <= 0:
                    rejects.append(f"{sym}/{horizon_name}/Waseem V2: STALE/INVALID — {fresh_reason}")
                    continue
                news = await self._news_context(sym)
                a, q = await self._analyze(sym, stock_type, benchmark, regime, news)
                if not a or a.get("direction") not in {"LONG", "SHORT"}:
                    rejects.append(f"{sym}/{horizon_name}/Waseem V2: WATCH — direction not confirmed")
                    continue
                # Wider proximity tolerance than legacy Core; V2 can wait/recheck instead of killing a setup early.
                atr = max(float(a.get("atr", 0.0) or 0.0), current_price * 0.002)
                tolerance = max(0.65 * atr, current_price * 0.004)
                if current_price < float(a["entry_low"]) - tolerance or current_price > float(a["entry_high"]) + tolerance:
                    rejects.append(f"{sym}/{horizon_name}/Waseem V2: WATCH — price away from setup zone")
                    continue
                a["current_price"] = current_price
                context = await self.waseem_v2_context.equity_context(sym, SECTOR_MAP.get(sym), a, a["direction"])
                economic = await self.economic_context.equity_context(sym)
                ctx_mod = float(context.get("modifier", 0.0) or 0.0) + float(economic.get("modifier", 0.0) or 0.0)
                opt_type = "call" if a["direction"] == "LONG" else "put"
                chain = await self.provider.option_chain(sym, min_dte, max_dte, opt_type)
                atr_pct = float(a.get("atr_pct", 0.0) or 0.0)
                base_move = max(current_price * atr_pct / 100.0, current_price * 0.005)
                move_mult = 0.85 if horizon_name == "DAILY" else 1.8 if horizon_name == "WEEKLY" else 3.0
                expected_move = base_move * move_mult
                ranked, diag = self.waseem_v2_selector.rank(
                    chain, a["direction"], sym, current_price,
                    min_dte=min_dte, max_dte=max_dte, horizon=horizon_name,
                    expected_move=expected_move,
                    max_contract_price=contract_search_rules.get_max_price("equity_option", horizon_name.lower()),
                    is_index=False, max_results=5,
                )
                if not ranked:
                    rejects.append(f"{sym}/{horizon_name}/Waseem V2: " + (", ".join(diag[:5]) or "no executable contract"))
                    continue
                c = ranked[0]
                # Hard execution veto only for genuinely poor quotes. Normal spread differences stay in Strike Efficiency.
                if float(c.get("spread_pct", 99) or 99) > 12.0:
                    rejects.append(f"{sym}/{horizon_name}/Waseem V2: NO TRADE — untradable spread")
                    continue
                score = round(max(0.0, min(100.0, 0.60 * float(a["score"]) + 0.40 * float(c["contract_score"]) + ctx_mod)), 1)
                required = self._v2_ready_threshold(a, horizon_name)
                if score < required:
                    state = "WATCH" if score >= 84.0 else "NO TRADE"
                    rejects.append(f"{sym}/{horizon_name}/Waseem V2: {state} {score:.1f} < {required:.1f}; context={ctx_mod:+.1f}")
                    continue
                entry_low, entry_high = c["mid"], c["ask"]
                prem = max(entry_high * 0.22, 0.01)
                stop = round(max(0.01, entry_low - prem), 2)
                risk = min(0.0035 if horizon_name == "DAILY" else 0.005, settings.max_risk_per_trade)
                c.update({
                    "entry_low": entry_low, "entry_high": entry_high, "underlying_direction": a["direction"],
                    "underlying_entry_low": a["entry_low"], "underlying_entry_high": a["entry_high"], "underlying_stop": a["stop"],
                    "underlying_tp1": a["tp1"], "underlying_tp2": a["tp2"], "underlying_tp3": a["tp3"],
                    "underlying_current_price": current_price, "underlying_data_timestamp": fresh_iso,
                    "underlying_data_age_minutes": round(float(fresh_age or 0), 2),
                    "horizon": horizon_name, "dte_mode": horizon_name, "strategy_mode": "WASEEM_V2", "engine_source": "Waseem V2",
                    "waseem_diagnostics": diag[:8], "waseem_alternatives": ranked[1:4],
                    "market_context_lines": context.get("lines", []) + economic.get("lines", []) + [f"NEWS: {'AVAILABLE — ' + str(news.get('headline'))[:90] if news.get('headline') else 'UNAVAILABLE'}"],
                    "market_context_source": f"{context.get('source')} + FRED + Alpha Vantage", "market_context_modifier": round(ctx_mod, 2),
                })
                p = self.prob.summarize(self.history.all(), (TradeType.EQUITY_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.EQUITY_OPTION_INTRADAY).value)
                t = TradeType.EQUITY_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.EQUITY_OPTION_INTRADAY
                reasons = ["Waseem V2 — Soft Context + Regime Transition + Strike Efficiency"] + list(a.get("reasons", [])[:5]) + [f"Context {ctx_mod:+.1f}: " + ", ".join(context.get("notes", [])[:3])]
                out.append(Signal(
                    sym, t, "LONG", Decision.READY, score, entry_low, entry_high, stop,
                    round(entry_high + prem*1.5,2), round(entry_high + prem*2,2), round(entry_high + prem*2.8,2), 2.0,
                    risk, reasons, [f"إبطال التحليل الأساسي عند {a['stop']:.2f}"], list(a.get("scores", {}).keys()),
                    a.get("market_regime", "UNKNOWN"), SECTOR_MAP.get(sym, "N/A"), "LIMITED", p["status"], p["samples"], p.get("probability"), c,
                    current_price=current_price, market_timestamp=fresh_iso, market_age_minutes=round(float(fresh_age or 0),2),
                    market_state="WASEEM_V2_SOFT_CONTEXT", required_score=required,
                    liquidity_state="HIGH" if float(c.get("spread_pct",99)) <= 3 else "NORMAL",
                    volatility_state="NORMAL", market_context={**self._market_context(a), "waseem_v2": context, "economic": economic},
                ))
            except Exception as exc:
                rejects.append(f"{sym}/{horizon_name}/Waseem V2: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: x.score, reverse=True)
        return out[:max_results], rejects

    async def best_index_options_waseem_v2(self, max_results: int = 3, horizon: str | None = None):
        """Waseem V2 SPX/SPXW: market context + reversal-aware soft scoring + strike efficiency."""
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_index_options_waseem_v2(max_results, horizon="daily")
            weekly, rw = await self.best_index_options_waseem_v2(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: x.score, reverse=True)
            return combined[:max_results], ["Waseem V2 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        index = settings.indices[0] if settings.indices else "SPX"
        proxy = settings.index_analysis_proxy_spx if index == "SPX" else index
        horizon_name, min_dte, max_dte = self._expiration_horizon(horizon)
        horizon_name = horizon_name or "DAILY"
        min_dte = 0 if min_dte is None else int(min_dte)
        max_dte = 0 if max_dte is None else int(max_dte)
        t = TradeType.INDEX_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.INDEX_OPTION_INTRADAY
        try:
            try:
                regime = await MarketRegimeEngine(self.provider).get()
            except Exception:
                regime = "UNKNOWN"
            news = await self._news_context(proxy)
            a, q = await self._analyze(proxy, t, None, regime, news)
            if not a or a.get("direction") not in {"LONG", "SHORT"}:
                return [], [f"{index}/{horizon_name}/Waseem V2: WATCH — direction not confirmed"]
            context = await self.waseem_v2_context.index_context(a, a["direction"])
            economic = await self.economic_context.index_context()
            ctx_mod = float(context.get("modifier", 0.0) or 0.0) + float(economic.get("modifier", 0.0) or 0.0)
            if index == "SPX" and hasattr(self.provider, "public_index_bars"):
                bars = await self.provider.public_index_bars("SPX", "15Min", 2)
                valid, why = validate_bars(bars, 14, max_age_minutes=settings.spx_reference_max_age_minutes, require_same_ny_date=True)
                if not valid:
                    return [], [f"SPX/{horizon_name}/Waseem V2: STALE underlying — {why}"]
                spot = float(bars.iloc[-1]["close"])
                atr15 = float(add_indicators(bars).iloc[-1]["atr"])
                now_ny = pd.Timestamp.now(tz="America/New_York")
                close_ny = now_ny.normalize() + pd.Timedelta(hours=16)
                remaining_15m = max(1.0, (close_ny-now_ny).total_seconds()/900.0) if horizon_name == "DAILY" else 26.0 * max(1, max_dte)
                expected_move = atr15 * (remaining_15m ** 0.5)
                ref_ts = latest_bar_timestamp(bars)
                _, _, market_age, market_ts = freshness_info(ref_ts, max_age_minutes=settings.spx_reference_max_age_minutes, require_same_ny_date=True)
            else:
                spot = float(a.get("last_close") or ((a["entry_low"]+a["entry_high"])/2))
                expected_move = max(spot * float(a.get("atr_pct",0.5))/100.0, 10.0)
                market_age, market_ts = None, None
            opt_type = "call" if a["direction"] == "LONG" else "put"
            chain = await self.provider.index_option_chain(index, min_dte, max_dte, opt_type)
            ranked, diag = self.waseem_v2_selector.rank(
                chain, a["direction"], index, spot, min_dte=min_dte, max_dte=max_dte, horizon=horizon_name,
                expected_move=expected_move, max_contract_price=contract_search_rules.get_max_price("index_option", horizon_name.lower()),
                is_index=True, max_results=5,
            )
            if not ranked:
                source = chain.get("_chain_source", "unknown") if isinstance(chain, dict) else "unknown"
                return [], [f"{index}/{horizon_name}/Waseem V2: no executable near-OTM contract; source={source}; " + ", ".join(diag[:5])]
            c = ranked[0]
            if float(c.get("spread_pct",99) or 99) > 12.0:
                return [], [f"{index}/{horizon_name}/Waseem V2: NO TRADE — untradable spread"]
            score = round(max(0.0, min(100.0, 0.60 * float(a["score"]) + 0.40 * float(c["contract_score"]) + ctx_mod)), 1)
            required = self._v2_ready_threshold(a, horizon_name)
            if score < required:
                state = "WATCH" if score >= 84.0 else "NO TRADE"
                return [], [f"{index}/{horizon_name}/Waseem V2: {state} {score:.1f} < {required:.1f}; context={ctx_mod:+.1f}"]
            entry_low, entry_high = c["mid"], c["ask"]
            prem = max(entry_high * 0.22, 0.01)
            stop = round(max(0.01, entry_low-prem), 2)
            c.update({
                "entry_low": entry_low, "entry_high": entry_high, "underlying_direction": a["direction"],
                "underlying_entry_low": a["entry_low"], "underlying_entry_high": a["entry_high"], "underlying_stop": a["stop"],
                "underlying_tp1": a["tp1"], "underlying_tp2": a["tp2"], "underlying_tp3": a["tp3"],
                "underlying_current_price": spot, "underlying_data_timestamp": market_ts,
                "underlying_data_age_minutes": round(float(market_age or 0),2) if market_age is not None else None,
                "analysis_proxy": proxy, "horizon": horizon_name, "dte_mode": horizon_name,
                "strategy_mode": "WASEEM_V2", "engine_source": "Waseem V2",
                "waseem_diagnostics": diag[:8], "waseem_alternatives": ranked[1:4],
                "market_context_lines": context.get("lines", []) + economic.get("lines", []) + [f"NEWS: {'AVAILABLE — ' + str(news.get('headline'))[:90] if news.get('headline') else 'UNAVAILABLE'}", "NYSE TICK: UNAVAILABLE", "GEX: UNAVAILABLE", "Options Flow: UNAVAILABLE"],
                "market_context_source": f"{context.get('source')} + FRED", "market_context_modifier": round(ctx_mod, 2),
            })
            p = self.prob.summarize(self.history.all(), t.value)
            reasons = ["Waseem V2 — SPX Context + Regime Transition + Strike Efficiency"] + list(a.get("reasons", [])[:5]) + [f"Context {ctx_mod:+.1f}: " + ", ".join(context.get("notes", [])[:3])]
            sig = Signal(
                index, t, "LONG", Decision.READY, score, entry_low, entry_high, stop,
                round(entry_high+prem*1.5,2), round(entry_high+prem*2,2), round(entry_high+prem*2.8,2), 2.0,
                min(0.0035 if horizon_name == "DAILY" else 0.005, settings.max_risk_per_trade),
                reasons, [f"إبطال بنية {proxy} عند {a['stop']:.2f}"], list(a.get("scores",{}).keys()), a.get("market_regime","UNKNOWN"),
                "INDEX", "LIMITED", p["status"], p["samples"], p.get("probability"), c, current_price=spot,
                market_timestamp=market_ts, market_age_minutes=round(float(market_age or 0),2) if market_age is not None else None,
                market_state="WASEEM_V2_SOFT_CONTEXT", required_score=required,
                liquidity_state="HIGH" if float(c.get("spread_pct",99)) <= 3 else "NORMAL", volatility_state="NORMAL",
                market_context={**self._market_context(a), "waseem_v2": context, "economic": economic},
            )
            return [sig][:max_results], []
        except Exception as exc:
            return [], [f"{index}/{horizon_name}/Waseem V2: {type(exc).__name__}: {exc}"]


    @staticmethod
    def spx_option_session_status(now_ny=None) -> dict:
        """Session gate used only by Waseem V3 SPX/SPXW.

        Cboe SPX GTH is modeled as 20:15-09:25 ET and RTH as 09:30-16:15 ET.
        The returned object also exposes concrete ET/KSA boundaries and the
        effective trade date so Telegram/status diagnostics are auditable.
        """
        from datetime import datetime, time as dtime, timedelta
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        ksa = ZoneInfo("Asia/Riyadh")
        now_ny = now_ny or datetime.now(ny)
        if now_ny.tzinfo is None:
            now_ny = now_ny.replace(tzinfo=ny)
        else:
            now_ny = now_ny.astimezone(ny)
        t = now_ny.timetz().replace(tzinfo=None)
        weekday = now_ny.weekday()

        def at(day, hh, mm):
            return datetime.combine(day, dtime(hh, mm), tzinfo=ny)

        base = {
            "timestamp": now_ny.isoformat(),
            "timestamp_ksa": now_ny.astimezone(ksa).isoformat(),
            "cash_spx_state": "PREVIOUS_CLOSE",
            "session_start_et": None, "session_end_et": None,
            "session_start_ksa": None, "session_end_ksa": None,
            "trade_date": None,
        }
        # Weekend guard. Sunday evening GTH belongs to Monday's trade date.
        if weekday == 5 or (weekday == 6 and t < dtime(20, 15)) or (weekday == 4 and t >= dtime(20, 15)):
            return {**base, "open": False, "session": "CLOSED"}

        if t >= dtime(20, 15) or t <= dtime(9, 25):
            if t >= dtime(20, 15):
                start_day = now_ny.date()
                trade_day = start_day + timedelta(days=1)
            else:
                trade_day = now_ny.date()
                start_day = trade_day - timedelta(days=1)
            start_dt = at(start_day, 20, 15)
            end_dt = at(trade_day, 9, 25)
            return {
                **base, "open": True, "session": "GTH", "trade_date": trade_day.isoformat(),
                "session_start_et": start_dt.isoformat(), "session_end_et": end_dt.isoformat(),
                "session_start_ksa": start_dt.astimezone(ksa).isoformat(),
                "session_end_ksa": end_dt.astimezone(ksa).isoformat(),
            }

        if dtime(9, 30) <= t <= dtime(16, 15):
            start_dt = at(now_ny.date(), 9, 30)
            end_dt = at(now_ny.date(), 16, 15)
            return {
                **base, "open": True, "session": "RTH", "cash_spx_state": "LIVE",
                "trade_date": now_ny.date().isoformat(),
                "session_start_et": start_dt.isoformat(), "session_end_et": end_dt.isoformat(),
                "session_start_ksa": start_dt.astimezone(ksa).isoformat(),
                "session_end_ksa": end_dt.astimezone(ksa).isoformat(),
            }

        # 09:25-09:30 or post-RTH/pre-GTH gap.
        return {**base, "open": False, "session": "SESSION_BREAK", "trade_date": now_ny.date().isoformat()}

    @staticmethod
    def _snapshot_for_contract(chain: dict, symbol: str) -> dict:
        return ((chain or {}).get("snapshots") or {}).get(symbol) or {}

    async def spx_gth_data_diagnostics(self, force: bool = False) -> dict:
        """Probe the *actual* SPX/SPXW option-data feed and cash reference.

        This is observational only: no order/trading action is performed. The
        result explicitly separates Cboe session state from Alpaca data-feed
        availability, because a GTH session may be open while the configured
        indicative feed is delayed, stale, or unavailable.
        """
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        if not force and self._spx_gth_diag_cache is not None and (time.monotonic() - self._spx_gth_diag_cache_at) < 60.0:
            return dict(self._spx_gth_diag_cache)

        session = self.spx_option_session_status()
        now_utc = datetime.now(timezone.utc)
        now_ny = now_utc.astimezone(ZoneInfo("America/New_York"))
        out = {
            "session": session.get("session"),
            "session_open": bool(session.get("open")),
            "trade_date": session.get("trade_date"),
            "session_start_et": session.get("session_start_et"),
            "session_end_et": session.get("session_end_et"),
            "session_start_ksa": session.get("session_start_ksa"),
            "session_end_ksa": session.get("session_end_ksa"),
            "checked_at": now_utc.isoformat(),
            "checked_at_ny": now_ny.isoformat(),
            "cash_spx_state": session.get("cash_spx_state"),
            "options_feed": str(settings.alpaca_options_feed).upper(),
            "option_data_status": "UNAVAILABLE",
            "latest_quote_status": "UNAVAILABLE",
            "latest_trade_status": "UNAVAILABLE",
            "chain_source": "unknown",
            "snapshot_count": 0,
            "quote_count": 0,
            "trade_count": 0,
            "latest_quote_time": None,
            "latest_quote_age_minutes": None,
            "latest_quote_contract": None,
            "latest_quote_bid": None,
            "latest_quote_ask": None,
            "latest_trade_time": None,
            "latest_trade_age_minutes": None,
            "latest_trade_contract": None,
            "latest_trade_price": None,
            "cash_last_point_time": None,
            "cash_last_point_age_minutes": None,
            "cash_last_point_price": None,
            "cash_last_point_session": "UNAVAILABLE",
            "errors": [],
        }

        def parse_dt(value):
            if not value:
                return None
            try:
                dt = pd.to_datetime(value, utc=True, errors="coerce")
                if pd.isna(dt):
                    return None
                return dt.to_pydatetime()
            except Exception:
                return None

        # Probe both daily and weekly windows so a weekend/holiday/expiry edge
        # does not incorrectly make the feed look unavailable.
        chains = []
        for lo, hi in ((0, 0), (1, 7)):
            try:
                chain = await self.provider.index_option_chain("SPX", lo, hi, None)
                if isinstance(chain, dict):
                    chains.append(chain)
            except Exception as exc:
                out["errors"].append(f"option_chain_{lo}_{hi}:{type(exc).__name__}")

        snaps = {}
        sources = []
        for chain in chains:
            snaps.update(chain.get("snapshots") or {})
            src = chain.get("_chain_source")
            if src and src not in sources:
                sources.append(str(src))
        out["chain_source"] = "+".join(sources) if sources else "unavailable"
        out["snapshot_count"] = len(snaps)

        latest_q = None
        latest_t = None
        for symbol, snap in snaps.items():
            q = (snap or {}).get("latestQuote") or (snap or {}).get("latest_quote") or {}
            qdt = parse_dt(q.get("t") or q.get("timestamp") or q.get("time"))
            if qdt:
                out["quote_count"] += 1
                if latest_q is None or qdt > latest_q[0]:
                    latest_q = (qdt, symbol, q)
            tr = (snap or {}).get("latestTrade") or (snap or {}).get("latest_trade") or {}
            tdt = parse_dt(tr.get("t") or tr.get("timestamp") or tr.get("time"))
            if tdt:
                out["trade_count"] += 1
                if latest_t is None or tdt > latest_t[0]:
                    latest_t = (tdt, symbol, tr)

        if latest_q:
            qdt, sym, q = latest_q
            age = max(0.0, (now_utc - qdt).total_seconds() / 60.0)
            out.update({
                "latest_quote_time": qdt.isoformat(),
                "latest_quote_age_minutes": round(age, 1),
                "latest_quote_contract": sym,
                "latest_quote_bid": q.get("bp", q.get("bid_price")),
                "latest_quote_ask": q.get("ap", q.get("ask_price")),
            })
        if latest_t:
            tdt, sym, tr = latest_t
            age = max(0.0, (now_utc - tdt).total_seconds() / 60.0)
            out.update({
                "latest_trade_time": tdt.isoformat(),
                "latest_trade_age_minutes": round(age, 1),
                "latest_trade_contract": sym,
                "latest_trade_price": tr.get("p", tr.get("price")),
            })

        # Classify quote and trade freshness independently.  Alpaca's free
        # indicative feed can expose a delayed latestTrade while latestQuote
        # remains from the prior RTH session.  One stale quote must therefore
        # not hide a usable delayed GTH trade.
        def freshness(age):
            if age is None:
                return "UNAVAILABLE"
            if age <= 5:
                return "AVAILABLE"
            if age <= 25:
                return "DELAYED"
            return "STALE"

        qstatus = freshness(out.get("latest_quote_age_minutes"))
        tstatus = freshness(out.get("latest_trade_age_minutes"))
        out["latest_quote_status"] = qstatus
        out["latest_trade_status"] = tstatus

        usable = {"AVAILABLE", "DELAYED"}
        if qstatus in usable and tstatus in usable:
            # Keep the more conservative label when both are usable.
            out["option_data_status"] = "DELAYED" if "DELAYED" in {qstatus, tstatus} else "AVAILABLE"
        elif qstatus in usable or tstatus in usable:
            out["option_data_status"] = "PARTIAL"
        elif qstatus == "STALE" or tstatus == "STALE" or out.get("snapshot_count", 0) > 0:
            out["option_data_status"] = "STALE"
        else:
            out["option_data_status"] = "UNAVAILABLE"

        try:
            bars = await self.provider.public_index_bars("SPX", "15Min", 5)
            if bars is not None and not bars.empty:
                row = bars.iloc[-1]
                ts = row.get("timestamp") if "timestamp" in bars.columns else bars.index[-1]
                dt = parse_dt(ts)
                px = row.get("close")
                if dt:
                    age = max(0.0, (now_utc - dt).total_seconds() / 60.0)
                    dt_ny = dt.astimezone(ZoneInfo("America/New_York"))
                    out["cash_last_point_time"] = dt.isoformat()
                    out["cash_last_point_age_minutes"] = round(age, 1)
                    out["cash_last_point_price"] = round(float(px), 2) if px is not None else None
                    in_cash_hours = (
                        dt_ny.date() == now_ny.date()
                        and ((dt_ny.hour == 9 and dt_ny.minute >= 30) or 10 <= dt_ny.hour < 16)
                    )
                    if session.get("session") == "GTH" and in_cash_hours:
                        out["cash_last_point_session"] = f"PREVIOUS_RTH_{dt_ny.date().isoformat()}"
                    elif in_cash_hours:
                        out["cash_last_point_session"] = "CURRENT_RTH"
                    else:
                        out["cash_last_point_session"] = f"PREVIOUS_SESSION_{dt_ny.date().isoformat()}"
        except Exception as exc:
            out["errors"].append(f"cash_spx:{type(exc).__name__}")

        self._spx_gth_diag_cache = dict(out)
        self._spx_gth_diag_cache_at = time.monotonic()
        return out

    @staticmethod
    def _v3_signal_direction_from_context(context: dict) -> tuple[str | None, float, list[str]]:
        """Futures-led direction used during SPX Global Trading Hours."""
        data = (context or {}).get("data") or {}
        weights = {"ES": 4.0, "NQ": 2.0, "YM": 1.0, "RTY": 1.0}
        value = 0.0
        available_weight = 0.0
        notes = []
        for label, weight in weights.items():
            row = data.get(label) or {}
            if row.get("status") not in {"AVAILABLE", "DELAYED"}:
                notes.append(f"{label}=UNAVAILABLE")
                continue
            chg = float(row.get("change_pct") or 0.0)
            value += chg * weight
            available_weight += weight
            notes.append(f"{label}={chg:+.2f}%/{row.get('status')}")
        if available_weight < 4.0:
            return None, 0.0, notes
        normalized = value / available_weight
        if normalized >= 0.08:
            return "LONG", normalized, notes
        if normalized <= -0.08:
            return "SHORT", normalized, notes
        return None, normalized, notes

    async def best_equity_options_waseem_v3(self, max_results: int = 3, horizon: str | None = None):
        """Waseem V3 Equity: V2 setup/contract quality + V3 entry efficiency/anti-chase."""
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_equity_options_waseem_v3(max_results, horizon="daily")
            weekly, rw = await self.best_equity_options_waseem_v3(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: (str(x.decision.value) == "READY", x.score), reverse=True)
            return combined[:max_results], ["Waseem V3 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        # V3 deliberately reuses V2's setup and strike logic without modifying it.
        bases, rejects = await self.best_equity_options_waseem_v2(max_results=max(8, max_results * 3), horizon=horizon)
        out = []
        for sig in bases:
            try:
                c = dict(sig.option or {})
                symbol = c.get("symbol")
                horizon_name = str(c.get("horizon") or c.get("dte_mode") or "WEEKLY").upper()
                min_dte = int(c.get("dte") or 0)
                max_dte = min_dte
                opt_type = "call" if str(c.get("type", "")).upper() == "CALL" else "put"
                chain = await self.provider.option_chain(sig.symbol, min_dte, max_dte, opt_type)
                snap = self._snapshot_for_contract(chain, symbol)
                plan = self.waseem_v3_entry.evaluate(c, snap, horizon=horizon_name)
                c.update({
                    "strategy_mode": "WASEEM_V3", "engine_source": "Waseem V3",
                    "entry_state": plan.state, "entry_quality": plan.entry_quality,
                    "current_contract_price": plan.current_price,
                    "preferred_entry_low": plan.entry_low, "preferred_entry_high": plan.entry_high,
                    "watch_reason": plan.reason, "chase_risk": plan.chase_risk,
                    "entry_diagnostics": plan.diagnostics,
                    "market_context_lines": list(c.get("market_context_lines") or []) + [
                        "V3 Entry Engine: AVAILABLE",
                        f"V3 Entry State: {plan.state}",
                        f"V3 Entry Quality: {plan.entry_quality}/100",
                        f"V3 Chase Risk: {'YES' if plan.chase_risk else 'NO'}",
                    ],
                })
                prem = max(plan.entry_high * 0.22, 0.01)
                sig.option = c
                sig.entry_low = plan.entry_low
                sig.entry_high = plan.entry_high
                sig.stop = round(max(0.01, plan.entry_low - prem), 2)
                sig.tp1 = round(plan.entry_high + prem * 1.5, 2)
                sig.tp2 = round(plan.entry_high + prem * 2.0, 2)
                sig.tp3 = round(plan.entry_high + prem * 2.8, 2)
                sig.decision = Decision.READY if plan.state == "READY" else Decision.WATCH
                sig.market_state = "WASEEM_V3_ENTRY_READY" if plan.state == "READY" else "WASEEM_V3_WATCH_ENTRY"
                sig.reasons = ["Waseem V3 — V2 setup preserved + Entry Efficiency/Anti-Chase"] + list(sig.reasons[:6]) + [plan.reason]
                ctx = dict(sig.market_context or {})
                ctx["waseem_v3_entry"] = plan.to_dict()
                sig.market_context = ctx
                out.append(sig)
            except Exception as exc:
                rejects.append(f"{sig.symbol}/Waseem V3 Entry: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
        return out[:max_results], rejects

    async def best_index_options_waseem_v3(self, max_results: int = 3, horizon: str | None = None):
        """Waseem V3 SPX/SPXW with GTH support and V3 entry efficiency.

        RTH uses the existing V2 setup. GTH does NOT pretend the cash SPX is live:
        it derives direction from futures, uses previous SPX cash as a reference,
        and requires an actually usable SPX/SPXW option quote before creating an
        entry or WATCH candidate.
        """
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_index_options_waseem_v3(max_results, horizon="daily")
            weekly, rw = await self.best_index_options_waseem_v3(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
            return combined[:max_results], ["Waseem V3 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw

        session = self.spx_option_session_status()
        if not session.get("open"):
            return [], [f"SPX/Waseem V3: session {session.get('session')} — options scan closed"]
        if session.get("session") == "RTH":
            bases, rejects = await self.best_index_options_waseem_v2(max_results=max(5, max_results * 2), horizon=horizon)
            if not bases:
                return [], rejects
        else:
            # GTH: futures-led setup. Cash SPX is a previous-close reference only.
            horizon_name, min_dte, max_dte = self._expiration_horizon(horizon)
            horizon_name = horizon_name or "DAILY"
            min_dte = 0 if min_dte is None else int(min_dte)
            max_dte = 0 if max_dte is None else int(max_dte)
            dummy = {"scores": {}, "reasons": [], "relative_strength": None}
            context = await self.waseem_v2_context.index_context(dummy, "LONG")
            direction, futures_move, direction_notes = self._v3_signal_direction_from_context(context)
            if direction is None:
                return [], ["SPX/Waseem V3 GTH: WATCH — futures direction not strong/available enough: " + ", ".join(direction_notes)]
            # Recompute context for the actual side so VIX/DXY/yields modifier is side-aware.
            context = await self.waseem_v2_context.index_context(dummy, direction)
            economic = await self.economic_context.index_context()
            try:
                bars = await self.provider.public_index_bars("SPX", "15Min", 5)
                if bars is None or bars.empty:
                    return [], ["SPX/Waseem V3 GTH: previous cash SPX reference UNAVAILABLE"]
                bars_i = add_indicators(bars)
                previous_close = float(bars.iloc[-1]["close"])
                ref_ts = latest_bar_timestamp(bars)
                _, _, cash_age, cash_ts = freshness_info(ref_ts, max_age_minutes=10000, require_same_ny_date=False)
                atr15 = float(bars_i.iloc[-1].get("atr") or max(previous_close * 0.002, 10.0))
            except Exception as exc:
                return [], [f"SPX/Waseem V3 GTH: cash reference error {type(exc).__name__}"]
            es = (context.get("data") or {}).get("ES") or {}
            es_change = float(es.get("change_pct") or futures_move or 0.0)
            indicative_spx = previous_close * (1.0 + es_change / 100.0)
            expected_move = max(atr15 * (1.0 if horizon_name == "DAILY" else 2.2), abs(indicative_spx - previous_close) * 1.25, 8.0)
            opt_type = "call" if direction == "LONG" else "put"
            chain = await self.provider.index_option_chain("SPX", min_dte, max_dte, opt_type)
            gth_diag = await self.spx_gth_data_diagnostics()
            ranked, diag = self.waseem_v2_selector.rank(
                chain, direction, "SPX", indicative_spx,
                min_dte=min_dte, max_dte=max_dte, horizon=horizon_name,
                expected_move=expected_move,
                max_contract_price=contract_search_rules.get_max_price("index_option", horizon_name.lower()),
                is_index=True, max_results=max(5, max_results * 2),
            )
            if not ranked:
                source = chain.get("_chain_source", "unknown") if isinstance(chain, dict) else "unknown"
                return [], [f"SPX/{horizon_name}/Waseem V3 GTH: SPXW quote/chain unavailable or not executable; source={source}; " + ", ".join(diag[:8])]
            c = ranked[0]
            ctx_mod = float(context.get("modifier", 0.0) or 0.0) + float(economic.get("modifier", 0.0) or 0.0)
            setup_score = max(84.0, min(98.0, 90.0 + abs(futures_move) * 8.0 + ctx_mod))
            score = round(max(0.0, min(100.0, 0.58 * setup_score + 0.42 * float(c.get("contract_score", 0.0)))), 1)
            required = 90.0
            c.update({
                "underlying_direction": direction,
                "underlying_current_price": round(indicative_spx, 2),
                "underlying_reference_price": round(previous_close, 2),
                "underlying_reference_state": "PREVIOUS_CLOSE",
                "indicative_spx_reference": round(indicative_spx, 2),
                "underlying_data_timestamp": cash_ts,
                "underlying_data_age_minutes": round(float(cash_age or 0.0), 2),
                "horizon": horizon_name, "dte_mode": horizon_name,
                "strategy_mode": "WASEEM_V3", "engine_source": "Waseem V3",
                "spx_session": "GTH", "cash_spx_state": "PREVIOUS_CLOSE",
                "futures_implied_move_pct": round(es_change, 3),
                "gth_data_diagnostics": gth_diag,
                "market_context_lines": list(context.get("lines") or []) + list(economic.get("lines") or []) + [
                    "SPX Session: GTH",
                    f"Cash SPX: PREVIOUS_CLOSE {previous_close:.2f}",
                    f"Cash SPX Last Point: {gth_diag.get('cash_last_point_price','N/A')} | {gth_diag.get('cash_last_point_time','N/A')} | {gth_diag.get('cash_last_point_session','UNAVAILABLE')}",
                    f"Indicative SPX Reference: {indicative_spx:.2f}",
                    f"Futures-led direction: {direction}",
                    f"SPXW GTH Data: {gth_diag.get('option_data_status','UNAVAILABLE')} | feed={gth_diag.get('options_feed','N/A')} | snapshots={gth_diag.get('snapshot_count',0)}",
                    f"SPXW Latest Quote: {gth_diag.get('latest_quote_contract','N/A')} | {gth_diag.get('latest_quote_time','N/A')} | age={gth_diag.get('latest_quote_age_minutes','N/A')}m | bid={gth_diag.get('latest_quote_bid','N/A')} ask={gth_diag.get('latest_quote_ask','N/A')}",
                    f"SPXW Latest Trade: {gth_diag.get('latest_trade_contract','N/A')} | {gth_diag.get('latest_trade_time','N/A')} | age={gth_diag.get('latest_trade_age_minutes','N/A')}m | price={gth_diag.get('latest_trade_price','N/A')}",
                    f"SPXW Chain Source: {gth_diag.get('chain_source','unavailable')}",
                    "SPXW quote required: YES",
                    "NYSE TICK: UNAVAILABLE",
                    "Institutional GEX: UNAVAILABLE",
                    "Institutional Options Flow: UNAVAILABLE",
                    "Full Level 2 / DOM: UNAVAILABLE",
                ],
                "market_context_modifier": round(ctx_mod, 2),
                "waseem_diagnostics": diag[:10],
            })
            t = TradeType.INDEX_OPTION_SWING if horizon_name == "MONTHLY" else TradeType.INDEX_OPTION_INTRADAY
            p = self.prob.summarize(self.history.all(), t.value)
            base = Signal(
                "SPX", t, "LONG", Decision.READY, score,
                c["mid"], c["ask"], max(0.01, round(c["mid"] * 0.78, 2)),
                round(c["ask"] * 1.25, 2), round(c["ask"] * 1.45, 2), round(c["ask"] * 1.70, 2), 2.0,
                min(0.0035 if horizon_name == "DAILY" else 0.005, settings.max_risk_per_trade),
                ["Waseem V3 GTH — Futures-led SPX/SPXW setup", f"Futures composite {futures_move:+.3f}%"],
                ["GTH setup invalidates if futures direction/option premium structure reverses"],
                ["FUTURES", "OPTION_PREMIUM", "GTH"], "GTH", "INDEX", "LIMITED",
                p["status"], p["samples"], p.get("probability"), c,
                current_price=round(indicative_spx, 2), market_timestamp=cash_ts,
                market_age_minutes=round(float(cash_age or 0.0), 2), market_state="WASEEM_V3_GTH",
                required_score=required, liquidity_state="HIGH" if float(c.get("spread_pct", 99)) <= 3 else "NORMAL",
                volatility_state="GTH", market_context={"waseem_v3_context": context, "economic": economic, "session": session},
            )
            bases, rejects = [base], []

        out = []
        for sig in bases:
            try:
                c = dict(sig.option or {})
                # RTH V2 rows need V3 identity; GTH rows already carry it.
                c["strategy_mode"] = "WASEEM_V3"
                c["engine_source"] = "Waseem V3"
                dte = int(c.get("dte") or 0)
                opt_type = "call" if str(c.get("type", "")).upper() == "CALL" else "put"
                chain = await self.provider.index_option_chain("SPX", dte, dte, opt_type)
                snap = self._snapshot_for_contract(chain, c.get("symbol"))
                plan = self.waseem_v3_entry.evaluate(c, snap, horizon=str(c.get("horizon") or "DAILY"))
                c.update({
                    "entry_state": plan.state, "entry_quality": plan.entry_quality,
                    "current_contract_price": plan.current_price,
                    "preferred_entry_low": plan.entry_low, "preferred_entry_high": plan.entry_high,
                    "watch_reason": plan.reason, "chase_risk": plan.chase_risk,
                    "entry_diagnostics": plan.diagnostics,
                    "market_context_lines": list(c.get("market_context_lines") or []) + [
                        "V3 Entry Engine: AVAILABLE",
                        f"V3 Entry State: {plan.state}",
                        f"V3 Entry Quality: {plan.entry_quality}/100",
                        f"V3 Chase Risk: {'YES' if plan.chase_risk else 'NO'}",
                    ],
                })
                prem = max(plan.entry_high * 0.22, 0.01)
                sig.option = c
                sig.entry_low, sig.entry_high = plan.entry_low, plan.entry_high
                sig.stop = round(max(0.01, plan.entry_low - prem), 2)
                sig.tp1 = round(plan.entry_high + prem * 1.5, 2)
                sig.tp2 = round(plan.entry_high + prem * 2.0, 2)
                sig.tp3 = round(plan.entry_high + prem * 2.8, 2)
                sig.decision = Decision.READY if plan.state == "READY" else Decision.WATCH
                sig.market_state = "WASEEM_V3_GTH_ENTRY_READY" if (c.get("spx_session") == "GTH" and plan.state == "READY") else ("WASEEM_V3_WATCH_ENTRY" if plan.state == "WATCH" else "WASEEM_V3_ENTRY_READY")
                sig.reasons = ["Waseem V3 — Entry Efficiency/Anti-Chase"] + list(sig.reasons[:7]) + [plan.reason]
                out.append(sig)
            except Exception as exc:
                rejects.append(f"SPX/Waseem V3 Entry: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
        return out[:max_results], rejects


    async def _v4_enrich_signal(self, sig, *, is_index: bool = False):
        """Attach V4 liquidity/pre-move intelligence without mutating V1/V2/V3 engines."""
        c = dict(sig.option or {})
        direction = str(c.get("underlying_direction") or sig.direction or "LONG").upper()
        session = str(c.get("spx_session") or (self.spx_option_session_status().get("session") if is_index else "RTH"))
        try:
            if is_index:
                bars = await self.provider.public_index_bars("SPX", "15Min", 35)
            else:
                bars = await self.provider.bars(sig.symbol, settings.intraday_timeframe, settings.intraday_lookback_days)
            liq = self.waseem_v4_liquidity.evaluate(bars, direction, session=session)
        except Exception as exc:
            liq = self.waseem_v4_liquidity.evaluate(None, direction, session=session)
            liq.diagnostics.append(f"liquidity_fetch={type(exc).__name__}")
        base_score = float(sig.score or 0.0)
        # V4 remains quality-led: V2/V3 setup dominates, liquidity/pre-move adds independent evidence.
        v4_score = round(max(0.0, min(100.0, 0.78 * base_score + 0.22 * liq.score)), 1)
        c.update({
            "strategy_mode": "WASEEM_V4", "engine_source": "Waseem V4",
            "v4_score": v4_score, "v4_liquidity_score": liq.score,
            "v4_pre_move_score": liq.pre_move_score, "v4_flow_confidence": liq.flow_confidence,
            "v4_internal_liquidity": liq.internal_reference, "v4_external_liquidity": liq.external_target,
            "v4_liquidity_density": liq.liquidity_density_score,
            "v4_volume_acceleration": liq.volume_acceleration_score,
            "v4_momentum_acceleration": liq.momentum_acceleration_score,
            "v4_compression": liq.compression_score, "v4_diagnostics": liq.diagnostics,
            "market_context_lines": list(c.get("market_context_lines") or []) + [
                f"V4 Liquidity Map: {liq.score}/100",
                f"V4 Pre-Move: {liq.pre_move_score}/100",
                f"V4 Flow Confidence: {liq.flow_confidence}",
            ],
        })
        sig.option = c
        sig.score = v4_score
        # Keep the V3 entry decision; V4 never turns a WATCH/chasing premium into READY.
        sig.market_state = "WASEEM_V4_READY" if sig.decision == Decision.READY else "WASEEM_V4_WATCH"
        sig.reasons = ["Waseem V4 — V2 setup + V3 entry + Liquidity/Pre-Move"] + list(sig.reasons[:6]) + [f"Liquidity {liq.score:.1f} | Pre-Move {liq.pre_move_score:.1f}"]
        ctx = dict(sig.market_context or {})
        ctx["waseem_v4"] = liq.to_dict()
        sig.market_context = ctx
        return sig

    async def best_equity_options_waseem_v4(self, max_results: int = 3, horizon: str | None = None):
        """Independent Waseem V4 Equity: V2 + V3 + liquidity/pre-move intelligence."""
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_equity_options_waseem_v4(max_results, horizon="daily")
            weekly, rw = await self.best_equity_options_waseem_v4(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
            return combined[:max_results], ["Waseem V4 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        bases, rejects = await self.best_equity_options_waseem_v3(max_results=max(8, max_results * 3), horizon=horizon)
        out=[]
        for sig in bases:
            try:
                out.append(await self._v4_enrich_signal(sig, is_index=False))
            except Exception as exc:
                rejects.append(f"{getattr(sig,'symbol','?')}/Waseem V4: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
        return out[:max_results], rejects

    async def best_index_options_waseem_v4(self, max_results: int = 3, horizon: str | None = None):
        """Independent Waseem V4 SPX/SPXW: V3 GTH/RTH + liquidity/pre-move intelligence."""
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_index_options_waseem_v4(max_results, horizon="daily")
            weekly, rw = await self.best_index_options_waseem_v4(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
            return combined[:max_results], ["Waseem V4 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        bases, rejects = await self.best_index_options_waseem_v3(max_results=max(6, max_results * 3), horizon=horizon)
        out=[]
        for sig in bases:
            try:
                out.append(await self._v4_enrich_signal(sig, is_index=True))
            except Exception as exc:
                rejects.append(f"SPX/Waseem V4: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
        return out[:max_results], rejects

    async def _v5_enrich_signal(self, sig, *, is_index: bool = False):
        """Independent V5: V4 setup/pre-move + observable top-of-book/order-flow evidence."""
        c = dict(sig.option or {})
        contract_symbol = str(c.get("symbol") or "").upper()
        direction = str(c.get("underlying_direction") or sig.direction or "LONG").upper()
        snap = {}
        flow_fetch_error = None
        if contract_symbol:
            try:
                snaps = await self.provider.option_snapshots([contract_symbol])
                snap = snaps.get(contract_symbol) or {}
            except Exception as exc:
                flow_fetch_error = f"{type(exc).__name__}: {exc}"
        flow = self.waseem_v5_orderflow.evaluate(contract_symbol, snap, direction)
        if flow_fetch_error:
            flow.diagnostics.append(f"order_flow_fetch={flow_fetch_error}")

        v4_score = float(c.get("v4_score", sig.score) or 0.0)
        usable_flow = flow.flow_confidence not in {"UNAVAILABLE", "LOW"}
        v5_score = round(max(0.0, min(100.0, (0.72 * v4_score + 0.28 * flow.score) if usable_flow else v4_score)), 1)

        current_premium = c.get("current_contract_price")
        entry_low = float(c.get("preferred_entry_low", sig.entry_low) or sig.entry_low or 0.0)
        entry_high = float(c.get("preferred_entry_high", sig.entry_high) or sig.entry_high or 0.0)
        try:
            premium = float(current_premium)
        except Exception:
            premium = None
        in_entry = premium is not None and entry_low > 0 and entry_low <= premium <= max(entry_high, entry_low)
        quote_age = float(c.get("quote_age_minutes", 999.0) or 999.0)
        spread_pct = float(c.get("spread_pct", c.get("spread_percent", 999.0)) or 999.0)
        fresh = quote_age <= float(settings.option_quote_max_age_minutes)
        spread_ok = spread_pct <= float(settings.option_max_spread_pct)
        flow_ok = usable_flow and flow.score >= float(settings.waseem_v5_min_flow_score)
        base_ready = sig.decision == Decision.READY
        ready = bool(base_ready and in_entry and fresh and spread_ok and flow_ok and v5_score >= float(settings.waseem_v5_ready_floor))

        # Keep V3 preferred entry and structural option stop. V5 targets are nudged
        # only when V4 exposes a directional liquidity objective and delta is usable.
        delta = abs(float(c.get("delta", 0.0) or 0.0))
        underlying = float(sig.current_price or c.get("underlying_price", 0.0) or 0.0)
        external = c.get("v4_external_liquidity")
        projected = 0.0
        try:
            ext = float(external)
            move = (ext - underlying) if direction in {"LONG", "CALL", "BUY"} else (underlying - ext)
            if move > 0 and 0.05 <= delta <= 1.0:
                projected = move * delta
        except Exception:
            projected = 0.0
        if projected > 0 and entry_high > 0:
            flow_factor = max(0.70, min(1.20, flow.score / 75.0))
            p = projected * flow_factor
            sig.tp1 = round(max(sig.tp1, entry_high + p * 0.45), 2)
            sig.tp2 = round(max(sig.tp2, entry_high + p * 0.75), 2)
            sig.tp3 = round(max(sig.tp3, entry_high + p), 2)

        c.update({
            "strategy_mode": "WASEEM_V5", "engine_source": "Waseem V5",
            "v5_score": v5_score, "v5_ready_floor": float(settings.waseem_v5_ready_floor),
            "v5_order_flow_score": flow.score, "v5_flow_confidence": flow.flow_confidence,
            "v5_bid_ask_pressure": flow.bid_ask_pressure_score,
            "v5_trade_aggression": flow.trade_aggression_score,
            "v5_execution_pressure": flow.execution_pressure_score,
            "v5_book_imbalance": flow.book_imbalance_score,
            "v5_absorption": flow.absorption_score, "v5_replenishment": flow.replenishment_score,
            "v5_quote_status": flow.quote_status, "v5_trade_status": flow.trade_status,
            "v5_flow_samples": flow.samples, "v5_diagnostics": flow.diagnostics,
            "v5_entry_in_range": in_entry, "v5_fresh": fresh, "v5_spread_ok": spread_ok,
            "v5_flow_ok": flow_ok,
            "v5_plan_method": "V3 preferred entry + V3 structural stop + V4 liquidity target adjusted by V5 observable flow",
            "market_context_lines": list(c.get("market_context_lines") or []) + [
                f"V5 Order Flow: {flow.score}/100", f"V5 Flow Confidence: {flow.flow_confidence}",
                f"V5 Decision Gate: base_ready={base_ready} entry={in_entry} fresh={fresh} spread={spread_ok} flow={flow_ok}",
            ],
        })
        sig.option = c
        sig.score = v5_score
        sig.decision = Decision.READY if ready else Decision.WATCH
        sig.market_state = "WASEEM_V5_READY" if ready else "WASEEM_V5_WATCH"
        sig.reasons = ["Waseem V5 — V4 + Observable Order Flow + Entry/Execution Gate"] + list(sig.reasons[:6]) + [f"Order Flow {flow.score:.1f} | Confidence {flow.flow_confidence}"]
        ctx = dict(sig.market_context or {})
        ctx["waseem_v5"] = flow.to_dict()
        sig.market_context = ctx
        return sig

    async def best_equity_options_waseem_v5(self, max_results: int = 3, horizon: str | None = None):
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_equity_options_waseem_v5(max_results, horizon="daily")
            weekly, rw = await self.best_equity_options_waseem_v5(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
            return combined[:max_results], ["Waseem V5 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        bases, rejects = await self.best_equity_options_waseem_v4(max_results=max(8, max_results * 3), horizon=horizon)
        out=[]
        for sig in bases:
            try:
                out.append(await self._v5_enrich_signal(sig, is_index=False))
            except Exception as exc:
                rejects.append(f"{getattr(sig,'symbol','?')}/Waseem V5: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
        return out[:max_results], rejects

    async def best_index_options_waseem_v5(self, max_results: int = 3, horizon: str | None = None):
        if str(horizon or "").lower() in {"both", "daily_weekly"}:
            daily, rd = await self.best_index_options_waseem_v5(max_results, horizon="daily")
            weekly, rw = await self.best_index_options_waseem_v5(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
            return combined[:max_results], ["Waseem V5 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        bases, rejects = await self.best_index_options_waseem_v4(max_results=max(6, max_results * 3), horizon=horizon)
        out=[]
        for sig in bases:
            try:
                out.append(await self._v5_enrich_signal(sig, is_index=True))
            except Exception as exc:
                rejects.append(f"SPX/Waseem V5: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
        return out[:max_results], rejects

    async def _v6_enrich_signal(self, sig: Signal, *, is_index: bool = False):
        """Independent V6 overlay. V2/V3/V4/V5 remain unchanged."""
        symbol = str(sig.symbol).upper()
        if is_index and symbol == "SPX" and hasattr(self.provider, "public_index_bars"):
            provider = self.provider
            class _SPXStructureProvider:
                async def bars(self, _symbol, timeframe, days):
                    # Force StockIntelligenceEngine to aggregate 4H/weekly/monthly
                    # from genuine SPX 1H/daily bars instead of substituting SPY.
                    if timeframe in {"4Hour", "1Week", "1Month"}:
                        return pd.DataFrame()
                    return await provider.public_index_bars("SPX", timeframe, days)
            intelligence = await self.stock_intelligence.analyze(_SPXStructureProvider(), "SPX")
            try:
                bars15 = await self.provider.public_index_bars("SPX", "15Min", 25)
            except Exception:
                bars15 = pd.DataFrame()
        else:
            intelligence = await self.stock_intelligence.analyze(self.provider, symbol)
            try:
                bars15 = await self.provider.bars(symbol, "15Min", 25)
            except Exception:
                bars15 = pd.DataFrame()
        option = dict(sig.option or {})
        ages = []
        for value in (option.get("underlying_data_age_minutes"), option.get("quote_age_minutes"), sig.market_age_minutes):
            try:
                if value is not None:
                    ages.append(float(value))
            except Exception:
                pass
        age = max(ages) if ages else None
        # A fresh timestamp does not turn an indicative feed into OPRA/SIP.
        feed_limited = str(settings.alpaca_options_feed or "").lower() != "opra" or str(settings.alpaca_stock_feed or "").lower() != "sip"
        v6 = self.waseem_v6.evaluate(
            signal=sig, intelligence=intelligence, bars15=bars15,
            data_age_minutes=age,
            delayed_threshold=float(settings.waseem_v6_delayed_threshold_minutes),
            flow_score=option.get("v5_order_flow_score"),
            flow_confidence=option.get("v5_flow_confidence"),
            ready_floor=max(float(settings.waseem_v6_ready_floor), float(getattr(sig, "required_score", 0) or 0)),
            force_delayed=feed_limited,
        )
        option.update({
            "strategy_mode":"WASEEM_V6", "engine_source":"Waseem V6",
            "v6_score":v6.score, "v6_session":v6.session, "v6_delayed_data":v6.delayed_data,
            "v6_stock_feed":str(settings.alpaca_stock_feed).upper(), "v6_options_feed":str(settings.alpaca_options_feed).upper(),
            "v6_freshness_score":v6.freshness_score, "v6_multi_timeframe_score":v6.multi_timeframe_score,
            "v6_room_to_target_score":v6.room_to_target_score,
            "v6_momentum_decay_score":v6.momentum_decay_score,
            "v6_late_entry_score":v6.late_entry_score,
            "v6_breakout_quality_score":v6.breakout_quality_score,
            "v6_reversal_risk_score":v6.reversal_risk_score,
            "v6_ict_score":v6.ict_score, "v6_fibonacci_score":v6.fibonacci_score,
            "v6_cross_state":v6.cross_state, "v6_cross_score":v6.cross_score,
            "v6_no_trade":v6.no_trade, "v6_watch_reason":v6.watch_reason,
            "v6_next_target":v6.next_target, "v6_nearest_support":v6.nearest_support,
            "v6_nearest_resistance":v6.nearest_resistance, "v6_diagnostics":v6.diagnostics,
            "v6_structure_symbol":symbol,
            "v6_phase":"CONTRACT_CONFIRMATION",
            "v6_underlying_target":v6.next_target,
            "v6_contract_confirmed_after_open":v6.session in {"OPENING","RTH_MORNING","MIDDAY","RTH_AFTERNOON","POWER_HOUR"},
            "v6_frames": intelligence.get("frames") if intelligence.get("ok") else [],
            "v6_ict": intelligence.get("ict") if intelligence.get("ok") else {},
            "v6_fib": intelligence.get("fib") if intelligence.get("ok") else {},
            "market_context_lines": list(option.get("market_context_lines") or []) + [
                f"V6 Session: {v6.session}", f"V6 Delayed Data: {v6.delayed_data}",
                f"V6 Room-to-Target: {v6.room_to_target_score}/100",
                f"V6 Momentum Decay: {v6.momentum_decay_score}/100",
                f"V6 Reversal Risk: {v6.reversal_risk_score}/100",
            ],
        })
        try:
            delta=abs(float(option.get("delta") or 0.0))
            current_under=float(option.get("underlying_current_price") or sig.current_price or 0.0)
            target_under=float(v6.next_target) if v6.next_target is not None else None
            current_contract=float(option.get("current_contract_price") or option.get("mid") or 0.0)
            if target_under is not None and current_under>0 and current_contract>0 and 0.05<=delta<=1.0:
                underlying_move=(target_under-current_under) if str(option.get("option_type") or option.get("type") or sig.direction).upper() in {"CALL","LONG","BUY","C"} else (current_under-target_under)
                if underlying_move>0:
                    projected=max(0.0,underlying_move*delta)
                    option["v6_projected_underlying_move"]=round(underlying_move,2)
                    option["v6_projected_contract_price"]=round(current_contract+projected,2)
                    option["v6_projected_contract_gain_pct"]=round(projected/current_contract*100,1)
        except Exception:
            pass
        sig.option = option
        sig.score = v6.score
        sig.required_score = max(float(getattr(sig, "required_score", 0) or 0), float(settings.waseem_v6_ready_floor))
        if v6.no_trade:
            sig.decision = Decision.REJECT
            sig.market_state = "WASEEM_V6_NO_TRADE"
        elif v6.ready:
            sig.decision = Decision.READY
            sig.market_state = "WASEEM_V6_READY"
        else:
            sig.decision = Decision.WATCH
            sig.market_state = "WASEEM_V6_WATCH"
        sig.reasons = ["Waseem V6 — Delayed-Aware Structure + ICT/Fibonacci + Anti-Late-Entry"] + list(sig.reasons[:5]) + [v6.watch_reason]
        ctx=dict(sig.market_context or {})
        ctx["waseem_v6"] = v6.to_dict()
        sig.market_context=ctx
        return sig

    async def _v6_premarket_equity_plans(self, max_results: int = 3):
        """Build non-executable V6 underlying plans before RTH.

        No option contract is selected here. After 09:30 ET the normal V6 path
        re-scans the live chain and can promote a setup to READY.
        """
        out=[]; rejects=[]
        for sym in await self.equity_symbols():
            try:
                intel=await self.stock_intelligence.analyze(self.provider, sym)
                if not intel.get('ok'):
                    rejects.append(f"{sym}/V6 premarket: {intel.get('reason','NO_DATA')}")
                    continue
                try:
                    bars15=await self.provider.bars(sym,'15Min',25)
                except Exception:
                    bars15=pd.DataFrame()
                plan=self.waseem_v6.plan_from_intelligence(intel,bars15)
                if float(plan.get('confidence',0) or 0) < 55:
                    continue
                current=float(plan.get('current') or 0.0)
                trigger=plan.get('trigger'); invalid=plan.get('invalidation'); target=plan.get('target')
                direction=str(plan.get('direction') or 'CALL')
                if direction=='CALL':
                    entry_low=current; entry_high=float(trigger or current)
                    stop=float(invalid or max(0.01,current-(plan.get('atr') or current*0.01)))
                    tp1=float(trigger or current); tp2=float(target or tp1); tp3=float(target or tp2)
                else:
                    entry_low=float(trigger or current); entry_high=current
                    stop=float(invalid or current+(plan.get('atr') or current*0.01))
                    tp1=float(trigger or current); tp2=float(target or tp1); tp3=float(target or tp2)
                option={
                    'strategy_mode':'WASEEM_V6','engine_source':'Waseem V6',
                    'v6_premarket_plan':True,'v6_contract_pending_rth':True,
                    'v6_session':plan.get('session'),'v6_plan_direction':direction,
                    'v6_plan_confidence':plan.get('confidence'),'v6_nearest_support':plan.get('support'),
                    'v6_nearest_resistance':plan.get('resistance'),'v6_next_target':plan.get('target'),
                    'v6_target_horizon':plan.get('target_horizon'),'v6_target_confirmation':plan.get('target_confirmation'),
                    'v6_invalidation_level':plan.get('invalidation'),'v6_ict':plan.get('ict'),
                    'v6_fib':plan.get('fib'),'v6_cross_state':plan.get('cross_state'),
                    'v6_cross_score':plan.get('cross_score'),'v6_rvol':plan.get('rvol'),
                    'v6_vwap':plan.get('vwap'),'v6_momentum5_pct':plan.get('momentum5_pct'),
                    'v6_atr':plan.get('atr'),'v6_frames':plan.get('frames'),
                    'underlying_current_price':current,'type':direction,'option_type':direction,
                    'horizon':'PREMARKET_PLAN','expiration':'بعد الافتتاح','dte':'—','strike':'يحدد بعد الافتتاح',
                    'current_contract_price':None,'bid':None,'ask':None,'mid':None,
                    'market_context_lines':['V6 PREMARKET PLAN: underlying-first; option contract pending RTH confirmation'],
                }
                sig=Signal(
                    sym,TradeType.EQUITY_OPTION_INTRADAY,direction,Decision.WATCH,float(plan.get('confidence',0)),
                    min(entry_low,entry_high),max(entry_low,entry_high),stop,tp1,tp2,tp3,0.0,float(settings.max_risk_per_trade),
                    ['Waseem V6 — Pre-Market Underlying Plan','No contract selected before RTH'],
                    [f"إلغاء السيناريو عند {invalid}" if invalid is not None else 'إلغاء السيناريو عند فشل البنية'],
                    ['V6_PREMARKET','ICT','FIBONACCI','MTF'],data_quality='LIMITED',option=option,current_price=current,
                    market_state='WASEEM_V6_PREMARKET_WATCH',required_score=float(settings.waseem_v6_ready_floor),
                    liquidity_state='CONTEXT',volatility_state='CONTEXT',market_context={'waseem_v6_plan':plan},
                )
                out.append(sig)
            except Exception as exc:
                rejects.append(f"{sym}/V6 premarket: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x:x.score,reverse=True)
        return out[:max_results], rejects

    async def _v6_direct_equity_contracts(self, max_results: int = 3, horizon: str | None = None):
        """V6 RTH path: analyze the underlying first, then select/confirm the option.

        This deliberately does not call best_equity_options_waseem_v2/v3/v4/v5.
        It may reuse their isolated selector/entry/liquidity/order-flow components,
        but V6 owns the candidate universe and decision path.
        """
        horizon_name, min_dte, max_dte = self._expiration_horizon(horizon)
        horizon_name = horizon_name or "WEEKLY"
        min_dte = 1 if min_dte is None else int(min_dte)
        max_dte = 7 if max_dte is None else int(max_dte)
        out, rejects = [], []
        for sym in await self.equity_symbols():
            try:
                intel = await self.stock_intelligence.analyze(self.provider, sym)
                if not intel.get("ok"):
                    rejects.append(f"{sym}/{horizon_name}/V6: underlying data unavailable")
                    continue
                try:
                    bars15 = await self.provider.bars(sym, "15Min", 25)
                except Exception:
                    bars15 = pd.DataFrame()
                plan = self.waseem_v6.plan_from_intelligence(intel, bars15)
                if float(plan.get("confidence", 0) or 0) < 55.0:
                    rejects.append(f"{sym}/{horizon_name}/V6: NO TRADE — weak underlying plan")
                    continue
                current = float(plan.get("current") or 0.0)
                if current <= 0:
                    continue
                direction = "LONG" if str(plan.get("direction")).upper() == "CALL" else "SHORT"
                opt_type = "call" if direction == "LONG" else "put"
                chain = await self.provider.option_chain(sym, min_dte, max_dte, opt_type)
                atr = float(plan.get("atr") or max(current * 0.01, 0.01))
                move_mult = 0.85 if horizon_name == "DAILY" else 1.8 if horizon_name == "WEEKLY" else 3.0
                expected_move = max(atr * move_mult, current * 0.005)
                ranked, diag = self.waseem_v2_selector.rank(
                    chain, direction, sym, current,
                    min_dte=min_dte, max_dte=max_dte, horizon=horizon_name,
                    expected_move=expected_move,
                    max_contract_price=contract_search_rules.get_max_price("equity_option", horizon_name.lower()),
                    is_index=False, max_results=5,
                )
                if not ranked:
                    rejects.append(f"{sym}/{horizon_name}/V6: no executable contract — " + ", ".join(diag[:4]))
                    continue
                c = dict(ranked[0])
                if float(c.get("spread_pct", 99) or 99) > 12.0:
                    rejects.append(f"{sym}/{horizon_name}/V6: NO TRADE — untradable spread")
                    continue
                news = await self._news_context(sym)
                try:
                    ctx = await self.waseem_v2_context.equity_context(sym, SECTOR_MAP.get(sym), intel, direction)
                except Exception:
                    ctx = {"modifier": 0.0, "lines": [], "source": "UNAVAILABLE", "notes": []}
                try:
                    econ = await self.economic_context.equity_context(sym)
                except Exception:
                    econ = {"modifier": 0.0, "lines": []}
                ctx_mod = float(ctx.get("modifier", 0.0) or 0.0) + float(econ.get("modifier", 0.0) or 0.0)
                base_score = round(max(0.0, min(100.0, 0.62 * float(plan.get("confidence", 50)) + 0.38 * float(c.get("contract_score", 0)) + ctx_mod)), 1)
                c.update({
                    "underlying_direction": direction,
                    "underlying_current_price": current,
                    "underlying_entry_low": min(current, float(plan.get("trigger") or current)),
                    "underlying_entry_high": max(current, float(plan.get("trigger") or current)),
                    "underlying_stop": plan.get("invalidation"),
                    "underlying_tp1": plan.get("trigger"), "underlying_tp2": plan.get("target"), "underlying_tp3": plan.get("target"),
                    "horizon": horizon_name, "dte_mode": horizon_name,
                    "strategy_mode": "WASEEM_V6", "engine_source": "Waseem V6",
                    "expected_move": round(expected_move, 2),
                    "market_context_lines": list(ctx.get("lines") or []) + list(econ.get("lines") or []) + [f"NEWS: {'AVAILABLE — ' + str(news.get('headline'))[:90] if news.get('headline') else 'UNAVAILABLE'}"],
                    "v6_underlying_first": True, "v6_plan_confidence": plan.get("confidence"),
                })
                snap = self._snapshot_for_contract(chain, c.get("symbol"))
                entry = self.waseem_v3_entry.evaluate(c, snap, horizon=horizon_name)
                c.update({
                    "entry_state": entry.state, "entry_quality": entry.entry_quality,
                    "current_contract_price": entry.current_price,
                    "preferred_entry_low": entry.entry_low, "preferred_entry_high": entry.entry_high,
                    "watch_reason": entry.reason, "chase_risk": entry.chase_risk,
                    "entry_diagnostics": entry.diagnostics,
                })
                prem = max(float(entry.entry_high or c.get("ask") or c.get("mid") or 0.01) * 0.22, 0.01)
                stop = round(max(0.01, float(entry.entry_low or 0.01) - prem), 2)
                t = TradeType.EQUITY_OPTION_INTRADAY
                sig = Signal(
                    sym, t, direction, Decision.READY if entry.state == "READY" else Decision.WATCH, base_score,
                    float(entry.entry_low), float(entry.entry_high), stop,
                    round(float(entry.entry_high) + prem * 1.5, 2), round(float(entry.entry_high) + prem * 2.0, 2), round(float(entry.entry_high) + prem * 2.8, 2), 2.0,
                    min(0.0035 if horizon_name == "DAILY" else 0.005, settings.max_risk_per_trade),
                    ["Waseem V6 — Underlying-first candidate", f"Underlying plan {plan.get('confidence')}/100", entry.reason],
                    [f"إبطال السيناريو عند {plan.get('invalidation')}" if plan.get("invalidation") is not None else "إبطال السيناريو عند فشل البنية"],
                    ["V6_UNDERLYING_FIRST", "ICT", "FIBONACCI", "MTF", "OPTION_EXECUTION"],
                    data_quality="LIMITED", option=c, current_price=current,
                    market_state="WASEEM_V6_CONTRACT_CANDIDATE", required_score=float(settings.waseem_v6_ready_floor),
                    liquidity_state="HIGH" if float(c.get("spread_pct", 99) or 99) <= 3 else "NORMAL",
                    volatility_state="NORMAL", market_context={"waseem_v6_plan": plan},
                )
                # Reuse isolated enrichment components; no older engine candidate list is called.
                sig = await self._v4_enrich_signal(sig, is_index=False)
                sig = await self._v5_enrich_signal(sig, is_index=False)
                sig = await self._v6_enrich_signal(sig, is_index=False)
                out.append(sig)
            except Exception as exc:
                rejects.append(f"{sym}/{horizon_name}/V6: {type(exc).__name__}: {exc}")
        out.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
        return out[:max_results], rejects

    async def best_equity_options_waseem_v6(self, max_results: int = 3, horizon: str | None = None):
        session=self.waseem_v6.session()
        if session in {'PREMARKET','AFTER_HOURS'}:
            return await self._v6_premarket_equity_plans(max_results=max_results)
        if session == 'CLOSED':
            return [], ['Waseem V6: session CLOSED — no executable contract scan']
        if str(horizon or "").lower() in {"both","daily_weekly"}:
            daily, rd = await self.best_equity_options_waseem_v6(max_results, horizon="daily")
            weekly, rw = await self.best_equity_options_waseem_v6(max_results, horizon="weekly")
            combined = daily + weekly
            combined.sort(key=lambda x: (x.decision == Decision.READY, x.score), reverse=True)
            return combined[:max_results], ["Waseem V6 Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        return await self._v6_direct_equity_contracts(max_results=max_results, horizon=horizon)

    async def _v6_spx_intelligence(self):
        provider = self.provider
        class _SPXStructureProvider:
            async def bars(self, _symbol, timeframe, days):
                if timeframe in {"4Hour", "1Week", "1Month"}:
                    return pd.DataFrame()
                return await provider.public_index_bars("SPX", timeframe, days)
        intel = await self.stock_intelligence.analyze(_SPXStructureProvider(), "SPX")
        try:
            bars15 = await self.provider.public_index_bars("SPX", "15Min", 25)
        except Exception:
            bars15 = pd.DataFrame()
        return intel, bars15

    async def _v6_premarket_index_plans(self, max_results: int = 1):
        """Non-executable SPX plan before RTH; no SPXW contract is fixed here."""
        try:
            intel, bars15 = await self._v6_spx_intelligence()
            if not intel.get("ok"):
                return [], ["SPX/V6 premarket: underlying structure unavailable"]
            plan = self.waseem_v6.plan_from_intelligence(intel, bars15)
            current = float(plan.get("current") or 0.0)
            direction = str(plan.get("direction") or "CALL")
            trigger = plan.get("trigger"); invalid = plan.get("invalidation"); target = plan.get("target")
            if direction == "CALL":
                entry_low, entry_high = current, float(trigger or current)
                stop = float(invalid or max(0.01, current - (plan.get("atr") or current * 0.01)))
            else:
                entry_low, entry_high = float(trigger or current), current
                stop = float(invalid or current + (plan.get("atr") or current * 0.01))
            tp1 = float(trigger or current); tp2 = float(target or tp1); tp3 = float(target or tp2)
            option = {
                "strategy_mode":"WASEEM_V6", "engine_source":"Waseem V6",
                "v6_premarket_plan":True, "v6_contract_pending_rth":True,
                "v6_session":plan.get("session"), "v6_plan_direction":direction,
                "v6_plan_confidence":plan.get("confidence"), "v6_nearest_support":plan.get("support"),
                "v6_nearest_resistance":plan.get("resistance"), "v6_next_target":plan.get("target"),
                "v6_target_horizon":plan.get("target_horizon"), "v6_target_confirmation":plan.get("target_confirmation"),
                "v6_invalidation_level":plan.get("invalidation"), "v6_ict":plan.get("ict"), "v6_fib":plan.get("fib"),
                "v6_cross_state":plan.get("cross_state"), "v6_cross_score":plan.get("cross_score"),
                "v6_rvol":plan.get("rvol"), "v6_vwap":plan.get("vwap"), "v6_momentum5_pct":plan.get("momentum5_pct"),
                "v6_atr":plan.get("atr"), "v6_frames":plan.get("frames"), "underlying_current_price":current,
                "type":direction, "option_type":direction, "horizon":"PREMARKET_PLAN", "expiration":"بعد الافتتاح",
                "dte":"—", "strike":"يحدد بعد الافتتاح", "current_contract_price":None, "bid":None, "ask":None, "mid":None,
                "market_context_lines":["V6 SPX PREMARKET PLAN: cash structure first; SPXW contract pending RTH confirmation"],
            }
            sig = Signal(
                "SPX", TradeType.INDEX_OPTION_INTRADAY, direction, Decision.WATCH, float(plan.get("confidence", 0)),
                min(entry_low, entry_high), max(entry_low, entry_high), stop, tp1, tp2, tp3, 0.0,
                min(0.0035, settings.max_risk_per_trade),
                ["Waseem V6 — SPX Pre-Market Underlying Plan", "No SPXW contract selected before RTH"],
                [f"إلغاء السيناريو عند {invalid}" if invalid is not None else "إلغاء السيناريو عند فشل البنية"],
                ["V6_PREMARKET", "SPX", "ICT", "FIBONACCI", "MTF"], data_quality="LIMITED", option=option,
                current_price=current, market_state="WASEEM_V6_PREMARKET_WATCH", required_score=float(settings.waseem_v6_ready_floor),
                liquidity_state="CONTEXT", volatility_state="CONTEXT", market_context={"waseem_v6_plan":plan},
            )
            return [sig][:max_results], []
        except Exception as exc:
            return [], [f"SPX/V6 premarket: {type(exc).__name__}: {exc}"]

    async def _v6_direct_index_contracts(self, max_results: int = 3, horizon: str | None = None):
        horizon_name, min_dte, max_dte = self._expiration_horizon(horizon)
        horizon_name = horizon_name or "DAILY"
        min_dte = 0 if min_dte is None else int(min_dte)
        max_dte = 0 if max_dte is None else int(max_dte)
        try:
            intel, bars15 = await self._v6_spx_intelligence()
            if not intel.get("ok"):
                return [], ["SPX/V6: underlying structure unavailable"]
            plan = self.waseem_v6.plan_from_intelligence(intel, bars15)
            current = float(plan.get("current") or 0.0)
            direction = "LONG" if str(plan.get("direction")).upper() == "CALL" else "SHORT"
            opt_type = "call" if direction == "LONG" else "put"
            chain = await self.provider.index_option_chain("SPX", min_dte, max_dte, opt_type)
            atr = float(plan.get("atr") or max(current * 0.003, 10.0))
            expected_move = max(atr * (0.9 if horizon_name == "DAILY" else 1.8), 8.0)
            ranked, diag = self.waseem_v2_selector.rank(
                chain, direction, "SPX", current, min_dte=min_dte, max_dte=max_dte,
                horizon=horizon_name, expected_move=expected_move,
                max_contract_price=contract_search_rules.get_max_price("index_option", horizon_name.lower()),
                is_index=True, max_results=5,
            )
            if not ranked:
                return [], ["SPX/V6: no executable contract — " + ", ".join(diag[:5])]
            c = dict(ranked[0])
            if float(c.get("spread_pct", 99) or 99) > 12.0:
                return [], ["SPX/V6: NO TRADE — untradable spread"]
            base_score = round(max(0.0, min(100.0, 0.62 * float(plan.get("confidence", 50)) + 0.38 * float(c.get("contract_score", 0)))), 1)
            c.update({
                "underlying_direction":direction, "underlying_current_price":current,
                "underlying_entry_low":min(current, float(plan.get("trigger") or current)),
                "underlying_entry_high":max(current, float(plan.get("trigger") or current)),
                "underlying_stop":plan.get("invalidation"), "underlying_tp1":plan.get("trigger"),
                "underlying_tp2":plan.get("target"), "underlying_tp3":plan.get("target"),
                "horizon":horizon_name, "dte_mode":horizon_name, "strategy_mode":"WASEEM_V6", "engine_source":"Waseem V6",
                "expected_move":round(expected_move,2), "v6_underlying_first":True, "v6_plan_confidence":plan.get("confidence"),
                "market_context_lines":["V6 SPX: underlying structure first, contract execution second"],
            })
            snap = self._snapshot_for_contract(chain, c.get("symbol"))
            entry = self.waseem_v3_entry.evaluate(c, snap, horizon=horizon_name)
            c.update({"entry_state":entry.state,"entry_quality":entry.entry_quality,"current_contract_price":entry.current_price,
                      "preferred_entry_low":entry.entry_low,"preferred_entry_high":entry.entry_high,"watch_reason":entry.reason,
                      "chase_risk":entry.chase_risk,"entry_diagnostics":entry.diagnostics})
            prem=max(float(entry.entry_high or c.get("ask") or c.get("mid") or 0.01)*0.22,0.01)
            sig=Signal(
                "SPX", TradeType.INDEX_OPTION_INTRADAY, direction, Decision.READY if entry.state=="READY" else Decision.WATCH,
                base_score, float(entry.entry_low), float(entry.entry_high), round(max(0.01,float(entry.entry_low)-prem),2),
                round(float(entry.entry_high)+prem*1.5,2),round(float(entry.entry_high)+prem*2.0,2),round(float(entry.entry_high)+prem*2.8,2),2.0,
                min(0.0035 if horizon_name=="DAILY" else 0.005,settings.max_risk_per_trade),
                ["Waseem V6 — SPX underlying-first candidate",entry.reason],
                [f"إبطال السيناريو عند {plan.get('invalidation')}" if plan.get("invalidation") is not None else "إبطال السيناريو عند فشل البنية"],
                ["V6_UNDERLYING_FIRST","SPX","ICT","FIBONACCI","OPTION_EXECUTION"], data_quality="LIMITED", option=c,
                current_price=current, market_state="WASEEM_V6_CONTRACT_CANDIDATE", required_score=float(settings.waseem_v6_ready_floor),
                liquidity_state="HIGH" if float(c.get("spread_pct",99) or 99)<=3 else "NORMAL", volatility_state="NORMAL",
                market_context={"waseem_v6_plan":plan},
            )
            sig=await self._v4_enrich_signal(sig,is_index=True)
            sig=await self._v5_enrich_signal(sig,is_index=True)
            sig=await self._v6_enrich_signal(sig,is_index=True)
            return [sig][:max_results], []
        except Exception as exc:
            return [], [f"SPX/V6: {type(exc).__name__}: {exc}"]

    async def best_index_options_waseem_v6(self, max_results: int = 3, horizon: str | None = None):
        session=self.waseem_v6.session()
        if session in {"PREMARKET","AFTER_HOURS"}:
            return await self._v6_premarket_index_plans(max_results=max_results)
        if session == "CLOSED":
            return [], ["SPX/Waseem V6: session CLOSED — no executable contract scan"]
        if str(horizon or "").lower() in {"both","daily_weekly"}:
            daily, rd = await self.best_index_options_waseem_v6(max_results, horizon="daily")
            weekly, rw = await self.best_index_options_waseem_v6(max_results, horizon="weekly")
            combined=daily+weekly
            combined.sort(key=lambda x:(x.decision==Decision.READY,x.score),reverse=True)
            return combined[:max_results], ["Waseem V6 SPX Dual Scan: Daily 0DTE + Weekly 1–7 DTE"] + rd + rw
        return await self._v6_direct_index_contracts(max_results=max_results,horizon=horizon)

    async def best_index_options(self, max_results: int = 3, strategy_mode: str = "core", horizon: str | None = None):
        mode = str(strategy_mode or "core").strip().lower()
        if mode in {"waseem_v6", "waseem6", "v6"}:
            return await self.best_index_options_waseem_v6(max_results, horizon=horizon)
        if mode in {"waseem_v5", "waseem5", "v5"}:
            return await self.best_index_options_waseem_v5(max_results, horizon=horizon)
        if mode in {"waseem_v4", "waseem4", "v4"}:
            return await self.best_index_options_waseem_v4(max_results, horizon=horizon)
        if mode in {"waseem_v3", "waseem3", "v3"}:
            return await self.best_index_options_waseem_v3(max_results, horizon=horizon)
        if mode in {"waseem_v2", "waseem2", "v2"}:
            return await self.best_index_options_waseem_v2(max_results, horizon=horizon)
        if mode in {"waseem", "waseem_v1"}:
            return await self.best_index_options_waseem(max_results, horizon=horizon)
        if mode in {"v20", "spx_v20", "radar"}:
            return await self._best_index_options_v20(max_results, horizon=horizon)
        if mode in {"confirmed", "confirmed_setup", "setup"}:
            return await self.best_index_options_confirmed(max_results, horizon=horizon)
        return await self._best_index_options_core(max_results, horizon=horizon)

    async def best_index_option(self, strategy_mode: str = "core", horizon: str | None = None):
        c, r = await self.best_index_options(1, strategy_mode=strategy_mode, horizon=horizon)
        return (c[0] if c else None), r
