"""
Role: Hold a strong reference to each in-flight background sync task so the event loop can't GC it
      mid-run, and log any exception that escapes it (the triggering request already returned 202).
Used by: SyncService.start_sync (schedules the runner via spawn_sync_task).
Depends on: asyncio + logging (stdlib).
Key invariants:
  - asyncio.create_task returns a task the loop only weakly references; without a strong ref a
    long-running sync can be garbage-collected and silently cancelled. We keep it in a module-level
    set until it finishes, then discard via a done-callback.
  - The runner is written to never let an exception escape; this is the belt-and-suspenders that
    surfaces one in the logs if it ever does, instead of an unretrieved-task-exception warning.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class SyncTaskSpawner(Protocol):
    """Schedules a background sync coroutine (the seam tests swap to capture instead of run)."""

    def __call__(self, coro: Coroutine[Any, Any, None], *, label: str) -> asyncio.Task[None]: ...


# Strong refs to in-flight sync tasks — without this the loop may GC a running task (it holds only a
# weak reference), silently cancelling a long sync mid-run.
_RUNNING_SYNC_TASKS: set[asyncio.Task[None]] = set()


def spawn_sync_task(coro: Coroutine[Any, Any, None], *, label: str) -> asyncio.Task[None]:
    """Schedule a background sync coroutine, holding a strong ref until it completes."""
    task = asyncio.create_task(coro, name=label)
    _RUNNING_SYNC_TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[None]) -> None:
    """Drop the finished task's ref and log any exception that escaped the runner."""
    _RUNNING_SYNC_TASKS.discard(task)
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.error("Background sync task %s raised", task.get_name(), exc_info=error)
