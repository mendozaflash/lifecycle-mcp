"""Tests for ArchitectureHandler v2 (DB-06 / BF-05).

Validates all 7 MCP tools:
  create_architecture_decision, update_architecture_decision,
  update_architecture_status, archive_architecture_decision,
  query_architecture_decisions, get_architecture_details,
  add_architecture_review
"""

import json

import pytest

from lifecycle_mcp.handlers.architecture_handler import ArchitectureHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def setup(v2_db_manager):
    """Set up an ArchitectureHandler + a test project. Returns (handler, db, project_id)."""
    handler = ArchitectureHandler(v2_db_manager)
    await v2_db_manager.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0001", "Test Project"],
    )
    return handler, v2_db_manager, "PROJ-0001"


# -- Helpers -----------------------------------------------------------------


async def _create_adr(handler, project_id, title="Test ADR", **extra):
    """Shorthand to create an architecture decision via handle_tool_call."""
    params = {
        "project_id": project_id,
        "title": title,
        "context": "Test context",
        "decision": "Test decision",
    }
    params.update(extra)
    return await handler.handle_tool_call("create_architecture_decision", params)


async def _create_req(db, project_id, req_id, title="Test Requirement"):
    """Insert a requirement directly into the DB for relationship testing."""
    await db.execute_query(
        "INSERT INTO requirements (id, project_id, title, type, priority, status) "
        "VALUES (?, ?, ?, 'FUNC', 'P1', 'Under Review')",
        [req_id, project_id, title],
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
        "create_architecture_decision",
        "update_architecture_decision",
        "update_architecture_status",
        "archive_architecture_decision",
        "query_architecture_decisions",
        "get_architecture_details",
        "add_architecture_review",
    ]
    for name in expected:
        assert name in names, f"Missing tool definition: {name}"
    assert "query_architecture_decisions_json" not in names, "Removed tool still present"
    assert len(tools) == 7


# =============================================================================
#  create_architecture_decision
# =============================================================================


@pytest.mark.asyncio
async def test_create_adr_basic(setup):
    handler, db, pid = setup
    result = await _create_adr(handler, pid)
    text = result[0].text
    assert "ADR-0001" in text
    assert "SUCCESS" in text


@pytest.mark.asyncio
async def test_create_adr_sequential_ids(setup):
    handler, db, pid = setup
    r1 = await _create_adr(handler, pid, title="A")
    r2 = await _create_adr(handler, pid, title="B")
    r3 = await _create_adr(handler, pid, title="C")
    assert "ADR-0001" in r1[0].text
    assert "ADR-0002" in r2[0].text
    assert "ADR-0003" in r3[0].text


