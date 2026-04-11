"""Tests for RelationshipHandler v2 (DB-07).

Validates:
  - CRUD: create, delete, query, query_all, get_entity_relationships
  - Entity validation: source/target must exist before creating a relationship
  - Project scoping: project_id stored and used for filtering
  - Entity type detection: new ID format (REQ-, TASK-, ADR-, PROJ-)
  - Duplicate rejection
"""

import json

import pytest

from lifecycle_mcp.handlers.relationship_handler import RelationshipHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def setup(v2_db_manager):
    """Set up a RelationshipHandler + test entities. Returns (handler, db, project_id)."""
    handler = RelationshipHandler(v2_db_manager)

    # Create project
    await v2_db_manager.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0001", "Test Project"],
    )

    # Create requirement
    await v2_db_manager.execute_query(
        "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
        ["REQ-0001", "PROJ-0001", "FUNC", "Test Requirement", "P1"],
    )

    # Create task
    await v2_db_manager.execute_query(
        "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
        ["TASK-0001", "PROJ-0001", "Test Task", "P1"],
    )

    # Create architecture decision
    await v2_db_manager.execute_query(
        "INSERT INTO architecture (id, project_id, title, context, decision) VALUES (?, ?, ?, ?, ?)",
        ["ADR-0001", "PROJ-0001", "Test ADR", "ctx", "dec"],
    )

    return handler, v2_db_manager, "PROJ-0001"


# -- Helpers -----------------------------------------------------------------


async def _create_relationship(handler, source_id, target_id, rel_type, project_id):
    """Shorthand to create a relationship via handle_tool_call."""
    return await handler.handle_tool_call(
        "create_relationship",
        {
            "source_id": source_id,
            "target_id": target_id,
            "relationship_type": rel_type,
            "project_id": project_id,
        },
    )


def _text(result):
    """Extract text from MCP response."""
    return result[0].text


# ===========================================================================
# Entity type detection
# ===========================================================================


class TestEntityTypeDetection:
    """Test _get_entity_type for new v2 ID formats."""

    @pytest.mark.asyncio
    async def test_req_prefix(self, setup):
        handler, _, _ = setup
        assert handler._get_entity_type("REQ-0001") == "requirement"

    @pytest.mark.asyncio
    async def test_task_prefix(self, setup):
        handler, _, _ = setup
        assert handler._get_entity_type("TASK-0001") == "task"

    @pytest.mark.asyncio
    async def test_adr_prefix(self, setup):
        handler, _, _ = setup
        assert handler._get_entity_type("ADR-0001") == "architecture"

    @pytest.mark.asyncio
    async def test_proj_prefix(self, setup):
        handler, _, _ = setup
        assert handler._get_entity_type("PROJ-0001") == "project"

    @pytest.mark.asyncio
    async def test_unknown_prefix(self, setup):
        handler, _, _ = setup
        assert handler._get_entity_type("FOO-0001") is None

    @pytest.mark.asyncio
    async def test_tdd_prefix_removed(self, setup):
        """TDD- prefix should no longer be recognized."""
        handler, _, _ = setup
        assert handler._get_entity_type("TDD-0001") is None


# ===========================================================================
# Create relationship
# ===========================================================================


class TestCreateRelationship:
    """Test creating relationships with project_id and entity validation."""

    @pytest.mark.asyncio
    async def test_create_basic(self, setup):
        """Creating a relationship between existing entities succeeds."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        text = _text(result)
        assert "SUCCESS" in text
        assert "TASK-0001" in text
        assert "REQ-0001" in text

    @pytest.mark.asyncio
    async def test_create_stores_project_id(self, setup):
        """Created relationship stores the project_id in the DB."""
        handler, db, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        rows = await db.get_records(
            "relationships", "*", "source_id = ? AND target_id = ?", ["TASK-0001", "REQ-0001"]
        )
        assert len(rows) == 1
        assert rows[0]["project_id"] == "PROJ-0001"

    @pytest.mark.asyncio
    async def test_create_nonexistent_source_rejected(self, setup):
        """Creating a relationship with a nonexistent source entity is rejected."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "TASK-9999", "REQ-0001", "implements", project_id)
        text = _text(result)
        assert "ERROR" in text
        assert "not found" in text.lower() or "TASK-9999" in text

    @pytest.mark.asyncio
    async def test_create_nonexistent_target_rejected(self, setup):
        """Creating a relationship with a nonexistent target entity is rejected."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "TASK-0001", "REQ-9999", "implements", project_id)
        text = _text(result)
        assert "ERROR" in text
        assert "not found" in text.lower() or "REQ-9999" in text

    @pytest.mark.asyncio
    async def test_create_duplicate_rejected(self, setup):
        """Creating the same relationship twice is rejected."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        result = await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        text = _text(result)
        assert "ERROR" in text
        assert "already exists" in text.lower()

    @pytest.mark.asyncio
    async def test_create_project_id_required(self, setup):
        """project_id is a required parameter."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "create_relationship",
            {
                "source_id": "TASK-0001",
                "target_id": "REQ-0001",
                "relationship_type": "implements",
                # project_id omitted
            },
        )
        text = _text(result)
        assert "ERROR" in text
        assert "project_id" in text.lower()

    @pytest.mark.asyncio
    async def test_create_adr_relationship(self, setup):
        """Creating a relationship involving an ADR succeeds."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "ADR-0001", "REQ-0001", "addresses", project_id)
        text = _text(result)
        assert "SUCCESS" in text


