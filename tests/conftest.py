"""
Test configuration and fixtures for MCP Lifecycle Management Server
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

import pytest

from lifecycle_mcp.database_manager import DatabaseManager
from lifecycle_mcp.handlers import RequirementHandler

# Configure pytest-asyncio to avoid deprecation warnings
pytest_plugins = ("pytest_asyncio",)

# Configure logging for tests
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Path to v2 schema
V2_SCHEMA_PATH = Path(__file__).parent.parent / "src" / "lifecycle_mcp" / "lifecycle-schema-v2.sql"


def pytest_configure(config):
    """Configure pytest with asyncio settings"""
    config.option.asyncio_default_fixture_loop_scope = "function"


@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for each test function."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ------------------------------------------------------------------
# v2 schema fixtures (used by new test_schema.py, test_database_manager.py)
# ------------------------------------------------------------------


@pytest.fixture
async def v2_db_manager(tmp_path):
    """Create a DatabaseManager with v2 schema for testing."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    yield db
    await db.close()


# ------------------------------------------------------------------
# Shared fixtures
# ------------------------------------------------------------------


@pytest.fixture
def temp_db():
    """Create a temporary database with the v2 schema applied."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    if V2_SCHEMA_PATH.exists():
        import sqlite3

        conn = sqlite3.connect(db_path)
        try:
            with open(V2_SCHEMA_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())
        finally:
            conn.close()

    yield db_path

    for attempt in range(3):
        try:
            os.unlink(db_path)
            break
        except OSError:
            if attempt < 2:
                time.sleep(0.1)
            else:
                pass


@pytest.fixture
async def db_manager(temp_db):
    """Create an async DatabaseManager instance with temporary database"""
    manager = DatabaseManager(temp_db)
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
def requirement_handler(db_manager):
    """Create a RequirementHandler instance"""
    handler = RequirementHandler(db_manager)
    handler._testing_mode = True  # Disable LLM analysis for tests
    return handler