@pytest.mark.asyncio
async def test_create_adr_stores_all_fields(setup):
    handler, db, pid = setup
    await _create_adr(
        handler,
        pid,
        title="Full ADR",
        context="Full context",
        decision="Full decision",
        decision_drivers=["Driver 1", "Driver 2"],
        considered_options=["Option A", "Option B"],
        consequences={"positive": "Good", "negative": "Trade-offs"},
        authors=["Alice", "Bob"],
    )
    row = await db.execute_query(
        "SELECT * FROM architecture WHERE id = 'ADR-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row is not None
    assert row["title"] == "Full ADR"
    assert row["context"] == "Full context"
    assert row["decision"] == "Full decision"
    assert json.loads(row["decision_drivers"]) == ["Driver 1", "Driver 2"]
    assert json.loads(row["considered_options"]) == ["Option A", "Option B"]
    assert json.loads(row["consequences"]) == {"positive": "Good", "negative": "Trade-offs"}
    assert json.loads(row["authors"]) == ["Alice", "Bob"]
    assert row["status"] == "Under Review"
    assert row["project_id"] == pid


@pytest.mark.asyncio
async def test_create_adr_validates_project_exists(setup):
    handler, db, pid = setup
    result = await _create_adr(handler, "PROJ-9999", title="Orphan ADR")
    text = result[0].text
    assert "ERROR" in text
    assert "PROJ-9999" in text


@pytest.mark.asyncio
async def test_create_adr_missing_required_fields(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "create_architecture_decision", {"project_id": pid, "title": "No context"}
    )
    assert "ERROR" in result[0].text
    assert "Missing required" in result[0].text


# =============================================================================
#  update_architecture_decision (broad field update)
# =============================================================================


@pytest.mark.asyncio
async def test_update_adr_title_and_context(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Old Title")
    result = await handler.handle_tool_call(
        "update_architecture_decision",
        {"architecture_id": "ADR-0001", "title": "New Title", "context": "New context"},
    )
    assert "SUCCESS" in result[0].text
    row = await db.execute_query(
        "SELECT title, context FROM architecture WHERE id = 'ADR-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["title"] == "New Title"
    assert row["context"] == "New context"


@pytest.mark.asyncio
async def test_update_adr_decision_and_json_fields(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)
    result = await handler.handle_tool_call(
        "update_architecture_decision",
        {
            "architecture_id": "ADR-0001",
            "decision": "Updated decision",
            "decision_drivers": ["New driver"],
            "considered_options": ["New option"],
            "consequences": {"risk": "low"},
            "authors": ["Charlie"],
        },
    )
    assert "SUCCESS" in result[0].text
    row = await db.execute_query(
        "SELECT decision, decision_drivers, considered_options, consequences, authors "
        "FROM architecture WHERE id = 'ADR-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["decision"] == "Updated decision"
    assert json.loads(row["decision_drivers"]) == ["New driver"]
    assert json.loads(row["considered_options"]) == ["New option"]
    assert json.loads(row["consequences"]) == {"risk": "low"}
    assert json.loads(row["authors"]) == ["Charlie"]


@pytest.mark.asyncio
async def test_update_adr_rejects_archived(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)
    await handler.handle_tool_call("archive_architecture_decision", {"architecture_id": "ADR-0001"})
    result = await handler.handle_tool_call(
        "update_architecture_decision", {"architecture_id": "ADR-0001", "title": "Nope"}
    )
    assert "ERROR" in result[0].text
    assert "archived" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_adr_rejects_nonexistent(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "update_architecture_decision", {"architecture_id": "ADR-9999", "title": "Nope"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_adr_no_fields(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)
    result = await handler.handle_tool_call(
        "update_architecture_decision", {"architecture_id": "ADR-0001"}
    )
    assert "ERROR" in result[0].text
    assert "No fields" in result[0].text


# =============================================================================
#  update_architecture_status
# =============================================================================


@pytest.mark.asyncio
async def test_update_status_valid_transitions(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)

    # Under Review -> Proposed
    result = await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-0001", "new_status": "Proposed"}
    )
    assert "SUCCESS" in result[0].text

    # Proposed -> Accepted
    result = await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-0001", "new_status": "Accepted"}
    )
    assert "SUCCESS" in result[0].text

    # Accepted -> Deprecated
    result = await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-0001", "new_status": "Deprecated"}
    )
    assert "SUCCESS" in result[0].text


@pytest.mark.asyncio
async def test_update_status_invalid_transition(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)  # status = "Under Review"

    # Under Review -> Rejected (not allowed, must go through Proposed first)
    result = await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-0001", "new_status": "Rejected"}
    )
    assert "ERROR" in result[0].text
    assert "Invalid transition" in result[0].text or "transition" in result[0].text.lower()


@pytest.mark.asyncio
async def test_update_status_deprecated_is_terminal(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)

    # Under Review -> Deprecated
    await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-0001", "new_status": "Deprecated"}
    )

    # Deprecated -> Under Review (not allowed, Deprecated is terminal)
    result = await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-0001", "new_status": "Under Review"}
    )
    assert "ERROR" in result[0].text


