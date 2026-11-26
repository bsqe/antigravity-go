"""Official `google-antigravity` Python SDK runtime adapter.

Manages one stateful `Agent` context per session, streams semantic chunks
(`Thought`, `Text`, `ToolCall`, `ToolResult`) in exact order via
`ChatResponse.chunks`, supports multimodal image attachments via `from_file`,
and invokes native `await response.cancel()` on mid-turn abort.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import BridgeConfig
from ..store import AttachmentDiskRecord
from .base import AgentRuntime, RuntimeEventEmitter, TurnHandle

READ_ONLY_TOOLS = (
    "LIST_DIR",
    "VIEW_FILE",
    "FIND_FILE",
    "SEARCH_DIR",
    "SEARCH_WEB",
    "READ_URL_CONTENT",
    "FINISH",
)


@dataclass(slots=True)
class _ActiveSessionAgent:
    agent: Any
    workspace_dir: Path
    model: str | None


class AntigravitySdkRuntime(AgentRuntime):
    """Runtime backed directly by the official `google-antigravity` Python SDK."""

    def __init__(self, config: BridgeConfig, sessions_save_dir: Path) -> None:
        self._config = config
        self._save_dir = sessions_save_dir
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, _ActiveSessionAgent] = {}
        self._active_turns: dict[str, TurnHandle] = {}
        self._lock = asyncio.Lock()

    def _build_local_config(
        self,
        *,
        session_id: str,
        workspace_dir: Path,
        model: str | None,
    ) -> Any:
        import google.antigravity as ag

        capabilities_kwargs: dict[str, Any] = {}
        if not self._config.sdk_allow_write_tools:
            enabled = [
                getattr(ag.BuiltinTools, name)
                for name in READ_ONLY_TOOLS
                if hasattr(ag.BuiltinTools, name)
            ]
            capabilities_kwargs["enabled_tools"] = enabled

        sdk_conversation_id = (
            session_id
            if len(session_id) >= 32
            else str(uuid.uuid5(uuid.NAMESPACE_URL, f"agy-go:{session_id}"))
        )
        config_kwargs: dict[str, Any] = {
            "workspaces": [str(workspace_dir)],
            "conversation_id": sdk_conversation_id,
            "save_dir": str(self._save_dir / session_id),
            "capabilities": ag.CapabilitiesConfig(**capabilities_kwargs),
        }

        effective_model = (model or self._config.default_model or "").strip()
        if effective_model:
            config_kwargs["model"] = effective_model

        api_key = self._config.effective_api_key()
        is_vertex_express_key = api_key.startswith("AQ.")
        if self._config.auth_mode == "vertex" or is_vertex_express_key:
            config_kwargs["vertex"] = True
            if api_key:
                config_kwargs["api_key"] = api_key
        elif api_key:
            config_kwargs["api_key"] = api_key

        return ag.LocalAgentConfig(**config_kwargs)

    async def _get_or_start_agent(
        self,
        *,
        session_id: str,
        workspace_dir: Path,
        model: str | None,
    ) -> Any:
        import google.antigravity as ag

        async with self._lock:
            existing = self._agents.get(session_id)
            if (
                existing is not None
                and existing.workspace_dir == workspace_dir
                and existing.model == model
            ):
                return existing.agent

            if existing is not None:
                await self._close_agent_unlocked(session_id)

            local_cfg = self._build_local_config(
                session_id=session_id,
                workspace_dir=workspace_dir,
                model=model,
            )
            agent = ag.Agent(local_cfg)
            await agent.__aenter__()
            self._agents[session_id] = _ActiveSessionAgent(
                agent=agent,
                workspace_dir=workspace_dir,
                model=model,
            )
            return agent

    async def _close_agent_unlocked(self, session_id: str) -> None:
        entry = self._agents.pop(session_id, None)
        if entry is not None:
            try:
                await entry.agent.__aexit__(None, None, None)
            except Exception:
                pass

    @staticmethod
    def _build_prompt_payload(
        prompt: str, attachments: list[AttachmentDiskRecord]
    ) -> Any:
        import google.antigravity as ag

        if not attachments:
            return prompt

        parts: list[Any] = []
        if prompt:
            parts.append(prompt)
        for att in attachments:
            parts.append(ag.from_file(att.disk_path, description=att.info.filename))
        return parts

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
    ) -> TurnHandle:
        from google.antigravity import types as ag_types

        cancelled_flag = asyncio.Event()
        response_box: dict[str, Any] = {"response": None}

        async def cancel_backend() -> None:
            cancelled_flag.set()
            resp = response_box.get("response")
            if resp is not None:
                try:
                    await resp.cancel()
                except Exception:
                    pass

        async def run() -> None:
            try:
                agent = await self._get_or_start_agent(
                    session_id=session_id,
                    workspace_dir=workspace_dir,
                    model=model,
                )
                payload = self._build_prompt_payload(prompt, attachments)
                response = await agent.chat(payload)
                response_box["response"] = response

                if cancelled_flag.is_set():
                    await response.cancel()
                    await emit("turn.done", {"status": "cancelled"})
                    return

                async for chunk in response.chunks:
                    if cancelled_flag.is_set():
                        await response.cancel()
                        await emit("turn.done", {"status": "cancelled"})
                        return

                    if isinstance(chunk, ag_types.Thought):
                        if chunk.text:
                            await emit("thought.delta", {"delta": chunk.text})
                    elif isinstance(chunk, ag_types.Text):
                        if chunk.text:
                            await emit("text.delta", {"delta": chunk.text})
                    elif isinstance(chunk, ag_types.ToolCall):
                        await emit(
                            "tool.call",
                            {
                                "id": chunk.id or chunk.step_id or chunk.name,
                                "name": chunk.name,
                                "args": chunk.args if isinstance(chunk.args, dict) else {},
                            },
                        )
                    elif isinstance(chunk, ag_types.ToolResult):
                        preview = (
                            str(chunk.error)
                            if chunk.error
                            else (str(chunk.result)[:400] if chunk.result is not None else "")
                        )
                        await emit(
                            "tool.result",
                            {
                                "id": chunk.id or chunk.step_id or chunk.name,
                                "name": chunk.name,
                                "outputPreview": preview,
                            },
                        )

                done_data: dict[str, Any] = {
                    "status": "cancelled" if cancelled_flag.is_set() else "completed",
                }
                usage = response.usage_metadata
                if usage is not None and hasattr(usage, "model_dump"):
                    done_data["usage"] = usage.model_dump(mode="json")
                await emit("turn.done", done_data)
            except asyncio.CancelledError:
                resp = response_box.get("response")
                if resp is not None:
                    try:
                        await resp.cancel()
                    except Exception:
                        pass
                await emit("turn.done", {"status": "cancelled"})
            except Exception as exc:
                await emit("turn.done", {"status": "error", "error": str(exc)})
            finally:
                async with self._lock:
                    cur = self._active_turns.get(session_id)
                    if cur is not None and cur.turn_id == turn_id:
                        self._active_turns.pop(session_id, None)

        task = asyncio.create_task(run(), name=f"agy-turn-{session_id}-{turn_id}")
        handle = TurnHandle(
            turn_id=turn_id,
            session_id=session_id,
            task=task,
            cancel_fn=cancel_backend,
        )
        async with self._lock:
            self._active_turns[session_id] = handle
        return handle

    async def cancel_turn(self, session_id: str, turn_id: str | None = None) -> bool:
        async with self._lock:
            handle = self._active_turns.get(session_id)
            if handle is None:
                return False
            if turn_id is not None and handle.turn_id != turn_id:
                return False
        await handle.cancel()
        return True

    async def close_session(self, session_id: str) -> None:
        await self.cancel_turn(session_id)
        async with self._lock:
            await self._close_agent_unlocked(session_id)

    async def shutdown(self) -> None:
        async with self._lock:
            handles = list(self._active_turns.values())
            session_ids = list(self._agents.keys())
        for handle in handles:
            await handle.cancel()
        async with self._lock:
            for sid in session_ids:
                await self._close_agent_unlocked(sid)
