"""Tests for ProjectHandler (DB-03 / BF-06).

Validates:
- create_project: returns PROJ-XXXX, sequential IDs, optional fields stored
- update_project: updates fields, rejects archived/nonexistent project
- archive_project: cascading archive of project + children
- list_projects: default excludes archived, include_archived flag, status filter, slim output
- get_project_details: detail_level summary/status/metrics
"""

import json

import pytest

from lifecycle_mcp.handlers.project_handler import ProjectHandler


@pytest.fixture
async def project_handler(v2_db_manager):
    return ProjectHandler(v2_db_manager)


# ── create_project ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_basic(project_handler):
    """create_project returns PROJ-XXXX format ID."""
    result = await project_handler.handle_tool_call(
        "create_project", {"name": "My Project"}
    )
    text = result[0].text
    assert "PROJ-0001" in text
    assert "SUCCESS" in text


@pytest.mark.asyncio
async def test_create_project_sequential_ids(project_handler):
    """Multiple creates produce sequential PROJ-XXXX IDs."""
    r1 = await project_handler.handle_tool_call(
        "create_project", {"name": "Project A"}
    )
    r2 = await project_handler.handle_tool_call(
        "create_project", {"name": "Project B"}
    )
    r3 = await project_handler.handle_tool_call(
        "create_project", {"name": "Project C"}
    )
    assert "PROJ-0001" in r1[0].text
    assert "PROJ-0002" in r2[0].text
    assert "PROJ-0003" in r3[0].text


@pytest.mark.asyncio
async def test_create_project_optional_fields(project_handler):
    """Optional fields (description, tech_stack, constraints) are stored."""
    await project_handler.handle_tool_call(
        "create_project",
        {
            "name": "Full Project",
            "description": "A complete project",
            "tech_stack": ["Python", "SQLite"],
            "constraints": ["Must be fast"],
        },
    )
    row = await project_handler.db.execute_query(
        "SELECT name, description, tech_stack, constraints FROM projects WHERE id = ?",
        ["PROJ-0001"],
        fetch_one=True,
        row_factory=True,
    )
    assert row["name"] == "Full Project"
    assert row["description"] == "A complete project"
    assert json.loads(row["tech_stack"]) == ["Python", "SQLite"]
    assert json.loads(row["constraints"]) == ["Must be fast"]


@pytest.mark.asyncio
async def test_create_project_missing_name(project_handler):
    """create_project without name returns error."""
    result = await project_handler.handle_tool_call("create_project", {})
    text = result[0].text
    assert "ERROR" in text
    assert "name" in text.lower()


# ── update_project ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_project_name(project_handler):
    """update_project can change name."""
    await project_handler.handle_tool_call("create_project", {"name": "Old Name"})
    result = await project_handler.handle_tool_call(
        "update_project", {"project_id": "PROJ-0001", "name": "New Name"}
    )
    text = result[0].text
    assert "SUCCESS" in text

    row = await project_handler.db.execute_query(
        "SELECT name FROM projects WHERE id = ?",
        ["PROJ-0001"],
        fetch_one=True,
        row_factory=True,
    )
    assert row["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_project_tech_stack(project_handler):
    """update_project can change tech_stack (JSON array)."""
    await project_handler.handle_tool_call("create_project", {"name": "Tech Test"})
    result = await project_handler.handle_tool_call(
        "update_project",
        {"project_id": "PROJ-0001", "tech_stack": ["Rust", "PostgreSQL"]},
    )
    assert "SUCCESS" in result[0].text

    row = await project_handler.db.execute_query(
        "SELECT tech_stack FROM projects WHERE id = ?",
        ["PROJ-0001"],
        fetch_one=True,
        row_factory=True,
    )
    assert json.loads(row["tech_stack"]) == ["Rust", "PostgreSQL"]


@pytest.mark.asyncio
async def test_update_project_rejects_archived(project_handler):
    """update_project rejects update on archived project."""
    await project_handler.handle_tool_call("create_project", {"name": "To Archive"})
    await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-0001"}
    )
    result = await project_handler.handle_tool_call(
        "update_project", {"project_id": "PROJ-0001", "name": "Updated?"}
    )
    assert "ERROR" in result[0].text
    assert "archived" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_project_rejects_nonexistent(project_handler):
    """update_project rejects update on nonexistent project."""
    result = await project_handler.handle_tool_call(
        "update_project", {"project_id": "PROJ-9999", "name": "Nope"}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_update_project_missing_project_id(project_handler):
    """update_project without project_id returns error."""
    result = await project_handler.handle_tool_call(
        "update_project", {"name": "No ID"}
    )
    assert "ERROR" in result[0].text


# ── archive_project ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_project_sets_flag(project_handler):
    """archive_project sets is_archived=1 and archived_at."""
    await project_handler.handle_tool_call("create_project", {"name": "Archivable"})
    result = await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-0001"}
    )
    assert "SUCCESS" in result[0].text

    row = await project_handler.db.execute_query(
        "SELECT is_archived, archived_at, status FROM projects WHERE id = ?",
        ["PROJ-0001"],
        fetch_one=True,
        row_factory=True,
    )
    assert row["is_archived"] == 1
    assert row["archived_at"] is not None
    assert row["status"] == "archived"