@pytest.mark.asyncio
async def test_update_status_deprecated_with_superseded_by(setup):
    """When setting status to Deprecated with superseded_by, store the FK."""
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Old ADR")
    await _create_adr(handler, pid, title="New ADR")

    result = await handler.handle_tool_call(
        "update_architecture_status",
        {
            "architecture_id": "ADR-0001",
            "new_status": "Deprecated",
            "superseded_by": "ADR-0002",
        },
    )
    assert "SUCCESS" in result[0].text

    row = await db.execute_query(
        "SELECT status, superseded_by FROM architecture WHERE id = 'ADR-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Deprecated"
    assert row["superseded_by"] == "ADR-0002"


@pytest.mark.asyncio
async def test_update_status_with_comment(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)
    result = await handler.handle_tool_call(
        "update_architecture_status",
        {"architecture_id": "ADR-0001", "new_status": "Proposed", "comment": "Ready for review"},
    )
    assert "SUCCESS" in result[0].text

    # Verify review comment was stored
    reviews = await db.execute_query(
        "SELECT * FROM reviews WHERE entity_type = 'architecture' AND entity_id = 'ADR-0001'",
        fetch_all=True,
        row_factory=True,
    )
    assert len(reviews) >= 1
    assert reviews[0]["comment"] == "Ready for review"


@pytest.mark.asyncio
async def test_update_status_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-9999", "new_status": "Under Review"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  ADR shortcut transitions
# =============================================================================


@pytest.mark.asyncio
async def test_adr_under_review_to_accepted_shortcut_direct(setup):
    """Under Review -> Accepted shortcut transition works in one call (from initial status)."""
    handler, db, pid = setup
    await _create_adr(handler, pid)  # starts as Under Review

    result = await handler.handle_tool_call(
        "update_architecture_status",
        {"architecture_id": "ADR-0001", "new_status": "Accepted"},
    )
    assert "SUCCESS" in result[0].text
    assert "Under Review" in result[0].text
    assert "Accepted" in result[0].text

    # Verify DB
    row = await db.execute_query(
        "SELECT status FROM architecture WHERE id = 'ADR-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Accepted"


@pytest.mark.asyncio
async def test_adr_proposed_to_accepted_transition(setup):
    """Under Review -> Proposed -> Accepted works through normal flow."""
    handler, db, pid = setup
    await _create_adr(handler, pid)

    # Under Review -> Proposed
    await handler.handle_tool_call(
        "update_architecture_status",
        {"architecture_id": "ADR-0001", "new_status": "Proposed"},
    )

    # Proposed -> Accepted
    result = await handler.handle_tool_call(
        "update_architecture_status",
        {"architecture_id": "ADR-0001", "new_status": "Accepted"},
    )
    assert "SUCCESS" in result[0].text
    assert "Proposed" in result[0].text
    assert "Accepted" in result[0].text

    # Verify DB
    row = await db.execute_query(
        "SELECT status FROM architecture WHERE id = 'ADR-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["status"] == "Accepted"


# =============================================================================
#  archive_architecture_decision
# =============================================================================


@pytest.mark.asyncio
async def test_archive_adr(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)
    result = await handler.handle_tool_call(
        "archive_architecture_decision", {"architecture_id": "ADR-0001"}
    )
    assert "SUCCESS" in result[0].text

    row = await db.execute_query(
        "SELECT is_archived, archived_at FROM architecture WHERE id = 'ADR-0001'",
        fetch_one=True,
        row_factory=True,
    )
    assert row["is_archived"] == 1
    assert row["archived_at"] is not None


@pytest.mark.asyncio
async def test_archive_adr_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "archive_architecture_decision", {"architecture_id": "ADR-9999"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  query_architecture_decisions
# =============================================================================


@pytest.mark.asyncio
async def test_query_by_project(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="ADR Alpha")
    await _create_adr(handler, pid, title="ADR Beta")
    result = await handler.handle_tool_call("query_architecture_decisions", {"project_id": pid})
    text = result[0].text
    assert "ADR-0001" in text
    assert "ADR-0002" in text


@pytest.mark.asyncio
async def test_query_excludes_archived(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Visible")
    await _create_adr(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_architecture_decision", {"architecture_id": "ADR-0002"})

    result = await handler.handle_tool_call("query_architecture_decisions", {"project_id": pid})
    text = result[0].text
    assert "ADR-0001" in text
    assert "ADR-0002" not in text


@pytest.mark.asyncio
async def test_query_includes_archived_with_flag(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Visible")
    await _create_adr(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_architecture_decision", {"architecture_id": "ADR-0002"})

    result = await handler.handle_tool_call(
        "query_architecture_decisions", {"project_id": pid, "include_archived": True}
    )
    text = result[0].text
    assert "ADR-0001" in text
    assert "ADR-0002" in text


@pytest.mark.asyncio
async def test_query_filters_by_status(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="UR ADR")
    await _create_adr(handler, pid, title="Proposed ADR")
    await handler.handle_tool_call(
        "update_architecture_status", {"architecture_id": "ADR-0002", "new_status": "Proposed"}
    )

    result = await handler.handle_tool_call(
        "query_architecture_decisions", {"status": "Proposed"}
    )
    text = result[0].text
    assert "Proposed ADR" in text
    assert "UR ADR" not in text


@pytest.mark.asyncio
async def test_query_filters_by_search_text(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Microservices Architecture")
    await _create_adr(handler, pid, title="Database Sharding")

    result = await handler.handle_tool_call(
        "query_architecture_decisions", {"search_text": "Microservices"}
    )
    text = result[0].text
    assert "Microservices" in text
    assert "Sharding" not in text


@pytest.mark.asyncio
async def test_query_no_results(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "query_architecture_decisions", {"status": "Rejected"}
    )
    text = result[0].text
    assert "No architecture decisions found" in text or "0" in text


# =============================================================================
#  query_architecture_decisions — output_format, limit, offset
# =============================================================================


@pytest.mark.asyncio
async def test_query_architecture_json_output_format(setup):
    """output_format=json returns a JSON array with parsed JSON fields."""
    handler, db, pid = setup
    await _create_adr(
        handler,
        pid,
        title="JSON ADR",
        decision_drivers=["Speed"],
        consequences={"pro": "fast"},
    )

    result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "output_format": "json"},
    )
    data = json.loads(result[0].text)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "JSON ADR"
    assert data[0]["id"] == "ADR-0001"
    assert data[0]["status"] == "Under Review"
    # JSON fields should be parsed
    assert data[0]["decision_drivers"] == ["Speed"]
    assert data[0]["consequences"] == {"pro": "fast"}


@pytest.mark.asyncio
async def test_query_architecture_json_excludes_archived(setup):
    """output_format=json respects include_archived flag."""
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Visible")
    await _create_adr(handler, pid, title="Hidden")
    await handler.handle_tool_call("archive_architecture_decision", {"architecture_id": "ADR-0002"})

    result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "output_format": "json"},
    )
    data = json.loads(result[0].text)
    assert len(data) == 1
    assert data[0]["title"] == "Visible"


@pytest.mark.asyncio
async def test_query_architecture_summary_format(setup):
    """output_format=summary (default) returns one-line-per-ADR: 'ADR-XXXX | title | status'."""
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Summary ADR")

    result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "output_format": "summary"},
    )
    text = result[0].text
    assert "ADR-0001 | Summary ADR | Under Review" in text


@pytest.mark.asyncio
async def test_query_architecture_summary_is_default(setup):
    """When output_format is omitted, summary format is used by default."""
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Default Format ADR")

    result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid},
    )
    text = result[0].text
    assert "ADR-0001 | Default Format ADR | Under Review" in text


@pytest.mark.asyncio
async def test_query_architecture_markdown_format(setup):
    """output_format=markdown returns the verbose/detailed format."""
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Markdown ADR")

    result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "output_format": "markdown"},
    )
    text = result[0].text
    # Markdown format should contain the ADR-ID and title in verbose style
    assert "ADR-0001" in text
    assert "Markdown ADR" in text
    assert "Under Review" in text


