"""Tests for TaskHandler v2 (BF-04 tool simplification).

Validates all 8 MCP tools:
  create_task, update_task, update_task_status, archive_task,
  query_tasks, get_task_details,
  batch_create_tasks, clone_task

Removed tools (merged into query_tasks / get_task_details):
  query_tasks_json -> query_tasks(output_format="json")
  get_task_requirement_context -> get_task_details(sections=["requirements"])
  get_task_adr_context -> get_task_details(sections=["adrs"])
  get_task_full_context -> get_task_details(sections=["requirements","adrs"])
"""

import json

import pytest

from lifecycle_mcp.handlers.task_handler import TaskHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def task_env(v2_db_manager):
    """Set up a TaskHandler + a test project. Returns (handler, db, project_id)."""
    handler = TaskHandler(v2_db_manager)
    await v2_db_manager.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0001", "Test Project"],
    )
    return handler, v2_db_manager, "PROJ-0001"


# -- Helpers -----------------------------------------------------------------


async def _create_task(handler, project_id, title="Test Task", priority="P1", **extra):
    """Shorthand to create a task via handle_tool_call."""
    params = {"project_id": project_id, "title": title, "priority": priority}
    params.update(extra)
    return await handler.handle_tool_call("create_task", params)


async def _create_requirement(db, project_id, req_id, title="Test Requirement"):
    """Insert a requirement directly into the DB for relationship testing."""
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, priority, status) "
        "VALUES (?, ?, 'FUNC', ?, 'P1', 'Under Review')",
        [req_id, project_id, title],
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
async def test_tool_definitions_list(task_env):
    handler, _, _ = task_env
    tools = handler.get_tool_definitions()
    names = [t["name"] for t in tools]
    expected = [
        "create_task",
        "update_task",
        "update_task_status",
        "archive_task",
        "query_tasks",
        "get_task_details",
        "batch_create_tasks",
        "clone_task",
    ]
    for name in expected:
        assert name in names, f"Missing tool definition: {name}"
    assert len(tools) == 8


@pytest.mark.asyncio
async def test_removed_tools_not_present(task_env):
    handler, _, _ = task_env
    tools = handler.get_tool_definitions()
    names = [t["name"] for t in tools]
    removed = [
        "query_tasks_json",
        "get_task_requirement_context",
        "get_task_adr_context",
        "get_task_full_context",
    ]
    for name in removed:
        assert name not in names, f"Tool should have been removed: {name}"


@pytest.mark.asyncio
async def test_no_github_tools(task_env):
    handler, _, _ = task_env
    tools = handler.get_tool_definitions()
    names = [t["name"] for t in tools]
    assert "sync_task_from_github" not in names
    assert "bulk_sync_github_tasks" not in names


# =============================================================================
#  create_task
# =============================================================================


@pytest.mark.asyncio
async def test_create_task_basic(task_env):
    handler, db, pid = task_env
    result = await _create_task(handler, pid)
    text = result[0].text
    assert "TASK-0001" in text
    assert "SUCCESS" in text


@pytest.mark.asyncio
async def test_create_task_sequential_ids(task_env):
    handler, db, pid = task_env
    r1 = await _create_task(handler, pid, title="A")
    r2 = await _create_task(handler, pid, title="B")
    r3 = await _create_task(handler, pid, title="C")
    assert "TASK-0001" in r1[0].text
    assert "TASK-0002" in r2[0].text
    assert "TASK-0003" in r3[0].text


