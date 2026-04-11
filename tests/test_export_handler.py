"""Tests for ExportHandler v2 (DB-09).

Validates both MCP tools with project scoping:
  export_project_documentation, create_architectural_diagrams

All queries must be scoped to project_id, use the relationships table
(not legacy join tables), and exclude archived entities.
"""

import json
import tempfile
from pathlib import Path

import pytest

from lifecycle_mcp.handlers.export_handler import ExportHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def setup(v2_db_manager):
    """Set up ExportHandler + two projects with entities.

    Returns (handler, db).

    Project layout:
      PROJ-0001 "ProjectOne"
        - REQ-0001  "Req One"     (FUNC, P1)
        - TASK-0001 "Task One"    (P1)
        - ADR-0001  "ADR One"     (context=ctx, decision=dec)
        - relationship: REQ-0001 --implements--> TASK-0001
      PROJ-0002 "ProjectTwo"
        - REQ-0002  "Req Two"     (FUNC, P1)
    """
    handler = ExportHandler(v2_db_manager)
    db = v2_db_manager

    # -- Projects --
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0001", "ProjectOne"]
    )
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0002", "ProjectTwo"]
    )

    # -- Project 1 entities --
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, priority, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["REQ-0001", "PROJ-0001", "FUNC", "Req One", "P1", "Under Review"],
    )
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, priority, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ["TASK-0001", "PROJ-0001", "Task One", "P1", "Under Review"],
    )
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, context, decision, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["ADR-0001", "PROJ-0001", "ADR One", "ctx", "dec", "Draft"],
    )
    # Relationship: requirement implements task
    await db.execute_query(
        "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, "
        "relationship_type, project_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["rel-1", "requirement", "REQ-0001", "task", "TASK-0001", "implements", "PROJ-0001"],
    )

    # -- Project 2 entity --
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, priority, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["REQ-0002", "PROJ-0002", "FUNC", "Req Two", "P1", "Under Review"],
    )

    return handler, db


# =============================================================================
#  Tool definitions
# =============================================================================


@pytest.mark.asyncio
async def test_tool_definitions(setup):
    """Both tools must be defined with required output parameters."""
    handler, _ = setup
    tools = handler.get_tool_definitions()
    assert len(tools) == 2

    names = {t["name"] for t in tools}
    assert names == {"export_project_documentation", "create_architectural_diagrams"}

    # Both tools require project_id
    for tool in tools:
        schema = tool["inputSchema"]
        assert "project_id" in schema["properties"]
        assert "project_id" in schema.get("required", [])

    # export_project_documentation requires output_directory
    export_tool = next(t for t in tools if t["name"] == "export_project_documentation")
    assert "output_directory" in export_tool["inputSchema"]["required"]

    # create_architectural_diagrams requires output_path
    diagram_tool = next(t for t in tools if t["name"] == "create_architectural_diagrams")
    assert "output_path" in diagram_tool["inputSchema"]["required"]

    # interactive parameter must NOT be present on create_architectural_diagrams
    assert "interactive" not in diagram_tool["inputSchema"]["properties"]


# =============================================================================
#  Required parameter enforcement
# =============================================================================


@pytest.mark.asyncio
async def test_export_requires_output_directory(setup):
    """export_project_documentation must fail when output_directory is missing."""
    handler, _ = setup
    result = await handler.handle_tool_call(
        "export_project_documentation",
        {"project_id": "PROJ-0001"},
    )
    assert len(result) == 1
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_diagrams_requires_output_path(setup):
    """create_architectural_diagrams must fail when output_path is missing."""
    handler, _ = setup
    result = await handler.handle_tool_call(
        "create_architectural_diagrams",
        {"project_id": "PROJ-0001", "diagram_type": "requirements"},
    )
    assert len(result) == 1
    assert "ERROR" in result[0].text


# =============================================================================
#  export_project_documentation
# =============================================================================


@pytest.mark.asyncio
async def test_export_scoped_to_project(setup):
    """Export for PROJ-0001 must contain only ProjectOne entities."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp},
        )
        assert len(result) == 1
        assert "SUCCESS" in result[0].text

        # Read all exported markdown
        content = ""
        for md in Path(tmp).glob("*.md"):
            content += md.read_text()

        assert "Req One" in content
        assert "Task One" in content
        assert "ADR One" in content
        # Must NOT include project 2 data
        assert "Req Two" not in content


@pytest.mark.asyncio
async def test_export_project_name_from_db(setup):
    """Exported files must use project name from DB, not cwd."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp},
        )

        content = ""
        for md in Path(tmp).glob("*.md"):
            content += md.read_text()

        # The project name "ProjectOne" should appear in the content
        assert "ProjectOne" in content


