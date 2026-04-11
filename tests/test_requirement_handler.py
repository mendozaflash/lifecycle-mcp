"""Tests for RequirementHandler v2 (DB-04, BF-03).

Validates all 8 MCP tools:
  create_requirement, update_requirement, update_requirement_status,
  archive_requirement, query_requirements (with output_format/limit/offset),
  get_requirement_details (with trace), batch_create_requirements,
  clone_requirement

Also includes TestAutoProgression (LI-05): integration tests for the 2
consolidated DB triggers that auto-progress requirement status when tasks advance.
"""

import json

import pytest

from lifecycle_mcp.handlers.requirement_handler import RequirementHandler
from lifecycle_mcp.handlers.task_handler import TaskHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def setup(v2_db_manager):
    """Set up a RequirementHandler + a test project. Returns (handler, db, project_id)."""
    handler = RequirementHandler(v2_db_manager)
    handler._testing_mode = True
    await v2_db_manager.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0001", "Test Project"],
    )
    return handler, v2_db_manager, "PROJ-0001"


# -- Helpers -----------------------------------------------------------------


async def _create_req(handler, project_id, title="Test Requirement", priority="P1", req_type="FUNC", **extra):
    """Shorthand to create a requirement via handle_tool_call."""
    params = {
        "project_id": project_id,
        "type": req_type,
        "title": title,
        "priority": priority,
    }
    params.update(extra)
    return await handler.handle_tool_call("create_requirement", params)


def _extract_id(result):
    """Extract REQ-XXXX ID from handler response text."""
    import re
    text = result[0].text
    match = re.search(r"REQ-\d{4}", text)
    assert match, f"Could not extract REQ ID from: {text}"
    return match.group()


async def _create_task(db, project_id, task_id, title="Test Task"):
    """Insert a task directly into the DB for relationship testing."""
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, priority, status) "
        "VALUES (?, ?, ?, 'P1', 'Under Review')",
        [task_id, project_id, title],
    )


async def _create_adr(db, project_id, adr_id, title="Test ADR"):
    """Insert an architecture decision directly into the DB."""
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, status) "
        "VALUES (?, ?, ?, 'Draft')",
        [adr_id, project_id, title],
    )


async def _link(db, source_type, source_id, target_type, target_id, rel_type, project_id=None):
    """Insert a relationship record."""
    rel_id = f"rel-{source_id}-{target_id}-{rel_type}"
    await db.insert_record(
        "relationships",
        {
            "id": rel_id,
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            "relationship_type": rel_type,
            "project_id": project_id,
        },
    )


# =============================================================================
#  Tool definitions
# =============================================================================


@pytest.mark.asyncio
async def test_tool_definitions_list(setup):
    handler, _, _ = setup
    tools = handler.get_tool_definitions()
    names = [t["name"] for t in tools]
    expected = [
        "create_requirement",
        "update_requirement",
        "update_requirement_status",
        "archive_requirement",
        "query_requirements",
        "get_requirement_details",
        "batch_create_requirements",
        "clone_requirement",
    ]
    for name in expected:
        assert name in names, f"Missing tool definition: {name}"
    # Removed tools should NOT be present
    assert "query_requirements_json" not in names
    assert "trace_requirement" not in names
    assert len(tools) == 8


# =============================================================================
#  create_requirement
# =============================================================================


@pytest.mark.asyncio
async def test_create_requirement_basic(setup):
    handler, db, pid = setup
    result = await _create_req(handler, pid)
    text = result[0].text
    assert "REQ-0001" in text
    assert "SUCCESS" in text


@pytest.mark.asyncio
async def test_create_requirement_sequential_ids(setup):
    handler, db, pid = setup
    r1 = await _create_req(handler, pid, title="A")
    r2 = await _create_req(handler, pid, title="B")
    r3 = await _create_req(handler, pid, title="C")
    assert "REQ-0001" in r1[0].text
    assert "REQ-0002" in r2[0].text
    assert "REQ-0003" in r3[0].text


