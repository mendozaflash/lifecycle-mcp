"""
Concurrency stress tests for the lifecycle-mcp server.

These tests validate correct behavior under concurrent async workloads.
They are designed to PASS only after the async database migration (Tasks 8+).
Until then, they will fail because handler methods are not yet fully async.
"""

import asyncio
import re
import time
from statistics import median

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestConcurrentCreationStorm:
    """Verify that 20 concurrent create_requirement calls all succeed with unique IDs."""

    async def test_20_concurrent_creates_all_unique(self, requirement_handler):
        params_list = [
            {
                "type": "FUNC",
                "title": f"Concurrent Requirement {i}",
                "priority": "P1",
                "current_state": f"Current state {i}",
                "desired_state": f"Desired state {i}",
            }
            for i in range(20)
        ]

        results = await asyncio.gather(
            *(
                requirement_handler._create_single_requirement(params)
                for params in params_list
            )
        )

        # No exceptions: all 20 returned successfully
        assert len(results) == 20
        # All IDs are strings (not exceptions)
        for r in results:
            assert isinstance(r, str), f"Expected a requirement ID string, got {type(r)}: {r}"
        # All IDs are unique
        assert len(set(results)) == 20, f"Expected 20 unique IDs, got {len(set(results))}"


class TestMixedReadWriteWorkload:
    """Verify that a mixed concurrent workload of creates, queries, and updates completes without errors."""

    async def test_mixed_concurrent_operations(self, requirement_handler):
        # Phase 1: create 3 requirements sequentially so we have data to query/update
        seed_ids = []
        for i in range(3):
            req_id = await requirement_handler._create_single_requirement(
                {
                    "type": "FUNC",
                    "title": f"Seed Requirement {i}",
                    "priority": "P1",
                    "current_state": f"Current state {i}",
                    "desired_state": f"Desired state {i}",
                }
            )
            seed_ids.append(req_id)

        # Phase 2: concurrent mixed workload — 5 creates + 5 queries + 3 status updates
        create_coros = [
            requirement_handler._create_single_requirement(
                {
                    "type": "FUNC",
                    "title": f"Mixed Create {i}",
                    "priority": "P2",
                    "current_state": "Current",
                    "desired_state": "Desired",
                }
            )
            for i in range(5)
        ]

        query_coros = [
            requirement_handler._query_requirements(search_text=f"Seed Requirement {i % 3}")
            for i in range(5)
        ]

        update_coros = [
            requirement_handler._update_requirement_status(
                requirement_id=seed_ids[i],
                new_status="Under Review",
                comment=f"Concurrent update {i}",
            )
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


class TestInterviewSessionIsolation:
    """Verify that 5 concurrent interview sessions each receive a unique session ID."""

    async def test_concurrent_sessions_unique_ids(self, interview_handler):
        coros = [
            interview_handler._start_requirement_interview(
                project_context=f"Project {i}",
                stakeholder_role="Developer",
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*coros)

        session_ids = []
        for result in results:
            # result is list[TextContent]; extract text from the first element
            text = result[0].text
            match = re.search(r"\*\*Session ID\*\*:\s*(\S+)", text)
            assert match, f"Could not extract session ID from response: {text[:200]}"
            session_ids.append(match.group(1))

        assert len(session_ids) == 5
        assert len(set(session_ids)) == 5, (
            f"Expected 5 unique session IDs, got {len(set(session_ids))}: {session_ids}"
        )


class TestLatencyUnderLoad:
    """Verify that query latency stays within acceptable bounds under concurrent load."""

    async def test_p50_and_p95_latency(self, requirement_handler):
        # Phase 1: seed 5 requirements sequentially
        for i in range(5):
            await requirement_handler._create_single_requirement(
                {
                    "type": "FUNC",
                    "title": f"Latency Seed {i}",
                    "priority": "P1",
                    "current_state": "Current",
                    "desired_state": "Desired",
                }
            )

        # Phase 2: run 10 concurrent queries, timing each one
        async def timed_query(index: int) -> float:
            start = time.monotonic()
            await requirement_handler._query_requirements(search_text="Latency Seed")
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
