"""Tests for PatternHandler (ADR-COH-03 / TASK-0032).

Validates all 4 MCP tools:
  create_architectural_pattern, link_adr_to_pattern,
  query_architectural_patterns, get_architectural_overview
"""

import json

import pytest

from lifecycle_mcp.handlers.pattern_handler import PatternHandler


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def setup(v2_db_manager):
    """Set up a PatternHandler + a test project. Returns (handler, db, project_id)."""
    handler = PatternHandler(v2_db_manager)
    await v2_db_manager.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0001", "Test Project"],
    )
    return handler, v2_db_manager, "PROJ-0001"


# -- Helpers -----------------------------------------------------------------


async def _create_pattern(handler, project_id, name="Test Pattern", type_="database", **extra):
    """Shorthand to create an architectural pattern via handle_tool_call."""
    params = {"project_id": project_id, "name": name, "type": type_}
    params.update(extra)
    return await handler.handle_tool_call("create_architectural_pattern", params)


async def _create_adr(db, project_id, adr_id, title="Test ADR", status="Under Review", decision="Test decision"):
    """Insert an ADR directly into the DB."""
    await db.execute_query(
        "INSERT INTO architecture (id, project_id, title, context, decision, status) "
        "VALUES (?, ?, ?, 'Test context', ?, ?)",
        [adr_id, project_id, title, decision, status],
    )


async def _link(handler, adr_id, pattern_id, role="follows"):
    """Link an ADR to a pattern via handle_tool_call."""
    return await handler.handle_tool_call(
        "link_adr_to_pattern",
        {"adr_id": adr_id, "pattern_id": pattern_id, "role": role},
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
        "create_architectural_pattern",
        "link_adr_to_pattern",
        "query_architectural_patterns",
        "get_architectural_overview",
    ]
    for name in expected:
        assert name in names, f"Missing tool definition: {name}"
    assert len(tools) == 4


# =============================================================================
#  create_architectural_pattern
# =============================================================================


@pytest.mark.asyncio
async def test_create_pattern_basic(setup):
    handler, db, pid = setup
    result = await _create_pattern(handler, pid)
    text = result[0].text
    assert "PAT-0001" in text
    assert "SUCCESS" in text


@pytest.mark.asyncio
async def test_create_pattern_sequential_ids(setup):
    handler, db, pid = setup
    r1 = await _create_pattern(handler, pid, name="A")
    r2 = await _create_pattern(handler, pid, name="B")
    r3 = await _create_pattern(handler, pid, name="C")
    assert "PAT-0001" in r1[0].text
    assert "PAT-0002" in r2[0].text
    assert "PAT-0003" in r3[0].text


@pytest.mark.asyncio
async def test_create_pattern_stores_all_fields(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="DB Adapter", type_="adapter", description="Decouples storage")
    row = await db.execute_query(
        "SELECT * FROM architectural_patterns WHERE id = 'PAT-0001'",
        fetch_one=True, row_factory=True,
    )
    assert row is not None
    assert row["name"] == "DB Adapter"
    assert row["type"] == "adapter"
    assert row["description"] == "Decouples storage"
    assert row["project_id"] == pid


@pytest.mark.asyncio
async def test_create_pattern_valid_type_accepted(setup):
    """Each of the 15 pattern types should be accepted."""
    handler, db, pid = setup
    from lifecycle_mcp.constants import PATTERN_TYPES

    for ptype in sorted(PATTERN_TYPES):
        result = await _create_pattern(handler, pid, name=f"pat-{ptype}", type_=ptype)
        assert "SUCCESS" in result[0].text, f"Type '{ptype}' should be accepted"


@pytest.mark.asyncio
async def test_create_pattern_invalid_type_rejected(setup):
    handler, db, pid = setup
    result = await _create_pattern(handler, pid, name="Bad", type_="nonexistent_type")
    text = result[0].text
    assert "ERROR" in text
    assert "Invalid pattern type" in text


