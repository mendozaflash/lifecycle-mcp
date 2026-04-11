#!/usr/bin/env python3
"""
Database Manager for Lifecycle MCP Server (v2)

Async-only database manager using aiosqlite with connection pooling.
No legacy migrations -- fresh v2 schema applied on first init.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Centralized async database manager for lifecycle MCP operations.

    Uses an internal pool of aiosqlite connections managed via an asyncio.Queue
    and a Semaphore to bound concurrency.

    The constructor is synchronous and stores config only -- it does NOT touch
    disk.  Call ``await db.initialize()`` once before any async operations.
    """

    # ------------------------------------------------------------------
    # Construction (synchronous -- stores config only)
    # ------------------------------------------------------------------

    def __init__(
        self,
        db_path: str | None = None,
        pool_size: int = 5,
        timeout: float = 30.0,
        retry_attempts: int = 3,
        retry_delay: float = 0.1,
    ):
        """Initialize database manager -- sync only, stores config."""
        self.db_path: str = db_path or os.environ.get("LIFECYCLE_DB", "lifecycle.db")
        self.pool_size: int = pool_size
        self.timeout: float = timeout
        self.retry_attempts: int = retry_attempts
        self.retry_delay: float = retry_delay

        # Async pool state (populated in initialize())
        self._connections: list[aiosqlite.Connection] = []
        self._available: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(pool_size)
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Async initialization (call once after construction)
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the async connection pool and apply schema if needed.

        Must be awaited before first use.  Safe to call multiple times --
        subsequent calls are no-ops.
        """
        if self._initialized:
            return

        # Ensure parent directory exists
        db_dir = Path(self.db_path).parent
        if str(db_dir) not in ("", "."):
            db_dir.mkdir(parents=True, exist_ok=True)

        # Create pool connections with proper PRAGMAs
        for _ in range(self.pool_size):
            conn = await aiosqlite.connect(self.db_path, timeout=self.timeout)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA cache_size=10000")
            await conn.execute("PRAGMA temp_store=MEMORY")
            self._connections.append(conn)
            await self._available.put(conn)

        self._initialized = True

        # Check if schema is already applied
        needs_schema = not await self._schema_exists()

        if needs_schema:
            logger.info("Initializing v2 database schema at %s", self.db_path)
            await self._apply_schema()
            logger.info("Database schema initialized")
        else:
            logger.info("Database schema already present at %s", self.db_path)

        logger.info(
            "Async connection pool initialized: %d connections, %.1fs timeout",
            self.pool_size,
            self.timeout,
        )

    async def _schema_exists(self) -> bool:
        """Check if the v2 schema is already applied by looking for the projects table."""
        result = await self.execute_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'",
            fetch_one=True,
        )
        return result is not None

    async def _apply_schema(self) -> None:
        """Read and execute the v2 schema SQL file."""
        schema_path = Path(__file__).parent / "lifecycle-schema-v2.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found at {schema_path}")

        schema_sql = schema_path.read_text(encoding="utf-8")

        async with self.get_connection() as conn:
            await conn.executescript(schema_sql)

    # ------------------------------------------------------------------
    # Connection acquisition
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def get_connection(self, row_factory: bool = False):
        """Async context manager that borrows a connection from the pool.

        Every connection has ``PRAGMA foreign_keys = ON`` re-applied on
        checkout to guarantee FK enforcement even for recycled connections.

        Usage::

            async with db.get_connection(row_factory=True) as conn:
                cursor = await conn.execute("SELECT ...")
                rows = await cursor.fetchall()
        """
        if not self._initialized:
            await self.initialize()

        await self._semaphore.acquire()
        conn: aiosqlite.Connection | None = None
        try:
            conn = await asyncio.wait_for(self._available.get(), timeout=self.timeout)
            # Re-enforce FK on every checkout (survives pool recycling)
            await conn.execute("PRAGMA foreign_keys=ON")
            if row_factory:
                conn.row_factory = aiosqlite.Row
            yield conn
        finally:
            if conn is not None:
                conn.row_factory = None  # reset before returning to pool
                await self._available.put(conn)
            self._semaphore.release()

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def execute_query(
        self,
        query: str,
        params: list[Any] | None = None,
        fetch_one: bool = False,
        fetch_all: bool = False,
        row_factory: bool = False,
    ) -> Any:
        """Execute a query with retry logic.

        Returns:
            - A single row when *fetch_one* is True
            - A list of rows when *fetch_all* is True
            - ``cursor.lastrowid`` for write operations (INSERT/UPDATE/DELETE)
        """
        params = params or []
        last_error: Exception | None = None

        for attempt in range(self.retry_attempts):
            try:
                async with self.get_connection(row_factory=row_factory) as conn:
                    cursor = await conn.execute(query, params)

                    if fetch_one:
                        return await cursor.fetchone()
                    elif fetch_all:
                        return await cursor.fetchall()
                    else:
                        await conn.commit()
                        return cursor.lastrowid

            except Exception as exc:
                last_error = exc
                err_msg = str(exc).lower()
                if (
                    ("database is locked" in err_msg or "disk i/o error" in err_msg)
                    and attempt < self.retry_attempts - 1
                ):
                    logger.warning(
                        "execute_query failed (attempt %d/%d): %s",
                        attempt + 1,
                        self.retry_attempts,
                        exc,
                    )
                    await asyncio.sleep(self.retry_delay * (2**attempt))
                    continue
                raise

        # Should not reach here, but just in case
        raise last_error  # type: ignore[misc]

    async def execute_many(self, query: str, params_list: list[list[Any]]) -> None:
        """Execute a query multiple times with different parameter sets."""
        async with self.get_connection() as conn:
            await conn.executemany(query, params_list)
            await conn.commit()

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self, row_factory: bool = False):
        """Async context manager for explicit transactions.

        Acquires an immediate write lock (``BEGIN IMMEDIATE``), yields the
        connection, and commits on clean exit or rolls back on exception.

        Usage::

            async with db.transaction() as conn:
                await conn.execute("INSERT INTO ...")
                await conn.execute("UPDATE ...")
        """
        last_error: Exception | None = None

        for attempt in range(self.retry_attempts):
            try:
                async with self.get_connection(row_factory=row_factory) as conn:
                    await conn.execute("BEGIN IMMEDIATE")
                    try:
                        yield conn
                        await conn.commit()
                    except BaseException:
                        with suppress(Exception):
                            await conn.rollback()
                        raise
                return  # success
            except Exception as exc:
                last_error = exc
                err_msg = str(exc).lower()
                if "database is locked" in err_msg and attempt < self.retry_attempts - 1:
                    logger.warning(
                        "Transaction failed (attempt %d/%d): %s",
                        attempt + 1,
                        self.retry_attempts,
                        exc,
                    )
                    await asyncio.sleep(self.retry_delay * (2**attempt))
                    continue
                raise

        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------
    # CRUD convenience methods
    # ------------------------------------------------------------------

    async def insert_record(self, table: str, data: dict[str, Any]) -> int | None:
        """Insert a record and return ``cursor.lastrowid``."""
        columns = list(data.keys())
        placeholders = ["?" for _ in columns]
        values = list(data.values())
        query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
        return await self.execute_query(query, values)

    async def update_record(
        self, table: str, data: dict[str, Any], where_clause: str, where_params: list[Any]
    ) -> None:
        """Update records matching *where_clause*."""
        set_clauses = [f"{col} = ?" for col in data]
        values = list(data.values()) + where_params
        query = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {where_clause}"
        await self.execute_query(query, values)

    async def delete_record(self, table: str, where_clause: str, where_params: list[Any]) -> None:
        """Delete records matching *where_clause*."""
        query = f"DELETE FROM {table} WHERE {where_clause}"
        await self.execute_query(query, where_params)

    async def get_records(
        self,
        table: str,
        columns: str = "*",
        where_clause: str = "",
        where_params: list[Any] | None = None,
        order_by: str = "",
        limit: int | None = None,
        row_factory: bool = True,
    ) -> list:
        """Retrieve records with optional filtering and ordering."""
        where_params = where_params or []
        query = f"SELECT {columns} FROM {table}"
        if where_clause:
            query += f" WHERE {where_clause}"
        if order_by:
            query += f" ORDER BY {order_by}"
        if limit:
            query += f" LIMIT {limit}"
        return await self.execute_query(query, where_params, fetch_all=True, row_factory=row_factory)

    async def check_exists(self, table: str, where_clause: str, where_params: list[Any]) -> bool:
        """Return True if at least one record matches."""
        query = f"SELECT 1 FROM {table} WHERE {where_clause} LIMIT 1"
        result = await self.execute_query(query, where_params, fetch_one=True)
        return result is not None

    # ------------------------------------------------------------------
    # Atomic ID generation (v2)
    # ------------------------------------------------------------------

    async def generate_id(self, entity_type: str) -> tuple[str, int]:
        """Atomically generate next sequential ID for entity type.

        Returns (formatted_id, number).  E.g. ('REQ-0042', 42).

        Raises KeyError if *entity_type* is not one of the known types.
        """
        prefixes = {
            "requirement": "REQ",
            "task": "TASK",
            "architecture": "ADR",
            "project": "PROJ",
        }
        if entity_type not in prefixes:
            raise KeyError(f"Unknown entity type: {entity_type!r}")
        prefix = prefixes[entity_type]

        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE sequences SET next_val = next_val + 1 WHERE entity_type = ?",
                [entity_type],
            )
            cursor = await conn.execute(
                "SELECT next_val - 1 FROM sequences WHERE entity_type = ?",
                [entity_type],
            )
            row = await cursor.fetchone()
            number = row[0]

        return (f"{prefix}-{number:04d}", number)

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------

    async def configure_pool(
        self,
        pool_size: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Reconfigure connection pool settings (drains and recreates pool)."""
        old_config = {
            "pool_size": self.pool_size,
            "timeout": self.timeout,
        }

        if pool_size is not None:
            self.pool_size = pool_size
        if timeout is not None:
            self.timeout = timeout

        # Drain existing pool and rebuild
        await self.close()
        self._semaphore = asyncio.Semaphore(self.pool_size)
        self._available = asyncio.Queue()
        await self.initialize()

        return {
            "old_config": old_config,
            "new_config": {
                "pool_size": self.pool_size,
                "timeout": self.timeout,
            },
        }

    async def get_pool_stats(self) -> dict[str, Any]:
        """Return connection pool statistics."""
        return {
            "pooling_enabled": True,
            "pool_health": "healthy" if not self._available.empty() else "depleted",
            "pool_size": self.pool_size,
            "available_connections": self._available.qsize(),
            "total_connections": len(self._connections),
            "timeout": self.timeout,
            "initialized": self._initialized,
            "retry_config": {
                "retry_attempts": self.retry_attempts,
                "retry_delay": self.retry_delay,
            },
        }

    async def test_connection(self, timeout: float | None = None) -> dict[str, Any]:
        """Test database connectivity and measure response time."""
        start_time = time.monotonic()
        try:
            async with self.get_connection() as conn:
                cursor = await conn.execute("SELECT 1, datetime('now') as current_time")
                result = await cursor.fetchone()

            elapsed_ms = (time.monotonic() - start_time) * 1000
            return {
                "status": "success",
                "response_time_ms": round(elapsed_ms, 2),
                "database_time": result[1] if result else None,
                "pool_stats": await self.get_pool_stats(),
            }
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return {
                "status": "failed",
                "error": str(exc),
                "response_time_ms": round(elapsed_ms, 2),
                "pool_stats": await self.get_pool_stats(),
            }

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Drain the pool and close every aiosqlite connection."""
        # Drain the queue
        while not self._available.empty():
            with suppress(asyncio.QueueEmpty):
                self._available.get_nowait()

        # Close all connections
        for conn in self._connections:
            with suppress(Exception):
                await conn.close()

        self._connections.clear()
        self._initialized = False
        logger.info("Async database connection pool closed")

    # ------------------------------------------------------------------
    # Async context manager protocol
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "DatabaseManager":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
