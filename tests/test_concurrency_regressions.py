"""
Regression tests for HTTP concurrency bugs (Issues 3-5).

These tests verify that the async DatabaseManager migration has
fixed Issues 3, 4, and 5 at the DB layer.

Note: Issues 1 and 2 (InterviewHandler) are no longer relevant --
InterviewHandler was removed in the v2 rearchitecture.
"""

import ast
import asyncio
import inspect
from pathlib import Path

import aiosqlite


# ---------------------------------------------------------------------------
# Test Class 1 -- async connection manager
# ---------------------------------------------------------------------------
class TestAsyncConnectionManager:
    """Verify that DatabaseManager.get_connection is an async context manager."""

    async def test_get_connection_is_now_async_contextmanager(self, db_manager):
        """Verify: DatabaseManager.get_connection is now an @asynccontextmanager,
        supporting 'async with' for proper async usage."""
        # get_connection should return an async context manager
        ctx = db_manager.get_connection()
        assert hasattr(ctx, "__aenter__"), "get_connection now returns an async context manager"
        assert hasattr(ctx, "__aexit__"), "get_connection now returns an async context manager"

        # It should actually work with async with
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute("SELECT 1")
            row = await cursor.fetchone()
            assert row is not None, "Connection should be usable via async with"


# ---------------------------------------------------------------------------
# Test Class 2 -- Issue 5: Row factory leak between pooled connections
# ---------------------------------------------------------------------------
class TestRowFactoryLeak:
    """Verify that the async connection pool properly resets row_factory
    when connections are returned to the pool."""

    async def test_row_factory_reset_after_checkout(self, db_manager):
        """After using get_connection(row_factory=True), the next checkout
        should have row_factory=None because the finally block resets it."""
        # First checkout with row_factory=True
        async with db_manager.get_connection(row_factory=True) as conn1:
            assert conn1.row_factory is aiosqlite.Row, "Should be aiosqlite.Row during checkout"

        # Second checkout without row_factory -- should be clean
        async with db_manager.get_connection() as conn2:
            assert conn2.row_factory is None, (
                "row_factory should be None after previous checkout reset it"
            )

    async def test_alternating_row_factory_no_leak(self, db_manager):
        """Multiple alternating checkouts with and without row_factory
        should never leak state between uses."""
        for i in range(5):
            # Checkout WITH row_factory
            async with db_manager.get_connection(row_factory=True) as conn:
                assert conn.row_factory is aiosqlite.Row, f"Iteration {i}: should be Row"

            # Checkout WITHOUT row_factory
            async with db_manager.get_connection(row_factory=False) as conn:
                assert conn.row_factory is None, f"Iteration {i}: should be None"


# ---------------------------------------------------------------------------
# Test Class 3 -- Issue 4: Atomic ID generation via generate_id (v2)
# ---------------------------------------------------------------------------
class TestAtomicIdGeneration:
    """generate_id() atomically computes the next sequential ID using the
    sequences table inside a single BEGIN IMMEDIATE transaction.  Concurrent
    callers via asyncio.gather should each receive a unique ID."""

    async def test_generate_id_returns_unique_ids(self, db_manager):
        """Concurrent generate_id calls via asyncio.gather should each
        produce a unique ID thanks to the atomic transaction."""
        results = await asyncio.gather(
            *[db_manager.generate_id("requirement") for _ in range(5)]
        )

        # All IDs should be unique
        ids = [r[0] for r in results]
        assert len(set(ids)) == len(ids), (
            f"All concurrent generate_id calls should return unique IDs: {ids}"
        )

    async def test_generate_id_is_async(self, db_manager):
        """generate_id is a coroutine for non-blocking use."""
        assert hasattr(db_manager, "generate_id"), "generate_id method exists"
        assert inspect.iscoroutinefunction(db_manager.generate_id), (
            "generate_id is an async method"
        )

        # insert_with_next_id was removed in v2, replaced by generate_id
        assert not hasattr(db_manager, "insert_with_next_id"), (
            "insert_with_next_id removed in v2 -- replaced by generate_id"
        )
        assert not hasattr(db_manager, "get_next_id"), (
            "get_next_id removed -- replaced by generate_id"
        )

    async def test_generate_id_sequential_values(self, db_manager):
        """Sequential calls to generate_id should produce incrementing IDs."""
        ids = []
        for _ in range(3):
            formatted_id, number = await db_manager.generate_id("requirement")
            ids.append(number)

        # Numbers should be strictly increasing
        assert ids == sorted(ids), f"IDs should be sequential: {ids}"
        assert len(set(ids)) == 3, f"All IDs should be unique: {ids}"


# ---------------------------------------------------------------------------
# Test Class 4 -- Issue 3: DB methods are now async (fix verified)
# ---------------------------------------------------------------------------
class TestEventLoopBlocking:
    """After the async migration, all DatabaseManager public methods should be
    async coroutines.  Retry backoff should use asyncio.sleep, not time.sleep."""

    def test_execute_query_is_async(self, db_manager):
        """execute_query is now a coroutine -- it does NOT block the calling thread."""
        assert inspect.iscoroutinefunction(db_manager.execute_query), (
            "Fix verified: execute_query is now async"
        )

    def test_insert_record_is_async(self, db_manager):
        """insert_record is now a coroutine -- it does NOT block the calling thread."""
        assert inspect.iscoroutinefunction(db_manager.insert_record), (
            "Fix verified: insert_record is now async"
        )

    def test_get_records_is_async(self, db_manager):
        """get_records is now a coroutine -- it does NOT block the calling thread."""
        assert inspect.iscoroutinefunction(db_manager.get_records), (
            "Fix verified: get_records is now async"
        )

    def test_update_record_is_async(self, db_manager):
        """update_record is now a coroutine -- it does NOT block the calling thread."""
        assert inspect.iscoroutinefunction(db_manager.update_record), (
            "Fix verified: update_record is now async"
        )

    def test_no_time_sleep_in_retry_backoff(self):
        """After async migration, retry backoff uses asyncio.sleep,
        not blocking time.sleep."""
        source_path = Path(__file__).parent.parent / "src" / "lifecycle_mcp" / "database_manager.py"
        source_code = source_path.read_text(encoding="utf-8")

        tree = ast.parse(source_code)

        # Find all calls to time.sleep in the AST
        sleep_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Match time.sleep(...)
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "sleep"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "time"
                ):
                    sleep_calls.append(node.lineno)

        assert len(sleep_calls) == 0, (
            f"Fix verified: no time.sleep calls should remain in database_manager.py "
            f"(found at lines {sleep_calls})"
        )