@pytest.mark.asyncio
async def test_create_pattern_validates_project_exists(setup):
    handler, db, pid = setup
    result = await _create_pattern(handler, "PROJ-9999", name="Orphan")
    text = result[0].text
    assert "ERROR" in text
    assert "PROJ-9999" in text


@pytest.mark.asyncio
async def test_create_pattern_missing_required_fields(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "create_architectural_pattern", {"project_id": pid, "name": "No type"}
    )
    assert "ERROR" in result[0].text
    assert "Missing required" in result[0].text


# =============================================================================
#  link_adr_to_pattern
# =============================================================================


@pytest.mark.asyncio
async def test_link_default_role_follows(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1")
    await _create_adr(db, pid, "ADR-0001")

    result = await _link(handler, "ADR-0001", "PAT-0001")
    assert "SUCCESS" in result[0].text
    assert "follows" in result[0].text

    row = await db.execute_query(
        "SELECT role FROM adr_patterns WHERE adr_id = 'ADR-0001' AND pattern_id = 'PAT-0001'",
        fetch_one=True, row_factory=True,
    )
    assert row["role"] == "follows"


@pytest.mark.asyncio
async def test_link_explicit_role_establishes(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1")
    await _create_adr(db, pid, "ADR-0001")

    result = await _link(handler, "ADR-0001", "PAT-0001", role="establishes")
    assert "SUCCESS" in result[0].text
    assert "establishes" in result[0].text

    row = await db.execute_query(
        "SELECT role FROM adr_patterns WHERE adr_id = 'ADR-0001' AND pattern_id = 'PAT-0001'",
        fetch_one=True, row_factory=True,
    )
    assert row["role"] == "establishes"


@pytest.mark.asyncio
async def test_link_explicit_role_refines(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1")
    await _create_adr(db, pid, "ADR-0001")

    result = await _link(handler, "ADR-0001", "PAT-0001", role="refines")
    assert "SUCCESS" in result[0].text
    assert "refines" in result[0].text


@pytest.mark.asyncio
async def test_link_cross_project_rejected(setup):
    handler, db, pid = setup
    # Create a second project
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0002", "Other Project"],
    )
    await _create_pattern(handler, pid, name="Pat1")  # PROJ-0001
    await _create_adr(db, "PROJ-0002", "ADR-0001")   # PROJ-0002

    result = await _link(handler, "ADR-0001", "PAT-0001")
    text = result[0].text
    assert "ERROR" in text
    assert "Cross-project" in text


@pytest.mark.asyncio
async def test_link_duplicate_rejected(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1")
    await _create_adr(db, pid, "ADR-0001")

    result1 = await _link(handler, "ADR-0001", "PAT-0001")
    assert "SUCCESS" in result1[0].text

    result2 = await _link(handler, "ADR-0001", "PAT-0001")
    assert "ERROR" in result2[0].text
    assert "Duplicate" in result2[0].text


@pytest.mark.asyncio
async def test_link_adr_not_found(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1")

    result = await _link(handler, "ADR-9999", "PAT-0001")
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


@pytest.mark.asyncio
async def test_link_pattern_not_found(setup):
    handler, db, pid = setup
    await _create_adr(db, pid, "ADR-0001")

    result = await _link(handler, "ADR-0001", "PAT-9999")
    assert "ERROR" in result[0].text
    assert "not found" in result[0].text.lower()


@pytest.mark.asyncio
async def test_link_invalid_role_rejected(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1")
    await _create_adr(db, pid, "ADR-0001")

    result = await handler.handle_tool_call(
        "link_adr_to_pattern",
        {"adr_id": "ADR-0001", "pattern_id": "PAT-0001", "role": "invalid_role"},
    )
    assert "ERROR" in result[0].text
    assert "Invalid role" in result[0].text


# =============================================================================
#  query_architectural_patterns
# =============================================================================


@pytest.mark.asyncio
async def test_query_all_patterns(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat A", type_="database")
    await _create_pattern(handler, pid, name="Pat B", type_="api")

    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"project_id": pid}
    )
    text = result[0].text
    assert "PAT-0001" in text
    assert "PAT-0002" in text


@pytest.mark.asyncio
async def test_query_filter_by_type(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="DB Pattern", type_="database")
    await _create_pattern(handler, pid, name="API Pattern", type_="api")

    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"project_id": pid, "type": "database"}
    )
    text = result[0].text
    assert "DB Pattern" in text
    assert "API Pattern" not in text


@pytest.mark.asyncio
async def test_query_filter_by_search_text(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Connection Pool Pattern", type_="database")
    await _create_pattern(handler, pid, name="REST Gateway", type_="api")

    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"search_text": "Pool"}
    )
    text = result[0].text
    assert "Connection Pool" in text
    assert "REST Gateway" not in text


@pytest.mark.asyncio
async def test_query_role_breakdown_counts(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Establishing ADR")
    await _create_adr(db, pid, "ADR-0002", title="Following ADR")
    await _create_adr(db, pid, "ADR-0003", title="Refining ADR")

    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")
    await _link(handler, "ADR-0002", "PAT-0001", role="follows")
    await _link(handler, "ADR-0003", "PAT-0001", role="refines")

    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"project_id": pid}
    )
    text = result[0].text
    assert "1 establishing" in text
    assert "1 following" in text
    assert "1 refining" in text


