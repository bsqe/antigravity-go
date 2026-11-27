"""Abstract runtime contract decoupling Bridge API/WS from the underlying Agent SDK."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..store import AttachmentDiskRecord

RuntimeEventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class TurnHandle:
    """Handle representing an active in-flight agent turn."""

    turn_id: str
    session_id: str
    task: asyncio.Task[None]
    cancel_fn: Callable[[], Awaitable[None]] | None = None

    async def cancel(self) -> None:
        if self.cancel_fn is not None:
            try:
                await self.cancel_fn()
            except Exception:
                pass
        if not self.task.done():
            self.task.cancel()


class AgentRuntime(ABC):
    """Runtime lifecycle and streaming turn interface."""

    @abstractmethod
    async def start_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        workspace_dir: Path,
        prompt: str,
        attachments: list[AttachmentDiskRecord],
        model: str | None,
        emit: RuntimeEventEmitter,
        system_instructions: str | None = None,
    ) -> TurnHandle:
        """Start an asynchronous streaming turn and emit protocol deltas via `emit`."""

    @abstractmethod
    async def cancel_turn(self, session_id: str, turn_id: str | None = None) -> bool:
        """Abort the active turn for `session_id` (optionally matching `turn_id`)."""

    @abstractmethod
    async def close_session(self, session_id: str) -> None:
        """Release runtime resources associated with `session_id`."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Gracefully close all active sessions and turns."""
