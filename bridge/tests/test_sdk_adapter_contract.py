"""Contract tests against the real installed `google-antigravity` Python SDK."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import google.antigravity as ag
from google.antigravity import types as ag_types

from agy_bridge.config import BridgeConfig
from agy_bridge.protocol import AttachmentInfo
from agy_bridge.runtime.antigravity_sdk import AntigravitySdkRuntime
from agy_bridge.store import AttachmentDiskRecord

VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_build_local_config_accepts_real_sdk_types(tmp_path: Path) -> None:
    cfg = BridgeConfig(
        auth_mode="api_key",
        gemini_api_key="test-key-placeholder",
        default_model="gemini-3.8-flash",
        sdk_allow_write_tools=False,
    )
    runtime = AntigravitySdkRuntime(cfg, tmp_path / "sdk_sessions")
    uuid_sid = "12345678-1234-4234-8234-1234567890ab"
    local_cfg = runtime._build_local_config(
        session_id=uuid_sid,
        workspace_dir=tmp_path,
        model=None,
    )
    assert isinstance(local_cfg, ag.LocalAgentConfig)
    assert local_cfg.conversation_id == uuid_sid
    assert local_cfg.session_continuation_mode == ag_types.SessionContinuationMode.CREATE_OR_RESUME
    assert local_cfg.workspaces == [str(tmp_path)]

    # Short session IDs are normalized deterministically to satisfy SDK >= 32 char constraint
    short_cfg = runtime._build_local_config(
        session_id="ses_short",
        workspace_dir=tmp_path,
        model=None,
    )
    assert len(short_cfg.conversation_id or "") >= 32
    assert local_cfg.capabilities is not None
    assert ag.BuiltinTools.VIEW_FILE in (local_cfg.capabilities.enabled_tools or [])
    assert ag.BuiltinTools.RUN_COMMAND not in (local_cfg.capabilities.enabled_tools or [])

    # Vertex Express Mode key (`AQ.*`) auto-selects vertex=True and passes api_key
    express_cfg = BridgeConfig(auth_mode="api_key", gemini_api_key="AQ.test_express_key")
    express_runtime = AntigravitySdkRuntime(express_cfg, tmp_path / "sdk_sessions_ex")
    ex_local = express_runtime._build_local_config(
        session_id=uuid_sid,
        workspace_dir=tmp_path,
        model=None,
    )
    assert isinstance(ex_local, ag.LocalAgentConfig)
    assert ex_local.vertex is True
    assert ex_local.api_key == "AQ.test_express_key"

    # Vertex mode builds cleanly as well
    vertex_cfg = BridgeConfig(auth_mode="vertex", sdk_allow_write_tools=True)
    vertex_runtime = AntigravitySdkRuntime(vertex_cfg, tmp_path / "sdk_sessions_v")
    v_local = vertex_runtime._build_local_config(
        session_id="ses_v",
        workspace_dir=tmp_path,
        model="gemini-4-pro",
    )
    assert isinstance(v_local, ag.LocalAgentConfig)
    assert v_local.vertex is True


def test_build_prompt_payload_creates_real_sdk_image(tmp_path: Path) -> None:
    img_path = tmp_path / "sample.png"
    img_path.write_bytes(VALID_PNG_BYTES)
    att = AttachmentDiskRecord(
        info=AttachmentInfo(
            id="att_1",
            sessionId="ses_1",
            filename="sample.png",
            mimeType="image/png",
            sizeBytes=len(VALID_PNG_BYTES),
            sha256="abc",
            createdAtMs=1,
        ),
        disk_path=img_path,
    )
    payload = AntigravitySdkRuntime._build_prompt_payload("Analyze image", [att])
    assert isinstance(payload, list)
    assert payload[0] == "Analyze image"
    assert isinstance(payload[1], ag.Image)


async def test_sdk_runtime_streams_real_chat_response_and_cancels(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cancel_called = False

    class StubConversation:
        last_turn_usage = None
        _last_turn_stop_reason = ag_types.StopReason.UNSPECIFIED

        def get_last_structured_output(self) -> None:
            return None

        async def cancel(self) -> None:
            nonlocal cancel_called
            cancel_called = True

    class StubAgent:
        async def chat(self, prompt: Any) -> ag_types.ChatResponse:
            async def gen() -> AsyncIterator[Any]:
                yield ag_types.Thought(step_index=0, text="Thinking deeply...")
                yield ag_types.ToolCall(name="view_file", args={"path": "README.md"}, id="tc_1")
                yield ag_types.ToolResult(name="view_file", id="tc_1", result="hello")
                yield ag_types.Text(step_index=1, text="Final answer.")

            return ag_types.ChatResponse(gen(), StubConversation())

    runtime = AntigravitySdkRuntime(BridgeConfig(), tmp_path / "sdk_sessions")

    async def fake_get_or_start(**_: Any) -> StubAgent:
        return StubAgent()

    monkeypatch.setattr(runtime, "_get_or_start_agent", fake_get_or_start)

    collected: list[tuple[str, dict[str, Any]]] = []

    async def emit(ev_type: str, data: dict[str, Any]) -> None:
        collected.append((ev_type, data))

    handle = await runtime.start_turn(
        session_id="ses_sdk",
        turn_id="turn_sdk",
        workspace_dir=tmp_path,
        prompt="hi",
        attachments=[],
        model=None,
        emit=emit,
    )
    await handle.task

    assert [t for t, _ in collected] == [
        "thought.delta",
        "tool.call",
        "tool.result",
        "text.delta",
        "turn.done",
    ]
    assert collected[0][1]["delta"] == "Thinking deeply..."
    assert collected[3][1]["delta"] == "Final answer."
    assert collected[4][1]["status"] == "completed"
    assert cancel_called is False
