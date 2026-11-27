"""Shared pytest fixtures and deterministic async runtime test double."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agy_bridge.api.app import create_app
from agy_bridge.config import ConfigManager
from agy_bridge.runtime.base import AgentRuntime, RuntimeEventEmitter, TurnHandle
from agy_bridge.store import AttachmentDiskRecord, BridgeStore
from agy_bridge.tunnels import CloudflaredManager


class FakeAgentRuntime(AgentRuntime):
    """Deterministic async runtime for testing streaming, attachments, and cancellation."""

    def __init__(self) -> None:
        self._handles: dict[str, TurnHandle] = {}
        self.received_calls: list[dict[str, Any]] = []
        self.slow_mode: bool = False
        self.cancel_observed = asyncio.Event()

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
        self.received_calls.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "workspace_dir": str(workspace_dir),
                "prompt": prompt,
                "attachment_ids": [a.info.id for a in attachments],
                "model": model,
                "system_instructions": system_instructions,
            }
        )

        async def run() -> None:
            try:
                await emit("thought.delta", {"delta": "Planning step 1. "})
                await emit("thought.delta", {"delta": "Checking workspace."})
                await emit(
                    "tool.call",
                    {"id": "call_1", "name": "list_dir", "args": {"path": "."}},
                )
                await emit(
                    "tool.result",
                    {"id": "call_1", "name": "list_dir", "outputPreview": "README.md"},
                )
                if self.slow_mode:
                    for i in range(50):
                        await emit("text.delta", {"delta": f"chunk-{i} "})
                        await asyncio.sleep(0.05)
                else:
                    await emit("text.delta", {"delta": f"Echo: {prompt}"})
                    if attachments:
                        await emit(
                            "text.delta",
                            {"delta": f" [images={len(attachments)}]"},
                        )
                await emit("turn.done", {"status": "completed"})
            except asyncio.CancelledError:
                self.cancel_observed.set()
                await emit("turn.done", {"status": "cancelled"})
            finally:
                cur = self._handles.get(session_id)
                if cur is not None and cur.turn_id == turn_id:
                    self._handles.pop(session_id, None)

        task = asyncio.create_task(run())
        handle = TurnHandle(turn_id=turn_id, session_id=session_id, task=task)
        self._handles[session_id] = handle
        return handle

    async def cancel_turn(self, session_id: str, turn_id: str | None = None) -> bool:
        handle = self._handles.get(session_id)
        if handle is None:
            return False
        if turn_id is not None and handle.turn_id != turn_id:
            return False
        await handle.cancel()
        return True

    async def close_session(self, session_id: str) -> None:
        await self.cancel_turn(session_id)

    async def shutdown(self) -> None:
        for handle in list(self._handles.values()):
            await handle.cancel()
        self._handles.clear()


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    root.mkdir()
    proj = root / "demo_repo"
    proj.mkdir()
    return root


@pytest.fixture()
def bridge_env(tmp_path: Path, workspace_root: Path) -> dict[str, Any]:
    data_dir = tmp_path / "data"
    cfg_mgr = ConfigManager(data_dir)
    cfg_mgr.save(
        cfg_mgr.config.model_copy(
            update={
                "host_name": "TestPC",
                "allowed_roots": [str(workspace_root)],
                "cloudflared_mode": "off",
            }
        )
    )
    store = BridgeStore(cfg_mgr.db_path)
    runtime = FakeAgentRuntime()
    cf_mgr = CloudflaredManager(cfg_mgr.config.port)
    app = create_app(
        config_manager=cfg_mgr,
        store=store,
        runtime=runtime,
        tunnel_manager=cf_mgr,
        auto_start_tunnel=False,
    )
    with TestClient(app) as client:
        yield {
            "client": client,
            "cfg_mgr": cfg_mgr,
            "store": store,
            "runtime": runtime,
            "workspace_root": workspace_root,
            "demo_project_dir": workspace_root / "demo_repo",
        }
    store.close()


@pytest.fixture()
def paired_headers(bridge_env: dict[str, Any]) -> dict[str, str]:
    client: TestClient = bridge_env["client"]
    pairing_token = bridge_env["cfg_mgr"].config.pairing_token
    res = client.post(
        "/api/v1/auth/pair",
        json={"pairingToken": pairing_token, "deviceName": "Pixel 9", "platform": "android"},
    )
    assert res.status_code == 200
    body = res.json()
    return {
        "Authorization": f"Bearer {body['deviceToken']}",
        "_device_id": body["deviceId"],
        "_device_token": body["deviceToken"],
    }
