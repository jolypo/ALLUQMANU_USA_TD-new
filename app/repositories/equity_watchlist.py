from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from app.config import settings


@dataclass
class WatchlistStatus:
    persistent: bool
    backend: str
    detail: str


class EquityWatchlistRepository:
    """Persistent master equity watchlist.

    PostgreSQL is the only writable backend. If DATABASE_URL is not configured,
    the project safely falls back to the static Settings list for reads and
    refuses Telegram mutations instead of pretending ephemeral JSON is durable.
    """
    def __init__(self):
        self.database_url = str(getattr(settings, "database_url", None) or "").strip()
        self._initialized = False
        self._lock = asyncio.Lock()
        self._memory = {str(s).upper(): True for s in settings.stocks}

    def status(self) -> WatchlistStatus:
        if self.database_url:
            return WatchlistStatus(True, "POSTGRESQL", "configured")
        return WatchlistStatus(False, "MEMORY_EPHEMERAL", "temporary in-memory watchlist; changes may be lost on restart/redeploy")

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url)

    def _init_sync(self):
        if not self.database_url:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS equity_watchlist (
                        symbol VARCHAR(16) PRIMARY KEY,
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        source VARCHAR(32) NOT NULL DEFAULT 'telegram',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("SELECT COUNT(*) FROM equity_watchlist")
                count = int(cur.fetchone()[0])
                if count == 0:
                    for sym in settings.stocks:
                        cur.execute(
                            "INSERT INTO equity_watchlist(symbol, enabled, source) VALUES (%s, TRUE, 'bootstrap') ON CONFLICT DO NOTHING",
                            (sym,),
                        )
            conn.commit()

    async def initialize(self):
        if self._initialized or not self.database_url:
            return
        async with self._lock:
            if not self._initialized:
                await asyncio.to_thread(self._init_sync)
                self._initialized = True

    def _list_sync(self, include_disabled: bool):
        with self._connect() as conn:
            with conn.cursor() as cur:
                if include_disabled:
                    cur.execute("SELECT symbol, enabled, source, created_at, updated_at FROM equity_watchlist ORDER BY symbol")
                else:
                    cur.execute("SELECT symbol, enabled, source, created_at, updated_at FROM equity_watchlist WHERE enabled=TRUE ORDER BY symbol")
                rows = cur.fetchall()
        return [{"symbol": r[0], "enabled": bool(r[1]), "source": r[2], "created_at": r[3].isoformat(), "updated_at": r[4].isoformat()} for r in rows]

    async def list(self, include_disabled: bool = True):
        if not self.database_url:
            async with self._lock:
                return [{"symbol": s, "enabled": bool(enabled), "source": "memory", "created_at": None, "updated_at": None} for s, enabled in sorted(self._memory.items()) if include_disabled or enabled]
        await self.initialize()
        return await asyncio.to_thread(self._list_sync, include_disabled)

    async def enabled_symbols(self):
        return [r["symbol"] for r in await self.list(include_disabled=False)]

    def _upsert_sync(self, symbol: str, enabled: bool):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO equity_watchlist(symbol, enabled, source, created_at, updated_at)
                    VALUES (%s, %s, 'telegram', %s, %s)
                    ON CONFLICT(symbol) DO UPDATE SET enabled=EXCLUDED.enabled, updated_at=EXCLUDED.updated_at
                """, (symbol, enabled, datetime.now(timezone.utc), datetime.now(timezone.utc)))
            conn.commit()

    async def upsert(self, symbol: str, enabled: bool = True):
        if not self.database_url:
            async with self._lock:
                self._memory[symbol.upper()] = bool(enabled)
            return
        await self.initialize()
        await asyncio.to_thread(self._upsert_sync, symbol.upper(), enabled)

    def _delete_sync(self, symbol: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM equity_watchlist WHERE symbol=%s", (symbol,))
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    async def remove(self, symbol: str):
        if not self.database_url:
            async with self._lock:
                return self._memory.pop(symbol.upper(), None) is not None
        await self.initialize()
        return await asyncio.to_thread(self._delete_sync, symbol.upper())

    def _enable_sync(self, symbol: str, enabled: bool):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE equity_watchlist SET enabled=%s, updated_at=NOW() WHERE symbol=%s", (enabled, symbol))
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    async def set_enabled(self, symbol: str, enabled: bool):
        if not self.database_url:
            async with self._lock:
                sym = symbol.upper()
                if sym not in self._memory:
                    return False
                self._memory[sym] = bool(enabled)
                return True
        await self.initialize()
        return await asyncio.to_thread(self._enable_sync, symbol.upper(), enabled)
