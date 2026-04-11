"""Tests for DatabaseManager v2.

Validates async initialization, schema application, ID generation,
FK enforcement, pool management, and CRUD helpers against the v2 schema.
"""

import asyncio

import pytest

from lifecycle_mcp.database_manager import DatabaseManager


# ── Initialization ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_creates_schema(tmp_path):
    """Fresh init creates all v2 tables."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        result = await db.execute_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            fetch_all=True,
            row_factory=True,
        )
        table_names = [r["name"] for r in result]
        for t in [
            "sequences",
            "projects",
            "requirements",
            "tasks",
            "architecture",
            "relationships",
            "reviews",
            "lifecycle_events",
        ]:
            assert t in table_names, f"Table '{t}' not created by initialize()"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_initialize_skips_existing_schema(tmp_path):
    """Second init doesn't re-apply schema (data survives)."""
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    await db.initialize()
    # Insert a project
    await db.execute_query(
        "INSERT INTO projects (id, name) VALUES ('PROJ-0001', 'Test')"
    )
    await db.close()

    # Re-initialize with new manager
    db2 = DatabaseManager(db_path)
    await db2.initialize()
    try:
        result = await db2.execute_query(
            "SELECT COUNT(*) as cnt FROM projects",
            fetch_one=True,
            row_factory=True,
        )
        assert result["cnt"] == 1
    finally:
        await db2.close()


@pytest.mark.asyncio
async def test_initialize_creates_directory(tmp_path):
    """initialize() creates the parent directory if it doesn't exist."""
    db_path = str(tmp_path / "subdir" / "deep" / "test.db")
    db = DatabaseManager(db_path)
    await db.initialize()
    try:
        result = await db.execute_query("SELECT 1", fetch_one=True)
        assert result[0] == 1
    finally:
        await db.close()


# ── ID generation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_id_sequential(tmp_path):
    """generate_id returns sequential IDs."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        id1, n1 = await db.generate_id("requirement")
        id2, n2 = await db.generate_id("requirement")
        assert id1 == "REQ-0001"
        assert id2 == "REQ-0002"
        assert n1 == 1
        assert n2 == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_generate_id_different_types(tmp_path):
    """generate_id sequences are independent per entity type."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        req_id, req_n = await db.generate_id("requirement")
        task_id, task_n = await db.generate_id("task")
        adr_id, adr_n = await db.generate_id("architecture")
        proj_id, proj_n = await db.generate_id("project")

        assert req_id == "REQ-0001" and req_n == 1
        assert task_id == "TASK-0001" and task_n == 1
        assert adr_id == "ADR-0001" and adr_n == 1
        assert proj_id == "PROJ-0001" and proj_n == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_generate_id_format_padding(tmp_path):
    """generate_id pads numbers to 4 digits."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        # Generate 10 IDs to check padding
        for i in range(9):
            await db.generate_id("task")
        id10, n10 = await db.generate_id("task")
        assert id10 == "TASK-0010"
        assert n10 == 10
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_generate_id_concurrent_no_duplicates(tmp_path):
    """Concurrent generate_id produces no duplicates."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        results = await asyncio.gather(
            *[db.generate_id("task") for _ in range(20)]
        )
        ids = [r[0] for r in results]
        assert len(set(ids)) == 20, f"Got duplicates: {ids}"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_generate_id_invalid_type(tmp_path):
    """generate_id raises KeyError for unknown entity type."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        with pytest.raises(KeyError):
            await db.generate_id("nonexistent")
    finally:
        await db.close()


# ── Foreign key enforcement ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_foreign_keys_enabled(tmp_path):
    """PRAGMA foreign_keys returns 1 on connection checkout."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        result = await db.execute_query("PRAGMA foreign_keys", fetch_one=True)
        # result could be tuple or Row, check both forms
        fk_val = result[0] if isinstance(result, tuple) else result["foreign_keys"]
        assert fk_val == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fk_enforcement_via_manager(tmp_path):
    """DatabaseManager rejects inserts that violate FK constraints."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        with pytest.raises(Exception):  # IntegrityError
            await db.execute_query(
                "INSERT INTO requirements (id, project_id, type, title, priority) "
                "VALUES (?, ?, ?, ?, ?)",
                ["REQ-0001", "PROJ-9999", "FUNC", "Test", "P1"],
            )
    finally:
        await db.close()


# ── Pool management ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pool_checkout_return(tmp_path):
    """Connection can be checked out and returned."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        async with db.get_connection() as conn:
            result = await conn.execute("SELECT 1")
            row = await result.fetchone()
            assert row[0] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pool_stats(tmp_path):
    """get_pool_stats returns correct info."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        stats = await db.get_pool_stats()
        assert stats["pooling_enabled"] is True
        assert stats["pool_size"] == 5
        assert stats["initialized"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_test_connection(tmp_path):
    """test_connection returns success status."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        result = await db.test_connection()
        assert result["status"] == "success"
        assert "response_time_ms" in result
    finally:
        await db.close()