@pytest.mark.asyncio
async def test_create_requirement_stores_fields(setup):
    handler, db, pid = setup
    await _create_req(
        handler,
        pid,
        title="Full Requirement",
        priority="P0",
        req_type="TECH",
        current_state="Old system",
        desired_state="New system",
        functional_requirements=["FR-1", "FR-2"],
        nonfunctional_requirements=["NFR-1"],
        out_of_scope=["OOS-1"],
        acceptance_criteria=["AC-1", "AC-2"],
        business_value="High value",
        author="Alice",
    )
    row = await db.execute_query(
        "SELECT * FROM requirements WHERE id = 'REQ-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row is not None
    assert row["title"] == "Full Requirement"
    assert row["type"] == "TECH"
    assert row["priority"] == "P0"
    assert row["current_state"] == "Old system"
    assert row["desired_state"] == "New system"
    assert json.loads(row["functional_requirements"]) == ["FR-1", "FR-2"]
    assert json.loads(row["nonfunctional_requirements"]) == ["NFR-1"]
    assert json.loads(row["out_of_scope"]) == ["OOS-1"]
    assert json.loads(row["acceptance_criteria"]) == ["AC-1", "AC-2"]
    assert row["business_value"] == "High value"
    assert row["author"] == "Alice"
    assert row["status"] == "Under Review"
    assert row["project_id"] == pid


@pytest.mark.asyncio
async def test_create_requirement_rejects_bad_project(setup):
    handler, db, pid = setup
    result = await _create_req(handler, "PROJ-9999", title="Orphan")
    text = result[0].text
    assert "ERROR" in text
    assert "PROJ-9999" in text


@pytest.mark.asyncio
async def test_create_requirement_missing_required_fields(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call("create_requirement", {"title": "No project"})
    assert "ERROR" in result[0].text
    assert "Missing required" in result[0].text


# =============================================================================
#  update_requirement (new broad-update tool)
# =============================================================================


@pytest.mark.asyncio
async def test_update_requirement_title_and_priority(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Old Title", priority="P2")
    result = await handler.handle_tool_call(
        "update_requirement", {"requirement_id": "REQ-0001", "title": "New Title", "priority": "P0"}
    )
    assert "SUCCESS" in result[0].text
    row = await db.execute_query(
        "SELECT title, priority FROM requirements WHERE id = 'REQ-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["title"] == "New Title"
    assert row["priority"] == "P0"


@pytest.mark.asyncio
async def test_update_requirement_optional_fields(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)
    result = await handler.handle_tool_call(
        "update_requirement",
        {
            "requirement_id": "REQ-0001",
            "current_state": "Updated current",
            "desired_state": "Updated desired",
            "business_value": "Updated value",
            "functional_requirements": ["New FR"],
            "nonfunctional_requirements": ["New NFR"],
            "out_of_scope": ["New OOS"],
            "acceptance_criteria": ["New AC"],
            "author": "Bob",
        },
    )
    assert "SUCCESS" in result[0].text
    row = await db.execute_query(
        "SELECT current_state, desired_state, business_value, functional_requirements, "
        "nonfunctional_requirements, out_of_scope, acceptance_criteria, author "
        "FROM requirements WHERE id = 'REQ-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["current_state"] == "Updated current"
    assert row["desired_state"] == "Updated desired"
    assert row["business_value"] == "Updated value"
    assert json.loads(row["functional_requirements"]) == ["New FR"]
    assert json.loads(row["nonfunctional_requirements"]) == ["New NFR"]
    assert json.loads(row["out_of_scope"]) == ["New OOS"]
    assert json.loads(row["acceptance_criteria"]) == ["New AC"]
    assert row["author"] == "Bob"


@pytest.mark.asyncio
async def test_update_requirement_rejects_archived(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)
    await handler.handle_tool_call("archive_requirement", {"requirement_id": "REQ-0001"})
    result = await handler.handle_tool_call(
        "update_requirement", {"requirement_id": "REQ-0001", "title": "Nope"}
    )
    assert "ERROR" in result[0].text
    assert "archived" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_requirement_rejects_nonexistent(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "update_requirement", {"requirement_id": "REQ-9999", "title": "Nope"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_requirement_no_fields(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)
    result = await handler.handle_tool_call(
        "update_requirement", {"requirement_id": "REQ-0001"}
    )
    assert "ERROR" in result[0].text
    assert "No fields" in result[0].text


# =============================================================================
#  update_requirement_status
# =============================================================================


@pytest.mark.asyncio
async def test_update_status_valid_transitions(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)  # status = "Under Review"

    # Under Review -> Approved
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Approved"}
    )
    assert "SUCCESS" in result[0].text

    # Approved -> Deprecated
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Deprecated"}
    )
    assert "SUCCESS" in result[0].text


@pytest.mark.asyncio
async def test_update_status_invalid_transition(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)  # status = "Under Review"

    # Under Review -> Validated (not allowed)
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Validated"}
    )
    assert "ERROR" in result[0].text
    assert "Invalid transition" in result[0].text or "transition" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_status_deprecated_is_terminal(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)  # status = "Under Review"

    # Under Review -> Deprecated
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Deprecated"}
    )

    # Deprecated -> Under Review (not allowed, Deprecated is terminal)
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Under Review"}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_update_status_logs_event(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)  # status = "Under Review"

    await handler.handle_tool_call(
        "update_requirement_status",
        {"requirement_id": "REQ-0001", "new_status": "Approved", "comment": "Looks good"},
    )

    # Check lifecycle_events table for trigger-based log
    events = await db.execute_query(
        "SELECT * FROM lifecycle_events WHERE entity_id = 'REQ-0001' AND event_type = 'status_change'",
        fetch_all=True,
        row_factory=True,
    )
    assert len(events) >= 1
    assert events[0]["from_value"] == "Under Review"
    assert events[0]["to_value"] == "Approved"


@pytest.mark.asyncio
async def test_update_status_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-9999", "new_status": "Under Review"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_status_with_comment(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)  # status = "Under Review"
    result = await handler.handle_tool_call(
        "update_requirement_status",
        {"requirement_id": "REQ-0001", "new_status": "Approved", "comment": "Approved by lead"},
    )
    assert "SUCCESS" in result[0].text

    # Verify status changed
    row = await db.execute_query(
        "SELECT status FROM requirements WHERE id = 'REQ-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Approved"


# =============================================================================
#  archive_requirement
# =============================================================================


@pytest.mark.asyncio
async def test_archive_requirement(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)
    result = await handler.handle_tool_call("archive_requirement", {"requirement_id": "REQ-0001"})
    assert "SUCCESS" in result[0].text

    row = await db.execute_query(
        "SELECT is_archived FROM requirements WHERE id = 'REQ-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["is_archived"] == 1


@pytest.mark.asyncio
async def test_archive_requirement_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call("archive_requirement", {"requirement_id": "REQ-9999"})
    assert "ERROR" in result[0].text


# =============================================================================
#  query_requirements
# =============================================================================


@pytest.mark.asyncio
async def test_query_requirements_by_project(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="A")
    await _create_req(handler, pid, title="B")
    result = await handler.handle_tool_call("query_requirements", {"project_id": pid})
    text = result[0].text
    assert "REQ-0001" in text
    assert "REQ-0002" in text


@pytest.mark.asyncio
async def test_query_requirements_excludes_archived(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Visible")
    await _create_req(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_requirement", {"requirement_id": "REQ-0002"})

    result = await handler.handle_tool_call("query_requirements", {"project_id": pid})
    text = result[0].text
    assert "REQ-0001" in text
    assert "REQ-0002" not in text


@pytest.mark.asyncio
async def test_query_requirements_include_archived(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Visible")
    await _create_req(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_requirement", {"requirement_id": "REQ-0002"})

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "include_archived": True}
    )
    text = result[0].text
    assert "REQ-0001" in text
    assert "REQ-0002" in text


@pytest.mark.asyncio
async def test_query_requirements_filters(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="High", priority="P0")
    await _create_req(handler, pid, title="Low", priority="P3")

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "priority": "P0"}
    )
    text = result[0].text
    assert "High" in text
    assert "Low" not in text


@pytest.mark.asyncio
async def test_query_requirements_by_status(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Review Req")
    await _create_req(handler, pid, title="Approved Req")
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0002", "new_status": "Approved"}
    )

    result = await handler.handle_tool_call(
        "query_requirements", {"status": "Approved"}
    )
    text = result[0].text
    assert "Approved Req" in text
    assert "Review Req" not in text


@pytest.mark.asyncio
async def test_query_requirements_search_text(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="User Authentication")
    await _create_req(handler, pid, title="Payment Processing")

    result = await handler.handle_tool_call(
        "query_requirements", {"search_text": "Authentication"}
    )
    text = result[0].text
    assert "User Authentication" in text
    assert "Payment Processing" not in text


@pytest.mark.asyncio
async def test_query_requirements_no_results(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call("query_requirements", {"status": "Validated"})
    text = result[0].text
    assert "No requirements found" in text or "0" in text


# =============================================================================
#  query_requirements — output_format, limit, offset
# =============================================================================


@pytest.mark.asyncio
async def test_query_requirements_summary_format(setup):
    """Default output_format='summary' returns one-line-per-requirement format."""
    handler, db, pid = setup
    await _create_req(handler, pid, title="Alpha Req", priority="P0")
    await _create_req(handler, pid, title="Beta Req", priority="P1")

    result = await handler.handle_tool_call("query_requirements", {"project_id": pid})
    text = result[0].text
    # Summary format: "REQ-XXXX | title | status | priority"
    assert "REQ-0001 | Alpha Req | Under Review | P0" in text
    assert "REQ-0002 | Beta Req | Under Review | P1" in text


@pytest.mark.asyncio
async def test_query_requirements_summary_format_explicit(setup):
    """Explicitly passing output_format='summary' works the same as default."""
    handler, db, pid = setup
    await _create_req(handler, pid, title="Explicit Summary", priority="P2")

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "output_format": "summary"}
    )
    text = result[0].text
    assert "REQ-0001 | Explicit Summary | Under Review | P2" in text


@pytest.mark.asyncio
async def test_query_requirements_json_output_format(setup):
    """output_format='json' returns a JSON array of {id, title, status, priority}."""
    handler, db, pid = setup
    await _create_req(handler, pid, title="JSON Req", priority="P0")
    await _create_req(handler, pid, title="Another Req", priority="P1")

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "output_format": "json"}
    )
    data = json.loads(result[0].text)
    assert isinstance(data, list)
    assert len(data) == 2
    # Each item has only the 4 specified keys
    for item in data:
        assert set(item.keys()) == {"id", "title", "status", "priority"}
    assert data[0]["title"] == "JSON Req"
    assert data[0]["priority"] == "P0"


@pytest.mark.asyncio
async def test_query_requirements_markdown_format(setup):
    """output_format='markdown' returns verbose markdown (backward-compat)."""
    handler, db, pid = setup
    await _create_req(handler, pid, title="Markdown Req", priority="P1")

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "output_format": "markdown"}
    )
    text = result[0].text
    # Markdown format has the detailed listing with dashes
    assert "REQ-0001" in text
    assert "Markdown Req" in text
    assert "Under Review" in text
    assert "P1" in text