@pytest.mark.asyncio
async def test_export_relationships_via_relationships_table(setup):
    """Linked entities should be resolved through the relationships table."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp},
        )

        # Read the tasks file to check linked requirements
        content = ""
        for md in Path(tmp).glob("*tasks*"):
            content += md.read_text()

        # The task export should reference REQ-0001 as a linked requirement
        assert "REQ-0001" in content


@pytest.mark.asyncio
async def test_export_creates_output_files(setup):
    """Export must write .md files in the output directory."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp},
        )

        md_files = list(Path(tmp).glob("*.md"))
        # Should have requirements, tasks, and architecture files
        assert len(md_files) == 3


@pytest.mark.asyncio
async def test_export_nonexistent_project(setup):
    """Exporting a nonexistent project must return an error."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-9999", "output_directory": tmp},
        )
        assert len(result) == 1
        assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_export_empty_project(setup):
    """Exporting a project with no entities should succeed gracefully."""
    handler, db = setup
    # Create a project with no entities
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0003", "EmptyProject"]
    )

    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0003", "output_directory": tmp},
        )
        assert len(result) == 1
        # Should return INFO (no data) or SUCCESS with 0 files
        assert "INFO" in result[0].text or "SUCCESS" in result[0].text


@pytest.mark.asyncio
async def test_export_excludes_archived_entities(setup):
    """Archived entities must not appear in the export."""
    handler, db = setup
    # Archive REQ-0001
    await db.execute_query(
        "UPDATE requirements SET is_archived = 1 WHERE id = ?", ["REQ-0001"]
    )
    # Archive TASK-0001
    await db.execute_query(
        "UPDATE tasks SET is_archived = 1 WHERE id = ?", ["TASK-0001"]
    )
    # Archive ADR-0001
    await db.execute_query(
        "UPDATE architecture SET is_archived = 1 WHERE id = ?", ["ADR-0001"]
    )

    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp},
        )
        assert len(result) == 1

        # No files should be exported since all entities are archived
        content = ""
        for md in Path(tmp).glob("*.md"):
            content += md.read_text()

        assert "Req One" not in content
        assert "Task One" not in content
        assert "ADR One" not in content


@pytest.mark.asyncio
async def test_export_selective_sections(setup):
    """Selective export must respect include_* flags."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {
                "project_id": "PROJ-0001",
                "output_directory": tmp,
                "include_requirements": True,
                "include_tasks": False,
                "include_architecture": False,
            },
        )

        md_files = list(Path(tmp).glob("*.md"))
        # Only requirements file
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "Req One" in content


# =============================================================================
#  create_architectural_diagrams
# =============================================================================


@pytest.mark.asyncio
async def test_diagram_scoped_to_project(setup):
    """Diagrams for PROJ-0001 must contain only that project's entities."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "requirements", "output_path": tmp},
        )
        assert len(result) == 1
        assert "SUCCESS" in result[0].text

        # Read the diagram file
        files = list(Path(tmp).glob("*"))
        assert len(files) > 0
        content = files[0].read_text()

        assert "REQ_0001" in content or "REQ-0001" in content
        assert "REQ_0002" not in content and "REQ-0002" not in content


@pytest.mark.asyncio
async def test_diagram_type_tasks(setup):
    """Task diagram for PROJ-0001 must include TASK-0001."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "tasks", "output_path": tmp},
        )
        assert len(result) == 1
        assert "SUCCESS" in result[0].text

        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        assert "TASK_0001" in content or "TASK-0001" in content