# ── CRUD helpers ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_record(tmp_path):
    """insert_record inserts and returns lastrowid."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.insert_record("projects", {"id": "PROJ-0001", "name": "Test Project"})
        result = await db.execute_query(
            "SELECT name FROM projects WHERE id = 'PROJ-0001'",
            fetch_one=True,
            row_factory=True,
        )
        assert result["name"] == "Test Project"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_update_record(tmp_path):
    """update_record modifies existing data."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.insert_record("projects", {"id": "PROJ-0001", "name": "Original"})
        await db.update_record("projects", {"name": "Updated"}, "id = ?", ["PROJ-0001"])
        result = await db.execute_query(
            "SELECT name FROM projects WHERE id = 'PROJ-0001'",
            fetch_one=True,
            row_factory=True,
        )
        assert result["name"] == "Updated"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_delete_record(tmp_path):
    """delete_record removes data."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.insert_record("projects", {"id": "PROJ-0001", "name": "Test"})
        await db.delete_record("projects", "id = ?", ["PROJ-0001"])
        exists = await db.check_exists("projects", "id = ?", ["PROJ-0001"])
        assert not exists
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_get_records(tmp_path):
    """get_records returns filtered, ordered results."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.insert_record("projects", {"id": "PROJ-0001", "name": "Alpha"})
        await db.insert_record("projects", {"id": "PROJ-0002", "name": "Beta"})
        await db.insert_record("projects", {"id": "PROJ-0003", "name": "Charlie"})

        records = await db.get_records(
            "projects", "id, name", "status = ?", ["active"], "name"
        )
        assert len(records) == 3
        assert records[0]["name"] == "Alpha"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_check_exists(tmp_path):
    """check_exists returns True/False correctly."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        assert not await db.check_exists("projects", "id = ?", ["PROJ-0001"])
        await db.insert_record("projects", {"id": "PROJ-0001", "name": "Test"})
        assert await db.check_exists("projects", "id = ?", ["PROJ-0001"])
    finally:
        await db.close()


# ── Transaction ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transaction_success(tmp_path):
    """Successful transaction commits data."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO projects (id, name) VALUES (?, ?)",
                ("PROJ-0001", "Test"),
            )

        result = await db.execute_query(
            "SELECT name FROM projects WHERE id = 'PROJ-0001'",
            fetch_one=True,
        )
        assert result[0] == "Test"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_transaction_rollback(tmp_path):
    """Failed transaction rolls back data."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    try:
        with pytest.raises(ValueError):
            async with db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO projects (id, name) VALUES (?, ?)",
                    ("PROJ-0001", "Test"),
                )
                raise ValueError("Force rollback")

        exists = await db.check_exists("projects", "id = ?", ["PROJ-0001"])
        assert not exists
    finally:
        await db.close()


# ── Constructor no longer touches disk ──────────────────────────────


@pytest.mark.asyncio
async def test_constructor_does_not_create_db(tmp_path):
    """DatabaseManager constructor should NOT create the DB file (initialize does)."""
    import os
    db_path = str(tmp_path / "lazy.db")
    _db = DatabaseManager(db_path)  # noqa: F841
    assert not os.path.exists(db_path), "Constructor should not create DB file"
