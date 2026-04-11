"""Tests for StatusHandler v2 (BF-06).

Validates:
  - diff_project: lifecycle_events in time window, empty window
  - Error handling: nonexistent project
  - Tool definitions: only diff_project remains
"""

import json

import pytest

from lifecycle_mcp.handlers.status_handler import StatusHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def setup(v2_db_manager):
    """Create StatusHandler + two test projects. Returns (handler, db)."""
    handler = StatusHandler(v2_db_manager)
    db = v2_db_manager

    # Create 2 projects
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0001", "TestProject"]
    )
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0002", "OtherProject"]
    )

    return handler, db


# -- Helpers -----------------------------------------------------------------


def _text(result):
    """Extract text from MCP response."""
    return result[0].text


def _json(result):
    """Extract parsed JSON from MCP response."""
    return json.loads(result[0].text)


async def _add_req(db, req_id, project_id, status="Draft", priority="P1"):
    """Insert a requirement directly."""
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority) VALUES (?, ?, ?, ?, ?, ?)",
        [req_id, project_id, "FUNC", f"Req {req_id}", status, priority],
    )


async def _add_task(db, task_id, project_id, status="Not Started", priority="P1", assignee=None):
    """Insert a task directly."""
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, status, priority, assignee) VALUES (?, ?, ?, ?, ?, ?)",
        [task_id, project_id, f"Task {task_id}", status, priority, assignee],
    )


async def _add_adr(db, adr_id, project_id, status="Draft"):
    """Insert an architecture decision directly."""
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, context, decision, status) VALUES (?, ?, ?, ?, ?, ?)",
        [adr_id, project_id, f"ADR {adr_id}", "ctx", "dec", status],
    )


async def _add_blocking_relationship(db, blocker_id, blocked_id, project_id):
    """Create a 'blocks' relationship."""
    import uuid

    rel_id = f"rel-{uuid.uuid4().hex[:8]}"
    await db.execute_query(
        "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, relationship_type, project_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [rel_id, "task", blocker_id, "task", blocked_id, "blocks", project_id],
    )


# ===========================================================================
# diff_project
# ===========================================================================


class TestDiffProject:

    @pytest.mark.asyncio
    async def test_diff_captures_status_changes(self, setup):
        """Status changes appear in the diff window."""
        handler, db = setup

        await _add_task(db, "TASK-0001", "PROJ-0001", status="Not Started")
        # Trigger a status change via UPDATE -- the trigger will log to lifecycle_events
        await db.execute_query(
            "UPDATE tasks SET status = 'In Progress' WHERE id = ?", ["TASK-0001"]
        )
        await db.execute_query(
            "UPDATE tasks SET status = 'Complete' WHERE id = ?", ["TASK-0001"]
        )

        result = await handler.handle_tool_call(
            "diff_project",
            {
                "project_id": "PROJ-0001",
                "from_timestamp": "2000-01-01T00:00:00",
                "to_timestamp": "2099-12-31T23:59:59",
            },
        )
        data = _json(result)
        assert data["summary"]["tasks_changed"] >= 1
        # Should have at least 2 change events for TASK-0001
        task_changes = [c for c in data["changes"] if c["entity_id"] == "TASK-0001"]
        assert len(task_changes) == 2
        # Verify ordering or content
        statuses = [(c["from_status"], c["to_status"]) for c in task_changes]
        assert ("Not Started", "In Progress") in statuses
        assert ("In Progress", "Complete") in statuses

    @pytest.mark.asyncio
    async def test_diff_project_scoped(self, setup):
        """Only changes from the requested project are returned."""
        handler, db = setup

        await _add_task(db, "TASK-0001", "PROJ-0001", status="Not Started")
        await _add_task(db, "TASK-0002", "PROJ-0002", status="Not Started")

        await db.execute_query(
            "UPDATE tasks SET status = 'In Progress' WHERE id = ?", ["TASK-0001"]
        )
        await db.execute_query(
            "UPDATE tasks SET status = 'In Progress' WHERE id = ?", ["TASK-0002"]
        )

        result = await handler.handle_tool_call(
            "diff_project",
            {
                "project_id": "PROJ-0001",
                "from_timestamp": "2000-01-01T00:00:00",
                "to_timestamp": "2099-12-31T23:59:59",
            },
        )
        data = _json(result)
        assert data["summary"]["tasks_changed"] == 1
        assert all(c["entity_id"] == "TASK-0001" for c in data["changes"])

    @pytest.mark.asyncio
    async def test_diff_empty_window(self, setup):
        """Time window with no changes returns empty change set."""
        handler, db = setup

        result = await handler.handle_tool_call(
            "diff_project",
            {
                "project_id": "PROJ-0001",
                "from_timestamp": "2000-01-01T00:00:00",
                "to_timestamp": "2000-01-02T00:00:00",
            },
        )
        data = _json(result)
        assert data["changes"] == []
        assert data["summary"]["requirements_changed"] == 0
        assert data["summary"]["tasks_changed"] == 0
        assert data["summary"]["adrs_changed"] == 0

    @pytest.mark.asyncio
    async def test_diff_nonexistent_project(self, setup):
        """Returns error for nonexistent project."""
        handler, db = setup
        result = await handler.handle_tool_call(
            "diff_project",
            {
                "project_id": "PROJ-9999",
                "from_timestamp": "2000-01-01T00:00:00",
                "to_timestamp": "2099-12-31T23:59:59",
            },
        )
        text = _text(result)
        assert "ERROR" in text
        assert "PROJ-9999" in text

    @pytest.mark.asyncio
    async def test_diff_requirement_changes(self, setup):
        """Requirement status changes appear in diff."""
        handler, db = setup

        await _add_req(db, "REQ-0001", "PROJ-0001", status="Draft")
        await db.execute_query(
            "UPDATE requirements SET status = 'Under Review' WHERE id = ?", ["REQ-0001"]
        )

        result = await handler.handle_tool_call(
            "diff_project",
            {
                "project_id": "PROJ-0001",
                "from_timestamp": "2000-01-01T00:00:00",
                "to_timestamp": "2099-12-31T23:59:59",
            },
        )
        data = _json(result)
        assert data["summary"]["requirements_changed"] == 1
        req_changes = [c for c in data["changes"] if c["entity_type"] == "requirement"]
        assert len(req_changes) == 1
        assert req_changes[0]["from_status"] == "Draft"
        assert req_changes[0]["to_status"] == "Under Review"


# ===========================================================================
# Tool definitions & routing
# ===========================================================================


class TestToolDefinitions:

    def test_tool_count(self, setup):
        """Handler exposes 1 tool (diff_project only)."""
        handler, _ = setup
        tools = handler.get_tool_definitions()
        assert len(tools) == 1

    def test_tool_names(self, setup):
        """Only diff_project is present."""
        handler, _ = setup
        tools = handler.get_tool_definitions()
        names = {t["name"] for t in tools}
        assert names == {"diff_project"}

    @pytest.mark.asyncio
    async def test_unknown_tool(self, setup):
        """Unknown tool name returns error."""
        handler, _ = setup
        result = await handler.handle_tool_call("unknown_tool", {})
        text = _text(result)
        assert "ERROR" in text
        assert "unknown_tool" in text