@pytest.mark.asyncio
async def test_diagram_type_full_project(setup):
    """Full project diagram must include all entity types."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {
                "project_id": "PROJ-0001",
                "diagram_type": "full_project",
                "output_path": tmp,
                "include_relationships": True,
            },
        )
        assert len(result) == 1
        assert "SUCCESS" in result[0].text

        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        # Should contain all entity types
        assert "REQ" in content
        assert "TASK" in content
        assert "ADR" in content


@pytest.mark.asyncio
async def test_diagram_nonexistent_project(setup):
    """Diagram for nonexistent project must return error."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-9999", "diagram_type": "requirements", "output_path": tmp},
        )
        assert len(result) == 1
        assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_diagram_invalid_type(setup):
    """Invalid diagram_type must return error."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "invalid_type", "output_path": tmp},
        )
        assert len(result) == 1
        assert "ERROR" in result[0].text
        assert "Invalid diagram type" in result[0].text


@pytest.mark.asyncio
async def test_diagram_excludes_archived(setup):
    """Archived entities must not appear in diagrams."""
    handler, db = setup
    # Archive REQ-0001
    await db.execute_query(
        "UPDATE requirements SET is_archived = 1 WHERE id = ?", ["REQ-0001"]
    )

    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "requirements", "output_path": tmp},
        )
        # Either INFO (no data) or SUCCESS with no REQ-0001
        text = result[0].text
        if "SUCCESS" in text:
            files = list(Path(tmp).glob("*"))
            content = files[0].read_text()
            assert "REQ_0001" not in content and "REQ-0001" not in content
        else:
            assert "INFO" in text


@pytest.mark.asyncio
async def test_diagram_dependencies(setup):
    """Dependencies diagram must use relationships table."""
    handler, db = setup
    # Add a second task and a depends relationship
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, priority, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ["TASK-0002", "PROJ-0001", "Task Two", "P1", "Under Review"],
    )
    await db.execute_query(
        "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, "
        "relationship_type, project_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["rel-dep", "task", "TASK-0002", "task", "TASK-0001", "depends", "PROJ-0001"],
    )

    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "dependencies", "output_path": tmp},
        )
        assert len(result) == 1
        assert "SUCCESS" in result[0].text

        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        # Both tasks should appear in the dependency diagram
        assert "TASK_0001" in content or "TASK-0001" in content
        assert "TASK_0002" in content or "TASK-0002" in content


@pytest.mark.asyncio
async def test_handle_tool_call_unknown_tool(setup):
    """Unknown tool name must return error."""
    handler, _ = setup
    result = await handler.handle_tool_call("unknown_tool", {})
    assert len(result) == 1
    assert "Unknown tool" in result[0].text


@pytest.mark.asyncio
async def test_diagram_markdown_format(setup):
    """Output format markdown_with_mermaid wraps in code fences."""
    handler, _ = setup
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {
                "project_id": "PROJ-0001",
                "diagram_type": "requirements",
                "output_path": tmp,
                "output_format": "markdown_with_mermaid",
            },
        )
        assert "SUCCESS" in result[0].text
        files = list(Path(tmp).glob("*.md"))
        assert len(files) > 0
        content = files[0].read_text()
        assert "```mermaid" in content


# =============================================================================
#  Coverage improvement tests
# =============================================================================


@pytest.mark.asyncio
async def test_export_missing_project_id(setup):
    """export_project_documentation must fail when project_id is missing."""
    handler, _ = setup
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "export_project_documentation",
            {"output_directory": tmp},
        )
        assert "ERROR" in result[0].text
        assert "project_id" in result[0].text


@pytest.mark.asyncio
async def test_diagrams_missing_project_id(setup):
    """create_architectural_diagrams must fail when project_id is missing."""
    handler, _ = setup
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"output_path": tmp, "diagram_type": "requirements"},
        )
        assert "ERROR" in result[0].text
        assert "project_id" in result[0].text


@pytest.mark.asyncio
async def test_export_requirements_with_rich_metadata(setup):
    """Export renders author, business_value, functional_requirements, acceptance_criteria."""
    handler, db = setup
    import json as _json
    # Create a requirement with all rich metadata
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, priority, status, "
        "author, business_value, functional_requirements, acceptance_criteria) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "REQ-0099", "PROJ-0001", "FUNC", "Rich Req", "P0", "Under Review",
            "Alice", "Critical business value",
            _json.dumps(["FR-1: Must do X", "FR-2: Must do Y"]),
            _json.dumps(["AC-1: Verify X", "AC-2: Verify Y"]),
        ],
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp, "include_tasks": False, "include_architecture": False},
        )

        content = ""
        for md in Path(tmp).glob("*.md"):
            content += md.read_text()

        assert "Alice" in content
        assert "Critical business value" in content
        assert "FR-1" in content
        assert "AC-1" in content


@pytest.mark.asyncio
async def test_export_tasks_with_user_story_and_criteria(setup):
    """Export renders user_story and acceptance_criteria on tasks."""
    handler, db = setup
    import json as _json
    await db.execute_query(
        "UPDATE tasks SET user_story = ?, acceptance_criteria = ? WHERE id = ?",
        [
            "As a user, I want to login",
            _json.dumps(["AC: can login with email"]),
            "TASK-0001",
        ],
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp, "include_requirements": False, "include_architecture": False},
        )

        content = ""
        for md in Path(tmp).glob("*.md"):
            content += md.read_text()

        assert "As a user, I want to login" in content
        assert "AC: can login with email" in content


@pytest.mark.asyncio
async def test_export_architecture_with_rich_metadata(setup):
    """Export renders authors, decision_drivers, considered_options, consequences on ADRs."""
    handler, db = setup
    import json as _json
    await db.execute_query(
        "UPDATE architecture SET authors = ?, decision_drivers = ?, "
        "considered_options = ?, consequences = ? WHERE id = ?",
        [
            _json.dumps(["Alice", "Bob"]),
            _json.dumps(["Performance", "Scalability"]),
            _json.dumps(["Option A", "Option B"]),
            _json.dumps({"positive": "Fast", "negative": "Complex"}),
            "ADR-0001",
        ],
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp, "include_requirements": False, "include_tasks": False},
        )

        content = ""
        for md in Path(tmp).glob("*.md"):
            content += md.read_text()

        assert "Alice" in content
        assert "Bob" in content
        assert "Performance" in content
        assert "Option A" in content
        assert "Fast" in content
        assert "Complex" in content


@pytest.mark.asyncio
async def test_export_architecture_with_linked_requirements(setup):
    """Export renders linked requirements on ADRs."""
    handler, db = setup
    # Add a relationship: REQ-0001 -> ADR-0001
    await db.execute_query(
        "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, "
        "relationship_type, project_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["rel-adr", "requirement", "REQ-0001", "architecture", "ADR-0001", "addresses", "PROJ-0001"],
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        await handler.handle_tool_call(
            "export_project_documentation",
            {"project_id": "PROJ-0001", "output_directory": tmp, "include_requirements": False, "include_tasks": False},
        )

        content = ""
        for md in Path(tmp).glob("*.md"):
            content += md.read_text()

        assert "REQ-0001" in content
        assert "Linked Requirements" in content


@pytest.mark.asyncio
async def test_diagram_type_architecture(setup):
    """Architecture diagram renders ADR nodes."""
    handler, _ = setup
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "architecture", "output_path": tmp},
        )
        assert "SUCCESS" in result[0].text
        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        assert "ADR_0001" in content or "ADR-0001" in content


@pytest.mark.asyncio
async def test_diagram_type_directory_structure(setup):
    """Directory structure diagram renders static content."""
    handler, _ = setup
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "directory_structure", "output_path": tmp},
        )
        assert "SUCCESS" in result[0].text
        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        assert "Project Root" in content


@pytest.mark.asyncio
async def test_diagram_dependencies_no_deps(setup):
    """Dependencies diagram with no task dependencies shows placeholder."""
    handler, _ = setup
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "dependencies", "output_path": tmp},
        )
        assert "SUCCESS" in result[0].text
        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        assert "No task dependencies found" in content


@pytest.mark.asyncio
async def test_diagram_tasks_with_parent_child(setup):
    """Task diagram renders parent-child arrows when parent_task_id is set."""
    handler, db = setup
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, priority, status, parent_task_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["TASK-0002", "PROJ-0001", "Child Task", "P1", "Under Review", "TASK-0001"],
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {"project_id": "PROJ-0001", "diagram_type": "tasks", "output_path": tmp},
        )
        assert "SUCCESS" in result[0].text
        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        # Parent-child arrow should be present
        assert "TASK_0001 --> TASK_0002" in content


@pytest.mark.asyncio
async def test_diagram_requirements_filtered_by_ids(setup):
    """Diagram with requirement_ids filter includes only specified requirements."""
    handler, db = setup
    # Add a second requirement
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, priority, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["REQ-0099", "PROJ-0001", "TECH", "Other Req", "P2", "Under Review"],
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await handler.handle_tool_call(
            "create_architectural_diagrams",
            {
                "project_id": "PROJ-0001",
                "diagram_type": "requirements",
                "output_path": tmp,
                "requirement_ids": ["REQ-0001"],
            },
        )
        assert "SUCCESS" in result[0].text
        files = list(Path(tmp).glob("*"))
        content = files[0].read_text()
        assert "REQ_0001" in content
        assert "REQ_0099" not in content


@pytest.mark.asyncio
async def test_handle_tool_call_general_exception(setup):
    """handle_tool_call wraps unexpected exceptions in error response."""
    handler, _ = setup

    async def raise_error(**kwargs):
        raise RuntimeError("unexpected boom")

    handler._export_project_documentation = raise_error
    result = await handler.handle_tool_call("export_project_documentation", {"project_id": "PROJ-0001"})
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_validate_output_path_empty(setup):
    """Empty output path is rejected."""
    handler, _ = setup
    assert handler._validate_output_path("") is False


@pytest.mark.asyncio
async def test_ensure_output_directory_permission_error(setup, monkeypatch):
    """Permission errors in makedirs return False."""
    handler, _ = setup
    monkeypatch.setattr("os.makedirs", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("nope")))
    assert handler._ensure_output_directory("/some/path") is False
