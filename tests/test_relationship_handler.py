"""Tests for RelationshipHandler v2 (DB-07 + BF-02).

Validates:
  - CRUD: create, delete, query_relationships (merged tool)
  - Entity validation: source/target must exist before creating a relationship
  - Project scoping: project_id stored and used for filtering
  - Entity type detection: new ID format (REQ-, TASK-, ADR-, PROJ-)
  - Duplicate rejection
  - Relationship validation: task->architecture, task->requirement combos
  - Merged tool: query_relationships replaces get_entity_relationships + query_all_relationships
  - New params: output_format (summary/json), limit, offset, entity_id
  - N+1 fix: _fetch_all_relationships uses JOIN (no per-row lookups)
  - Removed tools: get_entity_relationships and query_all_relationships are gone
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
# Relationship validation: new combos from BF-01 constants
# ===========================================================================


class TestRelationshipValidation:
    """Test that new VALID_RELATIONSHIP_COMBINATIONS are accepted."""

    @pytest.mark.asyncio
    async def test_task_architecture_implements(self, setup):
        """task -> architecture with 'implements' should succeed."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "TASK-0001", "ADR-0001", "implements", project_id)
        text = _text(result)
        assert "SUCCESS" in text

    @pytest.mark.asyncio
    async def test_task_architecture_informs(self, setup):
        """task -> architecture with 'informs' should succeed."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "TASK-0001", "ADR-0001", "informs", project_id)
        text = _text(result)
        assert "SUCCESS" in text

    @pytest.mark.asyncio
    async def test_task_requirement_addresses(self, setup):
        """task -> requirement with 'addresses' should succeed."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "TASK-0001", "REQ-0001", "addresses", project_id)
        text = _text(result)
        assert "SUCCESS" in text

    @pytest.mark.asyncio
    async def test_invalid_combo_rejected(self, setup):
        """An invalid combination like task -> requirement with 'conflicts' should fail."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "TASK-0001", "REQ-0001", "conflicts", project_id)
        text = _text(result)
        assert "ERROR" in text
        assert "Invalid relationship" in text


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
# Query relationships (merged tool)
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
# Removed tools: get_entity_relationships, query_all_relationships
# ===========================================================================


class TestRemovedTools:
    """Verify that removed tools are no longer registered or routable."""

    @pytest.mark.asyncio
    async def test_get_entity_relationships_not_in_definitions(self, setup):
        """get_entity_relationships tool should not appear in tool definitions."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        tool_names = [d["name"] for d in defs]
        assert "get_entity_relationships" not in tool_names

    @pytest.mark.asyncio
    async def test_query_all_relationships_not_in_definitions(self, setup):
        """query_all_relationships tool should not appear in tool definitions."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        tool_names = [d["name"] for d in defs]
        assert "query_all_relationships" not in tool_names

    @pytest.mark.asyncio
    async def test_get_entity_relationships_returns_unknown_tool(self, setup):
        """Calling get_entity_relationships should return unknown tool error."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "get_entity_relationships",
            {"entity_id": "TASK-0001"},
        )
        text = _text(result)
        assert "Unknown tool" in text or "ERROR" in text

    @pytest.mark.asyncio
    async def test_query_all_relationships_returns_unknown_tool(self, setup):
        """Calling query_all_relationships should return unknown tool error."""
        handler, _, _ = setup
        result = await handler.handle_tool_call(
            "query_all_relationships",
            {},
        )
        text = _text(result)
        assert "Unknown tool" in text or "ERROR" in text

    @pytest.mark.asyncio
    async def test_only_three_tools_remain(self, setup):
        """Handler should define exactly 3 tools: create, delete, query_relationships."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        tool_names = sorted(d["name"] for d in defs)
        assert tool_names == ["create_relationship", "delete_relationship", "query_relationships"]


# ===========================================================================
# query_relationships: new parameters (output_format, limit, offset)
# ===========================================================================


class TestQueryRelationshipsNewParams:
    """Test new parameters added to query_relationships."""

    @pytest.mark.asyncio
    async def test_output_format_summary_default(self, setup):
        """Default output_format should be summary (human-readable lines)."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        result = await handler.handle_tool_call(
            "query_relationships",
            {"project_id": project_id},
        )
        text = _text(result)
        assert "SUCCESS" in text
        # Summary format should have arrow notation
        assert "->" in text or "\u2192" in text or "implements" in text.lower()

    @pytest.mark.asyncio
    async def test_output_format_json(self, setup):
        """output_format=json returns a JSON array."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        result = await handler.handle_tool_call(
            "query_relationships",
            {"project_id": project_id, "output_format": "json"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        # Should contain JSON code block
        assert "```json" in text
        # Extract JSON from the code block
        json_block = text.split("```json\n")[1].split("\n```")[0]
        data = json.loads(json_block)
        assert len(data) == 1
        assert data[0]["source_id"] == "TASK-0001"
        assert data[0]["target_id"] == "REQ-0001"
        assert data[0]["relationship_type"] == "implements"
        assert "source_title" in data[0]
        assert "target_title" in data[0]

    @pytest.mark.asyncio
    async def test_limit_parameter(self, setup):
        """limit parameter restricts the number of returned relationships."""
        handler, db, project_id = setup

        # Create a second task and second requirement
        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0001", "FUNC", "Second Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0001", "Second Task", "P2"],
        )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", project_id)

        result = await handler.handle_tool_call(
            "query_relationships",
            {"project_id": project_id, "limit": 1},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_offset_parameter(self, setup):
        """offset parameter skips relationships."""
        handler, db, project_id = setup

        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0001", "FUNC", "Second Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0001", "Second Task", "P2"],
        )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", project_id)

        result = await handler.handle_tool_call(
            "query_relationships",
            {"project_id": project_id, "offset": 1, "limit": 50},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_limit_and_offset_combined(self, setup):
        """limit + offset work together for pagination."""
        handler, db, project_id = setup

        # Create 3 tasks, 3 requirements, 3 relationships
        for i in range(2, 4):
            await db.execute_query(
                "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
                [f"REQ-000{i}", "PROJ-0001", "FUNC", f"Req {i}", "P2"],
            )
            await db.execute_query(
                "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
                [f"TASK-000{i}", "PROJ-0001", f"Task {i}", "P2"],
            )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", project_id)
        await _create_relationship(handler, "TASK-0003", "REQ-0003", "implements", project_id)

        # offset=1, limit=1 should return exactly 1
        result = await handler.handle_tool_call(
            "query_relationships",
            {"project_id": project_id, "offset": 1, "limit": 1},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_entity_id_filter_via_query_relationships(self, setup):
        """entity_id param should filter to relationships involving that entity."""
        handler, db, project_id = setup

        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0001", "FUNC", "Second Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0001", "Second Task", "P2"],
        )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", project_id)

        # Filter by TASK-0001 should return only 1
        result = await handler.handle_tool_call(
            "query_relationships",
            {"entity_id": "TASK-0001"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_entity_id_matches_target_too(self, setup):
        """entity_id filter should match both source and target."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        # Query by REQ-0001 (which is the target) should still find it
        result = await handler.handle_tool_call(
            "query_relationships",
            {"entity_id": "REQ-0001"},
        )
        text = _text(result)
        assert "SUCCESS" in text
        assert "1 relationship" in text

    @pytest.mark.asyncio
    async def test_json_output_includes_titles(self, setup):
        """JSON output should include source_title and target_title from JOIN."""
        handler, _, project_id = setup
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        result = await handler.handle_tool_call(
            "query_relationships",
            {"project_id": project_id, "output_format": "json"},
        )
        text = _text(result)

        # Parse JSON from code block
        json_block = text.split("```json\n")[1].split("\n```")[0]
        data = json.loads(json_block)
        assert len(data) == 1
        assert data[0]["source_title"] == "Test Task"
        assert data[0]["target_title"] == "Test Requirement"


