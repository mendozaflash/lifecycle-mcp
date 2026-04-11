"""Tests for RequirementHandler v2 (DB-04).

Validates all 10 MCP tools:
  create_requirement, update_requirement, update_requirement_status,
  archive_requirement, query_requirements, query_requirements_json,
  get_requirement_details, trace_requirement,
  batch_create_requirements, clone_requirement
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
        "VALUES (?, ?, ?, 'P1', 'Not Started')",
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
        "query_requirements_json",
        "get_requirement_details",
        "trace_requirement",
        "batch_create_requirements",
        "clone_requirement",
    ]
    for name in expected:
        assert name in names, f"Missing tool definition: {name}"
    assert len(tools) == 10


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
    assert row["status"] == "Draft"
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
    await _create_req(handler, pid)

    # Draft -> Under Review
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Under Review"}
    )
    assert "SUCCESS" in result[0].text

    # Under Review -> Approved
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Approved"}
    )
    assert "SUCCESS" in result[0].text


@pytest.mark.asyncio
async def test_update_status_invalid_transition(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)  # status = "Draft"

    # Draft -> Validated (not allowed)
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Validated"}
    )
    assert "ERROR" in result[0].text
    assert "Invalid transition" in result[0].text or "transition" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_status_deprecated_is_terminal(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)

    # Draft -> Deprecated
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Deprecated"}
    )

    # Deprecated -> Draft (not allowed, Deprecated is terminal)
    result = await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Draft"}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_update_status_logs_event(setup):
    handler, db, pid = setup
    await _create_req(handler, pid)

    await handler.handle_tool_call(
        "update_requirement_status",
        {"requirement_id": "REQ-0001", "new_status": "Under Review", "comment": "Ready for review"},
    )

    # Check lifecycle_events table for trigger-based log
    events = await db.execute_query(
        "SELECT * FROM lifecycle_events WHERE entity_id = 'REQ-0001' AND event_type = 'status_change'",
        fetch_all=True,
        row_factory=True,
    )
    assert len(events) >= 1
    assert events[0]["from_value"] == "Draft"
    assert events[0]["to_value"] == "Under Review"


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
    await _create_req(handler, pid)
    result = await handler.handle_tool_call(
        "update_requirement_status",
        {"requirement_id": "REQ-0001", "new_status": "Under Review", "comment": "Please review"},
    )
    assert "SUCCESS" in result[0].text

    # Verify status changed
    row = await db.execute_query(
        "SELECT status FROM requirements WHERE id = 'REQ-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Under Review"


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
    await _create_req(handler, pid, title="Draft Req")
    await _create_req(handler, pid, title="Reviewed Req")
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0002", "new_status": "Under Review"}
    )

    result = await handler.handle_tool_call(
        "query_requirements", {"status": "Under Review"}
    )
    text = result[0].text
    assert "Reviewed Req" in text
    assert "Draft Req" not in text


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
#  query_requirements_json
# =============================================================================


@pytest.mark.asyncio
async def test_query_requirements_json_format(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="JSON Req", acceptance_criteria=["AC1"])

    result = await handler.handle_tool_call("query_requirements_json", {"project_id": pid})
    data = json.loads(result[0].text)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "JSON Req"
    # acceptance_criteria should be parsed from JSON string
    assert data[0]["acceptance_criteria"] == ["AC1"]


@pytest.mark.asyncio
async def test_query_requirements_json_excludes_archived(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Visible")
    await _create_req(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_requirement", {"requirement_id": "REQ-0002"})

    result = await handler.handle_tool_call("query_requirements_json", {"project_id": pid})
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
#  trace_requirement
# =============================================================================


@pytest.mark.asyncio
async def test_trace_requirement_linked_entities(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Traced Req")
    await _create_task(db, pid, "TASK-0001", title="Impl Task")
    await _create_adr(db, pid, "ADR-0001", title="Design Decision")
    await _link(db, "requirement", "REQ-0001", "task", "TASK-0001", "implements", pid)
    await _link(db, "requirement", "REQ-0001", "architecture", "ADR-0001", "addresses", pid)

    result = await handler.handle_tool_call(
        "trace_requirement", {"requirement_id": "REQ-0001"}
    )
    text = result[0].text
    assert "Traced Req" in text
    assert "TASK-0001" in text
    assert "ADR-0001" in text


@pytest.mark.asyncio
async def test_trace_requirement_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "trace_requirement", {"requirement_id": "REQ-9999"}
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

    # Cloned requirement should have same fields but new ID, Draft status
    row = await db.execute_query(
        "SELECT title, priority, type, status FROM requirements WHERE id = 'REQ-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["title"] == "Original"
    assert row["priority"] == "P1"
    assert row["type"] == "FUNC"
    assert row["status"] == "Draft"


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
async def test_clone_requirement_resets_status_to_draft(setup):
    handler, db, pid = setup
    await _create_req(handler, pid, title="Advanced Req")
    # Move through lifecycle
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Under Review"}
    )
    await handler.handle_tool_call(
        "update_requirement_status", {"requirement_id": "REQ-0001", "new_status": "Approved"}
    )

    await handler.handle_tool_call("clone_requirement", {"requirement_id": "REQ-0001"})

    row = await db.execute_query(
        "SELECT status FROM requirements WHERE id = 'REQ-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Draft"


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
#  Handle unknown tool
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_tool(setup):
    handler, _, _ = setup
    result = await handler.handle_tool_call("nonexistent_tool", {})
    assert "ERROR" in result[0].text or "Unknown" in result[0].text
