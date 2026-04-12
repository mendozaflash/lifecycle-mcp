"""Tests for RequirementLockManager.

Validates:
  - Single acquire/release with cleanup
  - Sorted lock acquisition order (deadlock prevention)
  - Reference counting with concurrent callers and cleanup at ref_count 0
"""

import asyncio

import pytest

from lifecycle_mcp.locks import RequirementLockManager


@pytest.mark.asyncio
async def test_acquire_release_single():
    """Acquire a single requirement lock, release it, verify internal cleanup."""
    mgr = RequirementLockManager()

    locked = await mgr.acquire_for_requirements({"REQ-0001"})
    assert locked == ["REQ-0001"]

    # While held, internal structures should have entries
    assert "REQ-0001" in mgr._locks
    assert mgr._ref_counts["REQ-0001"] == 1

    await mgr.release_for_requirements(locked)

    # After release with ref_count 0, entries should be cleaned up
    assert "REQ-0001" not in mgr._locks
    assert "REQ-0001" not in mgr._ref_counts


@pytest.mark.asyncio
async def test_sorted_acquisition():
    """Locks must be acquired in sorted ID order to prevent deadlocks."""
    mgr = RequirementLockManager()

    # Pass IDs in unsorted order
    locked = await mgr.acquire_for_requirements({"REQ-0003", "REQ-0001", "REQ-0002"})
    assert locked == ["REQ-0001", "REQ-0002", "REQ-0003"]

    await mgr.release_for_requirements(locked)

    # All cleaned up
    assert len(mgr._locks) == 0
    assert len(mgr._ref_counts) == 0


@pytest.mark.asyncio
async def test_ref_counting_cleanup():
    """Two callers acquire the same lock; cleanup only happens after both release."""
    mgr = RequirementLockManager()

    order: list[str] = []

    async def caller_1():
        locked = await mgr.acquire_for_requirements({"REQ-0001"})
        order.append("c1_acquired")
        # Hold the lock briefly so caller_2 has to wait
        await asyncio.sleep(0.05)
        order.append("c1_releasing")
        await mgr.release_for_requirements(locked)
        order.append("c1_released")

    async def caller_2():
        # Small delay to ensure caller_1 acquires first
        await asyncio.sleep(0.01)
        locked = await mgr.acquire_for_requirements({"REQ-0001"})
        order.append("c2_acquired")
        # After caller_1 released, ref_count should still be 1 (caller_2 holds it)
        assert mgr._ref_counts["REQ-0001"] == 1
        assert "REQ-0001" in mgr._locks
        await mgr.release_for_requirements(locked)
        order.append("c2_released")

    await asyncio.gather(caller_1(), caller_2())

    # Caller 2 must have acquired AFTER caller 1 released
    assert order.index("c2_acquired") > order.index("c1_releasing")

    # After both released, everything should be cleaned up
    assert len(mgr._locks) == 0
    assert len(mgr._ref_counts) == 0


@pytest.mark.asyncio
async def test_empty_req_ids():
    """Acquiring with an empty set should return an empty list and be a no-op."""
    mgr = RequirementLockManager()

    locked = await mgr.acquire_for_requirements(set())
    assert locked == []

    # Release with empty list should also be a no-op
    await mgr.release_for_requirements(locked)
    assert len(mgr._locks) == 0
    assert len(mgr._ref_counts) == 0


@pytest.mark.asyncio
async def test_disjoint_requirements_no_contention():
    """Callers locking different requirements should not block each other."""
    mgr = RequirementLockManager()

    order: list[str] = []

    async def caller_a():
        locked = await mgr.acquire_for_requirements({"REQ-0001"})
        order.append("a_acquired")
        await asyncio.sleep(0.05)
        await mgr.release_for_requirements(locked)
        order.append("a_released")

    async def caller_b():
        await asyncio.sleep(0.01)
        locked = await mgr.acquire_for_requirements({"REQ-0002"})
        order.append("b_acquired")
        await mgr.release_for_requirements(locked)
        order.append("b_released")

    await asyncio.gather(caller_a(), caller_b())

    # Caller B should acquire while caller A still holds its lock
    # (b_acquired should come before a_released)
    assert order.index("b_acquired") < order.index("a_released")

    # Cleanup
    assert len(mgr._locks) == 0
    assert len(mgr._ref_counts) == 0