@pytest.mark.asyncio
async def test_query_requirements_limit(setup):
    """limit parameter restricts number of results."""
    handler, db, pid = setup
    # Use different priorities to get deterministic ordering (P0 < P1 < P2 in sort)
    await _create_req(handler, pid, title="Prio0 Req", priority="P0")
    await _create_req(handler, pid, title="Prio1 Req", priority="P1")
    await _create_req(handler, pid, title="Prio2 Req", priority="P2")
    await _create_req(handler, pid, title="Prio3a Req", priority="P3")
    await _create_req(handler, pid, title="Prio3b Req", priority="P3")

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "limit": 2}
    )
    text = result[0].text
    # ORDER BY priority, created_at DESC — first two are P0 and P1
    assert "Prio0 Req" in text
    assert "Prio1 Req" in text
    assert "Prio2 Req" not in text
    assert "Found 2 requirement(s)" in text


@pytest.mark.asyncio
async def test_query_requirements_offset(setup):
    """offset parameter skips first N results."""
    handler, db, pid = setup
    # Use different priorities for deterministic ordering
    await _create_req(handler, pid, title="Prio0 Req", priority="P0")
    await _create_req(handler, pid, title="Prio1 Req", priority="P1")
    await _create_req(handler, pid, title="Prio2 Req", priority="P2")
    await _create_req(handler, pid, title="Prio3a Req", priority="P3")

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "limit": 2, "offset": 2}
    )
    text = result[0].text
    # Skip P0 and P1 (offset=2), return P2 and P3a (limit=2)
    assert "Prio0 Req" not in text
    assert "Prio1 Req" not in text
    assert "Prio2 Req" in text
    assert "Prio3a Req" in text


@pytest.mark.asyncio
async def test_query_requirements_default_limit(setup):
    """Default limit is 25 — creating fewer than 25 returns all."""
    handler, db, pid = setup
    for i in range(3):
        await _create_req(handler, pid, title=f"Req {i}", priority="P1")

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid}
    )
    text = result[0].text
    assert "REQ-0001" in text
    assert "REQ-0002" in text
    assert "REQ-0003" in text