@pytest.mark.asyncio
async def test_archive_project_cascading(project_handler):
    """archive_project cascades to requirements, tasks, and architecture."""
    db = project_handler.db
    # Create project
    await project_handler.handle_tool_call("create_project", {"name": "CascadeTest"})

    # Insert child entities directly
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority, is_archived) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["REQ-0001", "PROJ-0001", "FUNC", "Child Req", "Under Review", "P1", 0],
    )
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, status, priority, is_archived) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["TASK-0001", "PROJ-0001", "Child Task", "Under Review", "P1", 0],
    )
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, status, is_archived) "
        "VALUES (?, ?, ?, ?, ?)",
        ["ADR-0001", "PROJ-0001", "Child ADR", "Draft", 0],
    )

    # Archive the project
    await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-0001"}
    )

    # Verify all children are archived
    for table, entity_id in [
        ("requirements", "REQ-0001"),
        ("tasks", "TASK-0001"),
        ("architecture", "ADR-0001"),
    ]:
        row = await db.execute_query(
            f"SELECT is_archived, archived_at FROM {table} WHERE id = ?",
            [entity_id],
            fetch_one=True,
            row_factory=True,
        )
        assert row["is_archived"] == 1, f"{table} {entity_id} not archived"
        assert row["archived_at"] is not None, f"{table} {entity_id} missing archived_at"


@pytest.mark.asyncio
async def test_archive_project_nonexistent(project_handler):
    """archive_project returns error for nonexistent project."""
    result = await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-9999"}
    )
    assert "ERROR" in result[0].text


# ── list_projects ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_projects_excludes_archived(project_handler):
    """list_projects excludes archived by default."""
    await project_handler.handle_tool_call("create_project", {"name": "Active"})
    await project_handler.handle_tool_call("create_project", {"name": "To Archive"})
    await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-0002"}
    )

    result = await project_handler.handle_tool_call("list_projects", {})
    text = result[0].text
    assert "Active" in text
    assert "To Archive" not in text


@pytest.mark.asyncio
async def test_list_projects_includes_archived(project_handler):
    """list_projects with include_archived=True shows all."""
    await project_handler.handle_tool_call("create_project", {"name": "Active"})
    await project_handler.handle_tool_call("create_project", {"name": "Archived One"})
    await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-0002"}
    )

    result = await project_handler.handle_tool_call(
        "list_projects", {"include_archived": True}
    )
    text = result[0].text
    assert "Active" in text
    assert "Archived One" in text


@pytest.mark.asyncio
async def test_list_projects_status_filter(project_handler):
    """list_projects can filter by status."""
    await project_handler.handle_tool_call("create_project", {"name": "Active One"})
    await project_handler.handle_tool_call("create_project", {"name": "Active Two"})

    result = await project_handler.handle_tool_call(
        "list_projects", {"status": "active"}
    )
    text = result[0].text
    assert "Active One" in text
    assert "Active Two" in text


