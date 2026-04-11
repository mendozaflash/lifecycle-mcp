"""Tests for BaseHandler validation helpers (DB-02).

Validates:
- _validate_project_exists
- _validate_entity_exists (all entity types)
- _validate_not_archived
- _log_operation with project_id
"""

import pytest

from lifecycle_mcp.handlers.base_handler import BaseHandler
from mcp.types import TextContent


# -- Concrete subclass for testing abstract BaseHandler --


class ConcreteHandler(BaseHandler):
    def get_tool_definitions(self):
        return []

    async def handle_tool_call(self, tool_name, arguments):
        return self._create_response("ok")


@pytest.fixture
async def handler(v2_db_manager):
    return ConcreteHandler(v2_db_manager)


@pytest.fixture
async def seeded_db(v2_db_manager):
    """Insert a project and child entities for validation tests."""
    db = v2_db_manager
    # Insert a project
    await db.execute_query(
        "INSERT INTO projects (id, name, status, is_archived) VALUES (?, ?, ?, ?)",
        ["PROJ-0001", "Test Project", "active", 0],
    )
    # Insert an archived project
    await db.execute_query(
        "INSERT INTO projects (id, name, status, is_archived, archived_at) VALUES (?, ?, ?, ?, datetime('now'))",
        ["PROJ-0002", "Archived Project", "archived", 1],
    )
    # Insert a requirement under PROJ-0001
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority, is_archived) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["REQ-0001", "PROJ-0001", "FUNC", "Test Req", "Draft", "P1", 0],
    )
    # Insert an archived requirement
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority, is_archived, archived_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        ["REQ-0002", "PROJ-0001", "FUNC", "Archived Req", "Deprecated", "P2", 1],
    )
    # Insert a task under PROJ-0001
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, status, priority, is_archived) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["TASK-0001", "PROJ-0001", "Test Task", "Not Started", "P1", 0],
    )
    # Insert an architecture decision under PROJ-0001
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, status, is_archived) "
        "VALUES (?, ?, ?, ?, ?)",
        ["ADR-0001", "PROJ-0001", "Test ADR", "Draft", 0],
    )
    return db


# ── _validate_project_exists ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_project_exists_valid(handler, seeded_db):
    """Existing project returns None (no error)."""
    result = await handler._validate_project_exists("PROJ-0001")
    assert result is None


@pytest.mark.asyncio
async def test_validate_project_exists_nonexistent(handler, seeded_db):
    """Nonexistent project returns error string."""
    result = await handler._validate_project_exists("PROJ-9999")
    assert result is not None
    assert "PROJ-9999" in result
    assert isinstance(result, str)


# ── _validate_entity_exists ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_entity_exists_project(handler, seeded_db):
    """Existing project entity returns None."""
    result = await handler._validate_entity_exists("project", "PROJ-0001")
    assert result is None


@pytest.mark.asyncio
async def test_validate_entity_exists_requirement(handler, seeded_db):
    """Existing requirement entity returns None."""
    result = await handler._validate_entity_exists("requirement", "REQ-0001")
    assert result is None


@pytest.mark.asyncio
async def test_validate_entity_exists_task(handler, seeded_db):
    """Existing task entity returns None."""
    result = await handler._validate_entity_exists("task", "TASK-0001")
    assert result is None


@pytest.mark.asyncio
async def test_validate_entity_exists_architecture(handler, seeded_db):
    """Existing architecture entity returns None."""
    result = await handler._validate_entity_exists("architecture", "ADR-0001")
    assert result is None


@pytest.mark.asyncio
async def test_validate_entity_exists_nonexistent(handler, seeded_db):
    """Nonexistent entity returns error string."""
    result = await handler._validate_entity_exists("task", "TASK-9999")
    assert result is not None
    assert "TASK-9999" in result


@pytest.mark.asyncio
async def test_validate_entity_exists_unknown_type(handler, seeded_db):
    """Unknown entity type returns error string."""
    result = await handler._validate_entity_exists("widget", "W-0001")
    assert result is not None
    assert "widget" in result.lower()


# ── _validate_not_archived ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_not_archived_active(handler, seeded_db):
    """Active (not archived) entity returns None."""
    result = await handler._validate_not_archived("project", "PROJ-0001")
    assert result is None


@pytest.mark.asyncio
async def test_validate_not_archived_archived_project(handler, seeded_db):
    """Archived project returns error string."""
    result = await handler._validate_not_archived("project", "PROJ-0002")
    assert result is not None
    assert "archived" in result.lower()
    assert "PROJ-0002" in result


@pytest.mark.asyncio
async def test_validate_not_archived_archived_requirement(handler, seeded_db):
    """Archived requirement returns error string."""
    result = await handler._validate_not_archived("requirement", "REQ-0002")
    assert result is not None
    assert "archived" in result.lower()


@pytest.mark.asyncio
async def test_validate_not_archived_nonexistent(handler, seeded_db):
    """Nonexistent entity returns error string (not found)."""
    result = await handler._validate_not_archived("task", "TASK-9999")
    assert result is not None


@pytest.mark.asyncio
async def test_validate_not_archived_unknown_type(handler, seeded_db):
    """Unknown entity type returns error string."""
    result = await handler._validate_not_archived("widget", "W-0001")
    assert result is not None


# ── _log_operation with project_id ───────────────────────────────────


@pytest.mark.asyncio
async def test_log_operation_with_project_id(handler, seeded_db):
    """_log_operation stores project_id in lifecycle_events."""
    await handler._log_operation(
        entity_type="task",
        entity_id="TASK-0001",
        event_type="created",
        actor="TestUser",
        project_id="PROJ-0001",
    )
    row = await handler.db.execute_query(
        "SELECT entity_type, entity_id, event_type, actor, project_id "
        "FROM lifecycle_events WHERE entity_id = ?",
        ["TASK-0001"],
        fetch_one=True,
        row_factory=True,
    )
    assert row is not None
    assert row["entity_type"] == "task"
    assert row["entity_id"] == "TASK-0001"
    assert row["event_type"] == "created"
    assert row["actor"] == "TestUser"
    assert row["project_id"] == "PROJ-0001"


@pytest.mark.asyncio
async def test_log_operation_without_project_id(handler, seeded_db):
    """_log_operation works without project_id (backward compat)."""
    await handler._log_operation(
        entity_type="requirement",
        entity_id="REQ-0001",
        event_type="status_change",
    )
    row = await handler.db.execute_query(
        "SELECT project_id FROM lifecycle_events WHERE entity_id = ?",
        ["REQ-0001"],
        fetch_one=True,
        row_factory=True,
    )
    assert row is not None
    assert row["project_id"] is None