@pytest.mark.asyncio
async def test_query_excludes_archived(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Active")
    await _create_pattern(handler, pid, name="Archived")
    # Archive PAT-0002
    await db.execute_query(
        "UPDATE architectural_patterns SET is_archived = 1 WHERE id = 'PAT-0002'"
    )

    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"project_id": pid}
    )
    text = result[0].text
    assert "Active" in text
    assert "Archived" not in text


@pytest.mark.asyncio
async def test_query_includes_archived_with_flag(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Active")
    await _create_pattern(handler, pid, name="Archived")
    await db.execute_query(
        "UPDATE architectural_patterns SET is_archived = 1 WHERE id = 'PAT-0002'"
    )

    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"project_id": pid, "include_archived": True}
    )
    text = result[0].text
    assert "Active" in text
    assert "Archived" in text


@pytest.mark.asyncio
async def test_query_json_output_format(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="JSON Pat", type_="api", description="An API pattern")

    result = await handler.handle_tool_call(
        "query_architectural_patterns",
        {"project_id": pid, "output_format": "json"},
    )
    data = json.loads(result[0].text)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "JSON Pat"
    assert data[0]["type"] == "api"
    assert data[0]["establishing"] == 0
    assert data[0]["following"] == 0
    assert data[0]["refining"] == 0


@pytest.mark.asyncio
async def test_query_markdown_output_format(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="MD Pat", type_="transport")

    result = await handler.handle_tool_call(
        "query_architectural_patterns",
        {"project_id": pid, "output_format": "markdown"},
    )
    text = result[0].text
    assert "MD Pat" in text
    assert "transport" in text


@pytest.mark.asyncio
async def test_query_summary_is_default(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Default Pat", type_="auth")

    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"project_id": pid}
    )
    text = result[0].text
    # Summary format: PAT-XXXX | name | type | role counts
    assert "PAT-0001 | Default Pat | auth" in text


@pytest.mark.asyncio
async def test_query_limit(setup):
    handler, db, pid = setup
    for i in range(5):
        await _create_pattern(handler, pid, name=f"Pat {i}", type_="database")

    result = await handler.handle_tool_call(
        "query_architectural_patterns",
        {"project_id": pid, "output_format": "json", "limit": 2},
    )
    data = json.loads(result[0].text)
    assert len(data) == 2


