"""Per-requirement async lock manager for auto-progression race conditions.

When multiple tasks linked to the same requirement update their status
concurrently, the snapshot-compare logic in ``TaskHandler._update_task_status``
can produce stale before/after reports.  ``RequirementLockManager`` serialises
those critical sections on a per-requirement basis so each caller sees a
consistent view.

Locks are acquired in **sorted ID order** to prevent deadlocks when a single
task status update touches multiple requirements.  Reference counting ensures
that idle locks are cleaned up once all waiters have released.
"""

import asyncio
from collections import defaultdict


class RequirementLockManager:
    """Manages per-requirement ``asyncio.Lock`` instances with reference counting.

    Public API
    ----------
    acquire_for_requirements(req_ids)
        Sort the IDs, acquire each lock in order, return the sorted list.
    release_for_requirements(req_ids)
        Release each lock in reverse order, cleaning up at ref_count 0.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._ref_counts: dict[str, int] = defaultdict(int)
        self._manager_lock = asyncio.Lock()

    async def acquire_for_requirements(self, req_ids: set[str]) -> list[str]:
        """Acquire locks for *req_ids* in sorted order.

        Returns the sorted list of requirement IDs (caller passes this back
        to :meth:`release_for_requirements`).
        """
        sorted_ids = sorted(req_ids)
        for req_id in sorted_ids:
            async with self._manager_lock:
                self._ref_counts[req_id] += 1
                lock = self._locks[req_id]
            await lock.acquire()
        return sorted_ids

    async def release_for_requirements(self, req_ids: list[str]) -> None:
        """Release locks in **reverse** order and clean up at ref_count 0."""
        for req_id in reversed(req_ids):
            lock = self._locks.get(req_id)
            if lock is not None:
                lock.release()
            async with self._manager_lock:
                self._ref_counts[req_id] -= 1
                if self._ref_counts[req_id] <= 0:
                    self._locks.pop(req_id, None)
                    self._ref_counts.pop(req_id, None)