@pytest.mark.asyncio
async def test_create_task_stores_planning_fields(task_env):
    handler, db, pid = task_env
    await _create_task(
        handler,
        pid,
        title="Planned Task",
        effort="L",
        user_story="As a dev, I want tests",
        acceptance_criteria=["AC-1", "AC-2"],
        assignee="Alice",
        scope_boundaries="Within module X",
        technical_outline="Use pattern Y",
        files_touched=["a.py", "b.py"],
        verification_commands=["pytest tests/"],
        public_symbols=["MyClass", "my_func"],
        risk_notes="Might break Z",
    )
    row = await db.execute_query(
        "SELECT * FROM tasks WHERE id = 'TASK-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row is not None
    assert row["title"] == "Planned Task"
    assert row["effort"] == "L"
    assert row["user_story"] == "As a dev, I want tests"
    assert json.loads(row["acceptance_criteria"]) == ["AC-1", "AC-2"]
    assert row["assignee"] == "Alice"
    assert row["scope_boundaries"] == "Within module X"
    assert row["technical_outline"] == "Use pattern Y"
    assert json.loads(row["files_touched"]) == ["a.py", "b.py"]
    assert json.loads(row["verification_commands"]) == ["pytest tests/"]
    assert json.loads(row["public_symbols"]) == ["MyClass", "my_func"]
    assert row["risk_notes"] == "Might break Z"
    assert row["status"] == "Under Review"
    assert row["project_id"] == pid


@pytest.mark.asyncio
async def test_create_task_validates_project_exists(task_env):
    handler, db, pid = task_env
    result = await _create_task(handler, "PROJ-9999", title="Orphan")
    text = result[0].text
    assert "ERROR" in text
    assert "PROJ-9999" in text


@pytest.mark.asyncio
async def test_create_task_missing_required_fields(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call("create_task", {"title": "No project"})
    assert "ERROR" in result[0].text
    assert "Missing required" in result[0].text


@pytest.mark.asyncio
async def test_create_task_with_parent(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Parent")
    result = await _create_task(handler, pid, title="Child", parent_task_id="TASK-0001")
    text = result[0].text
    assert "SUCCESS" in text
    assert "TASK-0002" in text
    # Verify parent_task_id stored
    row = await db.execute_query(
        "SELECT parent_task_id FROM tasks WHERE id = 'TASK-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["parent_task_id"] == "TASK-0001"


# =============================================================================
#  update_task (new broad-update tool)
# =============================================================================


@pytest.mark.asyncio
async def test_update_task_title_and_priority(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Old Title", priority="P2")
    result = await handler.handle_tool_call(
        "update_task", {"task_id": "TASK-0001", "title": "New Title", "priority": "P0"}
    )
    assert "SUCCESS" in result[0].text
    row = await db.execute_query(
        "SELECT title, priority FROM tasks WHERE id = 'TASK-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["title"] == "New Title"
    assert row["priority"] == "P0"


@pytest.mark.asyncio
async def test_update_task_planning_fields(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid)
    result = await handler.handle_tool_call(
        "update_task",
        {
            "task_id": "TASK-0001",
            "scope_boundaries": "Module A only",
            "technical_outline": "New approach",
            "files_touched": ["x.py"],
            "verification_commands": ["make test"],
            "public_symbols": ["Foo"],
            "risk_notes": "Low risk",
        },
    )
    assert "SUCCESS" in result[0].text
    row = await db.execute_query(
        "SELECT scope_boundaries, technical_outline, files_touched, verification_commands, "
        "public_symbols, risk_notes FROM tasks WHERE id = 'TASK-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["scope_boundaries"] == "Module A only"
    assert row["technical_outline"] == "New approach"
    assert json.loads(row["files_touched"]) == ["x.py"]
    assert json.loads(row["verification_commands"]) == ["make test"]
    assert json.loads(row["public_symbols"]) == ["Foo"]
    assert row["risk_notes"] == "Low risk"


@pytest.mark.asyncio
async def test_update_task_rejects_archived(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid)
    await handler.handle_tool_call("archive_task", {"task_id": "TASK-0001"})
    result = await handler.handle_tool_call(
        "update_task", {"task_id": "TASK-0001", "title": "Nope"}
    )
    assert "ERROR" in result[0].text
    assert "archived" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_task_rejects_nonexistent(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call(
        "update_task", {"task_id": "TASK-9999", "title": "Nope"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  update_task_status (narrow write)
# =============================================================================


@pytest.mark.asyncio
async def test_update_task_status_valid_transitions(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid)

    # Under Review -> Approved
    result = await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
    )
    assert "SUCCESS" in result[0].text

    # Approved -> Implemented
    result = await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Implemented"}
    )
    assert "SUCCESS" in result[0].text


@pytest.mark.asyncio
async def test_update_task_status_invalid_transition(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid)  # status = "Under Review"

    # Under Review -> Implemented (not allowed, must go through Approved)
    result = await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Implemented"}
    )
    assert "ERROR" in result[0].text
    assert "Invalid transition" in result[0].text or "transition" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_task_status_no_backward_transitions(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid)
    await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
    )
    await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Implemented"}
    )
    # Implemented -> Approved (not allowed, no backward transitions)
    result = await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_update_task_status_deprecated_is_terminal(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid)
    await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Deprecated"}
    )
    result = await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_update_task_status_full_lifecycle(task_env):
    """Test the full task lifecycle: Under Review -> Approved -> Implemented -> Validated -> Deprecated."""
    handler, db, pid = task_env
    await _create_task(handler, pid)

    for status in ["Approved", "Implemented", "Validated", "Deprecated"]:
        result = await handler.handle_tool_call(
            "update_task_status", {"task_id": "TASK-0001", "new_status": status}
        )
        assert "SUCCESS" in result[0].text, f"Failed transition to {status}"


@pytest.mark.asyncio
async def test_update_task_status_narrow_write(task_env):
    """Verify update_task_status ONLY updates status, execution_notes, deviation_from_plan."""
    handler, db, pid = task_env
    await _create_task(
        handler,
        pid,
        title="Original Title",
        priority="P1",
        assignee="Alice",
        effort="M",
    )
    # Transition with execution_notes and deviation_from_plan
    await handler.handle_tool_call(
        "update_task_status",
        {
            "task_id": "TASK-0001",
            "new_status": "Approved",
            "execution_notes": "Started work",
            "deviation_from_plan": "None so far",
        },
    )
    row = await db.execute_query(
        "SELECT title, priority, assignee, effort, status, execution_notes, deviation_from_plan "
        "FROM tasks WHERE id = 'TASK-0001'",
        fetch_one=True,
        row_factory=True,
    )
    # Status and execution fields updated
    assert row["status"] == "Approved"
    assert row["execution_notes"] == "Started work"
    assert row["deviation_from_plan"] == "None so far"
    # Other fields unchanged
    assert row["title"] == "Original Title"
    assert row["priority"] == "P1"
    assert row["assignee"] == "Alice"
    assert row["effort"] == "M"


@pytest.mark.asyncio
async def test_update_task_status_not_found(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  archive_task
# =============================================================================


@pytest.mark.asyncio
async def test_archive_task(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid)
    result = await handler.handle_tool_call("archive_task", {"task_id": "TASK-0001"})
    assert "SUCCESS" in result[0].text

    row = await db.execute_query(
        "SELECT is_archived FROM tasks WHERE id = 'TASK-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["is_archived"] == 1


@pytest.mark.asyncio
async def test_archive_task_not_found(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call("archive_task", {"task_id": "TASK-9999"})
    assert "ERROR" in result[0].text


# =============================================================================
#  query_tasks — output_format, limit, offset
# =============================================================================


@pytest.mark.asyncio
async def test_query_tasks_by_project(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="A")
    await _create_task(handler, pid, title="B")
    result = await handler.handle_tool_call("query_tasks", {"project_id": pid})
    text = result[0].text
    assert "2" in text or "TASK-0001" in text


@pytest.mark.asyncio
async def test_query_tasks_excludes_archived(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Visible")
    await _create_task(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_task", {"task_id": "TASK-0002"})

    result = await handler.handle_tool_call("query_tasks", {"project_id": pid})
    text = result[0].text
    assert "TASK-0001" in text
    assert "TASK-0002" not in text


@pytest.mark.asyncio
async def test_query_tasks_include_archived(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Visible")
    await _create_task(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_task", {"task_id": "TASK-0002"})

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "include_archived": True}
    )
    text = result[0].text
    assert "TASK-0001" in text
    assert "TASK-0002" in text


@pytest.mark.asyncio
async def test_query_tasks_filters(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="P0 Task", priority="P0")
    await _create_task(handler, pid, title="P2 Task", priority="P2")

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "priority": "P0"}
    )
    text = result[0].text
    assert "P0 Task" in text
    assert "P2 Task" not in text


@pytest.mark.asyncio
async def test_query_tasks_by_status(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Todo")
    await _create_task(handler, pid, title="Active")
    await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0002", "new_status": "Approved"}
    )

    result = await handler.handle_tool_call(
        "query_tasks", {"status": "Approved"}
    )
    text = result[0].text
    assert "Active" in text
    assert "Todo" not in text


@pytest.mark.asyncio
async def test_query_tasks_by_assignee(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Alice Task", assignee="Alice")
    await _create_task(handler, pid, title="Bob Task", assignee="Bob")

    result = await handler.handle_tool_call(
        "query_tasks", {"assignee": "Alice"}
    )
    text = result[0].text
    assert "Alice Task" in text
    assert "Bob Task" not in text


@pytest.mark.asyncio
async def test_query_tasks_no_results(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call("query_tasks", {"status": "Implemented"})
    assert "No tasks found" in result[0].text or "0" in result[0].text


@pytest.mark.asyncio
async def test_query_tasks_summary_format(task_env):
    """Default output_format='summary' returns one-line-per-task pipe-delimited."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Sum Task", priority="P1")
    await _create_task(handler, pid, title="Another Task", priority="P0")

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "output_format": "summary"}
    )
    text = result[0].text
    # Each task should appear as a pipe-delimited line
    assert "TASK-0001" in text
    assert "|" in text
    assert "Sum Task" in text
    assert "TASK-0002" in text


@pytest.mark.asyncio
async def test_query_tasks_json_output_format(task_env):
    """output_format='json' returns a JSON array of task objects."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="JSON Task", priority="P1", acceptance_criteria=["AC1"])

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "output_format": "json"}
    )
    data = json.loads(result[0].text)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "JSON Task"
    assert data[0]["id"] == "TASK-0001"
    assert data[0]["status"] == "Under Review"
    assert data[0]["priority"] == "P1"


@pytest.mark.asyncio
async def test_query_tasks_markdown_format(task_env):
    """output_format='markdown' returns the verbose markdown-style format."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="MD Task", priority="P2", assignee="Bob")

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "output_format": "markdown"}
    )
    text = result[0].text
    # Markdown format uses the bullet-style format with square-bracket status
    assert "TASK-0001" in text
    assert "MD Task" in text
    assert "Bob" in text


@pytest.mark.asyncio
async def test_query_tasks_default_format_is_summary(task_env):
    """When no output_format given, should behave like 'summary'."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Default Task", priority="P1")

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid}
    )
    text = result[0].text
    # Summary format uses pipe delimiter
    assert "TASK-0001" in text
    assert "|" in text
    assert "Default Task" in text


@pytest.mark.asyncio
async def test_query_tasks_limit(task_env):
    """limit parameter restricts the number of returned tasks."""
    handler, db, pid = task_env
    for i in range(5):
        await _create_task(handler, pid, title=f"Task {i}", priority="P1")

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "limit": 2}
    )
    text = result[0].text
    # Should only contain 2 tasks
    assert "2" in text  # count in the summary line


@pytest.mark.asyncio
async def test_query_tasks_offset(task_env):
    """offset parameter skips tasks for pagination."""
    handler, db, pid = task_env
    for i in range(5):
        await _create_task(handler, pid, title=f"Task {i}", priority="P1")

    # Get first 2
    result1 = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "limit": 2, "offset": 0}
    )
    # Get next 2
    result2 = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "limit": 2, "offset": 2}
    )
    text1 = result1[0].text
    text2 = result2[0].text
    # The two result sets should not overlap (different tasks)
    assert text1 != text2


