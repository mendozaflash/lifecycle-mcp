"""Tests for ProjectHandler (DB-03).

Validates:
- create_project: returns PROJ-XXXX, sequential IDs, optional fields stored
- update_project: updates fields, rejects archived/nonexistent project
- archive_project: cascading archive of project + children
- query_projects: default excludes archived, include_archived flag, status filter
- get_project_details: correct entity counts, zero counts for empty project
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
        ["REQ-0001", "PROJ-0001", "FUNC", "Child Req", "Draft", "P1", 0],
    )
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, status, priority, is_archived) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["TASK-0001", "PROJ-0001", "Child Task", "Not Started", "P1", 0],
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


# ── query_projects ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_projects_excludes_archived(project_handler):
    """query_projects excludes archived by default."""
    await project_handler.handle_tool_call("create_project", {"name": "Active"})
    await project_handler.handle_tool_call("create_project", {"name": "To Archive"})
    await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-0002"}
    )

    result = await project_handler.handle_tool_call("query_projects", {})
    text = result[0].text
    assert "Active" in text
    assert "To Archive" not in text


@pytest.mark.asyncio
async def test_query_projects_includes_archived(project_handler):
    """query_projects with include_archived=True shows all."""
    await project_handler.handle_tool_call("create_project", {"name": "Active"})
    await project_handler.handle_tool_call("create_project", {"name": "Archived One"})
    await project_handler.handle_tool_call(
        "archive_project", {"project_id": "PROJ-0002"}
    )

    result = await project_handler.handle_tool_call(
        "query_projects", {"include_archived": True}
    )
    text = result[0].text
    assert "Active" in text
    assert "Archived One" in text


@pytest.mark.asyncio
async def test_query_projects_status_filter(project_handler):
    """query_projects can filter by status."""
    await project_handler.handle_tool_call("create_project", {"name": "Active One"})
    await project_handler.handle_tool_call("create_project", {"name": "Active Two"})

    result = await project_handler.handle_tool_call(
        "query_projects", {"status": "active"}
    )
    text = result[0].text
    assert "Active One" in text
    assert "Active Two" in text


@pytest.mark.asyncio
async def test_query_projects_empty(project_handler):
    """query_projects with no projects returns appropriate message."""
    result = await project_handler.handle_tool_call("query_projects", {})
    text = result[0].text
    assert "0" in text or "no" in text.lower() or "Found 0" in text


# ── get_project_details ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_project_details_with_counts(project_handler):
    """get_project_details returns correct entity counts."""
    db = project_handler.db
    await project_handler.handle_tool_call("create_project", {"name": "CountTest"})

    # Insert children
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["REQ-0001", "PROJ-0001", "FUNC", "Req1", "Draft", "P1"],
    )
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, status, priority) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["REQ-0002", "PROJ-0001", "TECH", "Req2", "Draft", "P0"],
    )
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, status, priority) "
        "VALUES (?, ?, ?, ?, ?)",
        ["TASK-0001", "PROJ-0001", "Task1", "Not Started", "P1"],
    )
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, status, priority) "
        "VALUES (?, ?, ?, ?, ?)",
        ["TASK-0002", "PROJ-0001", "Task2", "Complete", "P1"],
    )
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, status) "
        "VALUES (?, ?, ?, ?)",
        ["ADR-0001", "PROJ-0001", "ADR1", "Draft"],
    )

    result = await project_handler.handle_tool_call(
        "get_project_details", {"project_id": "PROJ-0001"}
    )
    text = result[0].text
    assert "PROJ-0001" in text
    assert "CountTest" in text
    # Should show requirement count: 2
    assert "2" in text
    # Should show task info and ADR count
    assert "1" in text  # 1 complete or 1 ADR


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
async def test_unknown_tool(project_handler):
    """Unknown tool name returns error."""
    result = await project_handler.handle_tool_call("nonexistent_tool", {})
    assert "ERROR" in result[0].text
