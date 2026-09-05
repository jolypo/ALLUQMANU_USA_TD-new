from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import asyncio
import httpx
import pandas as pd
import yfinance as yf
from app.config import settings


class AlpacaProvider:
    def __init__(self):
        self.headers = {
            "APCA-API-KEY-ID": settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
        }
        self.client = httpx.AsyncClient(timeout=20, headers=self.headers)
        self._sem = asyncio.Semaphore(5)

    async def close(self):
        await self.client.aclose()

    async def _get(self, url: str, params: dict | None = None) -> dict:
        async with self._sem:
            last = None
            for attempt in range(3):
                try:
                    r = await self.client.get(url, params=params)
                    if r.status_code == 429 and attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    return r.json()
                except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                    last = e
                    retryable = (
                        not isinstance(e, httpx.HTTPStatusError)
                        or e.response.status_code in {429, 500, 502, 503, 504}
                    )
                    if not retryable or attempt == 2:
                        raise
                    await asyncio.sleep(1.25 * (attempt + 1))
            if last:
                raise last
        return {}

    async def bars(self, symbol: str, timeframe: str, lookback_days: int) -> pd.DataFrame:
        start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        url = f"{settings.alpaca_data_base_url}/v2/stocks/{symbol}/bars"
        data = await self._get(url, {
            "timeframe": timeframe,
            "start": start,
            "adjustment": "all",
            "feed": settings.alpaca_stock_feed,
            "limit": 10000,
            "sort": "asc",
        })
        rows = data.get("bars", [])
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([{
            "timestamp": x.get("t"),
            "open": x.get("o"),
            "high": x.get("h"),
            "low": x.get("l"),
            "close": x.get("c"),
            "volume": x.get("v"),
        } for x in rows])

    async def public_index_bars(self, symbol: str, timeframe: str, lookback_days: int) -> pd.DataFrame:
        """Free public reference bars for index analysis (no trading/orders).

        SPX V20 needs the SPX price series itself while keeping SPY as its volume
        proxy, exactly like the supplied TradingView script. Alpaca's configured
        stock feed does not expose SPX price bars, so this read-only reference
        series uses yfinance, which is already a project dependency. If the
        reference data is unavailable, callers must reject the scan rather than
        substitute fabricated values.
        """
        ticker = "^GSPC" if str(symbol).upper() == "SPX" else str(symbol)
        tf = str(timeframe)
        interval_map = {
            "5Min": "5m",
            "15Min": "15m",
            "1Hour": "60m",
            "4Hour": "60m",
            "1Day": "1d",
        }
        interval = interval_map.get(tf)
        if not interval:
            raise ValueError(f"unsupported public index timeframe: {timeframe}")

        def _load() -> pd.DataFrame:
            start = datetime.now(timezone.utc) - timedelta(days=max(2, int(lookback_days)))
            frame = yf.Ticker(ticker).history(
                start=start,
                interval=interval,
                auto_adjust=False,
                actions=False,
                prepost=False,
            )
            if frame is None or frame.empty:
                return pd.DataFrame()
            frame = frame.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            keep = [c for c in ("open", "high", "low", "close", "volume") if c in frame.columns]
            frame = frame[keep].copy()
            idx = pd.to_datetime(frame.index, utc=True, errors="coerce")
            frame["timestamp"] = idx
            frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
            if "volume" not in frame.columns:
                frame["volume"] = 0.0
            if tf == "4Hour":
                z = frame.set_index("timestamp").sort_index()
                z.index = z.index.tz_convert("America/New_York")
                z = z.resample(
                    "4h", origin="start_day", offset="9h30min", label="left", closed="left"
                ).agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum",
                }).dropna(subset=["open", "high", "low", "close"])
                z.index = z.index.tz_convert("UTC")
                z["timestamp"] = z.index
                frame = z.reset_index(drop=True)
            return frame[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)

        return await asyncio.to_thread(_load)

    async def latest_bars(self, symbols: list[str]) -> dict:
        if not symbols:
            return {}
        url = f"{settings.alpaca_data_base_url}/v2/stocks/bars/latest"
        d = await self._get(url, {"symbols": ",".join(symbols), "feed": settings.alpaca_stock_feed})
        return d.get("bars", {})

    async def market_clock(self) -> dict:
        return await self._get(f"{settings.alpaca_trading_base_url}/clock")

    async def option_chain(self, underlying: str, min_dte: int, max_dte: int, opt_type: str | None = None) -> dict:
        now = datetime.now(ZoneInfo("America/New_York")).date()
        params = {
            "feed": settings.alpaca_options_feed,
            "limit": 1000,
            "expiration_date_gte": str(now + timedelta(days=min_dte)),
            "expiration_date_lte": str(now + timedelta(days=max_dte)),
        }
        if opt_type:
            params["type"] = opt_type
        url = f"{settings.alpaca_data_base_url}/v1beta1/options/snapshots/{underlying}"
        return await self._get(url, params)


    async def option_snapshots(self, contract_symbols: list[str]) -> dict:
        """Fetch option snapshots for explicit OCC symbols in API-safe chunks."""
        snapshots: dict = {}
        symbols = [str(x).upper() for x in contract_symbols if x]
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            if not chunk:
                continue
            url = f"{settings.alpaca_data_base_url}/v1beta1/options/snapshots"
            data = await self._get(url, {
                "symbols": ",".join(chunk),
                "feed": settings.alpaca_options_feed,
                "limit": min(1000, len(chunk)),
            })
            snapshots.update(data.get("snapshots", {}) or {})
        return snapshots

    async def option_contracts(
        self,
        underlying: str,
        min_dte: int,
        max_dte: int,
        opt_type: str | None = None,
        style: str | None = None,
    ) -> list[dict]:
        """Discover active contracts from Alpaca Trading API."""
        now = datetime.now(ZoneInfo("America/New_York")).date()
        params = {
            "underlying_symbols": underlying,
            "status": "active",
            "expiration_date_gte": str(now + timedelta(days=min_dte)),
            "expiration_date_lte": str(now + timedelta(days=max_dte)),
            "limit": 10000,
        }
        if opt_type:
            params["type"] = opt_type
        if style:
            params["style"] = style
        url = f"{settings.alpaca_trading_base_url}/options/contracts"
        data = await self._get(url, params)
        return data.get("option_contracts", []) or []

    async def index_option_chain(
        self,
        underlying: str,
        min_dte: int,
        max_dte: int,
        opt_type: str | None = None,
    ) -> dict:
        """Best-effort index option chain.

        First use the normal chain endpoint. If that returns no snapshots or is
        unavailable for the account, discover index contracts (SPX/SPXW) via
        the contracts endpoint and request snapshots by explicit OCC symbol.
        """
        primary_error = None
        try:
            data = await self.option_chain(underlying, min_dte, max_dte, opt_type)
            if data.get("snapshots"):
                data["_chain_source"] = "underlying_chain"
                return data
        except Exception as exc:
            primary_error = f"{type(exc).__name__}: {exc}"

        try:
            contracts = await self.option_contracts(
                underlying, min_dte, max_dte, opt_type, style="european"
            )
            symbols = [c.get("symbol") for c in contracts if c.get("symbol")]
            snapshots = await self.option_snapshots(symbols)
            return {
                "snapshots": snapshots,
                "_chain_source": "contracts_snapshots",
                "_contract_count": len(symbols),
                "_primary_error": primary_error,
            }
        except Exception as exc:
            return {
                "snapshots": {},
                "_chain_source": "unavailable",
                "_primary_error": primary_error,
                "_fallback_error": f"{type(exc).__name__}: {exc}",
            }

    async def entry_price_range_since(
        self,
        symbol: str,
        start: str,
        option_contract: str | None = None,
    ) -> tuple[float, float] | None:
        """Return observed low/high since *start* for entry-touch detection.

        The method follows pagination so a swing trade does not miss a touch
        simply because more than 1,000 one-minute bars elapsed.
        """
        try:
            lows: list[float] = []
            highs: list[float] = []
            page_token = None
            for _ in range(12):
                if option_contract:
                    url = f"{settings.alpaca_data_base_url}/v1beta1/options/bars"
                    params = {
                        "symbols": option_contract,
                        "timeframe": "1Min",
                        "start": start,
                        "limit": 1000,
                        "feed": settings.alpaca_options_feed,
                        "sort": "asc",
                    }
                else:
                    url = f"{settings.alpaca_data_base_url}/v2/stocks/{symbol}/bars"
                    params = {
                        "timeframe": "1Min",
                        "start": start,
                        "adjustment": "all",
                        "feed": settings.alpaca_stock_feed,
                        "limit": 1000,
                        "sort": "asc",
                    }
                if page_token:
                    params["page_token"] = page_token
                data = await self._get(url, params)
                if option_contract:
                    bars = (data.get("bars", {}) or {}).get(option_contract, []) or []
                else:
                    bars = data.get("bars", []) or []
                lows.extend(float(x["l"]) for x in bars if x.get("l") is not None)
                highs.extend(float(x["h"]) for x in bars if x.get("h") is not None)
                page_token = data.get("next_page_token")
                if not page_token:
                    break
            if not lows or not highs:
                return None
            return min(lows), max(highs)
        except Exception as exc:
            print(f"[entry-range] {symbol}: {type(exc).__name__}: {exc}")
            return None

    async def option_quotes(self, contract_symbols: list[str]) -> dict:
        if not contract_symbols:
            return {}
        url = f"{settings.alpaca_data_base_url}/v1beta1/options/quotes/latest"
        d = await self._get(url, {
            "symbols": ",".join(contract_symbols[:100]),
            "feed": settings.alpaca_options_feed,
        })
        return d.get("quotes", {})

    async def news(self, symbol: str, lookback_hours: int = 6, limit: int = 8) -> list[dict]:
        start = (datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))).isoformat()
        url = f"{settings.alpaca_data_base_url}/v1beta1/news"
        d = await self._get(url, {
            "symbols": symbol,
            "start": start,
            "sort": "desc",
            "limit": max(1, min(50, limit)),
            "include_content": "false",
        })
        return d.get("news", []) or []
