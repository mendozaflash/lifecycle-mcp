"""Tests for ValidationHandler (DB-08).

Validates:
  - validate_project_plan: orphan detection, cycle detection, missing fields,
    blocked tasks, invalid status combos, markdown file output
  - get_valid_status_transitions: correct transitions for all entity types,
    edge cases for unknown types and statuses
"""

import json
import os

import pytest

from lifecycle_mcp.handlers.validation_handler import ValidationHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def setup(v2_db_manager):
    """Set up a ValidationHandler + test project. Returns (handler, db, project_id)."""
    handler = ValidationHandler(v2_db_manager)

    await v2_db_manager.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0001", "Test Project"],
    )

    return handler, v2_db_manager, "PROJ-0001"


# -- Helpers -----------------------------------------------------------------


def _text(result):
    """Extract text from MCP response."""
    return result[0].text


def _parse_json(result):
    """Parse JSON from MCP response text."""
    return json.loads(result[0].text)


async def _insert_requirement(db, req_id, project_id, **kwargs):
    """Insert a test requirement."""
    defaults = {
        "type": "FUNC",
        "title": f"Requirement {req_id}",
        "priority": "P1",
        "status": "Draft",
        "acceptance_criteria": json.dumps(["AC-1"]),
    }
    defaults.update(kwargs)
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, priority, status, acceptance_criteria) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [req_id, project_id, defaults["type"], defaults["title"],
         defaults["priority"], defaults["status"], defaults["acceptance_criteria"]],
    )


async def _insert_task(db, task_id, project_id, **kwargs):
    """Insert a test task."""
    defaults = {
        "title": f"Task {task_id}",
        "priority": "P1",
        "status": "Not Started",
        "scope_boundaries": "some scope",
        "technical_outline": "some outline",
    }
    defaults.update(kwargs)
    await db.execute_query(
        "INSERT INTO tasks (id, project_id, title, priority, status, scope_boundaries, technical_outline) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [task_id, project_id, defaults["title"], defaults["priority"],
         defaults["status"], defaults["scope_boundaries"], defaults["technical_outline"]],
    )


async def _insert_adr(db, adr_id, project_id, **kwargs):
    """Insert a test architecture decision."""
    defaults = {
        "title": f"ADR {adr_id}",
        "context": "test context",
        "decision": "test decision",
        "status": "Draft",
        "superseded_by": None,
    }
    defaults.update(kwargs)
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, context, decision, status, superseded_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [adr_id, project_id, defaults["title"], defaults["context"],
         defaults["decision"], defaults["status"], defaults["superseded_by"]],
    )


async def _insert_relationship(db, source_id, target_id, rel_type, project_id):
    """Insert a relationship record."""
    rel_id = f"rel-{source_id}-{target_id}-{rel_type}"
    await db.execute_query(
        "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, "
        "relationship_type, project_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [rel_id, "task", source_id, "task", target_id, rel_type, project_id],
    )


# ===========================================================================
# validate_project_plan: Orphan Detection
# ===========================================================================