@pytest.mark.asyncio
async def test_query_tasks_json_excludes_archived(task_env):
    """JSON format should also respect include_archived flag."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Visible")
    await _create_task(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_task", {"task_id": "TASK-0002"})

    result = await handler.handle_tool_call(
        "query_tasks", {"project_id": pid, "output_format": "json"}
    )
    data = json.loads(result[0].text)
    assert len(data) == 1
    assert data[0]["title"] == "Visible"


# =============================================================================
#  get_task_details — sections parameter
# =============================================================================


@pytest.mark.asyncio
async def test_get_task_details_full_record(task_env):
    handler, db, pid = task_env
    await _create_task(
        handler,
        pid,
        title="Detailed Task",
        priority="P0",
        effort="XL",
        assignee="Bob",
    )
    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001"}
    )
    text = result[0].text
    assert "Detailed Task" in text
    assert "P0" in text
    assert "XL" in text
    assert "Bob" in text


@pytest.mark.asyncio
async def test_get_task_details_default_sections(task_env):
    """Default sections=['planning','requirements'] should show planning + requirements."""
    handler, db, pid = task_env
    await _create_task(
        handler,
        pid,
        title="Default Sections Task",
        scope_boundaries="Module A",
        technical_outline="Pattern X",
        risk_notes="Low risk",
    )
    await _create_requirement(db, pid, "REQ-0001", title="Linked Req")
    await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)

    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001"}
    )
    text = result[0].text
    # Planning section should be present
    assert "Planning" in text
    assert "Module A" in text
    assert "Pattern X" in text
    # Requirements section should be present
    assert "REQ-0001" in text
    assert "Linked Req" in text


@pytest.mark.asyncio
async def test_get_task_details_requirements_section(task_env):
    """sections=['requirements'] should include linked requirements."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Req Section Task")
    await _create_requirement(db, pid, "REQ-0001", title="Linked Req")
    await _create_requirement(db, pid, "REQ-0002", title="Unlinked Req")
    await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)

    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001", "sections": ["requirements"]}
    )
    text = result[0].text
    assert "REQ-0001" in text
    assert "Linked Req" in text
    assert "REQ-0002" not in text


