"""
Regression tests for HTTP concurrency bugs (Issues 1-5).

These tests assert the CURRENT broken behaviors exist in the unfixed codebase.
All tests should PASS against the unfixed code, proving the bugs are real.
Once fixes are applied, specific tests will need updating to assert the fixed behavior.
"""

import ast
import asyncio
import inspect
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lifecycle_mcp.database_manager import DatabaseManager
from lifecycle_mcp.handlers.interview_handler import InterviewHandler


# ---------------------------------------------------------------------------
# Test Class 1 — Issue 2: _get_existing_requirements uses wrong attribute
# ---------------------------------------------------------------------------
class TestGetExistingRequirementsBroken:
    """InterviewHandler._get_existing_requirements() silently fails because it
    references self.db_manager (does not exist) instead of self.db, and wraps
    the sync contextmanager with 'async with'.  The bare 'except Exception: return []'
    swallows the resulting AttributeError."""

    @pytest.mark.asyncio
    async def test_returns_empty_despite_existing_requirements(self, db_manager, interview_handler):
        """Bug: _get_existing_requirements always returns [] even when
        the database contains requirements, because self.db_manager
        raises AttributeError which is silently caught."""
        # Insert a requirement directly into the database
        db_manager.insert_record(
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

        # Verify the requirement exists at the DB level
        row = db_manager.execute_query(
            "SELECT id FROM requirements WHERE id = ?",
            ["REQ-0001-FUNC-00"],
            fetch_one=True,
        )
        assert row is not None, "Requirement should exist in DB"

        # Call the broken method — it should return data but returns []
        result = await interview_handler._get_existing_requirements()
        assert result == [], (
            "Bug confirmation: _get_existing_requirements returns [] "
            "even when requirements exist in the database"
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

    def test_get_connection_is_sync_contextmanager(self, db_manager):
        """Root cause: DatabaseManager.get_connection is a sync @contextmanager,
        not an async context manager. Using 'async with' on it is invalid."""
        # get_connection is a generator-based context manager (sync)
        assert not inspect.iscoroutinefunction(db_manager.get_connection), (
            "get_connection is NOT a coroutine function"
        )
        # Verify it's decorated with @contextmanager (returns a GeneratorContextManager)
        ctx = db_manager.get_connection()
        assert hasattr(ctx, "__enter__"), "get_connection returns a sync context manager"
        assert hasattr(ctx, "__exit__"), "get_connection returns a sync context manager"
        # It should NOT have async context manager protocol
        assert not hasattr(ctx, "__aenter__"), (
            "Bug confirmation: get_connection does NOT support 'async with'"
        )
        # Clean up the generator
        ctx.__enter__()
        ctx.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Test Class 2 — Issue 5: Row factory leak between pooled connections
# ---------------------------------------------------------------------------
class TestRowFactoryLeak:
    """Verify that the current codebase DOES reset row_factory in the finally
    block of get_connection().  These tests confirm the fix is already in place."""

    def test_row_factory_reset_after_checkout(self, db_manager):
        """After using get_connection(row_factory=True), the next checkout
        should have row_factory=None because the finally block resets it."""
        # First checkout with row_factory=True
        with db_manager.get_connection(row_factory=True) as conn1:
            assert conn1.row_factory is sqlite3.Row, "Should be sqlite3.Row during checkout"

        # Second checkout without row_factory — should be clean
        with db_manager.get_connection() as conn2:
            assert conn2.row_factory is None, (
                "row_factory should be None after previous checkout reset it"
            )

    def test_alternating_row_factory_no_leak(self, db_manager):
        """Multiple alternating checkouts with and without row_factory
        should never leak state between uses."""
        for i in range(5):
            # Checkout WITH row_factory
            with db_manager.get_connection(row_factory=True) as conn:
                assert conn.row_factory is sqlite3.Row, f"Iteration {i}: should be Row"

            # Checkout WITHOUT row_factory
            with db_manager.get_connection(row_factory=False) as conn:
                assert conn.row_factory is None, f"Iteration {i}: should be None"


# ---------------------------------------------------------------------------
# Test Class 3 — Issue 4: TOCTOU race in get_next_id / insert_record
# ---------------------------------------------------------------------------
class TestAtomicIdGeneration:
    """get_next_id() and insert_record() are separate calls with no atomicity.
    Concurrent callers can get the same 'next' ID, proving the TOCTOU race."""

    def test_concurrent_get_next_id_returns_same_value(self, db_manager):
        """Bug: When multiple threads call get_next_id on an empty table
        simultaneously, they all get the same ID because no insert has
        happened yet between the calls."""
        results = []
        barrier = threading.Barrier(3, timeout=5)

        def get_id():
            barrier.wait()  # Synchronize all threads to call at once
            next_id = db_manager.get_next_id("requirements", "CAST(SUBSTR(id, 5, 4) AS INTEGER)")
            results.append(next_id)

        threads = [threading.Thread(target=get_id) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All threads should get the same ID since no inserts happened
        assert len(results) == 3, "All threads should have completed"
        assert len(set(results)) == 1, (
            f"Bug confirmation: all concurrent get_next_id calls return the same value: {results}"
        )

    def test_get_next_id_and_insert_are_separate_calls(self, db_manager):
        """The TOCTOU pattern exists: get_next_id is a standalone method that
        does not atomically reserve the ID via insert."""
        # Verify get_next_id exists as a separate method
        assert hasattr(db_manager, "get_next_id"), "get_next_id method exists"
        assert hasattr(db_manager, "insert_record"), "insert_record method exists"

        # Verify they are separate functions (not a single atomic operation)
        assert db_manager.get_next_id is not db_manager.insert_record, (
            "get_next_id and insert_record are separate methods (TOCTOU pattern)"
        )

        # Verify get_next_id does NOT insert anything
        initial_count = db_manager.execute_query(
            "SELECT COUNT(*) FROM requirements", fetch_one=True
        )[0]
        db_manager.get_next_id("requirements", "CAST(SUBSTR(id, 5, 4) AS INTEGER)")
        after_count = db_manager.execute_query(
            "SELECT COUNT(*) FROM requirements", fetch_one=True
        )[0]
        assert initial_count == after_count, (
            "Bug confirmation: get_next_id does not insert — the ID is not reserved"
        )


# ---------------------------------------------------------------------------
# Test Class 4 — Issue 3: All DB methods are synchronous, blocking event loop
# ---------------------------------------------------------------------------
class TestEventLoopBlocking:
    """All DatabaseManager public methods are plain synchronous functions.
    When called from async handlers without run_in_executor, they block
    the event loop.  Also, retry backoff uses time.sleep (blocking)."""

    def test_execute_query_is_sync(self, db_manager):
        """execute_query is NOT a coroutine — it blocks the calling thread."""
        assert not inspect.iscoroutinefunction(db_manager.execute_query), (
            "Bug confirmation: execute_query is synchronous"
        )

    def test_insert_record_is_sync(self, db_manager):
        """insert_record is NOT a coroutine — it blocks the calling thread."""
        assert not inspect.iscoroutinefunction(db_manager.insert_record), (
            "Bug confirmation: insert_record is synchronous"
        )

    def test_get_records_is_sync(self, db_manager):
        """get_records is NOT a coroutine — it blocks the calling thread."""
        assert not inspect.iscoroutinefunction(db_manager.get_records), (
            "Bug confirmation: get_records is synchronous"
        )

    def test_update_record_is_sync(self, db_manager):
        """update_record is NOT a coroutine — it blocks the calling thread."""
        assert not inspect.iscoroutinefunction(db_manager.update_record), (
            "Bug confirmation: update_record is synchronous"
        )

    def test_time_sleep_in_retry_backoff(self):
        """The database_manager.py source uses time.sleep() for retry backoff,
        which blocks the entire event loop when called from async context."""
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

        assert len(sleep_calls) > 0, (
            f"Bug confirmation: time.sleep found at lines {sleep_calls} in database_manager.py"
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

    def test_no_interview_lock_attribute(self, interview_handler):
        """Bug: No asyncio.Lock protects interview_sessions dict.
        Concurrent access can corrupt session state."""
        assert not hasattr(interview_handler, "_interview_lock"), (
            "Bug confirmation: no _interview_lock attribute exists"
        )

    def test_no_architectural_lock_attribute(self, interview_handler):
        """Bug: No asyncio.Lock protects architectural_sessions dict.
        Concurrent access can corrupt session state."""
        assert not hasattr(interview_handler, "_architectural_lock"), (
            "Bug confirmation: no _architectural_lock attribute exists"
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