# ===========================================================================
# Delete relationship
# ===========================================================================


class TestDeleteRelationship:
    """Test deleting relationships."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, setup):
        """Deleting an existing relationship succeeds."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        result = await handler.handle_tool_call(
            "delete_relationship",
            {
                "source_id": "TASK-0001",
                "target_id": "REQ-0001",
                "relationship_type": "implements",
            },
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "Deleted" in text

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, setup):
        """Deleting a nonexistent relationship returns an error."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "delete_relationship",
            {
                "source_id": "TASK-0001",
                "target_id": "REQ-0001",
                "relationship_type": "implements",
            },
        )
        text = _text(result)
        assert "ERROR" in text or "No relationship" in text


# ===========================================================================
# Query relationships
# ===========================================================================


class TestQueryRelationships:
    """Test querying relationships with optional project_id filter."""

    @pytest.mark.asyncio
    async def test_query_by_entity_id(self, setup):
        """Querying relationships by entity_id returns matching ones."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        result = await handler.handle_tool_call(
            "query_relationships",
            {"entity_id": "TASK-0001"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_query_filtered_by_project_id(self, setup):
        """Querying with project_id filter returns only project-scoped relationships."""
        handler, db, project_id = setup

        # Create a second project + entities
        await db.execute_query(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0002", "Other Project"]
        )
        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0002", "FUNC", "Other Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0002", "Other Task", "P2"],
        )

        # Create relationships in each project
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", "PROJ-0001")
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", "PROJ-0002")

        # Query filtered by PROJ-0001
        result = await handler.handle_tool_call(
            "query_relationships",
            {"project_id": "PROJ-0001"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_query_no_project_filter_returns_all(self, setup):
        """Querying without project_id returns relationships from all projects."""
        handler, db, project_id = setup

        await db.execute_query(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0002", "Other Project"]
        )
        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0002", "FUNC", "Other Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0002", "Other Task", "P2"],
        )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", "PROJ-0001")
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", "PROJ-0002")

        result = await handler.handle_tool_call(
            "query_relationships",
            {},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "2 relationship" in text


# ===========================================================================
# Query all relationships
# ===========================================================================


class TestQueryAllRelationships:
    """Test query_all_relationships with project_id filter."""

    @pytest.mark.asyncio
    async def test_query_all_returns_all(self, setup):
        """query_all_relationships returns all relationships."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "ADR-0001", "REQ-0001", "addresses", project_id)

        result = await handler.handle_tool_call(
            "query_all_relationships",
            {},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "2 " in text

    @pytest.mark.asyncio
    async def test_query_all_filtered_by_project_id(self, setup):
        """query_all_relationships with project_id returns only that project's relationships."""
        handler, db, project_id = setup

        # Second project
        await db.execute_query(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ["PROJ-0002", "Other Project"]
        )
        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0002", "FUNC", "Other Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0002", "Other Task", "P2"],
        )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", "PROJ-0001")
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", "PROJ-0002")

        result = await handler.handle_tool_call(
            "query_all_relationships",
            {"project_id": "PROJ-0001"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 " in text


# ===========================================================================
# Get entity relationships
# ===========================================================================


class TestGetEntityRelationships:
    """Test get_entity_relationships."""

    @pytest.mark.asyncio
    async def test_get_entity_rels(self, setup):
        """get_entity_relationships returns both incoming and outgoing."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        result = await handler.handle_tool_call(
            "get_entity_relationships",
            {"entity_id": "TASK-0001"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_get_entity_rels_none(self, setup):
        """get_entity_relationships returns 0 when entity has no relationships."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_entity_relationships",
            {"entity_id": "TASK-0001"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "0 relationship" in text


# ===========================================================================
# Tool definitions
# ===========================================================================


class TestToolDefinitions:
    """Test that tool definitions include project_id where specified."""

    @pytest.mark.asyncio
    async def test_create_relationship_has_project_id(self, setup):
        """create_relationship tool def includes project_id as required."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        create_def = next(d for d in defs if d["name"] == "create_relationship")
        props = create_def["inputSchema"]["properties"]
        required = create_def["inputSchema"]["required"]
        assert "project_id" in props
        assert "project_id" in required

    @pytest.mark.asyncio
    async def test_query_relationships_has_project_id(self, setup):
        """query_relationships tool def includes optional project_id."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        query_def = next(d for d in defs if d["name"] == "query_relationships")
        props = query_def["inputSchema"]["properties"]
        assert "project_id" in props

    @pytest.mark.asyncio
    async def test_query_all_relationships_has_project_id(self, setup):
        """query_all_relationships tool def includes optional project_id."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        query_all_def = next(d for d in defs if d["name"] == "query_all_relationships")
        props = query_all_def["inputSchema"]["properties"]
        assert "project_id" in props