@pytest.mark.asyncio
async def test_query_architecture_limit(setup):
    """limit parameter restricts the number of returned results."""
    handler, db, pid = setup
    for i in range(5):
        await _create_adr(handler, pid, title=f"ADR {i}")

    result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "limit": 2},
    )
    text = result[0].text
    # With limit=2 and ORDER BY created_at DESC, we should get 2 results
    # Count the ADR-XXXX occurrences in summary format
    adr_count = text.count("ADR-")
    # Could have ADR- in header too, so count pipe-separated lines
    lines = [l for l in text.split("\n") if " | " in l and "ADR-" in l]
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_query_architecture_offset(setup):
    """offset parameter skips initial results."""
    handler, db, pid = setup
    for i in range(5):
        await _create_adr(handler, pid, title=f"ADR {i}")

    # Get all results first (ordered by created_at DESC -> ADR-0005 first)
    all_result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "output_format": "json"},
    )
    all_data = json.loads(all_result[0].text)

    # Now with offset=2
    offset_result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "output_format": "json", "offset": 2},
    )
    offset_data = json.loads(offset_result[0].text)

    assert len(offset_data) == 3  # 5 total - 2 skipped = 3
    assert offset_data[0]["id"] == all_data[2]["id"]


@pytest.mark.asyncio
async def test_query_architecture_limit_and_offset(setup):
    """limit and offset work together for pagination."""
    handler, db, pid = setup
    for i in range(5):
        await _create_adr(handler, pid, title=f"ADR {i}")

    # Get page 2 (offset=2, limit=2)
    result = await handler.handle_tool_call(
        "query_architecture_decisions",
        {"project_id": pid, "output_format": "json", "limit": 2, "offset": 2},
    )
    data = json.loads(result[0].text)
    assert len(data) == 2