@pytest.mark.asyncio
async def test_list_projects_empty(project_handler):
    """list_projects with no projects returns appropriate message."""
    result = await project_handler.handle_tool_call("list_projects", {})
    text = result[0].text
    assert "0" in text or "no" in text.lower() or "Found 0" in text


@pytest.mark.asyncio
async def test_list_projects_slim_output(project_handler):
    """list_projects returns only id, name, status per project (no entity counts)."""
    await project_handler.handle_tool_call("create_project", {"name": "Alpha"})
    await project_handler.handle_tool_call("create_project", {"name": "Beta"})

    # Add children to ensure counts are NOT in the output
    db = project_handler.db
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["REQ-0001", "PROJ-0001", "FUNC", "Req1", "Under Review", "P1"],
    )

    result = await project_handler.handle_tool_call("list_projects", {})
    text = result[0].text
    # Should contain id, name, status
    assert "PROJ-0001" in text
    assert "Alpha" in text
    assert "PROJ-0002" in text
    assert "Beta" in text
    assert "active" in text.lower()
    # Should NOT contain entity count aggregation (e.g. "Requirements:", "Tasks:")
    assert "Requirements:" not in text
    assert "Tasks:" not in text


# ── get_project_details ──────────────────────────────────────────────


# Helper functions for inserting test data
async def _add_req(db, req_id, project_id, status="Under Review", priority="P1"):
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority) VALUES (?, ?, ?, ?, ?, ?)",
        [req_id, project_id, "FUNC", f"Req {req_id}", status, priority],
    )


async def _add_task(db, task_id, project_id, status="Under Review", priority="P1", assignee=None):
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, status, priority, assignee) VALUES (?, ?, ?, ?, ?, ?)",
        [task_id, project_id, f"Task {task_id}", status, priority, assignee],
    )


async def _add_adr(db, adr_id, project_id, status="Draft"):
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, context, decision, status) VALUES (?, ?, ?, ?, ?, ?)",
        [adr_id, project_id, f"ADR {adr_id}", "ctx", "dec", status],
    )


@pytest.mark.asyncio
async def test_get_project_details_summary_default(project_handler):
    """get_project_details defaults to summary level with metadata + total counts."""
    db = project_handler.db
    await project_handler.handle_tool_call("create_project", {"name": "CountTest"})

    # Insert children
    await _add_req(db, "REQ-0001", "PROJ-0001")
    await _add_req(db, "REQ-0002", "PROJ-0001")
    await _add_task(db, "TASK-0001", "PROJ-0001", status="Under Review")
    await _add_task(db, "TASK-0002", "PROJ-0001", status="Validated")
    await _add_adr(db, "ADR-0001", "PROJ-0001")

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001"}
    )
    text = result[0].text
    assert "PROJ-0001" in text
    assert "CountTest" in text
    # Summary shows total counts
    assert "Requirements: 2" in text
    assert "Tasks: 2 (1 complete)" in text or ("Tasks: 2" in text and "1 complete" in text)
    assert "ADRs: 1" in text


@pytest.mark.asyncio
async def test_get_project_details_summary_explicit(project_handler):
    """get_project_details with detail_level=summary returns same as default."""
    await project_handler.handle_tool_call("create_project", {"name": "Explicit"})

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001", "detail_level": "summary"}
    )
    text = result[0].text
    assert "PROJ-0001" in text
    assert "Explicit" in text


@pytest.mark.asyncio
async def test_get_project_details_empty_project(project_handler):
    """get_project_details for empty project returns zero counts."""
    await project_handler.handle_tool_call("create_project", {"name": "Empty"})

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001"}
    )
    text = result[0].text
    assert "PROJ-0001" in text
    assert "Empty" in text