@pytest.mark.asyncio
async def test_query_requirements_json_excludes_archived(setup):
    """JSON output_format also excludes archived requirements by default."""
    handler, db, pid = setup
    await _create_req(handler, pid, title="Visible")
    await _create_req(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_requirement", {"requirement_id": "REQ-0002"})

    result = await handler.handle_tool_call(
        "query_requirements", {"project_id": pid, "output_format": "json"}
    )
    data = json.loads(result[0].text)
    assert len(data) == 1
    assert data[0]["title"] == "Visible"


# =============================================================================
#  get_requirement_details
# =============================================================================


@pytest.mark.asyncio
async def test_get_requirement_details_full_record(setup):
    handler, db, pid = setup
    await _create_req(
        handler,
        pid,
        title="Detailed Req",
        priority="P0",
        req_type="FUNC",
        current_state="Before",
        desired_state="After",
        business_value="Critical",
        author="Alice",
    )
    result = await handler.handle_tool_call(
        "get_requirement_details", {"requirement_id": "REQ-0001"}
    )
    text = result[0].text
    assert "Detailed Req" in text
    assert "P0" in text
    assert "FUNC" in text
    assert "Before" in text
    assert "After" in text
    assert "Critical" in text
    assert "Alice" in text


@pytest.mark.asyncio
async def test_get_requirement_details_with_relationships(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Linked Req")
    await _create_task(db, pid, "TASK-0001", title="Linked Task")
    await _link(db, "requirement", "REQ-0001", "task", "TASK-0001", "implements", pid)

    result = await handler.handle_tool_call(
        "get_requirement_details", {"requirement_id": "REQ-0001"}
    )
    text = result[0].text
    assert "TASK-0001" in text
    assert "Linked Task" in text


@pytest.mark.asyncio
async def test_get_requirement_details_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "get_requirement_details", {"requirement_id": "REQ-9999"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  get_requirement_details — trace parameter
# =============================================================================


@pytest.mark.asyncio
async def test_get_requirement_details_with_trace(setup):
    """trace=true includes parent/child requirements in addition to tasks/ADRs."""
    handler, db, pid = setup
    # Create parent, child, and main requirement
    await _create_req(handler, pid, title="Parent Req")
    await _create_req(handler, pid, title="Main Req")
    await _create_req(handler, pid, title="Child Req")

    # Create task and ADR
    await _create_task(db, pid, "TASK-0001", title="Impl Task")
    await _create_adr(db, pid, "ADR-0001", title="Design Decision")

    # Link: REQ-0002 -> TASK-0001 (implements)
    await _link(db, "requirement", "REQ-0002", "task", "TASK-0001", "implements", pid)
    # Link: REQ-0002 -> ADR-0001 (addresses)
    await _link(db, "requirement", "REQ-0002", "architecture", "ADR-0001", "addresses", pid)
    # Link: REQ-0002 has parent REQ-0001 (REQ-0002 is source, REQ-0001 is target, rel=parent)
    await _link(db, "requirement", "REQ-0002", "requirement", "REQ-0001", "parent", pid)
    # Link: REQ-0003 has parent REQ-0002 (REQ-0003 is source, REQ-0002 is target, rel=parent)
    await _link(db, "requirement", "REQ-0003", "requirement", "REQ-0002", "parent", pid)

    result = await handler.handle_tool_call(
        "get_requirement_details", {"requirement_id": "REQ-0002", "trace": True}
    )
    text = result[0].text
    assert "Main Req" in text
    assert "TASK-0001" in text
    assert "ADR-0001" in text
    assert "Parent Req" in text or "REQ-0001" in text
    assert "Child Req" in text or "REQ-0003" in text


@pytest.mark.asyncio
async def test_get_requirement_details_without_trace(setup):
    """trace=false (default) does NOT include parent/child requirements."""
    handler, db, pid = setup
    await _create_req(handler, pid, title="Parent Req")
    await _create_req(handler, pid, title="Main Req")
    await _create_req(handler, pid, title="Child Req")

    # Link parent/child
    await _link(db, "requirement", "REQ-0002", "requirement", "REQ-0001", "parent", pid)
    await _link(db, "requirement", "REQ-0003", "requirement", "REQ-0002", "parent", pid)

    result = await handler.handle_tool_call(
        "get_requirement_details", {"requirement_id": "REQ-0002"}
    )
    text = result[0].text
    assert "Main Req" in text
    # Parent/child sections should NOT appear when trace=false
    assert "Parent Requirements" not in text
    assert "Child Requirements" not in text


@pytest.mark.asyncio
async def test_get_requirement_details_trace_not_found(setup):
    """trace=true on a nonexistent requirement returns error."""
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "get_requirement_details", {"requirement_id": "REQ-9999", "trace": True}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  batch_create_requirements
# =============================================================================


@pytest.mark.asyncio
async def test_batch_create_requirements_valid(setup):
    handler, db, pid = setup
    reqs = [
        {"type": "FUNC", "title": "Batch A", "priority": "P0"},
        {"type": "TECH", "title": "Batch B", "priority": "P1"},
        {"type": "BUS", "title": "Batch C", "priority": "P2"},
    ]
    result = await handler.handle_tool_call(
        "batch_create_requirements", {"project_id": pid, "requirements": reqs}
    )
    text = result[0].text
    assert "SUCCESS" in text
    assert "REQ-0001" in text
    assert "REQ-0002" in text
    assert "REQ-0003" in text

    # Verify all 3 in DB
    rows = await db.get_records("requirements", where_clause="project_id = ?", where_params=[pid])
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_batch_create_requirements_rollback_on_invalid(setup):
    """If one requirement in the batch fails validation, none are created."""
    handler, db, pid = setup
    reqs = [
        {"type": "FUNC", "title": "Good", "priority": "P0"},
        {"type": "FUNC", "title": ""},  # missing priority and empty title
        {"type": "FUNC", "title": "Good too", "priority": "P1"},
    ]
    result = await handler.handle_tool_call(
        "batch_create_requirements", {"project_id": pid, "requirements": reqs}
    )
    text = result[0].text
    assert "ERROR" in text

    # No requirements should have been created
    rows = await db.get_records("requirements", where_clause="project_id = ?", where_params=[pid])
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_batch_create_requirements_empty(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "batch_create_requirements", {"project_id": pid, "requirements": []}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_batch_create_requirements_validates_project(setup):
    handler, db, pid = setup
    reqs = [{"type": "FUNC", "title": "Good", "priority": "P0"}]
    result = await handler.handle_tool_call(
        "batch_create_requirements", {"project_id": "PROJ-9999", "requirements": reqs}
    )
    assert "ERROR" in result[0].text


# =============================================================================
#  clone_requirement
# =============================================================================


@pytest.mark.asyncio
async def test_clone_requirement_basic(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Original", priority="P1", req_type="FUNC")

    result = await handler.handle_tool_call(
        "clone_requirement", {"requirement_id": "REQ-0001"}
    )
    text = result[0].text
    assert "SUCCESS" in text
    assert "REQ-0002" in text

    # Cloned requirement should have same fields but new ID, Under Review status
    row = await db.execute_query(
        "SELECT title, priority, type, status FROM requirements WHERE id = 'REQ-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["title"] == "Original"
    assert row["priority"] == "P1"
    assert row["type"] == "FUNC"
    assert row["status"] == "Under Review"


@pytest.mark.asyncio
async def test_clone_requirement_copies_relationships(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Original")
    await _create_task(db, pid, "TASK-0001", title="Linked Task")
    await _link(db, "requirement", "REQ-0001", "task", "TASK-0001", "implements", pid)

    await handler.handle_tool_call("clone_requirement", {"requirement_id": "REQ-0001"})

    # The cloned requirement should also have the relationship
    rels = await db.get_records(
        "relationships",
        where_clause="source_id = ? AND source_type = 'requirement'",
        where_params=["REQ-0002"],
    )
    assert len(rels) >= 1
    assert any(r["target_id"] == "TASK-0001" for r in rels)


@pytest.mark.asyncio
async def test_clone_requirement_resets_status_to_under_review(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Advanced Req")
    # Move through lifecycle
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Approved"}
    )

    await handler.handle_tool_call("clone_requirement", {"requirement_id": "REQ-0001"})

    row = await db.execute_query(
        "SELECT status FROM requirements WHERE id = 'REQ-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Under Review"


@pytest.mark.asyncio
async def test_clone_requirement_target_project(setup):
    handler, db, pid = setup
    # Create second project
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0002", "Other Project"],
    )
    await _create_req(handler, pid, title="Cross-project")

    result = await handler.handle_tool_call(
        "clone_requirement", {"requirement_id": "REQ-0001", "target_project_id": "PROJ-0002"}
    )
    assert "SUCCESS" in result[0].text

    row = await db.execute_query(
        "SELECT project_id FROM requirements WHERE id = 'REQ-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["project_id"] == "PROJ-0002"


@pytest.mark.asyncio
async def test_clone_requirement_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call("clone_requirement", {"requirement_id": "REQ-9999"})
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  update_requirement_status — auto-only transition rejection
# =============================================================================


@pytest.mark.asyncio
async def test_manual_partially_implemented_rejected(setup):
    """Approved -> Partially Implemented is auto-only and must be rejected via manual tool call."""
    handler, db, pid = setup
    await _create_req(handler, pid)
    # Under Review -> Approved
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Approved"}
    )

    # Approved -> Partially Implemented (auto-only, not in manual transitions)
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Partially Implemented"}
    )
    assert "ERROR" in result[0].text
    assert "Invalid transition" in result[0].text or "transition" in result[0].text.lower()


@pytest.mark.asyncio
async def test_manual_partially_validated_rejected(setup):
    """Implemented -> Partially Validated is auto-only and must be rejected via manual tool call."""
    handler, db, pid = setup
    await _create_req(handler, pid)
    # Set status directly to Implemented (no manual path to get there)
    await db.execute_query(
        "UPDATE requirements SET status = 'Implemented' WHERE id = ?",
        ["REQ-0001"],
    )

    # Implemented -> Partially Validated (auto-only, not in manual transitions)
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Partially Validated"}
    )
    assert "ERROR" in result[0].text
    assert "Invalid transition" in result[0].text or "transition" in result[0].text.lower()


# =============================================================================
#  Handle unknown tool
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_tool(setup):
    handler, _, _ = setup
    result = await handler.handle_tool_call("nonexistent_tool", {})
    assert "ERROR" in result[0].text or "Unknown" in result[0].text


# =============================================================================
#  Auto-progression integration tests (LI-05)
#  Verify that the 2 consolidated DB triggers correctly auto-progress
#  requirement status when tasks advance, and that deprecated tasks
#  are properly excluded from calculations.
# =============================================================================


class TestAutoProgression:
    """Tests for the auto-progression DB triggers.

    Each test creates a fresh DB, project, requirements, and tasks, then verifies
    that requirement status is updated automatically when tasks reach Implemented
    or Validated.
    """

    @pytest.fixture
    async def env(self, v2_db_manager):
        """Set up RequirementHandler + TaskHandler + a test project."""
        req_handler = RequirementHandler(v2_db_manager)
        req_handler._testing_mode = True
        task_handler = TaskHandler(v2_db_manager)
        pid = "PROJ-0001"
        await v2_db_manager.execute_query(
            "INSERT INTO projects (id, name) VALUES (?, ?)",
            [pid, "Auto-Progression Test"],
        )
        return req_handler, task_handler, v2_db_manager, pid

    # -- Helpers ----------------------------------------------------------

    async def _create_approved_req(self, req_handler, pid, title="Test Req"):
        """Create a requirement and approve it. Returns the requirement ID."""
        await _create_req(req_handler, pid, title=title)
        # IDs are sequential: first call = REQ-0001, etc.  We read it from DB.
        rows = await req_handler.db.execute_query(
            "SELECT id FROM requirements WHERE title = ? AND project_id = ?",
            [title, pid],
            fetch_all=True,
            row_factory=True,
        )
        req_id = rows[-1]["id"]  # latest with that title
        await req_handler.handle_tool_call(
            "update_requirement_status", {"requirement_id": req_id, "new_status": "Approved"}
        )
        return req_id

    async def _create_task(self, task_handler, pid, title="Test Task"):
        """Create a task and return its ID."""
        result = await task_handler.handle_tool_call(
            "create_task", {"project_id": pid, "title": title, "priority": "P1"}
        )
        assert "SUCCESS" in result[0].text
        rows = await task_handler.db.execute_query(
            "SELECT id FROM tasks WHERE title = ? AND project_id = ?",
            [title, pid],
            fetch_all=True,
            row_factory=True,
        )
        return rows[-1]["id"]

    async def _link_task_to_req(self, db, task_id, req_id, pid):
        """Insert an 'implements' relationship between task and requirement."""
        await db.insert_record(
            "relationships",
            {
                "id": f"rel-{task_id}-{req_id}",
                "source_type": "task",
                "source_id": task_id,
                "target_type": "requirement",
                "target_id": req_id,
                "relationship_type": "implements",
                "project_id": pid,
            },
        )

    async def _approve_task(self, task_handler, task_id):
        """Move a task from Under Review -> Approved."""
        result = await task_handler.handle_tool_call(
            "update_task_status", {"task_id": task_id, "new_status": "Approved"}
        )
        assert "SUCCESS" in result[0].text

    async def _implement_task(self, task_handler, task_id):
        """Move a task from Approved -> Implemented."""
        result = await task_handler.handle_tool_call(
            "update_task_status", {"task_id": task_id, "new_status": "Implemented"}
        )
        assert "SUCCESS" in result[0].text

    async def _validate_task(self, task_handler, task_id):
        """Move a task from Implemented -> Validated."""
        result = await task_handler.handle_tool_call(
            "update_task_status", {"task_id": task_id, "new_status": "Validated"}
        )
        assert "SUCCESS" in result[0].text

    async def _deprecate_task(self, task_handler, task_id):
        """Move a task to Deprecated (allowed from any non-terminal state)."""
        result = await task_handler.handle_tool_call(
            "update_task_status", {"task_id": task_id, "new_status": "Deprecated"}
        )
        assert "SUCCESS" in result[0].text

    async def _get_req_status(self, db, req_id):
        """Read current requirement status directly from DB."""
        row = await db.execute_query(
            "SELECT status FROM requirements WHERE id = ?",
            [req_id],
            fetch_one=True,
            row_factory=True,
        )
        return row["status"]

    # -- Tests: Implemented cascade ------------------------------------

    @pytest.mark.asyncio
    async def test_single_task_implemented_cascades_to_implemented(self, env):
        """1 req + 1 task: task -> Implemented -> req becomes Implemented (via PI)."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Single Impl")
        task_id = await self._create_task(task_h, pid, "Task A")
        await self._link_task_to_req(db, task_id, req_id, pid)
        await self._approve_task(task_h, task_id)

        await self._implement_task(task_h, task_id)

        assert await self._get_req_status(db, req_id) == "Implemented"

    @pytest.mark.asyncio
    async def test_one_of_three_implemented_gives_partially_implemented(self, env):
        """1 req + 3 tasks: 1 task -> Implemented -> req becomes Partially Implemented."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Partial Impl")
        task_ids = []
        for i in range(3):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        await self._implement_task(task_h, task_ids[0])

        assert await self._get_req_status(db, req_id) == "Partially Implemented"

    @pytest.mark.asyncio
    async def test_all_three_implemented_gives_implemented(self, env):
        """1 req + 3 tasks: all 3 -> Implemented -> req becomes Implemented."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Full Impl")
        task_ids = []
        for i in range(3):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        for tid in task_ids:
            await self._implement_task(task_h, tid)

        assert await self._get_req_status(db, req_id) == "Implemented"

    # -- Tests: Validated cascade --------------------------------------

    @pytest.mark.asyncio
    async def test_single_task_validated_cascades_to_validated(self, env):
        """1 req + 1 task: task -> Validated -> req becomes Validated (via PV)."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Single Val")
        task_id = await self._create_task(task_h, pid, "Task A")
        await self._link_task_to_req(db, task_id, req_id, pid)
        await self._approve_task(task_h, task_id)

        await self._implement_task(task_h, task_id)
        assert await self._get_req_status(db, req_id) == "Implemented"

        await self._validate_task(task_h, task_id)
        assert await self._get_req_status(db, req_id) == "Validated"

    @pytest.mark.asyncio
    async def test_one_of_three_validated_gives_partially_validated(self, env):
        """1 req + 3 tasks: all implemented then 1 validated -> req becomes Partially Validated."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Partial Val")
        task_ids = []
        for i in range(3):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        # Implement all 3 first -> req = Implemented
        for tid in task_ids:
            await self._implement_task(task_h, tid)
        assert await self._get_req_status(db, req_id) == "Implemented"

        # Validate only 1 -> req = Partially Validated
        await self._validate_task(task_h, task_ids[0])
        assert await self._get_req_status(db, req_id) == "Partially Validated"

    @pytest.mark.asyncio
    async def test_all_three_validated_gives_validated(self, env):
        """1 req + 3 tasks: all implemented then all validated -> req becomes Validated."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Full Val")
        task_ids = []
        for i in range(3):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        for tid in task_ids:
            await self._implement_task(task_h, tid)

        for tid in task_ids:
            await self._validate_task(task_h, tid)

        assert await self._get_req_status(db, req_id) == "Validated"

    # -- Tests: Unrelated tasks ----------------------------------------

    @pytest.mark.asyncio
    async def test_unrelated_task_does_not_affect_requirement(self, env):
        """Req with no linked tasks: unrelated task advances -> req stays Approved."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Unrelated Req")
        # Create a task but do NOT link it to the requirement
        task_id = await self._create_task(task_h, pid, "Unlinked Task")
        await self._approve_task(task_h, task_id)

        await self._implement_task(task_h, task_id)

        assert await self._get_req_status(db, req_id) == "Approved"

    # -- Tests: Deprecated tasks excluded ------------------------------

    @pytest.mark.asyncio
    async def test_deprecated_task_excluded_from_implemented_calc(self, env):
        """3 tasks, 1 deprecated: other 2 -> Implemented -> req becomes Implemented."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Depr Impl")
        task_ids = []
        for i in range(3):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        # Deprecate task 3
        await self._deprecate_task(task_h, task_ids[2])

        # Implement remaining 2
        await self._implement_task(task_h, task_ids[0])
        assert await self._get_req_status(db, req_id) == "Partially Implemented"

        await self._implement_task(task_h, task_ids[1])
        assert await self._get_req_status(db, req_id) == "Implemented"

    @pytest.mark.asyncio
    async def test_deprecated_task_excluded_from_validated_calc(self, env):
        """3 tasks, 1 deprecated: other 2 -> Validated -> req becomes Validated."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Depr Val")
        task_ids = []
        for i in range(3):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        # Deprecate task 3
        await self._deprecate_task(task_h, task_ids[2])

        # Implement remaining 2 -> req = Implemented
        for tid in task_ids[:2]:
            await self._implement_task(task_h, tid)
        assert await self._get_req_status(db, req_id) == "Implemented"

        # Validate remaining 2 -> req = Validated
        for tid in task_ids[:2]:
            await self._validate_task(task_h, tid)
        assert await self._get_req_status(db, req_id) == "Validated"

    # -- Tests: Mixed Implemented/Validated states ---------------------

    @pytest.mark.asyncio
    async def test_implemented_task_jumps_to_validated_gives_partially_validated(self, env):
        """3 tasks all Implemented: 1 jumps to Validated -> req becomes Partially Validated."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Jump Val")
        task_ids = []
        for i in range(3):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        for tid in task_ids:
            await self._implement_task(task_h, tid)
        assert await self._get_req_status(db, req_id) == "Implemented"

        await self._validate_task(task_h, task_ids[0])
        assert await self._get_req_status(db, req_id) == "Partially Validated"

    @pytest.mark.asyncio
    async def test_partially_implemented_task_validated_gives_piv(self, env):
        """1 req in Partially Implemented: task jumps to Validated -> req becomes PIV."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "PIV Req")
        # 2 tasks — implement only 1 to get PI, then validate it
        task_ids = []
        for i in range(2):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        # Implement only task 0 -> req = Partially Implemented
        await self._implement_task(task_h, task_ids[0])
        assert await self._get_req_status(db, req_id) == "Partially Implemented"

        # Validate task 0 -> req = Partially Implemented Validated
        await self._validate_task(task_h, task_ids[0])
        assert await self._get_req_status(db, req_id) == "Partially Implemented Validated"

    @pytest.mark.asyncio
    async def test_piv_all_validated_gives_validated(self, env):
        """1 req in PIV: all non-deprecated tasks -> Validated -> req becomes Validated."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "PIV Full")
        task_ids = []
        for i in range(2):
            tid = await self._create_task(task_h, pid, f"Task {i}")
            await self._link_task_to_req(db, tid, req_id, pid)
            await self._approve_task(task_h, tid)
            task_ids.append(tid)

        # Implement task 0 -> PI, then validate task 0 -> PIV
        await self._implement_task(task_h, task_ids[0])
        await self._validate_task(task_h, task_ids[0])
        assert await self._get_req_status(db, req_id) == "Partially Implemented Validated"

        # Now implement + validate task 1 -> req should reach Validated
        await self._implement_task(task_h, task_ids[1])
        # After implement: req is PIV, trigger 1 does not change PIV
        assert await self._get_req_status(db, req_id) == "Partially Implemented Validated"

        await self._validate_task(task_h, task_ids[1])
        assert await self._get_req_status(db, req_id) == "Validated"

    # -- Tests: Lifecycle event logging --------------------------------

    @pytest.mark.asyncio
    async def test_auto_progression_events_have_system_actor(self, env):
        """Auto-progression events in lifecycle_events have actor = 'system:auto-progression'."""
        req_h, task_h, db, pid = env

        req_id = await self._create_approved_req(req_h, pid, "Events Req")
        task_id = await self._create_task(task_h, pid, "Events Task")
        await self._link_task_to_req(db, task_id, req_id, pid)
        await self._approve_task(task_h, task_id)

        await self._implement_task(task_h, task_id)
        await self._validate_task(task_h, task_id)

        # Query status_change events for this requirement (excludes "created" etc.)
        events = await db.execute_query(
            "SELECT * FROM lifecycle_events "
            "WHERE entity_id = ? AND entity_type = 'requirement' "
            "AND event_type = 'status_change' "
            "ORDER BY id",
            [req_id],
            fetch_all=True,
            row_factory=True,
        )

        # The manual Approved transition is done by the handler -> actor = 'MCP User'
        manual_events = [e for e in events if e["actor"] == "MCP User"]
        auto_events = [e for e in events if e["actor"] == "system:auto-progression"]

        # Manual: Under Review -> Approved
        assert len(manual_events) == 1
        assert manual_events[0]["from_value"] == "Under Review"
        assert manual_events[0]["to_value"] == "Approved"

        # Auto-progression transitions: Approved->PI, PI->Implemented, Implemented->PV, PV->Validated
        assert len(auto_events) == 4
        auto_transitions = [(e["from_value"], e["to_value"]) for e in auto_events]
        assert ("Approved", "Partially Implemented") in auto_transitions
        assert ("Partially Implemented", "Implemented") in auto_transitions
        assert ("Implemented", "Partially Validated") in auto_transitions
        assert ("Partially Validated", "Validated") in auto_transitions


