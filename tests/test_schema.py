"""Tests for v2 database schema.

Validates the new clean-sheet schema: tables, views, triggers, indexes,
foreign key enforcement, and sequences initialization.
"""

import pytest
import aiosqlite
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "lifecycle_mcp" / "lifecycle-schema-v2.sql"

EXPECTED_TABLES = [
    "sequences",
    "projects",
    "requirements",
    "tasks",
    "architecture",
    "architectural_patterns",
    "adr_patterns",
    "relationships",
    "reviews",
    "lifecycle_events",
]

EXPECTED_VIEWS = [
    "project_summary",
    "task_hierarchy",
]

EXPECTED_INDEXES = [
    "idx_relationships_source",
    "idx_relationships_target",
    "idx_relationships_project",
    "idx_relationships_type",
    "idx_requirements_status",
    "idx_requirements_priority",
    "idx_requirements_project",
    "idx_tasks_status",
    "idx_tasks_project",
    "idx_architecture_project",
    "idx_adr_patterns_adr",
    "idx_adr_patterns_pattern",
    "idx_adr_patterns_role",
    "idx_architectural_patterns_project",
    "idx_architectural_patterns_type",
]


async def _init_db(tmp_path):
    """Helper: create a fresh DB with the v2 schema and FK enforcement."""
    db_path = str(tmp_path / "test.db")
    schema = SCHEMA_PATH.read_text()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.executescript(schema)
    return db_path


async def _connect(db_path):
    """Helper: open a connection with FK enforcement."""
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = aiosqlite.Row
    return conn


# ── Table/View/Index existence ──────────────────────────────────────


