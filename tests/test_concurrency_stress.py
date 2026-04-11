"""
Concurrency stress tests for the lifecycle-mcp server (v2).

These tests validate correct behavior under concurrent async workloads
using the v2 schema and handler API (params-dict, project-scoped).
"""

import asyncio
import re
import time
from statistics import median

import pytest

from lifecycle_mcp.handlers.requirement_handler import RequirementHandler

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def req_env(v2_db_manager):
    """Set up RequirementHandler + a test project for stress tests."""
    handler = RequirementHandler(v2_db_manager)
    handler._testing_mode = True
    await v2_db_manager.execute_query(
        "INSERT INTO projects (id, name) VALUES (?, ?)",
        ["PROJ-0001", "Stress Test Project"],
    )
    return handler, v2_db_manager, "PROJ-0001"


def _extract_req_id(result) -> str:
    """Extract REQ-XXXX from a handler response."""
    text = result[0].text
    match = re.search(r"REQ-\d{4}", text)
    assert match, f"Could not extract REQ ID from: {text}"
    return match.group()


class TestConcurrentCreationStorm:
    """Verify that 20 concurrent create_requirement calls all succeed with unique IDs."""

    async def test_20_concurrent_creates_all_unique(self, req_env):
        handler, db, project_id = req_env

        params_list = [
            {
                "project_id": project_id,
                "type": "FUNC",
                "title": f"Concurrent Requirement {i}",
                "priority": "P1",
                "current_state": f"Current state {i}",
                "desired_state": f"Desired state {i}",
            }
            for i in range(20)
        ]

        results = await asyncio.gather(
            *(handler._create_requirement(params) for params in params_list)
        )

        # All 20 should succeed
        assert len(results) == 20
        for r in results:
            assert "SUCCESS" in r[0].text

        # All IDs should be unique
        ids = [_extract_req_id(r) for r in results]
        assert len(set(ids)) == 20, f"Expected 20 unique IDs, got {len(set(ids))}"


class TestMixedReadWriteWorkload:
    """Verify that a mixed concurrent workload completes without errors."""

    async def test_mixed_concurrent_operations(self, req_env):
        handler, db, project_id = req_env

        # Phase 1: create 3 requirements sequentially so we have data to query/update
        seed_ids = []
        for i in range(3):
            result = await handler._create_requirement({
                "project_id": project_id,
                "type": "FUNC",
                "title": f"Seed Requirement {i}",
                "priority": "P1",
                "current_state": f"Current state {i}",
                "desired_state": f"Desired state {i}",
            })
            seed_ids.append(_extract_req_id(result))

        # Phase 2: concurrent mixed workload -- 5 creates + 5 queries + 3 status updates
        create_coros = [
            handler._create_requirement({
                "project_id": project_id,
                "type": "FUNC",
                "title": f"Mixed Create {i}",
                "priority": "P2",
                "current_state": "Current",
                "desired_state": "Desired",
            })
            for i in range(5)
        ]

        query_coros = [
            handler._query_requirements({"search_text": f"Seed Requirement {i % 3}"})
            for i in range(5)
        ]

        update_coros = [
            handler._update_requirement_status({
                "requirement_id": seed_ids[i],
                "new_status": "Approved",
                "comment": f"Concurrent update {i}",
            })
            for i in range(3)
        ]

        results = await asyncio.gather(
            *create_coros, *query_coros, *update_coros,
            return_exceptions=True,
        )

        # Verify no exceptions in the 13 results
        assert len(results) == 13
        for idx, r in enumerate(results):
            assert not isinstance(r, Exception), (
                f"Operation {idx} raised {type(r).__name__}: {r}"
            )
        # Verify creates succeeded
        for r in results[:5]:
            assert "SUCCESS" in r[0].text, f"Expected SUCCESS in create result: {r[0].text}"
        # Verify status updates succeeded
        for r in results[10:]:
            assert "SUCCESS" in r[0].text, f"Expected SUCCESS in update result: {r[0].text}"


class TestLatencyUnderLoad:
    """Verify that query latency stays within acceptable bounds under concurrent load."""

    async def test_p50_and_p95_latency(self, req_env):
        handler, db, project_id = req_env

        # Phase 1: seed 5 requirements sequentially
        for i in range(5):
            await handler._create_requirement({
                "project_id": project_id,
                "type": "FUNC",
                "title": f"Latency Seed {i}",
                "priority": "P1",
                "current_state": "Current",
                "desired_state": "Desired",
            })

        # Phase 2: run 10 concurrent queries, timing each one
        async def timed_query(index: int) -> float:
            start = time.monotonic()
            await handler._query_requirements({"search_text": "Latency Seed"})
            return time.monotonic() - start

        latencies = await asyncio.gather(
            *(timed_query(i) for i in range(10))
        )

        latencies_sorted = sorted(latencies)
        p50 = median(latencies_sorted)
        # p95 index for 10 items: ceil(0.95 * 10) - 1 = 9
        p95 = latencies_sorted[9]

        assert p50 < 1.0, f"p50 latency {p50:.3f}s exceeds 1.0s threshold"
        assert p95 < 5.0, f"p95 latency {p95:.3f}s exceeds 5.0s threshold"