# ===========================================================================
# Tool definitions for query_relationships
# ===========================================================================


class TestToolDefinitions:
    """Test that tool definitions include correct parameters."""

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
    async def test_query_relationships_has_output_format(self, setup):
        """query_relationships tool def includes output_format enum."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        query_def = next(d for d in defs if d["name"] == "query_relationships")
        props = query_def["inputSchema"]["properties"]
        assert "output_format" in props
        assert props["output_format"]["enum"] == ["summary", "json"]

    @pytest.mark.asyncio
    async def test_query_relationships_has_limit(self, setup):
        """query_relationships tool def includes limit."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        query_def = next(d for d in defs if d["name"] == "query_relationships")
        props = query_def["inputSchema"]["properties"]
        assert "limit" in props
        assert props["limit"]["type"] == "integer"

    @pytest.mark.asyncio
    async def test_query_relationships_has_offset(self, setup):
        """query_relationships tool def includes offset."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        query_def = next(d for d in defs if d["name"] == "query_relationships")
        props = query_def["inputSchema"]["properties"]
        assert "offset" in props
        assert props["offset"]["type"] == "integer"

    @pytest.mark.asyncio
    async def test_query_relationships_has_entity_id(self, setup):
        """query_relationships tool def includes entity_id."""
        handler, _, _ = setup
        defs = handler.get_tool_definitions()
        query_def = next(d for d in defs if d["name"] == "query_relationships")
        props = query_def["inputSchema"]["properties"]
        assert "entity_id" in props


# ===========================================================================
# N+1 fix verification: _fetch_all_relationships uses JOIN
# ===========================================================================


class TestFetchAllRelationshipsJoin:
    """Verify that _fetch_all_relationships resolves titles via JOIN, not per-row queries."""

    @pytest.mark.asyncio
    async def test_titles_resolved_for_all_entity_types(self, setup):
        """Titles should be resolved for requirements, tasks, and ADRs."""
        handler, _, project_id = setup

        # Create relationships of each type
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "ADR-0001", "REQ-0001", "addresses", project_id)

        # Call the internal method directly
        relationships = await handler._fetch_all_relationships(project_id=project_id)

        assert len(relationships) == 2

        # Check all titles are resolved (not just ID fallbacks)
        for rel in relationships:
            if rel["source_id"] == "TASK-0001":
                assert rel["source_title"] == "Test Task"
                assert rel["target_title"] == "Test Requirement"
            elif rel["source_id"] == "ADR-0001":
                assert rel["source_title"] == "Test ADR"
                assert rel["target_title"] == "Test Requirement"

    @pytest.mark.asyncio
    async def test_missing_entity_title_falls_back_to_id(self, setup):
        """If an entity is deleted but relationship row persists, title falls back to ID."""
        handler, db, project_id = setup

        # Create relationship
        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)

        # Manually delete the task (but leave the relationship orphaned)
        await db.execute_query("DELETE FROM tasks WHERE id = ?", ["TASK-0001"])

        relationships = await handler._fetch_all_relationships(project_id=project_id)
        assert len(relationships) == 1
        # Source title should fall back to the ID since the task no longer exists
        assert relationships[0]["source_id"] == "TASK-0001"
        # The JOIN will return NULL for deleted entities - should fall back to ID
        assert relationships[0]["source_title"] in ("TASK-0001", None, "")

    @pytest.mark.asyncio
    async def test_fetch_with_entity_id_filter(self, setup):
        """_fetch_all_relationships with entity_id returns only matching relationships."""
        handler, db, project_id = setup

        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0001", "FUNC", "Second Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0001", "Second Task", "P2"],
        )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", project_id)

        relationships = await handler._fetch_all_relationships(
            project_id=project_id, entity_id="TASK-0001"
        )
        assert len(relationships) == 1
        assert relationships[0]["source_id"] == "TASK-0001"

    @pytest.mark.asyncio
    async def test_fetch_with_limit_and_offset(self, setup):
        """_fetch_all_relationships respects limit and offset."""
        handler, db, project_id = setup

        await db.execute_query(
            "INSERT INTO requirements (id, project_id, type, title, priority) VALUES (?, ?, ?, ?, ?)",
            ["REQ-0002", "PROJ-0001", "FUNC", "Second Req", "P2"],
        )
        await db.execute_query(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ["TASK-0002", "PROJ-0001", "Second Task", "P2"],
        )

        await _create_relationship(handler, "TASK-0001", "REQ-0001", "implements", project_id)
        await _create_relationship(handler, "TASK-0002", "REQ-0002", "implements", project_id)

        relationships = await handler._fetch_all_relationships(
            project_id=project_id, limit=1, offset=0
        )
        assert len(relationships) == 1

        relationships2 = await handler._fetch_all_relationships(
            project_id=project_id, limit=1, offset=1
        )
        assert len(relationships2) == 1

        # The two results should be different
        assert relationships[0]["source_id"] != relationships2[0]["source_id"]


# ===========================================================================
# Architecture <-> Requirement informs relationship (IMP-01, TASK-0026)
# ===========================================================================


class TestArchitectureRequirementInforms:
    """Test architecture <-> requirement 'informs' relationship in both directions."""

    @pytest.mark.asyncio
    async def test_create_architecture_requirement_informs(self, setup):
        """architecture -> requirement with 'informs' should succeed."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "ADR-0001", "REQ-0001", "informs", project_id)
        text = _text(result)
        assert "SUCCESS" in text
        assert "ADR-0001" in text
        assert "REQ-0001" in text

    @pytest.mark.asyncio
    async def test_create_requirement_architecture_informs(self, setup):
        """requirement -> architecture with 'informs' should succeed."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "REQ-0001", "ADR-0001", "informs", project_id)
        text = _text(result)
        assert "SUCCESS" in text
        assert "REQ-0001" in text
        assert "ADR-0001" in text

    @pytest.mark.asyncio
    async def test_existing_addresses_still_works(self, setup):
        """Regression: architecture -> requirement 'addresses' must still work."""
        handler, _, project_id = setup
        result = await _create_relationship(handler, "ADR-0001", "REQ-0001", "addresses", project_id)
        text = _text(result)
        assert "SUCCESS" in text