@pytest.mark.asyncio
async def test_query_offset(setup):
    handler, db, pid = setup
    for i in range(5):
        await _create_pattern(handler, pid, name=f"Pat {i}", type_="database")

    result = await handler.handle_tool_call(
        "query_architectural_patterns",
        {"project_id": pid, "output_format": "json", "offset": 3},
    )
    data = json.loads(result[0].text)
    assert len(data) == 2  # 5 total - 3 skipped


@pytest.mark.asyncio
async def test_query_no_results(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "query_architectural_patterns", {"type": "observability"}
    )
    text = result[0].text
    assert "No architectural patterns found" in text


# =============================================================================
#  get_architectural_overview
# =============================================================================


@pytest.mark.asyncio
async def test_overview_basic(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="DB Adapter", type_="database", description="Decouples storage")
    await _create_adr(db, pid, "ADR-0001", title="SQLite Pool", decision="SQLite provides sufficient concurrency")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    assert "Architectural Overview" in text
    assert "DB Adapter" in text
    assert "PAT-0001" in text
    assert "ADR-0001" in text
    assert "SQLite Pool" in text


@pytest.mark.asyncio
async def test_overview_establishes_refines_follows_ordering(setup):
    """ADRs should appear in order: establishes -> refines -> follows."""
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Follower ADR")
    await _create_adr(db, pid, "ADR-0002", title="Establishing ADR")
    await _create_adr(db, pid, "ADR-0003", title="Refining ADR")

    await _link(handler, "ADR-0001", "PAT-0001", role="follows")
    await _link(handler, "ADR-0002", "PAT-0001", role="establishes")
    await _link(handler, "ADR-0003", "PAT-0001", role="refines")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid, "include_followers": True}
    )
    text = result[0].text

    # Establishes should appear before Refines, which should appear before Follows
    est_pos = text.index("Established by")
    ref_pos = text.index("Refined by")
    fol_pos = text.index("Followed by")
    assert est_pos < ref_pos < fol_pos


@pytest.mark.asyncio
async def test_overview_type_filter(setup):
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="DB Pattern", type_="database")
    await _create_pattern(handler, pid, name="API Pattern", type_="api")
    await _create_adr(db, pid, "ADR-0001", title="DB ADR")
    await _create_adr(db, pid, "ADR-0002", title="API ADR")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")
    await _link(handler, "ADR-0002", "PAT-0002", role="establishes")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid, "type": "database"}
    )
    text = result[0].text
    assert "DB Pattern" in text
    assert "API Pattern" not in text
    assert "[database]" in text


@pytest.mark.asyncio
async def test_overview_include_followers_false_hides_follows(setup):
    """By default (include_followers=false), ADRs with role 'follows' should not appear."""
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Establishing ADR")
    await _create_adr(db, pid, "ADR-0002", title="Following ADR")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")
    await _link(handler, "ADR-0002", "PAT-0001", role="follows")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    assert "Establishing ADR" in text
    assert "Following ADR" not in text
    assert "Followed by" not in text


@pytest.mark.asyncio
async def test_overview_include_followers_true_shows_follows(setup):
    """When include_followers=true, ADRs with role 'follows' should appear."""
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Establishing ADR")
    await _create_adr(db, pid, "ADR-0002", title="Following ADR")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")
    await _link(handler, "ADR-0002", "PAT-0001", role="follows")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid, "include_followers": True}
    )
    text = result[0].text
    assert "Establishing ADR" in text
    assert "Following ADR" in text
    assert "Followed by" in text


@pytest.mark.asyncio
async def test_overview_uncategorised_section(setup):
    """ADRs with no adr_patterns link should appear in the Uncategorised section."""
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Linked ADR")
    await _create_adr(db, pid, "ADR-0002", title="Orphan ADR", decision="Orphan decision text")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    assert "Uncategorised" in text
    assert "ADR-0002" in text
    assert "Orphan ADR" in text


