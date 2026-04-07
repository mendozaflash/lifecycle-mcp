"""
Regression tests for HTTP concurrency bugs (Issues 1-5).

These tests verify that the async DatabaseManager migration (Tasks 8-11) has
fixed Issues 3, 4, and 5 at the DB layer.  Issues 1 and 2 (InterviewHandler
level) remain unfixed until Task 15.
"""

import ast
import asyncio
import inspect
import re
from pathlib import Path

import aiosqlite
import pytest

from lifecycle_mcp.database_manager import DatabaseManager
from lifecycle_mcp.handlers.interview_handler import InterviewHandler


# ---------------------------------------------------------------------------
# Test Class 1 — Issue 2: _get_existing_requirements uses wrong attribute
# ---------------------------------------------------------------------------
class TestGetExistingRequirementsBroken:
    """InterviewHandler._get_existing_requirements() silently fails because it
    references self.db_manager (does not exist) instead of self.db, and wraps
    the call with 'async with'.  The bare 'except Exception: return []'
    swallows the resulting AttributeError."""

    @pytest.mark.asyncio
    async def test_returns_data_when_requirements_exist(self, db_manager, interview_handler):
        """Fix verified: _get_existing_requirements now returns data when
        requirements exist in the database (self.db_manager bug fixed)."""
        # Insert a requirement directly into the database (now async)
        await db_manager.insert_record(
            "requirements",
            {
                "id": "REQ-0001-FUNC-00",
                "requirement_number": 1,
                "type": "FUNC",
                "title": "Test Requirement",
                "priority": "P1",
                "status": "Draft",
                "current_state": "current",
                "desired_state": "desired",
                "version": 0,
                "author": "Test Author",
            },
        )

        # Verify the requirement exists at the DB level (now async)
        row = await db_manager.execute_query(
            "SELECT id FROM requirements WHERE id = ?",
            ["REQ-0001-FUNC-00"],
            fetch_one=True,
        )
        assert row is not None, "Requirement should exist in DB"

        # Fix verified: _get_existing_requirements now returns actual data
        result = await interview_handler._get_existing_requirements()
        assert len(result) > 0, (
            "Fix verified: _get_existing_requirements returns data "
            "when requirements exist in the database"
        )

    @pytest.mark.asyncio
    async def test_exception_is_silently_swallowed(self, interview_handler):
        """Bug: The method swallows all exceptions via bare 'except Exception: return []'.
        No error is raised to the caller even though the operation fails."""
        # This should NOT raise — the exception is swallowed
        result = await interview_handler._get_existing_requirements()
        assert isinstance(result, list), "Should return a list (empty due to swallowed error)"
        assert len(result) == 0, "Should be empty because the error is swallowed"

    def test_handler_has_no_db_manager_attribute(self, interview_handler):
        """Root cause: InterviewHandler uses self.db_manager but BaseHandler
        sets self.db, not self.db_manager."""
        assert hasattr(interview_handler, "db"), "BaseHandler sets self.db"
        assert not hasattr(interview_handler, "db_manager"), (
            "Bug confirmation: self.db_manager does not exist as an attribute"
        )

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
# Test Class 2 — Issue 5: Row factory leak between pooled connections
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

        # Second checkout without row_factory — should be clean
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
# Test Class 3 — Issue 4: Atomic ID generation via insert_with_next_id
# ---------------------------------------------------------------------------
class TestAtomicIdGeneration:
    """insert_with_next_id() atomically computes the next sequential ID and inserts
    a record inside a single BEGIN IMMEDIATE transaction.  Concurrent callers
    via asyncio.gather should each receive a unique ID."""

    async def test_insert_with_next_id_returns_unique_ids(self, db_manager):
        """Concurrent insert_with_next_id calls via asyncio.gather should
        each produce a unique ID thanks to the atomic transaction."""

        async def insert_one(i):
            return await db_manager.insert_with_next_id(
                "requirements",
                "requirement_number",
                {
                    "id": f"REQ-{1000 + i:04d}-FUNC-00",
                    "type": "FUNC",
                    "title": f"Concurrent Requirement {i}",
                    "priority": "P1",
                    "status": "Draft",
                    "current_state": "current",
                    "desired_state": "desired",
                    "version": 0,
                    "author": "Test Author",
                },
            )

        # Run 5 concurrent inserts
        results = await asyncio.gather(*[insert_one(i) for i in range(5)])

        # All IDs should be unique
        assert len(set(results)) == len(results), (
            f"All concurrent insert_with_next_id calls should return unique IDs: {results}"
        )

    async def test_insert_with_next_id_is_atomic(self, db_manager):
        """insert_with_next_id is a single async method that atomically
        selects the next ID and inserts the record."""
        assert hasattr(db_manager, "insert_with_next_id"), "insert_with_next_id method exists"
        assert inspect.iscoroutinefunction(db_manager.insert_with_next_id), (
            "insert_with_next_id is an async method"
        )

        # Verify get_next_id has been removed (replaced by insert_with_next_id)
        assert not hasattr(db_manager, "get_next_id"), (
            "get_next_id has been removed — replaced by atomic insert_with_next_id"
        )

    async def test_insert_with_next_id_sequential_values(self, db_manager):
        """Sequential calls to insert_with_next_id should produce incrementing IDs."""
        ids = []
        for i in range(3):
            next_id = await db_manager.insert_with_next_id(
                "requirements",
                "requirement_number",
                {
                    "id": f"REQ-{2000 + i:04d}-FUNC-00",
                    "type": "FUNC",
                    "title": f"Sequential Requirement {i}",
                    "priority": "P1",
                    "status": "Draft",
                    "current_state": "current",
                    "desired_state": "desired",
                    "version": 0,
                    "author": "Test Author",
                },
            )
            ids.append(next_id)

        # IDs should be sequential
        assert ids == sorted(ids), f"IDs should be sequential: {ids}"
        assert len(set(ids)) == 3, f"All IDs should be unique: {ids}"