# =============================================================================
#  Coverage improvement tests
# =============================================================================


class TestCoverageGaps:
    """Tests targeting specific uncovered lines in requirement_handler.py"""

    @pytest.mark.asyncio
    async def test_handle_tool_call_general_exception(self, setup):
        """handle_tool_call wraps unexpected exceptions in error response."""
        handler, db, project_id = setup

        async def raise_error(params):
            raise RuntimeError("boom")

        handler._create_requirement = raise_error
        result = await handler.handle_tool_call("create_requirement", {"project_id": "X"})
        assert "ERROR" in result[0].text

    @pytest.mark.asyncio
    async def test_update_requirement_missing_id(self, setup):
        """update_requirement without requirement_id returns error."""
        handler, db, project_id = setup
        result = await handler.handle_tool_call("update_requirement", {})
        assert "ERROR" in result[0].text
        assert "Missing required" in result[0].text

    @pytest.mark.asyncio
    async def test_update_requirement_status_missing_params(self, setup):
        """update_requirement_status without new_status returns error."""
        handler, db, project_id = setup
        result = await handler.handle_tool_call(
            "update_requirement_status", {"requirement_id": "REQ-0001"}
        )
        assert "ERROR" in result[0].text

    @pytest.mark.asyncio
    async def test_archive_requirement_missing_id(self, setup):
        """archive_requirement without requirement_id returns error."""
        handler, db, project_id = setup
        result = await handler.handle_tool_call("archive_requirement", {})
        assert "ERROR" in result[0].text

    @pytest.mark.asyncio
    async def test_batch_create_missing_params(self, setup):
        """batch_create_requirements without required params returns error."""
        handler, db, project_id = setup
        result = await handler.handle_tool_call("batch_create_requirements", {})
        assert "ERROR" in result[0].text

    @pytest.mark.asyncio
    async def test_batch_create_missing_type(self, setup):
        """batch_create_requirements with missing type field returns error."""
        handler, db, project_id = setup
        result = await handler.handle_tool_call(
            "batch_create_requirements",
            {"project_id": project_id, "requirements": [{"title": "X", "priority": "P0"}]},
        )
        assert "ERROR" in result[0].text
        assert "type" in result[0].text

    @pytest.mark.asyncio
    async def test_batch_create_missing_priority(self, setup):
        """batch_create_requirements with missing priority returns error."""
        handler, db, project_id = setup
        result = await handler.handle_tool_call(
            "batch_create_requirements",
            {"project_id": project_id, "requirements": [{"title": "X", "type": "FUNC"}]},
        )
        assert "ERROR" in result[0].text
        assert "priority" in result[0].text

    @pytest.mark.asyncio
    async def test_clone_requirement_invalid_target_project(self, setup):
        """clone_requirement to nonexistent target project returns error."""
        handler, db, project_id = setup
        result = await _create_req(handler, project_id)
        req_id = _extract_id(result)

        result = await handler.handle_tool_call(
            "clone_requirement",
            {"requirement_id": req_id, "target_project_id": "PROJ-9999"},
        )
        assert "ERROR" in result[0].text

    @pytest.mark.asyncio
    async def test_clone_requirement_copies_optional_fields(self, setup):
        """clone copies all optional fields from original."""
        handler, db, project_id = setup
        result = await _create_req(
            handler, project_id,
            title="With Extras",
            current_state="Old state",
            desired_state="New state",
            business_value="High",
            author="Alice",
            functional_requirements=["FR-1"],
            acceptance_criteria=["AC-1"],
        )
        req_id = _extract_id(result)

        clone_result = await handler.handle_tool_call(
            "clone_requirement", {"requirement_id": req_id}
        )
        assert "SUCCESS" in clone_result[0].text
        clone_id = _extract_id(clone_result)

        # Verify clone has the same optional fields
        rows = await db.get_records("requirements", "*", where_clause="id = ?", where_params=[clone_id])
        clone = dict(rows[0])
        assert clone["current_state"] == "Old state"
        assert clone["desired_state"] == "New state"
        assert clone["business_value"] == "High"
        assert clone["author"] == "Alice"

    @pytest.mark.asyncio
    async def test_get_requirement_details_with_json_arrays(self, setup):
        """get_requirement_details renders functional_requirements, nonfunctional, out_of_scope, acceptance_criteria."""
        handler, db, project_id = setup
        result = await _create_req(
            handler, project_id,
            title="Rich Details Req",
            functional_requirements=["FR-1: Must do X", "FR-2: Must do Y"],
            nonfunctional_requirements=["NFR-1: Performance"],
            out_of_scope=["OOS-1: Legacy system"],
            acceptance_criteria=["AC-1: System responds in 200ms"],
        )
        req_id = _extract_id(result)

        details = await handler.handle_tool_call(
            "get_requirement_details", {"requirement_id": req_id}
        )
        text = details[0].text
        assert "Functional Requirements" in text
        assert "FR-1" in text
        assert "Non-Functional Requirements" in text
        assert "NFR-1" in text
        assert "Out of Scope" in text
        assert "OOS-1" in text
        assert "Acceptance Criteria" in text
        assert "AC-1" in text

    @pytest.mark.asyncio
    async def test_get_requirement_details_trace_parent_child(self, setup):
        """get_requirement_details with trace=true renders parent and child sections."""
        handler, db, project_id = setup
        # Create parent and child requirements
        parent_result = await _create_req(handler, project_id, title="Parent Req")
        parent_id = _extract_id(parent_result)

        child_result = await _create_req(handler, project_id, title="Child Req")
        child_id = _extract_id(child_result)

        # Create parent relationship: child -> parent (child has parent)
        await db.execute_query(
            "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, "
            "relationship_type, project_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [f"rel-parent-{child_id}", "requirement", child_id, "requirement", parent_id, "parent", project_id],
        )

        # Get details of parent (should show child)
        details = await handler.handle_tool_call(
            "get_requirement_details", {"requirement_id": parent_id, "trace": True}
        )
        text = details[0].text
        assert "Child Requirements" in text
        assert child_id in text

        # Get details of child (should show parent)
        details = await handler.handle_tool_call(
            "get_requirement_details", {"requirement_id": child_id, "trace": True}
        )
        text = details[0].text
        assert "Parent Requirements" in text
        assert parent_id in text

    @pytest.mark.asyncio
    async def test_query_requirements_by_type(self, setup):
        """query_requirements filters by type correctly."""
        handler, db, project_id = setup
        await _create_req(handler, project_id, title="Func Req", req_type="FUNC")
        await _create_req(handler, project_id, title="Tech Req", req_type="TECH")

        result = await handler.handle_tool_call(
            "query_requirements", {"project_id": project_id, "type": "TECH"}
        )
        text = result[0].text
        assert "Tech Req" in text
        assert "Func Req" not in text

    @pytest.mark.asyncio
    async def test_query_requirements_filter_description_includes_type(self, setup):
        """query_requirements filter description includes type."""
        handler, db, project_id = setup
        await _create_req(handler, project_id, title="Req X", req_type="TECH")

        result = await handler.handle_tool_call(
            "query_requirements", {"project_id": project_id, "type": "TECH"}
        )
        text = result[0].text
        assert "type: TECH" in text