@pytest.mark.asyncio
async def test_schema_creates_all_tables(tmp_path):
    """All expected tables are created."""
    db_path = await _init_db(tmp_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        rows = await cursor.fetchall()
        table_names = [r[0] for r in rows]
        for t in EXPECTED_TABLES:
            assert t in table_names, f"Table '{t}' missing from schema"


@pytest.mark.asyncio
async def test_views_created(tmp_path):
    """All expected views are created."""
    db_path = await _init_db(tmp_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
        )
        rows = await cursor.fetchall()
        view_names = [r[0] for r in rows]
        for v in EXPECTED_VIEWS:
            assert v in view_names, f"View '{v}' missing from schema"


@pytest.mark.asyncio
async def test_indexes_created(tmp_path):
    """All expected indexes are created."""
    db_path = await _init_db(tmp_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        rows = await cursor.fetchall()
        index_names = [r[0] for r in rows]
        for idx in EXPECTED_INDEXES:
            assert idx in index_names, f"Index '{idx}' missing from schema"


# ── Sequences ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sequences_initialized(tmp_path):
    """All 5 entity types have sequence entries starting at 1."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        cursor = await conn.execute("SELECT entity_type, next_val FROM sequences ORDER BY entity_type")
        rows = await cursor.fetchall()
        seq_map = {r["entity_type"]: r["next_val"] for r in rows}
        assert seq_map == {
            "architectural_pattern": 1,
            "architecture": 1,
            "project": 1,
            "requirement": 1,
            "task": 1,
        }
    finally:
        await conn.close()


# ── Foreign Key enforcement ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_fk_enforcement_rejects_bad_project_id(tmp_path):
    """Insert requirement with nonexistent project_id should fail."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO requirements (id, project_id, type, title, priority) "
                "VALUES (?, ?, ?, ?, ?)",
                ("REQ-0001", "PROJ-9999", "FUNC", "Test", "P1"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fk_enforcement_task_requires_project(tmp_path):
    """Insert task with nonexistent project_id should fail."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
                ("TASK-0001", "PROJ-9999", "Test Task", "P1"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fk_enforcement_architecture_requires_project(tmp_path):
    """Insert architecture decision with nonexistent project_id should fail."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
                ("ADR-0001", "PROJ-9999", "Test ADR"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fk_allows_valid_project_id(tmp_path):
    """Insert requirement with existing project_id should succeed."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)",
            ("PROJ-0001", "Test Project"),
        )
        await conn.execute(
            "INSERT INTO requirements (id, project_id, type, title, priority) "
            "VALUES (?, ?, ?, ?, ?)",
            ("REQ-0001", "PROJ-0001", "FUNC", "Test Req", "P1"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT COUNT(*) FROM requirements")
        row = await cursor.fetchone()
        assert row[0] == 1
    finally:
        await conn.close()


# ── CHECK constraints ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_requirement_type_check_constraint(tmp_path):
    """Requirement type must be one of the allowed values."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO requirements (id, project_id, type, title, priority) "
                "VALUES (?, ?, ?, ?, ?)",
                ("REQ-0001", "PROJ-0001", "INVALID", "Test", "P1"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_task_status_check_constraint(tmp_path):
    """Task status must be one of the allowed values."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO tasks (id, project_id, title, priority, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("TASK-0001", "PROJ-0001", "Test", "P1", "BadStatus"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_relationship_type_check_constraint(tmp_path):
    """Relationship type must be one of the allowed values."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO relationships (id, source_type, source_id, target_type, "
                "target_id, relationship_type) VALUES (?, ?, ?, ?, ?, ?)",
                ("REL-1", "task", "TASK-0001", "task", "TASK-0002", "INVALID"),
            )
            await conn.commit()
    finally:
        await conn.close()


# ── Triggers: updated_at ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_updated_at_trigger_projects(tmp_path):
    """updated_at is auto-set on project update."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.commit()

        cursor = await conn.execute("SELECT updated_at FROM projects WHERE id = 'PROJ-0001'")
        row = await cursor.fetchone()
        original_updated = row["updated_at"]

        # Small delay then update
        import asyncio
        await asyncio.sleep(1.1)

        await conn.execute(
            "UPDATE projects SET name = 'Updated' WHERE id = 'PROJ-0001'"
        )
        await conn.commit()

        cursor = await conn.execute("SELECT updated_at FROM projects WHERE id = 'PROJ-0001'")
        row = await cursor.fetchone()
        new_updated = row["updated_at"]

        assert new_updated > original_updated
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_updated_at_trigger_requirements(tmp_path):
    """updated_at is auto-set on requirement update."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO requirements (id, project_id, type, title, priority) "
            "VALUES (?, ?, ?, ?, ?)",
            ("REQ-0001", "PROJ-0001", "FUNC", "Test", "P1"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT updated_at FROM requirements WHERE id = 'REQ-0001'")
        row = await cursor.fetchone()
        original_updated = row["updated_at"]

        import asyncio
        await asyncio.sleep(1.1)

        await conn.execute(
            "UPDATE requirements SET title = 'Updated' WHERE id = 'REQ-0001'"
        )
        await conn.commit()

        cursor = await conn.execute("SELECT updated_at FROM requirements WHERE id = 'REQ-0001'")
        row = await cursor.fetchone()
        assert row["updated_at"] > original_updated
    finally:
        await conn.close()


# ── Triggers: status change logging ─────────────────────────────────


@pytest.mark.asyncio
async def test_status_change_trigger_logs_event(tmp_path):
    """Requirement status change creates lifecycle_event."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO requirements (id, project_id, type, title, priority) "
            "VALUES (?, ?, ?, ?, ?)",
            ("REQ-0001", "PROJ-0001", "FUNC", "Test", "P1"),
        )
        await conn.commit()

        await conn.execute(
            "UPDATE requirements SET status = 'Approved' WHERE id = 'REQ-0001'"
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT * FROM lifecycle_events WHERE entity_id = 'REQ-0001'"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        event = rows[0]
        assert event["entity_type"] == "requirement"
        assert event["event_type"] == "status_change"
        assert event["from_value"] == "Under Review"
        assert event["to_value"] == "Approved"
        assert event["project_id"] == "PROJ-0001"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_task_status_change_trigger_logs_event(tmp_path):
    """Task status change creates lifecycle_event."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ("TASK-0001", "PROJ-0001", "Test Task", "P1"),
        )
        await conn.commit()

        await conn.execute(
            "UPDATE tasks SET status = 'Approved' WHERE id = 'TASK-0001'"
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT * FROM lifecycle_events WHERE entity_id = 'TASK-0001'"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        event = rows[0]
        assert event["entity_type"] == "task"
        assert event["from_value"] == "Under Review"
        assert event["to_value"] == "Approved"
    finally:
        await conn.close()


# ── Triggers: completed_at ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_completed_at_trigger(tmp_path):
    """Task completed_at is set when status changes to Validated, cleared otherwise."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ("TASK-0001", "PROJ-0001", "Test Task", "P1"),
        )
        await conn.commit()

        # Initially, completed_at should be NULL
        cursor = await conn.execute("SELECT completed_at FROM tasks WHERE id = 'TASK-0001'")
        row = await cursor.fetchone()
        assert row["completed_at"] is None

        # Mark as Validated (triggers completed_at)
        await conn.execute(
            "UPDATE tasks SET status = 'Validated' WHERE id = 'TASK-0001'"
        )
        await conn.commit()

        cursor = await conn.execute("SELECT completed_at FROM tasks WHERE id = 'TASK-0001'")
        row = await cursor.fetchone()
        assert row["completed_at"] is not None

        # Un-complete (e.g. back to Approved)
        await conn.execute(
            "UPDATE tasks SET status = 'Approved' WHERE id = 'TASK-0001'"
        )
        await conn.commit()

        cursor = await conn.execute("SELECT completed_at FROM tasks WHERE id = 'TASK-0001'")
        row = await cursor.fetchone()
        assert row["completed_at"] is None
    finally:
        await conn.close()


# ── Views ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_summary_view(tmp_path):
    """project_summary returns correct counts."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        # Add 2 requirements
        await conn.execute(
            "INSERT INTO requirements (id, project_id, type, title, priority) "
            "VALUES (?, ?, ?, ?, ?)",
            ("REQ-0001", "PROJ-0001", "FUNC", "Req 1", "P1"),
        )
        await conn.execute(
            "INSERT INTO requirements (id, project_id, type, title, priority) "
            "VALUES (?, ?, ?, ?, ?)",
            ("REQ-0002", "PROJ-0001", "TECH", "Req 2", "P2"),
        )
        # Add 3 tasks, 1 complete
        await conn.execute(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ("TASK-0001", "PROJ-0001", "Task 1", "P1"),
        )
        await conn.execute(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ("TASK-0002", "PROJ-0001", "Task 2", "P1"),
        )
        await conn.execute(
            "INSERT INTO tasks (id, project_id, title, priority, status) VALUES (?, ?, ?, ?, ?)",
            ("TASK-0003", "PROJ-0001", "Task 3", "P1", "Validated"),
        )
        # Add 1 ADR
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
            ("ADR-0001", "PROJ-0001", "ADR 1"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT * FROM project_summary WHERE id = 'PROJ-0001'")
        row = await cursor.fetchone()
        assert row["requirement_count"] == 2
        assert row["task_count"] == 3
        assert row["tasks_completed"] == 1
        assert row["adr_count"] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_project_summary_excludes_archived(tmp_path):
    """project_summary excludes archived entities."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO requirements (id, project_id, type, title, priority) "
            "VALUES (?, ?, ?, ?, ?)",
            ("REQ-0001", "PROJ-0001", "FUNC", "Active", "P1"),
        )
        await conn.execute(
            "INSERT INTO requirements (id, project_id, type, title, priority, is_archived) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("REQ-0002", "PROJ-0001", "FUNC", "Archived", "P1", 1),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT * FROM project_summary WHERE id = 'PROJ-0001'")
        row = await cursor.fetchone()
        assert row["requirement_count"] == 1  # Only non-archived
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_task_hierarchy_view(tmp_path):
    """task_hierarchy shows correct depth for parent/child tasks."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO tasks (id, project_id, title, priority) VALUES (?, ?, ?, ?)",
            ("TASK-0001", "PROJ-0001", "Parent", "P1"),
        )
        await conn.execute(
            "INSERT INTO tasks (id, project_id, title, priority, parent_task_id) VALUES (?, ?, ?, ?, ?)",
            ("TASK-0002", "PROJ-0001", "Child", "P1", "TASK-0001"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT * FROM task_hierarchy ORDER BY depth")
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0]["depth"] == 0
        assert rows[0]["id"] == "TASK-0001"
        assert rows[1]["depth"] == 1
        assert rows[1]["id"] == "TASK-0002"
    finally:
        await conn.close()


# ── Soft delete (is_archived) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_is_archived_default_zero(tmp_path):
    """New entities have is_archived = 0 by default."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.commit()

        cursor = await conn.execute("SELECT is_archived FROM projects WHERE id = 'PROJ-0001'")
        row = await cursor.fetchone()
        assert row["is_archived"] == 0
    finally:
        await conn.close()


# ── Unique constraints ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_relationship_unique_constraint(tmp_path):
    """Duplicate (source_id, target_id, relationship_type) should fail."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, "
            "relationship_type) VALUES (?, ?, ?, ?, ?, ?)",
            ("REL-1", "task", "TASK-0001", "task", "TASK-0002", "blocks"),
        )
        await conn.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO relationships (id, source_type, source_id, target_type, target_id, "
                "relationship_type) VALUES (?, ?, ?, ?, ?, ?)",
                ("REL-2", "task", "TASK-0001", "task", "TASK-0002", "blocks"),
            )
            await conn.commit()
    finally:
        await conn.close()


# ── Architecture: no Superseded status ──────────────────────────────


@pytest.mark.asyncio
async def test_architecture_no_superseded_status(tmp_path):
    """Architecture status should not accept 'Superseded' -- use superseded_by FK instead."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO architecture (id, project_id, title, status) VALUES (?, ?, ?, ?)",
                ("ADR-0001", "PROJ-0001", "Test", "Superseded"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_architecture_superseded_by_fk(tmp_path):
    """superseded_by should reference another architecture row."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
            ("ADR-0001", "PROJ-0001", "Original"),
        )
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title, superseded_by) VALUES (?, ?, ?, ?)",
            ("ADR-0002", "PROJ-0001", "Replacement", "ADR-0001"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT superseded_by FROM architecture WHERE id = 'ADR-0002'")
        row = await cursor.fetchone()
        assert row["superseded_by"] == "ADR-0001"
    finally:
        await conn.close()


# ── Architectural Patterns table ──────────────────────────────────


@pytest.mark.asyncio
async def test_architectural_patterns_table_exists(tmp_path):
    """architectural_patterns table with all columns and CHECK constraints."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architectural_patterns (id, project_id, name, type) VALUES (?, ?, ?, ?)",
            ("PAT-0001", "PROJ-0001", "Event Sourcing", "database"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT * FROM architectural_patterns WHERE id = 'PAT-0001'")
        row = await cursor.fetchone()
        assert row["name"] == "Event Sourcing"
        assert row["type"] == "database"
        assert row["project_id"] == "PROJ-0001"
        assert row["is_archived"] == 0
        assert row["created_at"] is not None
        assert row["updated_at"] is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_architectural_patterns_type_check_constraint(tmp_path):
    """architectural_patterns type must be one of the allowed values."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO architectural_patterns (id, project_id, name, type) VALUES (?, ?, ?, ?)",
                ("PAT-0001", "PROJ-0001", "Bad Pattern", "INVALID_TYPE"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_architectural_patterns_fk_requires_project(tmp_path):
    """architectural_patterns project_id must reference an existing project."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO architectural_patterns (id, project_id, name, type) VALUES (?, ?, ?, ?)",
                ("PAT-0001", "PROJ-9999", "Bad Pattern", "api"),
            )
            await conn.commit()
    finally:
        await conn.close()


# ── ADR Patterns table ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adr_patterns_table_exists(tmp_path):
    """adr_patterns table with composite PK, FKs, role CHECK, default role."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
            ("ADR-0001", "PROJ-0001", "Test ADR"),
        )
        await conn.execute(
            "INSERT INTO architectural_patterns (id, project_id, name, type) VALUES (?, ?, ?, ?)",
            ("PAT-0001", "PROJ-0001", "Test Pattern", "api"),
        )
        # Insert with default role
        await conn.execute(
            "INSERT INTO adr_patterns (adr_id, pattern_id) VALUES (?, ?)",
            ("ADR-0001", "PAT-0001"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT * FROM adr_patterns WHERE adr_id = 'ADR-0001'")
        row = await cursor.fetchone()
        assert row["adr_id"] == "ADR-0001"
        assert row["pattern_id"] == "PAT-0001"
        assert row["role"] == "follows"  # default
        assert row["created_at"] is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_adr_patterns_role_check_constraint(tmp_path):
    """adr_patterns role must be establishes, follows, or refines."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
            ("ADR-0001", "PROJ-0001", "Test ADR"),
        )
        await conn.execute(
            "INSERT INTO architectural_patterns (id, project_id, name, type) VALUES (?, ?, ?, ?)",
            ("PAT-0001", "PROJ-0001", "Test Pattern", "api"),
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO adr_patterns (adr_id, pattern_id, role) VALUES (?, ?, ?)",
                ("ADR-0001", "PAT-0001", "INVALID_ROLE"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_adr_patterns_composite_pk(tmp_path):
    """Duplicate (adr_id, pattern_id) should fail."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
            ("ADR-0001", "PROJ-0001", "Test ADR"),
        )
        await conn.execute(
            "INSERT INTO architectural_patterns (id, project_id, name, type) VALUES (?, ?, ?, ?)",
            ("PAT-0001", "PROJ-0001", "Test Pattern", "api"),
        )
        await conn.execute(
            "INSERT INTO adr_patterns (adr_id, pattern_id) VALUES (?, ?)",
            ("ADR-0001", "PAT-0001"),
        )
        await conn.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO adr_patterns (adr_id, pattern_id, role) VALUES (?, ?, ?)",
                ("ADR-0001", "PAT-0001", "refines"),
            )
            await conn.commit()
    finally:
        await conn.close()


# ── Architecture status CHECK (simplified) ────────────────────────


@pytest.mark.asyncio
async def test_architecture_rejects_draft_status(tmp_path):
    """Architecture status CHECK no longer allows 'Draft'."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO architecture (id, project_id, title, status) VALUES (?, ?, ?, ?)",
                ("ADR-0001", "PROJ-0001", "Test", "Draft"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_architecture_rejects_approved_status(tmp_path):
    """Architecture status CHECK no longer allows 'Approved'."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO architecture (id, project_id, title, status) VALUES (?, ?, ?, ?)",
                ("ADR-0001", "PROJ-0001", "Test", "Approved"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_architecture_rejects_implemented_status(tmp_path):
    """Architecture status CHECK no longer allows 'Implemented'."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO architecture (id, project_id, title, status) VALUES (?, ?, ?, ?)",
                ("ADR-0001", "PROJ-0001", "Test", "Implemented"),
            )
            await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_architecture_default_status_is_under_review(tmp_path):
    """Architecture default status should be 'Under Review'."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
            ("ADR-0001", "PROJ-0001", "Test ADR"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT status FROM architecture WHERE id = 'ADR-0001'")
        row = await cursor.fetchone()
        assert row["status"] == "Under Review"
    finally:
        await conn.close()


# ── Auto-archive trigger for Deprecated ADRs ──────────────────────


@pytest.mark.asyncio
async def test_auto_archive_deprecated_adr(tmp_path):
    """Setting architecture status to Deprecated auto-sets is_archived=1 and archived_at."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architecture (id, project_id, title) VALUES (?, ?, ?)",
            ("ADR-0001", "PROJ-0001", "Test ADR"),
        )
        await conn.commit()

        # Initially not archived
        cursor = await conn.execute("SELECT is_archived, archived_at FROM architecture WHERE id = 'ADR-0001'")
        row = await cursor.fetchone()
        assert row["is_archived"] == 0
        assert row["archived_at"] is None

        # Set to Deprecated
        await conn.execute(
            "UPDATE architecture SET status = 'Deprecated' WHERE id = 'ADR-0001'"
        )
        await conn.commit()

        cursor = await conn.execute("SELECT is_archived, archived_at FROM architecture WHERE id = 'ADR-0001'")
        row = await cursor.fetchone()
        assert row["is_archived"] == 1
        assert row["archived_at"] is not None
    finally:
        await conn.close()


# ── Architectural patterns updated_at trigger ─────────────────────


@pytest.mark.asyncio
async def test_architectural_patterns_updated_at_trigger(tmp_path):
    """updated_at is auto-set on architectural_patterns update."""
    db_path = await _init_db(tmp_path)
    conn = await _connect(db_path)
    try:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)", ("PROJ-0001", "Test")
        )
        await conn.execute(
            "INSERT INTO architectural_patterns (id, project_id, name, type) VALUES (?, ?, ?, ?)",
            ("PAT-0001", "PROJ-0001", "Test Pattern", "api"),
        )
        await conn.commit()

        cursor = await conn.execute("SELECT updated_at FROM architectural_patterns WHERE id = 'PAT-0001'")
        row = await cursor.fetchone()
        original_updated = row["updated_at"]

        import asyncio
        await asyncio.sleep(1.1)

        await conn.execute(
            "UPDATE architectural_patterns SET name = 'Updated Pattern' WHERE id = 'PAT-0001'"
        )
        await conn.commit()

        cursor = await conn.execute("SELECT updated_at FROM architectural_patterns WHERE id = 'PAT-0001'")
        row = await cursor.fetchone()
        assert row["updated_at"] > original_updated
    finally:
        await conn.close()


# ── generate_id for architectural_pattern ─────────────────────────


@pytest.mark.asyncio
async def test_generate_id_architectural_pattern(v2_db_manager):
    """generate_id('architectural_pattern') returns PAT-XXXX format."""
    pat_id, number = await v2_db_manager.generate_id("architectural_pattern")
    assert pat_id == "PAT-0001"
    assert number == 1

    pat_id2, number2 = await v2_db_manager.generate_id("architectural_pattern")
    assert pat_id2 == "PAT-0002"
    assert number2 == 2