# =============================================================================
#  get_architecture_details
# =============================================================================


@pytest.mark.asyncio
async def test_details_full_record(setup):
    handler, db, pid = setup
    await _create_adr(
        handler,
        pid,
        title="Detailed ADR",
        context="Detailed context",
        decision="Detailed decision",
        decision_drivers=["Scalability"],
        considered_options=["Monolith", "Microservices"],
        consequences={"positive": "Scales well"},
        authors=["Alice"],
    )
    result = await handler.handle_tool_call(
        "get_architecture_details", {"architecture_id": "ADR-0001"}
    )
    text = result[0].text
    assert "Detailed ADR" in text
    assert "Detailed context" in text
    assert "Detailed decision" in text
    assert "Scalability" in text
    assert "Monolith" in text
    assert "Microservices" in text
    assert "Alice" in text


@pytest.mark.asyncio
async def test_details_with_relationships(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Linked ADR")
    await _create_req(db, pid, "REQ-0001", title="Linked Requirement")
    await _link(db, "requirement", "REQ-0001", "architecture", "ADR-0001", "addresses", pid)

    result = await handler.handle_tool_call(
        "get_architecture_details", {"architecture_id": "ADR-0001"}
    )
    text = result[0].text
    assert "REQ-0001" in text
    assert "Linked Requirement" in text


@pytest.mark.asyncio
async def test_details_with_reviews(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Reviewed ADR")
    await handler.handle_tool_call(
        "add_architecture_review",
        {"architecture_id": "ADR-0001", "comment": "Looks good", "reviewer": "Bob"},
    )

    result = await handler.handle_tool_call(
        "get_architecture_details", {"architecture_id": "ADR-0001"}
    )
    text = result[0].text
    assert "Bob" in text
    assert "Looks good" in text


@pytest.mark.asyncio
async def test_details_shows_superseding_adr(setup):
    """When ADR has superseded_by set, details should show info about superseding ADR."""
    handler, db, pid = setup
    await _create_adr(handler, pid, title="Old ADR")
    await _create_adr(handler, pid, title="New ADR")
    await handler.handle_tool_call(
        "update_architecture_status",
        {"architecture_id": "ADR-0001", "new_status": "Deprecated", "superseded_by": "ADR-0002"},
    )

    result = await handler.handle_tool_call(
        "get_architecture_details", {"architecture_id": "ADR-0001"}
    )
    text = result[0].text
    assert "Deprecated" in text
    assert "ADR-0002" in text
    assert "New ADR" in text


@pytest.mark.asyncio
async def test_details_not_found(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "get_architecture_details", {"architecture_id": "ADR-9999"}
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  add_architecture_review
# =============================================================================


@pytest.mark.asyncio
async def test_add_review_basic(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)
    result = await handler.handle_tool_call(
        "add_architecture_review",
        {"architecture_id": "ADR-0001", "comment": "LGTM", "reviewer": "Alice"},
    )
    assert "SUCCESS" in result[0].text

    reviews = await db.execute_query(
        "SELECT * FROM reviews WHERE entity_type = 'architecture' AND entity_id = 'ADR-0001'",
        fetch_all=True,
        row_factory=True,
    )
    assert len(reviews) == 1
    assert reviews[0]["comment"] == "LGTM"
    assert reviews[0]["reviewer"] == "Alice"


@pytest.mark.asyncio
async def test_add_review_default_reviewer(setup):
    handler, db, pid = setup
    await _create_adr(handler, pid)
    result = await handler.handle_tool_call(
        "add_architecture_review",
        {"architecture_id": "ADR-0001", "comment": "Needs work"},
    )
    assert "SUCCESS" in result[0].text

    reviews = await db.execute_query(
        "SELECT reviewer FROM reviews WHERE entity_id = 'ADR-0001'",
        fetch_all=True,
        row_factory=True,
    )
    assert len(reviews) == 1
    assert reviews[0]["reviewer"] == "MCP User"


@pytest.mark.asyncio
async def test_add_review_validates_architecture_exists(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "add_architecture_review",
        {"architecture_id": "ADR-9999", "comment": "Test"},
    )
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


# =============================================================================
#  Supersession end-to-end
# =============================================================================


@pytest.mark.asyncio
async def test_supersession_full_flow(setup):
    """Create 2 ADRs, deprecate first with superseded_by=second, verify details shows superseding ADR."""
    handler, db, pid = setup

    # Create original and replacement ADRs
    await _create_adr(handler, pid, title="Original Design")
    await _create_adr(handler, pid, title="Improved Design")

    # Deprecate ADR-0001 with superseded_by
    result = await handler.handle_tool_call(
        "update_architecture_status",
        {
            "architecture_id": "ADR-0001",
            "new_status": "Deprecated",
            "superseded_by": "ADR-0002",
            "comment": "Replaced by improved design",
        },
    )
    assert "SUCCESS" in result[0].text

    # Verify details show superseding ADR info
    result = await handler.handle_tool_call(
        "get_architecture_details", {"architecture_id": "ADR-0001"}
    )
    text = result[0].text
    assert "Deprecated" in text
    assert "ADR-0002" in text
    assert "Improved Design" in text

    # Verify the superseding ADR is still active
    result2 = await handler.handle_tool_call(
        "get_architecture_details", {"architecture_id": "ADR-0002"}
    )
    assert "Under Review" in result2[0].text
    assert "Improved Design" in result2[0].text


# =============================================================================
#  Handle unknown tool
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_tool(setup):
    handler, _, _ = setup
    result = await handler.handle_tool_call("nonexistent_tool", {})
    assert "ERROR" in result[0].text or "Unknown" in result[0].text


# =============================================================================
#  Dead code removal verification
# =============================================================================


@pytest.mark.asyncio
async def test_no_mcp_client_parameter(setup):
    """Constructor should only accept db_manager, not mcp_client."""
    handler, _, _ = setup
    assert not hasattr(handler, "mcp_client")


@pytest.mark.asyncio
async def test_no_analyze_adr_for_diagrams(setup):
    """Dead LLM sampling code should be removed."""
    handler, _, _ = setup
    assert not hasattr(handler, "_analyze_adr_for_diagrams")
    assert not hasattr(handler, "_build_adr_context")
    assert not hasattr(handler, "_get_diagram_analysis_system_prompt")


@pytest.mark.asyncio
async def test_no_query_json_route(setup):
    """query_architecture_decisions_json should not be routed."""
    handler, db, pid = setup
    result = await handler.handle_tool_call("query_architecture_decisions_json", {})
    assert "ERROR" in result[0].text or "Unknown" in result[0].text
