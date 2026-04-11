"""Tests for StatusHandler v2 (DB-10).

Validates:
  - get_project_status: project-scoped, requirement/task/ADR breakdowns, completion %, blocked tasks
  - get_project_metrics: structured JSON scoped to project
  - diff_project: lifecycle_events in time window, empty window
  - Error handling: nonexistent project, empty project
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
# get_project_status
# ===========================================================================


class TestGetProjectStatus:

    @pytest.mark.asyncio
    async def test_empty_project(self, setup):
        """Empty project shows zero counts without errors."""
        handler, db = setup
        result = await handler.handle_tool_call("get_project_status", {"project_id": "PROJ-0001"})
        text = _text(result)
        assert "PROJ-0001" in text
        assert "TestProject" in text
        assert "Requirements: 0" in text
        assert "Tasks: 0" in text
        assert "Architecture: 0" in text

    @pytest.mark.asyncio
    async def test_project_scoped(self, setup):
        """Status only counts entities in the requested project."""
        handler, db = setup

        # Add entities to PROJ-0001
        await _add_req(db, "REQ-0001", "PROJ-0001", status="Draft")
        await _add_task(db, "TASK-0001", "PROJ-0001", status="Not Started")

        # Add entities to PROJ-0002 (should NOT appear)
        await _add_req(db, "REQ-0002", "PROJ-0002", status="Draft")
        await _add_req(db, "REQ-0003", "PROJ-0002", status="Draft")
        await _add_task(db, "TASK-0002", "PROJ-0002", status="In Progress")

        result = await handler.handle_tool_call("get_project_status", {"project_id": "PROJ-0001"})
        text = _text(result)
        assert "Requirements: 1" in text
        assert "Tasks: 1" in text

    @pytest.mark.asyncio
    async def test_requirement_status_breakdown(self, setup):
        """Requirement counts broken down by status."""
        handler, db = setup

        await _add_req(db, "REQ-0001", "PROJ-0001", status="Draft")
        await _add_req(db, "REQ-0002", "PROJ-0001", status="Draft")
        await _add_req(db, "REQ-0003", "PROJ-0001", status="Approved")
        await _add_req(db, "REQ-0004", "PROJ-0001", status="Implemented")
        await _add_req(db, "REQ-0005", "PROJ-0001", status="Implemented")

        result = await handler.handle_tool_call("get_project_status", {"project_id": "PROJ-0001"})
        text = _text(result)
        assert "Requirements: 5 total" in text
        assert "2 Draft" in text
        assert "1 Approved" in text
        assert "2 Implemented" in text

    @pytest.mark.asyncio
    async def test_task_status_breakdown_and_completion(self, setup):
        """Task counts broken down by status with completion percentage."""
        handler, db = setup

        await _add_task(db, "TASK-0001", "PROJ-0001", status="Not Started")
        await _add_task(db, "TASK-0002", "PROJ-0001", status="In Progress")
        await _add_task(db, "TASK-0003", "PROJ-0001", status="Complete")
        await _add_task(db, "TASK-0004", "PROJ-0001", status="Complete")

        result = await handler.handle_tool_call("get_project_status", {"project_id": "PROJ-0001"})
        text = _text(result)
        assert "Tasks: 4 total" in text
        assert "1 Not Started" in text
        assert "1 In Progress" in text
        assert "2 Complete" in text
        assert "50%" in text

    @pytest.mark.asyncio
    async def test_adr_status_breakdown(self, setup):
        """ADR counts broken down by status."""
        handler, db = setup

        await _add_adr(db, "ADR-0001", "PROJ-0001", status="Draft")
        await _add_adr(db, "ADR-0002", "PROJ-0001", status="Accepted")
        await _add_adr(db, "ADR-0003", "PROJ-0001", status="Accepted")

        result = await handler.handle_tool_call("get_project_status", {"project_id": "PROJ-0001"})
        text = _text(result)
        assert "Architecture: 3 total" in text
        assert "1 Draft" in text
        assert "2 Accepted" in text

    @pytest.mark.asyncio
    async def test_blocked_tasks_shown(self, setup):
        """Blocked tasks shown when include_blocked=true."""
        handler, db = setup

        await _add_task(db, "TASK-0001", "PROJ-0001", status="In Progress")
        await _add_task(db, "TASK-0002", "PROJ-0001", status="Blocked")
        await _add_blocking_relationship(db, "TASK-0001", "TASK-0002", "PROJ-0001")

        result = await handler.handle_tool_call(
            "get_project_status", {"project_id": "PROJ-0001", "include_blocked": True}
        )
        text = _text(result)
        assert "Blocked" in text
        assert "TASK-0002" in text
        assert "TASK-0001" in text

    @pytest.mark.asyncio
    async def test_blocked_tasks_hidden_by_default(self, setup):
        """Blocked tasks section not present when include_blocked=false."""
        handler, db = setup

        await _add_task(db, "TASK-0001", "PROJ-0001", status="In Progress")
        await _add_task(db, "TASK-0002", "PROJ-0001", status="Blocked")
        await _add_blocking_relationship(db, "TASK-0001", "TASK-0002", "PROJ-0001")

        result = await handler.handle_tool_call(
            "get_project_status", {"project_id": "PROJ-0001", "include_blocked": False}
        )
        text = _text(result)
        # "Blocked Tasks:" section should not appear (though "Blocked" may appear in task status breakdown)
        assert "Blocked Tasks:" not in text

    @pytest.mark.asyncio
    async def test_nonexistent_project(self, setup):
        """Returns error for nonexistent project."""
        handler, db = setup
        result = await handler.handle_tool_call("get_project_status", {"project_id": "PROJ-9999"})
        text = _text(result)
        assert "ERROR" in text
        assert "PROJ-9999" in text

    @pytest.mark.asyncio
    async def test_excludes_archived_entities(self, setup):
        """Archived entities are excluded from counts."""
        handler, db = setup

        await _add_req(db, "REQ-0001", "PROJ-0001", status="Draft")
        await _add_req(db, "REQ-0002", "PROJ-0001", status="Draft")
        # Archive one
        await db.execute_query(
            "UPDATE requirements SET is_archived = 1 WHERE id = ?", ["REQ-0002"]
        )

        result = await handler.handle_tool_call("get_project_status", {"project_id": "PROJ-0001"})
        text = _text(result)
        assert "Requirements: 1" in text


# ===========================================================================
# get_project_metrics
# ===========================================================================


class TestGetProjectMetrics:

    @pytest.mark.asyncio
    async def test_metrics_structure(self, setup):
        """Metrics returns proper JSON structure scoped to project."""
        handler, db = setup

        await _add_req(db, "REQ-0001", "PROJ-0001", status="Draft", priority="P0")
        await _add_req(db, "REQ-0002", "PROJ-0001", status="Approved", priority="P1")
        await _add_task(db, "TASK-0001", "PROJ-0001", status="Complete", priority="P1")
        await _add_task(db, "TASK-0002", "PROJ-0001", status="In Progress", priority="P2")
        await _add_adr(db, "ADR-0001", "PROJ-0001", status="Accepted")

        result = await handler.handle_tool_call("get_project_metrics", {"project_id": "PROJ-0001"})
        metrics = _json(result)

        assert metrics["project_id"] == "PROJ-0001"
        assert metrics["requirements"]["total"] == 2
        assert metrics["requirements"]["by_status"]["Draft"] == 1
        assert metrics["requirements"]["by_status"]["Approved"] == 1
        assert metrics["requirements"]["by_priority"]["P0"] == 1
        assert metrics["requirements"]["by_priority"]["P1"] == 1
        assert metrics["tasks"]["total"] == 2
        assert metrics["tasks"]["by_status"]["Complete"] == 1
        assert metrics["tasks"]["by_status"]["In Progress"] == 1
        assert metrics["tasks"]["completion_pct"] == 50.0
        assert metrics["architecture"]["total"] == 1
        assert metrics["architecture"]["by_status"]["Accepted"] == 1

    @pytest.mark.asyncio
    async def test_metrics_project_scoped(self, setup):
        """Metrics only include entities from the requested project."""
        handler, db = setup

        # PROJ-0001
        await _add_req(db, "REQ-0001", "PROJ-0001", status="Draft")
        # PROJ-0002
        await _add_req(db, "REQ-0002", "PROJ-0002", status="Draft")
        await _add_req(db, "REQ-0003", "PROJ-0002", status="Approved")

        result = await handler.handle_tool_call("get_project_metrics", {"project_id": "PROJ-0001"})
        metrics = _json(result)
        assert metrics["requirements"]["total"] == 1

    @pytest.mark.asyncio
    async def test_metrics_by_assignee(self, setup):
        """Tasks grouped by assignee."""
        handler, db = setup

        await _add_task(db, "TASK-0001", "PROJ-0001", assignee="Alice")
        await _add_task(db, "TASK-0002", "PROJ-0001", assignee="Alice")
        await _add_task(db, "TASK-0003", "PROJ-0001", assignee="Bob")
        await _add_task(db, "TASK-0004", "PROJ-0001", assignee=None)

        result = await handler.handle_tool_call("get_project_metrics", {"project_id": "PROJ-0001"})
        metrics = _json(result)
        assert metrics["tasks"]["by_assignee"]["Alice"] == 2
        assert metrics["tasks"]["by_assignee"]["Bob"] == 1
        assert metrics["tasks"]["by_assignee"]["Unassigned"] == 1

    @pytest.mark.asyncio
    async def test_metrics_blocked_count(self, setup):
        """Blocked count is included in metrics."""
        handler, db = setup

        await _add_task(db, "TASK-0001", "PROJ-0001", status="In Progress")
        await _add_task(db, "TASK-0002", "PROJ-0001", status="Blocked")
        await _add_blocking_relationship(db, "TASK-0001", "TASK-0002", "PROJ-0001")

        result = await handler.handle_tool_call("get_project_metrics", {"project_id": "PROJ-0001"})
        metrics = _json(result)
        assert metrics["blocked_count"] == 1

    @pytest.mark.asyncio
    async def test_metrics_empty_project(self, setup):
        """Empty project returns zero counts, no errors."""
        handler, db = setup
        result = await handler.handle_tool_call("get_project_metrics", {"project_id": "PROJ-0001"})
        metrics = _json(result)
        assert metrics["requirements"]["total"] == 0
        assert metrics["tasks"]["total"] == 0
        assert metrics["tasks"]["completion_pct"] == 0
        assert metrics["architecture"]["total"] == 0
        assert metrics["blocked_count"] == 0

    @pytest.mark.asyncio
    async def test_metrics_nonexistent_project(self, setup):
        """Returns error for nonexistent project."""
        handler, db = setup
        result = await handler.handle_tool_call("get_project_metrics", {"project_id": "PROJ-9999"})
        text = _text(result)
        assert "ERROR" in text
        assert "PROJ-9999" in text


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
        """Handler exposes 3 tools."""
        handler, _ = setup
        tools = handler.get_tool_definitions()
        assert len(tools) == 3

    def test_tool_names(self, setup):
        """All 3 expected tools are present."""
        handler, _ = setup
        tools = handler.get_tool_definitions()
        names = {t["name"] for t in tools}
        assert names == {"get_project_status", "get_project_metrics", "diff_project"}

    @pytest.mark.asyncio
    async def test_unknown_tool(self, setup):
        """Unknown tool name returns error."""
        handler, _ = setup
        result = await handler.handle_tool_call("unknown_tool", {})
        text = _text(result)
        assert "ERROR" in text
        assert "unknown_tool" in text
