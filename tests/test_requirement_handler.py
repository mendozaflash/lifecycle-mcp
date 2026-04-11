"""Tests for RequirementHandler v2 (DB-04, BF-03).

Validates all 8 MCP tools:
  create_requirement, update_requirement, update_requirement_status,
  archive_requirement, query_requirements (with output_format/limit/offset),
  get_requirement_details (with trace), batch_create_requirements,
  clone_requirement
"""

import json

import pytest

from lifecycle_mcp.handlers.requirement_handler import RequirementHandler


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
