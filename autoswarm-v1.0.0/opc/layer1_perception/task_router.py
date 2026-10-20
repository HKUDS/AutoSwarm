"""Task router — DEPRECATED.

Mode selection is now determined by user metadata, not LLM-based routing.
This module is retained for backward compatibility only. Importing TaskRouter
still works, but ``route()`` returns a default ModeSelection(TASK_MODE).
"""

from __future__ import annotations

import warnings
from typing import Any

from loguru import logger

from opc.core.models import ExecutionMode, ModeSelection
from opc.layer1_perception.context_loader import LoadedContext

RouterDecision = ModeSelection


class TaskRouter:
    """Deprecated — kept for backward compatibility.

    ``route()`` now returns a default ``ModeSelection`` with ``TASK_MODE``
    without making any LLM calls.  Callers should migrate to reading mode
    from user metadata directly.
    """

    def __init__(self, llm: Any = None) -> None:
        self.llm = llm
        warnings.warn(
            "TaskRouter is deprecated. Mode is now determined by user metadata.",
            DeprecationWarning,
            stacklevel=2,
        )

    async def route(
        self,
        user_message: str,
        context: LoadedContext | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> ModeSelection:
        logger.debug("TaskRouter.route() called — returning default TASK_MODE")
        return ModeSelection(
            mode=ExecutionMode.TASK_MODE,
            domains=["general"],
        )