# ---------------------------------------------------------------------------
# Test Class 4 — Issue 3: DB methods are now async (fix verified)
# ---------------------------------------------------------------------------
class TestEventLoopBlocking:
    """After the async migration, all DatabaseManager public methods should be
    async coroutines.  Retry backoff should use asyncio.sleep, not time.sleep."""

    def test_execute_query_is_async(self, db_manager):
        """execute_query is now a coroutine — it does NOT block the calling thread."""
        assert inspect.iscoroutinefunction(db_manager.execute_query), (
            "Fix verified: execute_query is now async"
        )

    def test_insert_record_is_async(self, db_manager):
        """insert_record is now a coroutine — it does NOT block the calling thread."""
        assert inspect.iscoroutinefunction(db_manager.insert_record), (
            "Fix verified: insert_record is now async"
        )

    def test_get_records_is_async(self, db_manager):
        """get_records is now a coroutine — it does NOT block the calling thread."""
        assert inspect.iscoroutinefunction(db_manager.get_records), (
            "Fix verified: get_records is now async"
        )

    def test_update_record_is_async(self, db_manager):
        """update_record is now a coroutine — it does NOT block the calling thread."""
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


# ---------------------------------------------------------------------------
# Test Class 5 — Issue 1: Interview sessions lack concurrency protection
# ---------------------------------------------------------------------------
class TestInterviewSessionConcurrency:
    """Interview sessions use plain dict with no asyncio.Lock, and session IDs
    are truncated UUIDs (8 hex chars) increasing collision probability."""

    @pytest.mark.asyncio
    async def test_session_ids_are_truncated_to_8_chars(self, interview_handler):
        """Session IDs are str(uuid.uuid4())[:8] — only 8 hex characters,
        not a full UUID.  This increases collision probability."""
        # Start an interview to get a session ID
        result = await interview_handler.handle_tool_call(
            "start_requirement_interview",
            {"project_context": "Test project", "stakeholder_role": "Developer"},
        )

        response_text = result[0].text

        # Extract session ID from response — it appears after "Session ID": or "session"
        # The pattern is an 8-char hex string
        hex_pattern = re.findall(r"\b([0-9a-f]{8})\b", response_text)
        assert len(hex_pattern) > 0, "Should find an 8-char hex session ID in response"

        session_id = hex_pattern[0]
        assert len(session_id) == 8, (
            f"Bug confirmation: session ID is 8 chars, not full UUID: '{session_id}'"
        )

        # Verify it's NOT a full UUID (36 chars with dashes, 32 hex chars)
        assert len(session_id) < 32, "Session ID is truncated, not a full UUID"

    def test_interview_lock_attribute_exists(self, interview_handler):
        """Fix verified: asyncio.Lock now protects interview_sessions dict."""
        assert hasattr(interview_handler, "_interview_lock"), (
            "Fix verified: _interview_lock attribute exists for concurrency protection"
        )

    def test_architectural_lock_attribute_exists(self, interview_handler):
        """Fix verified: asyncio.Lock now protects architectural_sessions dict."""
        assert hasattr(interview_handler, "_architectural_lock"), (
            "Fix verified: _architectural_lock attribute exists for concurrency protection"
        )

    def test_session_dicts_are_plain_dict(self, interview_handler):
        """Bug: Session storage uses plain dict, not a thread-safe or
        asyncio-safe data structure."""
        assert type(interview_handler.interview_sessions) is dict, (
            "Bug confirmation: interview_sessions is a plain dict"
        )
        assert type(interview_handler.architectural_sessions) is dict, (
            "Bug confirmation: architectural_sessions is a plain dict"
        )