@pytest.mark.asyncio
async def test_get_project_details_nonexistent(project_handler):
    """get_project_details for nonexistent project returns error."""
    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-9999"}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_get_project_details_status(project_handler):
    """get_project_details with detail_level=status shows per-status breakdowns, progress/validated %."""
    db = project_handler.db
    await project_handler.handle_tool_call("create_project", {"name": "StatusTest"})

    await _add_req(db, "REQ-0001", "PROJ-0001", status="Under Review")
    await _add_req(db, "REQ-0002", "PROJ-0001", status="Under Review")
    await _add_req(db, "REQ-0003", "PROJ-0001", status="Approved")
    await _add_req(db, "REQ-0004", "PROJ-0001", status="Implemented")
    await _add_task(db, "TASK-0001", "PROJ-0001", status="Under Review")
    await _add_task(db, "TASK-0002", "PROJ-0001", status="Approved")
    await _add_task(db, "TASK-0003", "PROJ-0001", status="Validated")
    await _add_task(db, "TASK-0004", "PROJ-0001", status="Validated")
    await _add_task(db, "TASK-0005", "PROJ-0001", status="Implemented")
    await _add_adr(db, "ADR-0001", "PROJ-0001", status="Draft")
    await _add_adr(db, "ADR-0002", "PROJ-0001", status="Accepted")

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001", "detail_level": "status"}
    )
    text = result[0].text

    # Should succeed now that blocked_tasks view references are removed
    assert "SUCCESS" in text or "PROJ-0001" in text

    # Per-status breakdowns
    assert "2 Under Review" in text  # requirements
    assert "2 Validated" in text
    # Task progress: (1 Implemented + 2 Validated) / 5 = 60% progress, 2/5 = 40% validated
    assert "60% progress" in text
    assert "40% validated" in text
    # Requirement progress: 1 Implemented / 4 = 25% progress, 0/4 = 0% validated
    assert "25% progress" in text
    assert "0% validated" in text
    # ADR breakdown
    assert "1 Draft" in text
    assert "1 Accepted" in text


@pytest.mark.asyncio
async def test_get_project_details_status_no_blocked_section(project_handler):
    """get_project_details with detail_level=status does not include a Blocked Tasks section.

    The blocked_tasks view was removed from the v2 schema and the handler
    no longer queries it. Verify the response succeeds and contains no
    'Blocked Tasks' section.
    """
    db = project_handler.db
    await project_handler.handle_tool_call("create_project", {"name": "BlockTest"})

    await _add_task(db, "TASK-0001", "PROJ-0001", status="Approved")
    await _add_task(db, "TASK-0002", "PROJ-0001", status="Under Review")

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001", "detail_level": "status"}
    )
    text = result[0].text
    assert "PROJ-0001" in text
    assert "SUCCESS" in text
    assert "Blocked Tasks" not in text


@pytest.mark.asyncio
async def test_get_project_details_metrics(project_handler):
    """get_project_details with detail_level=metrics returns JSON with all breakdowns."""
    db = project_handler.db
    await project_handler.handle_tool_call("create_project", {"name": "MetricsTest"})

    await _add_req(db, "REQ-0001", "PROJ-0001", status="Under Review", priority="P0")
    await _add_req(db, "REQ-0002", "PROJ-0001", status="Approved", priority="P1")
    await _add_req(db, "REQ-0003", "PROJ-0001", status="Implemented", priority="P1")
    await _add_task(db, "TASK-0001", "PROJ-0001", status="Validated", priority="P1", assignee="Alice")
    await _add_task(db, "TASK-0002", "PROJ-0001", status="Approved", priority="P2", assignee="Bob")
    await _add_task(db, "TASK-0003", "PROJ-0001", status="Under Review", priority="P1", assignee=None)
    await _add_task(db, "TASK-0004", "PROJ-0001", status="Implemented", priority="P1", assignee="Alice")
    await _add_adr(db, "ADR-0001", "PROJ-0001", status="Accepted")

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001", "detail_level": "metrics"}
    )
    text = result[0].text
    metrics = json.loads(text)
    assert metrics["project_id"] == "PROJ-0001"
    assert metrics["requirements"]["total"] == 3
    # Requirement progress: 1 Implemented / 3 = 33.3%
    assert metrics["requirements"]["progress_pct"] == 33.3
    assert metrics["requirements"]["validated_pct"] == 0
    assert metrics["tasks"]["total"] == 4
    # Task progress: (1 Implemented + 1 Validated) / 4 = 50.0%
    assert metrics["tasks"]["progress_pct"] == 50.0
    # Task validated: 1 Validated / 4 = 25.0%
    assert metrics["tasks"]["validated_pct"] == 25.0
    # completion_pct should no longer exist
    assert "completion_pct" not in metrics["tasks"]
    assert metrics["architecture"]["total"] == 1
    # No blocked_count key in metrics
    assert "blocked_count" not in metrics