@pytest.mark.asyncio
async def test_get_task_details_adrs_section(task_env):
    """sections=['adrs'] should include linked ADRs but not requirements."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="ADR Section Task")
    await _create_adr(db, pid, "ADR-0001", title="Design Decision")
    await _create_adr(db, pid, "ADR-0002", title="Unlinked ADR")
    await _link(db, "task", "TASK-0001", "architecture", "ADR-0001", "addresses", pid)

    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001", "sections": ["adrs"]}
    )
    text = result[0].text
    assert "ADR-0001" in text
    assert "Design Decision" in text
    assert "ADR-0002" not in text
    # Requirements section should NOT be present since not requested
    assert "Linked Requirement" not in text


@pytest.mark.asyncio
async def test_get_task_details_all_sections(task_env):
    """All sections requested should include planning, execution, requirements, adrs, subtasks."""
    handler, db, pid = task_env
    await _create_task(
        handler,
        pid,
        title="Full Task",
        scope_boundaries="Full scope",
        technical_outline="Full outline",
        risk_notes="Full risk",
    )
    # Set execution fields via status transition
    await handler.handle_tool_call(
        "update_task_status",
        {
            "task_id": "TASK-0001",
            "new_status": "Approved",
            "execution_notes": "Work started",
            "deviation_from_plan": "Minor deviation",
        },
    )
    # Create subtask
    await _create_task(handler, pid, title="Child Task", parent_task_id="TASK-0001")
    # Create linked requirement and ADR
    await _create_requirement(db, pid, "REQ-0001", title="Full Req")
    await _create_adr(db, pid, "ADR-0001", title="Full ADR")
    await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)
    await _link(db, "task", "TASK-0001", "architecture", "ADR-0001", "addresses", pid)

    all_sections = ["planning", "execution", "requirements", "adrs", "subtasks"]
    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001", "sections": all_sections}
    )
    text = result[0].text
    # Planning
    assert "Planning" in text
    assert "Full scope" in text
    # Execution
    assert "Execution" in text
    assert "Work started" in text
    assert "Minor deviation" in text
    # Requirements
    assert "REQ-0001" in text
    assert "Full Req" in text
    # ADRs
    assert "ADR-0001" in text
    assert "Full ADR" in text
    # Subtasks
    assert "Subtask" in text or "Child Task" in text
    assert "TASK-0002" in text


@pytest.mark.asyncio
async def test_get_task_details_execution_section_only(task_env):
    """sections=['execution'] should show execution fields only, not planning."""
    handler, db, pid = task_env
    await _create_task(
        handler,
        pid,
        title="Exec Task",
        scope_boundaries="Some scope",
    )
    await handler.handle_tool_call(
        "update_task_status",
        {
            "task_id": "TASK-0001",
            "new_status": "Approved",
            "execution_notes": "Doing work",
        },
    )

    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001", "sections": ["execution"]}
    )
    text = result[0].text
    assert "Execution" in text
    assert "Doing work" in text
    # Planning section should NOT be present
    assert "## Planning" not in text


@pytest.mark.asyncio
async def test_get_task_details_subtasks_section(task_env):
    """sections=['subtasks'] should show child tasks and parent info."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Parent Task")
    await _create_task(handler, pid, title="Child A", parent_task_id="TASK-0001")
    await _create_task(handler, pid, title="Child B", parent_task_id="TASK-0001")

    # Check parent's subtasks
    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001", "sections": ["subtasks"]}
    )
    text = result[0].text
    assert "Child A" in text
    assert "Child B" in text
    assert "TASK-0002" in text
    assert "TASK-0003" in text

    # Check child shows parent
    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0002", "sections": ["subtasks"]}
    )
    text = result[0].text
    assert "Parent Task" in text or "TASK-0001" in text


