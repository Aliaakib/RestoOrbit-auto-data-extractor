"""Async PostgreSQL connection pool built on pure-Python pg8000 (DLL-free, cross-platform)."""

import asyncio
import queue
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import unquote, urlparse

import pg8000.dbapi

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def parse_postgres_dsn(database_url: str) -> dict[str, Any]:
    """Parse a PostgreSQL connection URL into pg8000 keyword arguments."""
    parsed = urlparse(database_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/postgres").lstrip("/"),
    }


class AsyncDictCursor:
    """Async cursor wrapper around a pg8000 DB-API cursor returning dict rows."""

    def __init__(self, raw_cursor: Any) -> None:
        self._cur = raw_cursor

    async def __aenter__(self) -> "AsyncDictCursor":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await asyncio.to_thread(self._cur.close)

    def _to_dict(self, row: tuple[Any, ...] | list[Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        columns = [desc[0] for desc in (self._cur.description or [])]
        return dict(zip(columns, row))

    async def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> None:
        if params is not None:
            await asyncio.to_thread(self._cur.execute, sql, tuple(params))
        else:
            await asyncio.to_thread(self._cur.execute, sql)

    async def fetchone(self) -> dict[str, Any] | None:
        row = await asyncio.to_thread(self._cur.fetchone)
        return self._to_dict(row)

    async def fetchall(self) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self._cur.fetchall)
        if not rows:
            return []
        columns = [desc[0] for desc in (self._cur.description or [])]
        return [dict(zip(columns, row)) for row in rows]


class AsyncPgConnection:
    """Async connection wrapper over a pooled pg8000 connection."""

    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn

    def cursor(self) -> AsyncDictCursor:
        return AsyncDictCursor(self._conn.cursor())

    async def commit(self) -> None:
        await asyncio.to_thread(self._conn.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._conn.rollback)

    async def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> None:
        async with self.cursor() as cur:
            await cur.execute(sql, params)


class DatabasePool:
    """Thread-safe bounded PostgreSQL connection pool using pure-Python pg8000."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dsn_kwargs = parse_postgres_dsn(settings.DATABASE_URL)
        self._pool: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=settings.DB_POOL_MAX_SIZE)
        self._opened = False
        self._created_count = 0
        self._lock = asyncio.Lock()

    def _create_raw_connection(self) -> Any:
        conn = pg8000.dbapi.connect(
            timeout=self._settings.DB_CONNECT_TIMEOUT,
            **self._dsn_kwargs,
        )
        conn.autocommit = False
        return conn

    async def open(self) -> None:
        """Mark the pool ready and pre-warm minimum connections if PostgreSQL is reachable."""
        if self._opened:
            return
        self._opened = True
        try:
            for _ in range(self._settings.DB_POOL_MIN_SIZE):
                raw_conn = await asyncio.to_thread(self._create_raw_connection)
                self._pool.put_nowait(raw_conn)
                self._created_count += 1
            logger.info(
                "db_pool_initialized",
                min_size=self._settings.DB_POOL_MIN_SIZE,
                max_size=self._settings.DB_POOL_MAX_SIZE,
            )
        except Exception as exc:
            logger.warning("db_pool_prewarm_deferred", error=str(exc))

    async def close(self) -> None:
        """Drain and close all pooled PostgreSQL connections."""
        self._opened = False
        while not self._pool.empty():
            try:
                raw_conn = self._pool.get_nowait()
                await asyncio.to_thread(raw_conn.close)
            except Exception:
                pass
        self._created_count = 0
        logger.info("db_pool_closed")

    async def _acquire(self) -> Any:
        try:
            raw_conn = self._pool.get_nowait()
            # Validate liveness quickly; recreate if stale
            try:
                cur = raw_conn.cursor()
                await asyncio.to_thread(cur.execute, "SELECT 1")
                await asyncio.to_thread(cur.close)
                await asyncio.to_thread(raw_conn.rollback)
                return raw_conn
            except Exception:
                try:
                    await asyncio.to_thread(raw_conn.close)
                except Exception:
                    pass
        except queue.Empty:
            pass

        async with self._lock:
            return await asyncio.to_thread(self._create_raw_connection)

    async def _release(self, raw_conn: Any) -> None:
        if not self._opened:
            try:
                await asyncio.to_thread(raw_conn.close)
            except Exception:
                pass
            return
        try:
            self._pool.put_nowait(raw_conn)
        except queue.Full:
            try:
                await asyncio.to_thread(raw_conn.close)
            except Exception:
                pass

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncPgConnection]:
        """Acquire a pooled connection with automatic ACID commit/rollback."""
        raw_conn = await self._acquire()
        wrapped = AsyncPgConnection(raw_conn)
        try:
            yield wrapped
            await wrapped.commit()
        except Exception:
            await wrapped.rollback()
            raise
        finally:
            await self._release(raw_conn)

    async def health_check(self) -> dict[str, Any]:
        """Verify database connectivity with a lightweight query."""
        try:
            async with self.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1 AS ok, current_database() AS db")
                    row = await cur.fetchone()
                    return {"connected": True, "database": row["db"] if row else None}
        except Exception as exc:
            logger.warning("db_health_check_failed", error=str(exc))
            return {"connected": False, "error": str(exc)}