@pytest.mark.asyncio
async def test_overview_excludes_archived_adrs(setup):
    """Archived ADRs should not appear in the overview."""
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Active ADR")
    await _create_adr(db, pid, "ADR-0002", title="Archived ADR")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")
    await _link(handler, "ADR-0002", "PAT-0001", role="follows")

    # Archive ADR-0002
    await db.execute_query(
        "UPDATE architecture SET is_archived = 1 WHERE id = 'ADR-0002'"
    )

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid, "include_followers": True}
    )
    text = result[0].text
    assert "Active ADR" in text
    assert "Archived ADR" not in text


@pytest.mark.asyncio
async def test_overview_excludes_deprecated_adrs(setup):
    """Deprecated ADRs should not appear in the overview."""
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Active ADR", status="Accepted")
    await _create_adr(db, pid, "ADR-0002", title="Deprecated ADR", status="Deprecated")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")
    await _link(handler, "ADR-0002", "PAT-0001", role="follows")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid, "include_followers": True}
    )
    text = result[0].text
    assert "Active ADR" in text
    assert "Deprecated ADR" not in text


@pytest.mark.asyncio
async def test_overview_deprecated_excluded_from_uncategorised(setup):
    """Deprecated ADRs should not appear in the uncategorised section either."""
    handler, db, pid = setup
    await _create_adr(db, pid, "ADR-0001", title="Deprecated Orphan", status="Deprecated")
    await _create_adr(db, pid, "ADR-0002", title="Active Orphan", status="Under Review")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    assert "Active Orphan" in text
    assert "Deprecated Orphan" not in text


@pytest.mark.asyncio
async def test_overview_archived_excluded_from_uncategorised(setup):
    """Archived ADRs should not appear in the uncategorised section."""
    handler, db, pid = setup
    await _create_adr(db, pid, "ADR-0001", title="Archived Orphan")
    await db.execute_query(
        "UPDATE architecture SET is_archived = 1 WHERE id = 'ADR-0001'"
    )
    await _create_adr(db, pid, "ADR-0002", title="Active Orphan")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    assert "Active Orphan" in text
    assert "Archived Orphan" not in text


@pytest.mark.asyncio
async def test_overview_decision_preview_truncated(setup):
    """Decision text should be truncated to 300 chars."""
    handler, db, pid = setup
    long_decision = "A" * 500
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Long ADR", decision=long_decision)
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    # Should contain at most 300 A's in the decision preview
    assert "A" * 300 in text
    assert "A" * 301 not in text


@pytest.mark.asyncio
async def test_overview_validates_project_exists(setup):
    handler, db, pid = setup
    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": "PROJ-9999"}
    )
    assert "ERROR" in result[0].text
    assert "PROJ-9999" in result[0].text


@pytest.mark.asyncio
async def test_overview_no_patterns(setup):
    """When no patterns exist, should still show uncategorised ADRs."""
    handler, db, pid = setup
    await _create_adr(db, pid, "ADR-0001", title="Solo ADR")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    assert "No architectural patterns found" in text
    assert "Uncategorised" in text
    assert "Solo ADR" in text


@pytest.mark.asyncio
async def test_overview_no_uncategorised_when_all_linked(setup):
    """When all ADRs are linked to patterns, Uncategorised section should not appear."""
    handler, db, pid = setup
    await _create_pattern(handler, pid, name="Pat1", type_="database")
    await _create_adr(db, pid, "ADR-0001", title="Linked ADR")
    await _link(handler, "ADR-0001", "PAT-0001", role="establishes")

    result = await handler.handle_tool_call(
        "get_architectural_overview", {"project_id": pid}
    )
    text = result[0].text
    assert "Uncategorised" not in text


# =============================================================================
#  Handle unknown tool
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_tool(setup):
    handler, _, _ = setup
    result = await handler.handle_tool_call("nonexistent_tool", {})
    assert "ERROR" in result[0].text or "Unknown" in result[0].text