@pytest.mark.asyncio
async def test_get_task_details_with_relationships(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Linked Task")
    # Create a requirement and link it
    await _create_requirement(db, pid, "REQ-0001")
    await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)

    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001"}
    )
    text = result[0].text
    assert "REQ-0001" in text


@pytest.mark.asyncio
async def test_get_task_details_not_found(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-9999"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


@pytest.mark.asyncio
async def test_get_task_details_reverse_requirement_link(task_env):
    """Relationships where requirement is the source should also appear."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Context Task")
    await _create_requirement(db, pid, "REQ-0001", title="Reverse Linked Req")
    await _link(db, "requirement", "REQ-0001", "task", "TASK-0001", "implements", pid)

    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001", "sections": ["requirements"]}
    )
    text = result[0].text
    assert "REQ-0001" in text
    assert "Reverse Linked Req" in text


@pytest.mark.asyncio
async def test_get_task_details_excludes_archived_entities(task_env):
    """Archived requirements/ADRs should not appear in sections."""
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Context Task")
    await _create_requirement(db, pid, "REQ-0001", title="Active Req")
    await _create_requirement(db, pid, "REQ-0002", title="Archived Req")
    await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)
    await _link(db, "task", "TASK-0001", "requirement", "REQ-0002", "implements", pid)
    # Archive REQ-0002
    await db.execute_query(
        "UPDATE requirements SET is_archived = 1 WHERE id = 'REQ-0002'", []
    )

    result = await handler.handle_tool_call(
        "get_task_details", {"task_id": "TASK-0001", "sections": ["requirements"]}
    )
    text = result[0].text
    assert "REQ-0001" in text
    assert "REQ-0002" not in text


# =============================================================================
#  batch_create_tasks
# =============================================================================


@pytest.mark.asyncio
async def test_batch_create_tasks_valid(task_env):
    handler, db, pid = task_env
    tasks = [
        {"title": "Batch A", "priority": "P0"},
        {"title": "Batch B", "priority": "P1", "effort": "S"},
        {"title": "Batch C", "priority": "P2", "assignee": "Alice"},
    ]
    result = await handler.handle_tool_call(
        "batch_create_tasks", {"project_id": pid, "tasks": tasks}
    )
    text = result[0].text
    assert "SUCCESS" in text
    assert "TASK-0001" in text
    assert "TASK-0002" in text
    assert "TASK-0003" in text

    # Verify all 3 in DB
    rows = await db.get_records("tasks", where_clause="project_id = ?", where_params=[pid])
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_batch_create_tasks_rollback_on_invalid(task_env):
    """If one task in the batch fails validation, all are rolled back."""
    handler, db, pid = task_env
    tasks = [
        {"title": "Good", "priority": "P0"},
        {"title": "Bad - missing priority"},  # no priority field
        {"title": "Good too", "priority": "P1"},
    ]
    result = await handler.handle_tool_call(
        "batch_create_tasks", {"project_id": pid, "tasks": tasks}
    )
    text = result[0].text
    assert "ERROR" in text

    # No tasks should have been created
    rows = await db.get_records("tasks", where_clause="project_id = ?", where_params=[pid])
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_batch_create_tasks_empty(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call(
        "batch_create_tasks", {"project_id": pid, "tasks": []}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_batch_create_tasks_validates_project(task_env):
    handler, db, pid = task_env
    tasks = [{"title": "Good", "priority": "P0"}]
    result = await handler.handle_tool_call(
        "batch_create_tasks", {"project_id": "PROJ-9999", "tasks": tasks}
    )
    assert "ERROR" in result[0].text


# =============================================================================
#  clone_task
# =============================================================================


@pytest.mark.asyncio
async def test_clone_task_basic(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Original", priority="P1", effort="M")

    result = await handler.handle_tool_call(
        "clone_task", {"task_id": "TASK-0001"}
    )
    text = result[0].text
    assert "SUCCESS" in text
    assert "TASK-0002" in text

    # Cloned task should have same fields but new ID, Under Review
    row = await db.execute_query(
        "SELECT title, priority, effort, status FROM tasks WHERE id = 'TASK-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["title"] == "Original"
    assert row["priority"] == "P1"
    assert row["effort"] == "M"
    assert row["status"] == "Under Review"


@pytest.mark.asyncio
async def test_clone_task_copies_relationships(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Original")
    # Create a requirement and link it to the original
    await _create_requirement(db, pid, "REQ-0001")
    await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)

    await handler.handle_tool_call("clone_task", {"task_id": "TASK-0001"})

    # The cloned task should also have the relationship
    rels = await db.get_records(
        "relationships",
        where_clause="source_id = ? AND source_type = 'task'",
        where_params=["TASK-0002"],
    )
    assert len(rels) >= 1
    assert any(r["target_id"] == "REQ-0001" for r in rels)


@pytest.mark.asyncio
async def test_clone_task_resets_status(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Done Task")
    await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
    )
    await handler.handle_tool_call(
        "update_task_status", {"task_id": "TASK-0001", "new_status": "Implemented"}
    )

    await handler.handle_tool_call("clone_task", {"task_id": "TASK-0001"})

    row = await db.execute_query(
        "SELECT status, completed_at, execution_notes, deviation_from_plan "
        "FROM tasks WHERE id = 'TASK-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Under Review"
    assert row["completed_at"] is None
    assert row["execution_notes"] is None
    assert row["deviation_from_plan"] is None


@pytest.mark.asyncio
async def test_clone_task_with_children(task_env):
    handler, db, pid = task_env
    # Create parent + 2 children
    await _create_task(handler, pid, title="Parent")
    await _create_task(handler, pid, title="Child 1", parent_task_id="TASK-0001")
    await _create_task(handler, pid, title="Child 2", parent_task_id="TASK-0001")

    result = await handler.handle_tool_call(
        "clone_task", {"task_id": "TASK-0001", "include_children": True}
    )
    text = result[0].text
    assert "SUCCESS" in text

    # Should now have 6 tasks total (3 original + 3 clones)
    rows = await db.get_records("tasks", where_clause="project_id = ?", where_params=[pid])
    assert len(rows) == 6

    # Find the cloned parent (TASK-0004 since 3 originals used 1-3)
    cloned_parent = await db.execute_query(
        "SELECT id, title FROM tasks WHERE id = 'TASK-0004'",
        fetch_one=True,
        row_factory=True,
    )
    assert cloned_parent is not None
    assert cloned_parent["title"] == "Parent"

    # Find cloned children that reference the cloned parent
    cloned_children = await db.get_records(
        "tasks",
        where_clause="parent_task_id = ?",
        where_params=["TASK-0004"],
    )
    assert len(cloned_children) == 2


@pytest.mark.asyncio
async def test_clone_task_without_children(task_env):
    handler, db, pid = task_env
    await _create_task(handler, pid, title="Parent")
    await _create_task(handler, pid, title="Child", parent_task_id="TASK-0001")

    result = await handler.handle_tool_call(
        "clone_task", {"task_id": "TASK-0001", "include_children": False}
    )
    text = result[0].text
    assert "SUCCESS" in text

    # Should have 3 tasks: original parent, original child, cloned parent
    rows = await db.get_records("tasks", where_clause="project_id = ?", where_params=[pid])
    assert len(rows) == 3

    # Cloned parent should have no children
    cloned_children = await db.get_records(
        "tasks",
        where_clause="parent_task_id = 'TASK-0003'",
        where_params=[],
    )
    assert len(cloned_children) == 0


@pytest.mark.asyncio
async def test_clone_task_target_project(task_env):
    handler, db, pid = task_env
    # Create second project
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0002", "Other Project"],
    )
    await _create_task(handler, pid, title="Cross-project")

    result = await handler.handle_tool_call(
        "clone_task", {"task_id": "TASK-0001", "target_project_id": "PROJ-0002"}
    )
    assert "SUCCESS" in result[0].text

    row = await db.execute_query(
        "SELECT project_id FROM tasks WHERE id = 'TASK-0002'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["project_id"] == "PROJ-0002"


@pytest.mark.asyncio
async def test_clone_task_not_found(task_env):
    handler, db, pid = task_env
    result = await handler.handle_tool_call("clone_task", {"task_id": "TASK-9999"})
    assert "ERROR" in result[0].text


# =============================================================================
#  Task approval gating (TestTaskApprovalGating)
# =============================================================================


class TestTaskApprovalGating:
    """Integration tests for task approval gating.

    Verifies end-to-end that tasks can only be approved when their linked
    requirement(s) are in exactly 'Approved' status.
    """

    @pytest.mark.asyncio
    async def test_rejected_when_requirement_under_review(self, task_env):
        """Task linked to 'Under Review' requirement -> approval rejected with req ID."""
        handler, db, pid = task_env
        await _create_task(handler, pid, title="Gated Task")
        await _create_requirement(db, pid, "REQ-0001", title="Pending Req")
        # _create_requirement already inserts as 'Under Review'
        await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)

        result = await handler.handle_tool_call(
            "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
        )
        assert "ERROR" in result[0].text
        assert "REQ-0001" in result[0].text

    @pytest.mark.asyncio
    async def test_allowed_when_requirement_approved(self, task_env):
        """Task linked to 'Approved' requirement -> approval succeeds."""
        handler, db, pid = task_env
        await _create_task(handler, pid, title="Gated Task")
        await _create_requirement(db, pid, "REQ-0001", title="Approved Req")
        await db.execute_query(
            "UPDATE requirements SET status = 'Approved' WHERE id = 'REQ-0001'", []
        )
        await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)

        result = await handler.handle_tool_call(
            "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
        )
        assert "SUCCESS" in result[0].text

    @pytest.mark.asyncio
    async def test_allowed_when_no_requirements(self, task_env):
        """Task with no linked requirements -> approval succeeds (ungated)."""
        handler, db, pid = task_env
        await _create_task(handler, pid, title="Ungated Task")

        result = await handler.handle_tool_call(
            "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
        )
        assert "SUCCESS" in result[0].text

    @pytest.mark.asyncio
    async def test_rejected_when_requirement_partially_implemented(self, task_env):
        """Task linked to 'Partially Implemented' requirement -> approval rejected (must be exactly 'Approved')."""
        handler, db, pid = task_env
        await _create_task(handler, pid, title="Gated Task")
        await _create_requirement(db, pid, "REQ-0001", title="Partial Req")
        await db.execute_query(
            "UPDATE requirements SET status = 'Partially Implemented' WHERE id = 'REQ-0001'", []
        )
        await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)

        result = await handler.handle_tool_call(
            "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
        )
        assert "ERROR" in result[0].text
        assert "REQ-0001" in result[0].text
        assert "Partially Implemented" in result[0].text

    @pytest.mark.asyncio
    async def test_rejected_when_mixed_requirements(self, task_env):
        """Task linked to 2 requirements (1 Approved, 1 Under Review) -> rejected listing the non-approved req."""
        handler, db, pid = task_env
        await _create_task(handler, pid, title="Multi-Req Task")
        # First requirement: Approved
        await _create_requirement(db, pid, "REQ-0001", title="Approved Req")
        await db.execute_query(
            "UPDATE requirements SET status = 'Approved' WHERE id = 'REQ-0001'", []
        )
        # Second requirement: Under Review (not approved)
        await _create_requirement(db, pid, "REQ-0002", title="Pending Req")
        # Link both to the task
        await _link(db, "task", "TASK-0001", "requirement", "REQ-0001", "implements", pid)
        await _link(db, "task", "TASK-0001", "requirement", "REQ-0002", "implements", pid)

        result = await handler.handle_tool_call(
            "update_task_status", {"task_id": "TASK-0001", "new_status": "Approved"}
        )
        assert "ERROR" in result[0].text
        # The non-approved requirement should be listed in the error
        assert "REQ-0002" in result[0].text
        # The approved requirement should NOT be listed as a blocker
        assert "REQ-0001" not in result[0].text


# =============================================================================
#  Handle unknown tool
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_tool(task_env):
    handler, _, _ = task_env
    result = await handler.handle_tool_call("nonexistent_tool", {})
    assert "ERROR" in result[0].text or "Unknown" in result[0].text


# -- Agent Nudge Tests -------------------------------------------------------


def _extract_task_id(result):
    """Extract TASK-XXXX ID from handler response text."""
    import re
    text = result[0].text
    match = re.search(r"TASK-\d{4}", text)
    assert match, f"Could not extract TASK ID from: {text}"
    return match.group()


@pytest.mark.asyncio
async def test_nudge_create_task_reminds_to_link(task_env):
    """create_task response should remind agent to link requirements and set dependencies."""
    handler, _, project_id = task_env
    result = await _create_task(handler, project_id, "Nudge Test")
    text = result[0].text
    assert "create_relationship" in text
    assert "implements" in text
    assert "depends" in text


@pytest.mark.asyncio
async def test_nudge_implemented_reminds_to_validate(task_env):
    """When task moves to Implemented, response should remind to validate."""
    handler, _, project_id = task_env
    result = await _create_task(handler, project_id, "Validate Test")
    task_id = _extract_task_id(result)

    await handler.handle_tool_call("update_task_status", {"task_id": task_id, "new_status": "Approved"})
    result = await handler.handle_tool_call("update_task_status", {
        "task_id": task_id, "new_status": "Implemented",
        "execution_notes": "Done",
    })
    text = result[0].text
    assert "Validated" in text
    assert "verification" in text.lower() or "update_task_status" in text


@pytest.mark.asyncio
async def test_nudge_implemented_reports_auto_progression(task_env):
    """When task moves to Implemented, linked requirement should auto-progress and be reported."""
    handler, db, project_id = task_env
    await _create_requirement(db, project_id, "REQ-0001", "Auto-progress Test")
    await db.update_record("requirements", {"status": "Approved"}, "id = ?", ["REQ-0001"])

    result = await _create_task(handler, project_id, "AP Task")
    task_id = _extract_task_id(result)
    await _link(db, "task", task_id, "requirement", "REQ-0001", "implements", project_id)

    await handler.handle_tool_call("update_task_status", {"task_id": task_id, "new_status": "Approved"})
    result = await handler.handle_tool_call("update_task_status", {
        "task_id": task_id, "new_status": "Implemented",
    })
    text = result[0].text
    assert "REQ-0001" in text
    assert "auto-progressed" in text
    # With only one task, trigger goes Approved -> Partially Implemented -> Implemented
    # in a single trigger execution, so the final reported state is Implemented
    assert "Implemented" in text


@pytest.mark.asyncio
async def test_nudge_validated_reports_auto_progression(task_env):
    """When task moves to Validated, linked requirement auto-progression should be reported."""
    handler, db, project_id = task_env
    await _create_requirement(db, project_id, "REQ-0002", "Validate AP Test")
    await db.update_record("requirements", {"status": "Approved"}, "id = ?", ["REQ-0002"])

    result = await _create_task(handler, project_id, "VP Task")
    task_id = _extract_task_id(result)
    await _link(db, "task", task_id, "requirement", "REQ-0002", "implements", project_id)

    await handler.handle_tool_call("update_task_status", {"task_id": task_id, "new_status": "Approved"})
    await handler.handle_tool_call("update_task_status", {"task_id": task_id, "new_status": "Implemented"})
    result = await handler.handle_tool_call("update_task_status", {
        "task_id": task_id, "new_status": "Validated",
    })
    text = result[0].text
    assert "REQ-0002" in text
    assert "auto-progressed" in text


@pytest.mark.asyncio
async def test_nudge_validated_full_requirement_completion(task_env):
    """When the last task validates, requirement should reach Validated and include review hint."""
    handler, db, project_id = task_env
    await _create_requirement(db, project_id, "REQ-0003", "Full Completion Test")
    await db.update_record("requirements", {"status": "Approved"}, "id = ?", ["REQ-0003"])

    result = await _create_task(handler, project_id, "FC Task")
    task_id = _extract_task_id(result)
    await _link(db, "task", task_id, "requirement", "REQ-0003", "implements", project_id)

    await handler.handle_tool_call("update_task_status", {"task_id": task_id, "new_status": "Approved"})
    await handler.handle_tool_call("update_task_status", {"task_id": task_id, "new_status": "Implemented"})
    result = await handler.handle_tool_call("update_task_status", {
        "task_id": task_id, "new_status": "Validated",
    })
    text = result[0].text
    assert "Validated" in text
    assert "architectural consistency" in text.lower() or "code quality" in text.lower()


@pytest.mark.asyncio
async def test_nudge_approved_warns_missing_verification(task_env):
    """Approving a task without verification_commands should show a warning."""
    handler, _, project_id = task_env
    result = await _create_task(handler, project_id, "No VC Task",
                                acceptance_criteria=["AC1"])
    task_id = _extract_task_id(result)

    result = await handler.handle_tool_call("update_task_status", {
        "task_id": task_id, "new_status": "Approved",
    })
    text = result[0].text
    assert "verification_commands" in text
    assert "Warning" in text


@pytest.mark.asyncio
async def test_nudge_approved_warns_missing_acceptance_criteria(task_env):
    """Approving a task without acceptance_criteria should show a warning."""
    handler, _, project_id = task_env
    result = await _create_task(handler, project_id, "No AC Task",
                                verification_commands=["pytest tests/"])
    task_id = _extract_task_id(result)

    result = await handler.handle_tool_call("update_task_status", {
        "task_id": task_id, "new_status": "Approved",
    })
    text = result[0].text
    assert "acceptance_criteria" in text
    assert "Warning" in text


@pytest.mark.asyncio
async def test_nudge_approved_no_warning_when_fields_present(task_env):
    """Approving a task with both fields present should NOT show warnings."""
    handler, _, project_id = task_env
    result = await _create_task(handler, project_id, "Complete Task",
                                acceptance_criteria=["AC1"],
                                verification_commands=["pytest tests/"])
    task_id = _extract_task_id(result)

    result = await handler.handle_tool_call("update_task_status", {
        "task_id": task_id, "new_status": "Approved",
    })
    text = result[0].text
    assert "Warning" not in text


@pytest.mark.asyncio
async def test_nudge_batch_create_reports_missing_fields(task_env):
    """batch_create_tasks should report count of tasks missing AC or VC."""
    handler, _, project_id = task_env
    result = await handler.handle_tool_call("batch_create_tasks", {
        "project_id": project_id,
        "tasks": [
            {"title": "Has both", "priority": "P1",
             "acceptance_criteria": ["AC"], "verification_commands": ["cmd"]},
            {"title": "Missing AC", "priority": "P1",
             "verification_commands": ["cmd"]},
            {"title": "Missing VC", "priority": "P1",
             "acceptance_criteria": ["AC"]},
            {"title": "Missing both", "priority": "P1"},
        ],
    })
    text = result[0].text
    assert "2 task(s) missing acceptance_criteria" in text
    assert "2 task(s) missing verification_commands" in text


@pytest.mark.asyncio
async def test_nudge_batch_create_no_warning_when_complete(task_env):
    """batch_create_tasks with all fields present should NOT show notes."""
    handler, _, project_id = task_env
    result = await handler.handle_tool_call("batch_create_tasks", {
        "project_id": project_id,
        "tasks": [
            {"title": "Complete 1", "priority": "P1",
             "acceptance_criteria": ["AC"], "verification_commands": ["cmd"]},
            {"title": "Complete 2", "priority": "P1",
             "acceptance_criteria": ["AC"], "verification_commands": ["cmd"]},
        ],
    })
    text = result[0].text
    assert "Note:" not in text