class TestOrphanDetection:
    """Test orphan detection in validate_project_plan."""

    @pytest.mark.asyncio
    async def test_orphan_requirement_no_tasks(self, setup):
        """Requirement with no linked tasks should produce a warning."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["warnings"] >= 1
        orphan_reqs = [d for d in data["details"] if d["check"] == "orphan_requirement"]
        assert len(orphan_reqs) == 1
        assert "REQ-0001" in orphan_reqs[0]["entity_id"]

    @pytest.mark.asyncio
    async def test_orphan_task_no_requirement(self, setup):
        """Task with no linked requirement should produce a warning."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["warnings"] >= 1
        orphan_tasks = [d for d in data["details"] if d["check"] == "orphan_task"]
        assert len(orphan_tasks) == 1
        assert "TASK-0001" in orphan_tasks[0]["entity_id"]

    @pytest.mark.asyncio
    async def test_orphan_adr_no_requirement(self, setup):
        """ADR with no linked requirement should produce a warning."""
        handler, db, pid = setup
        await _insert_adr(db, "ADR-0001", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["warnings"] >= 1
        orphan_adrs = [d for d in data["details"] if d["check"] == "unlinked_adr"]
        assert len(orphan_adrs) == 1
        assert "ADR-0001" in orphan_adrs[0]["entity_id"]

    @pytest.mark.asyncio
    async def test_linked_requirement_not_orphan(self, setup):
        """Requirement linked to a task via relationship should NOT be orphan."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid)
        await _insert_task(db, "TASK-0001", pid)
        await _insert_relationship(db, "TASK-0001", "REQ-0001", "implements", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        orphan_reqs = [d for d in data["details"] if d["check"] == "orphan_requirement"]
        assert len(orphan_reqs) == 0

    @pytest.mark.asyncio
    async def test_linked_task_not_orphan(self, setup):
        """Task linked to a requirement via relationship should NOT be orphan."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid)
        await _insert_task(db, "TASK-0001", pid)
        await _insert_relationship(db, "TASK-0001", "REQ-0001", "implements", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        orphan_tasks = [d for d in data["details"] if d["check"] == "orphan_task"]
        assert len(orphan_tasks) == 0


# ===========================================================================
# validate_project_plan: Cycle Detection
# ===========================================================================


class TestCycleDetection:
    """Test dependency cycle detection."""

    @pytest.mark.asyncio
    async def test_simple_cycle(self, setup):
        """A -> B -> A should be detected as a cycle."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid)
        await _insert_task(db, "TASK-0002", pid)
        await _insert_relationship(db, "TASK-0001", "TASK-0002", "depends", pid)
        await _insert_relationship(db, "TASK-0002", "TASK-0001", "depends", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["errors"] >= 1
        cycle_issues = [d for d in data["details"] if d["check"] == "dependency_cycle"]
        assert len(cycle_issues) >= 1

    @pytest.mark.asyncio
    async def test_complex_cycle(self, setup):
        """A -> B -> C -> A should be detected as a cycle."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid)
        await _insert_task(db, "TASK-0002", pid)
        await _insert_task(db, "TASK-0003", pid)
        await _insert_relationship(db, "TASK-0001", "TASK-0002", "depends", pid)
        await _insert_relationship(db, "TASK-0002", "TASK-0003", "depends", pid)
        await _insert_relationship(db, "TASK-0003", "TASK-0001", "depends", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["errors"] >= 1
        cycle_issues = [d for d in data["details"] if d["check"] == "dependency_cycle"]
        assert len(cycle_issues) >= 1

    @pytest.mark.asyncio
    async def test_self_reference(self, setup):
        """A -> A (self-dependency) should be detected as a cycle."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid)
        await _insert_relationship(db, "TASK-0001", "TASK-0001", "depends", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["errors"] >= 1
        cycle_issues = [d for d in data["details"] if d["check"] == "dependency_cycle"]
        assert len(cycle_issues) >= 1

    @pytest.mark.asyncio
    async def test_no_cycle_linear(self, setup):
        """A -> B -> C (linear) should NOT be a cycle."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid)
        await _insert_task(db, "TASK-0002", pid)
        await _insert_task(db, "TASK-0003", pid)
        await _insert_relationship(db, "TASK-0001", "TASK-0002", "depends", pid)
        await _insert_relationship(db, "TASK-0002", "TASK-0003", "depends", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        cycle_issues = [d for d in data["details"] if d["check"] == "dependency_cycle"]
        assert len(cycle_issues) == 0

    @pytest.mark.asyncio
    async def test_blocks_relationship_cycle(self, setup):
        """Cycles via 'blocks' relationships should also be detected."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid)
        await _insert_task(db, "TASK-0002", pid)
        await _insert_relationship(db, "TASK-0001", "TASK-0002", "blocks", pid)
        await _insert_relationship(db, "TASK-0002", "TASK-0001", "blocks", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["errors"] >= 1
        cycle_issues = [d for d in data["details"] if d["check"] == "dependency_cycle"]
        assert len(cycle_issues) >= 1


# ===========================================================================
# validate_project_plan: Missing Fields
# ===========================================================================


class TestMissingFields:
    """Test missing field detection."""

    @pytest.mark.asyncio
    async def test_missing_acceptance_criteria_null(self, setup):
        """Requirement with NULL acceptance_criteria should produce warning."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid, acceptance_criteria=None)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        missing = [d for d in data["details"] if d["check"] == "missing_acceptance_criteria"]
        assert len(missing) == 1
        assert "REQ-0001" in missing[0]["entity_id"]

    @pytest.mark.asyncio
    async def test_missing_acceptance_criteria_empty(self, setup):
        """Requirement with empty JSON array acceptance_criteria should produce warning."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid, acceptance_criteria="[]")

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        missing = [d for d in data["details"] if d["check"] == "missing_acceptance_criteria"]
        assert len(missing) == 1

    @pytest.mark.asyncio
    async def test_has_acceptance_criteria(self, setup):
        """Requirement WITH acceptance_criteria should NOT produce warning."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid, acceptance_criteria=json.dumps(["AC-1"]))

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        missing = [d for d in data["details"] if d["check"] == "missing_acceptance_criteria"]
        assert len(missing) == 0

    @pytest.mark.asyncio
    async def test_missing_planning_fields(self, setup):
        """Task with both scope_boundaries AND technical_outline NULL should produce warning."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid, scope_boundaries=None, technical_outline=None)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        missing = [d for d in data["details"] if d["check"] == "missing_planning_fields"]
        assert len(missing) == 1
        assert "TASK-0001" in missing[0]["entity_id"]

    @pytest.mark.asyncio
    async def test_has_one_planning_field(self, setup):
        """Task with at least one planning field filled should NOT produce warning."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid, scope_boundaries="defined", technical_outline=None)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        missing = [d for d in data["details"] if d["check"] == "missing_planning_fields"]
        assert len(missing) == 0


# ===========================================================================
# validate_project_plan: Blocked Tasks & Invalid Status
# ===========================================================================


class TestBlockedAndInvalidStatus:
    """Test blocked tasks and invalid status combinations."""

    @pytest.mark.asyncio
    async def test_blocked_task_reported(self, setup):
        """Task with status=Blocked should be reported as info."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid, status="Blocked")

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        blocked = [d for d in data["details"] if d["check"] == "blocked_task"]
        assert len(blocked) == 1
        assert "TASK-0001" in blocked[0]["entity_id"]

    @pytest.mark.asyncio
    async def test_non_blocked_task_not_reported(self, setup):
        """Task with status!=Blocked should NOT be reported as blocked."""
        handler, db, pid = setup
        await _insert_task(db, "TASK-0001", pid, status="In Progress")

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        blocked = [d for d in data["details"] if d["check"] == "blocked_task"]
        assert len(blocked) == 0

    @pytest.mark.asyncio
    async def test_invalid_adr_superseded_but_not_deprecated(self, setup):
        """ADR with superseded_by set but status!='Deprecated' should produce error."""
        handler, db, pid = setup
        await _insert_adr(db, "ADR-0002", pid)
        await _insert_adr(db, "ADR-0001", pid, superseded_by="ADR-0002", status="Approved")

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["errors"] >= 1
        invalid = [d for d in data["details"] if d["check"] == "invalid_status_combination"]
        assert len(invalid) == 1
        assert "ADR-0001" in invalid[0]["entity_id"]

    @pytest.mark.asyncio
    async def test_valid_adr_superseded_and_deprecated(self, setup):
        """ADR with superseded_by set AND status='Deprecated' should NOT produce error."""
        handler, db, pid = setup
        await _insert_adr(db, "ADR-0002", pid)
        await _insert_adr(db, "ADR-0001", pid, superseded_by="ADR-0002", status="Deprecated")

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        invalid = [d for d in data["details"] if d["check"] == "invalid_status_combination"]
        assert len(invalid) == 0


# ===========================================================================
# validate_project_plan: Clean Project
# ===========================================================================


class TestCleanProject:
    """Test a properly linked project has no errors."""

    @pytest.mark.asyncio
    async def test_clean_project_zero_errors(self, setup):
        """Fully linked project should have 0 errors and 0 warnings."""
        handler, db, pid = setup

        # Create a requirement with acceptance_criteria
        await _insert_requirement(db, "REQ-0001", pid, acceptance_criteria=json.dumps(["AC-1"]))

        # Create a task with planning fields
        await _insert_task(db, "TASK-0001", pid, scope_boundaries="scope", technical_outline="outline")

        # Create an ADR
        await _insert_adr(db, "ADR-0001", pid)

        # Link task to requirement
        await _insert_relationship(db, "TASK-0001", "REQ-0001", "implements", pid)

        # Link ADR to requirement
        await _insert_relationship(db, "ADR-0001", "REQ-0001", "addresses", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["errors"] == 0
        assert data["warnings"] == 0

    @pytest.mark.asyncio
    async def test_empty_project_no_errors(self, setup):
        """Empty project (no entities) should have 0 errors, 0 warnings."""
        handler, db, pid = setup

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert data["errors"] == 0
        assert data["warnings"] == 0


# ===========================================================================
# validate_project_plan: Output Files
# ===========================================================================


class TestOutputFiles:
    """Test markdown file generation."""

    @pytest.mark.asyncio
    async def test_writes_three_files(self, setup, tmp_path):
        """Should write REQUIREMENTS_STATUS.md, TASK_STATUS.md, ADR_STATUS.md."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid)
        await _insert_task(db, "TASK-0001", pid)
        await _insert_adr(db, "ADR-0001", pid)

        output_dir = str(tmp_path / "output")
        result = await handler.handle_tool_call(
            "validate_project_plan",
            {"project_id": pid, "output_directory": output_dir, "summary_only": False},
        )
        data = _parse_json(result)

        assert os.path.isfile(os.path.join(output_dir, "REQUIREMENTS_STATUS.md"))
        assert os.path.isfile(os.path.join(output_dir, "TASK_STATUS.md"))
        assert os.path.isfile(os.path.join(output_dir, "ADR_STATUS.md"))
        assert data["files_written"] == 3

    @pytest.mark.asyncio
    async def test_output_files_contain_entities(self, setup, tmp_path):
        """Output files should contain the entity data."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid)
        await _insert_task(db, "TASK-0001", pid)
        await _insert_adr(db, "ADR-0001", pid)

        output_dir = str(tmp_path / "output")
        await handler.handle_tool_call(
            "validate_project_plan",
            {"project_id": pid, "output_directory": output_dir, "summary_only": False},
        )

        with open(os.path.join(output_dir, "REQUIREMENTS_STATUS.md")) as f:
            content = f.read()
            assert "REQ-0001" in content

        with open(os.path.join(output_dir, "TASK_STATUS.md")) as f:
            content = f.read()
            assert "TASK-0001" in content

        with open(os.path.join(output_dir, "ADR_STATUS.md")) as f:
            content = f.read()
            assert "ADR-0001" in content

    @pytest.mark.asyncio
    async def test_no_output_without_directory(self, setup):
        """When output_directory is not provided, no files should be written."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        assert "files_written" not in data


# ===========================================================================
# validate_project_plan: Error Cases
# ===========================================================================


class TestValidateProjectPlanErrors:
    """Test error cases for validate_project_plan."""

    @pytest.mark.asyncio
    async def test_nonexistent_project(self, setup):
        """Nonexistent project_id should return error."""
        handler, db, pid = setup

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": "PROJ-9999"}
        )
        text = _text(result)
        assert "ERROR" in text or "not found" in text.lower()

    @pytest.mark.asyncio
    async def test_missing_project_id(self, setup):
        """Missing project_id param should return error."""
        handler, db, pid = setup

        result = await handler.handle_tool_call(
            "validate_project_plan", {}
        )
        text = _text(result)
        assert "ERROR" in text or "required" in text.lower() or "Missing" in text


# ===========================================================================
# validate_project_plan: summary_only parameter
# ===========================================================================


class TestSummaryOnly:
    """Test the summary_only parameter for validate_project_plan."""

    @pytest.mark.asyncio
    async def test_validate_summary_only_default(self, setup):
        """Default summary_only=true returns a compact summary line, not JSON."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid, acceptance_criteria=None)
        await _insert_task(db, "TASK-0001", pid)

        # Default call (no summary_only specified) should return a summary line
        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid}
        )
        text = _text(result)
        # Must contain "Errors:" / "Warnings:" / "Info:" counts
        assert "Errors:" in text
        assert "Warnings:" in text
        assert "Info:" in text
        # Must NOT be parseable as the full JSON details dict
        try:
            data = json.loads(text)
            # If it parses as JSON, it should NOT have the 'details' key
            assert "details" not in data
        except json.JSONDecodeError:
            pass  # expected -- summary is plain text

    @pytest.mark.asyncio
    async def test_validate_summary_only_false(self, setup):
        """summary_only=false returns the full JSON with details list."""
        handler, db, pid = setup
        await _insert_requirement(db, "REQ-0001", pid, acceptance_criteria=None)
        await _insert_task(db, "TASK-0001", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid, "summary_only": False}
        )
        data = _parse_json(result)
        # Must have full structure
        assert "errors" in data
        assert "warnings" in data
        assert "details" in data
        assert isinstance(data["details"], list)
        assert data["warnings"] >= 1  # orphan req + orphan task + missing AC

    @pytest.mark.asyncio
    async def test_validate_summary_only_with_errors_shows_first_error(self, setup):
        """When summary_only=true and errors > 0, first error message is included."""
        handler, db, pid = setup
        # Create a cycle to produce an error
        await _insert_task(db, "TASK-0001", pid)
        await _insert_task(db, "TASK-0002", pid)
        await _insert_relationship(db, "TASK-0001", "TASK-0002", "depends", pid)
        await _insert_relationship(db, "TASK-0002", "TASK-0001", "depends", pid)

        result = await handler.handle_tool_call(
            "validate_project_plan", {"project_id": pid}
        )
        text = _text(result)
        assert "Errors: " in text
        # First error message should be included
        assert "cycle" in text.lower() or "Dependency" in text


# ===========================================================================
# get_valid_status_transitions
# ===========================================================================


class TestGetValidStatusTransitions:
    """Test status transition lookup."""

    @pytest.mark.asyncio
    async def test_requirement_draft(self, setup):
        """Draft requirement should transition to Under Review or Deprecated."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "requirement", "current_status": "Draft"},
        )
        data = _parse_json(result)
        assert set(data["valid_transitions"]) == {"Under Review", "Deprecated"}

    @pytest.mark.asyncio
    async def test_requirement_deprecated_no_transitions(self, setup):
        """Deprecated requirement should have no transitions."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "requirement", "current_status": "Deprecated"},
        )
        data = _parse_json(result)
        assert data["valid_transitions"] == []

    @pytest.mark.asyncio
    async def test_task_in_progress(self, setup):
        """In Progress task should transition to Complete, Blocked, or Abandoned."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "task", "current_status": "In Progress"},
        )
        data = _parse_json(result)
        assert set(data["valid_transitions"]) == {"Complete", "Blocked", "Abandoned"}

    @pytest.mark.asyncio
    async def test_task_complete_no_transitions(self, setup):
        """Complete task should have no transitions."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "task", "current_status": "Complete"},
        )
        data = _parse_json(result)
        assert data["valid_transitions"] == []

    @pytest.mark.asyncio
    async def test_architecture_proposed(self, setup):
        """Proposed ADR should transition to Accepted, Rejected, or Deprecated."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "architecture", "current_status": "Proposed"},
        )
        data = _parse_json(result)
        assert set(data["valid_transitions"]) == {"Accepted", "Rejected", "Deprecated"}

    @pytest.mark.asyncio
    async def test_unknown_entity_type(self, setup):
        """Unknown entity_type should return error."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "unknown", "current_status": "Draft"},
        )
        text = _text(result)
        assert "ERROR" in text or "unknown" in text.lower() or "Unknown" in text

    @pytest.mark.asyncio
    async def test_unknown_status(self, setup):
        """Unknown status for valid entity_type should return error."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "requirement", "current_status": "Nonexistent"},
        )
        text = _text(result)
        assert "ERROR" in text or "unknown" in text.lower() or "Unknown" in text

    @pytest.mark.asyncio
    async def test_missing_params(self, setup):
        """Missing required params should return error."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "requirement"},
        )
        text = _text(result)
        assert "ERROR" in text or "required" in text.lower() or "Missing" in text


# ===========================================================================
# Tool Definitions
# ===========================================================================


class TestToolDefinitions:
    """Test that tool definitions are correct."""

    @pytest.mark.asyncio
    async def test_returns_two_tools(self, setup):
        handler, _, _ = setup
        tools = handler.get_tool_definitions()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"validate_project_plan", "get_valid_status_transitions"}

    @pytest.mark.asyncio
    async def test_validate_project_plan_schema(self, setup):
        handler, _, _ = setup
        tools = handler.get_tool_definitions()
        validate_tool = next(t for t in tools if t["name"] == "validate_project_plan")
        assert "project_id" in validate_tool["inputSchema"]["properties"]
        assert "project_id" in validate_tool["inputSchema"]["required"]
        # summary_only parameter must exist with boolean type
        assert "summary_only" in validate_tool["inputSchema"]["properties"]
        assert validate_tool["inputSchema"]["properties"]["summary_only"]["type"] == "boolean"

    @pytest.mark.asyncio
    async def test_unknown_tool_name(self, setup):
        """Calling with unknown tool name should return an error."""
        handler, _, _ = setup
        result = await handler.handle_tool_call("nonexistent_tool", {})
        text = _text(result)
        assert "ERROR" in text or "Unknown" in text