@pytest.mark.asyncio
async def test_get_project_details_metrics_empty(project_handler):
    """get_project_details with detail_level=metrics on empty project."""
    await project_handler.handle_tool_call("create_project", {"name": "EmptyMetrics"})

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001", "detail_level": "metrics"}
    )
    text = result[0].text
    metrics = json.loads(text)
    assert metrics["requirements"]["total"] == 0
    assert metrics["requirements"]["progress_pct"] == 0
    assert metrics["requirements"]["validated_pct"] == 0
    assert metrics["tasks"]["total"] == 0
    assert metrics["tasks"]["progress_pct"] == 0
    assert metrics["tasks"]["validated_pct"] == 0
    assert "completion_pct" not in metrics["tasks"]
    assert metrics["architecture"]["total"] == 0
    assert "blocked_count" not in metrics


@pytest.mark.asyncio
async def test_get_project_details_progress_mixed_statuses(project_handler):
    """progress_pct counts Implemented+Validated tasks; validated_pct counts only Validated."""
    db = project_handler.db
    await project_handler.handle_tool_call("create_project", {"name": "ProgressTest"})

    # Tasks: 2 Approved, 1 Implemented, 2 Validated = 5 total
    await _add_task(db, "TASK-0001", "PROJ-0001", status="Approved", priority="P1")
    await _add_task(db, "TASK-0002", "PROJ-0001", status="Approved", priority="P2")
    await _add_task(db, "TASK-0003", "PROJ-0001", status="Implemented", priority="P1")
    await _add_task(db, "TASK-0004", "PROJ-0001", status="Validated", priority="P0")
    await _add_task(db, "TASK-0005", "PROJ-0001", status="Validated", priority="P1")

    # Requirements: 1 Approved, 1 Implemented, 1 Partially Validated, 1 Validated = 4 total
    await _add_req(db, "REQ-0001", "PROJ-0001", status="Approved", priority="P1")
    await _add_req(db, "REQ-0002", "PROJ-0001", status="Implemented", priority="P1")
    await _add_req(db, "REQ-0003", "PROJ-0001", status="Partially Validated", priority="P1")
    await _add_req(db, "REQ-0004", "PROJ-0001", status="Validated", priority="P0")

    # Check metrics level
    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001", "detail_level": "metrics"}
    )
    metrics = json.loads(result[0].text)

    # Task progress: (1 Implemented + 2 Validated) / 5 = 60.0%
    assert metrics["tasks"]["progress_pct"] == 60.0
    # Task validated: 2 Validated / 5 = 40.0%
    assert metrics["tasks"]["validated_pct"] == 40.0
    assert "completion_pct" not in metrics["tasks"]

    # Requirement progress: (1 Implemented + 1 Partially Validated + 1 Validated) / 4 = 75.0%
    assert metrics["requirements"]["progress_pct"] == 75.0
    # Requirement validated: 1 Validated / 4 = 25.0%
    assert metrics["requirements"]["validated_pct"] == 25.0

    # Check status level too
    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001", "detail_level": "status"}
    )
    text = result[0].text
    assert "60% progress" in text
    assert "40% validated" in text
    assert "75% progress" in text
    assert "25% validated" in text


@pytest.mark.asyncio
async def test_unknown_tool(project_handler):
    """Unknown tool name returns error."""
    result = await project_handler.handle_tool_call("nonexistent_tool", {})
    assert "ERROR" in result[0].text
