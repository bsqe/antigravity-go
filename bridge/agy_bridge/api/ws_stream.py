"""Authenticated WebSocket streaming hub with replay and mid-turn cancellation."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import authenticate_token
from ..protocol import ToolEventRecord, WsStreamEvent
from ..runtime.base import AgentRuntime
from ..store import BridgeStore, now_ms

router = APIRouter(tags=["ws"])

AUTH_TIMEOUT_SEC = 5.0


@dataclass(slots=True, eq=False)
class ConnectedClient:
    ws: WebSocket
    device_id: str
    subscriptions: set[str] = field(default_factory=set)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json_safe(self, payload: dict[str, Any]) -> bool:
        try:
            async with self.send_lock:
                await self.ws.send_json(payload)
            return True
        except Exception:
            return False


class ConnectionHub:
    """Tracks authenticated WebSocket clients and routes session/global events."""

    def __init__(self) -> None:
        self._clients: set[ConnectedClient] = set()
        self._active_turns: dict[str, str] = {}  # session_id -> turn_id
        self._lock = asyncio.Lock()

    async def register(self, client: ConnectedClient) -> None:
        async with self._lock:
            self._clients.add(client)

    async def unregister(self, client: ConnectedClient) -> None:
        async with self._lock:
            self._clients.discard(client)

    async def kick_device(self, device_id: str) -> None:
        async with self._lock:
            targets = [c for c in self._clients if c.device_id == device_id]
        for client in targets:
            try:
                await client.ws.close(code=4401, reason="Device revoked")
            except Exception:
                pass
            await self.unregister(client)

    async def broadcast_session(self, session_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = [c for c in self._clients if session_id in c.subscriptions]
        await asyncio.gather(*(c.send_json_safe(payload) for c in targets), return_exceptions=True)

    async def broadcast_global(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._clients)
        await asyncio.gather(*(c.send_json_safe(payload) for c in targets), return_exceptions=True)

    async def try_claim_turn(self, session_id: str, turn_id: str) -> bool:
        async with self._lock:
            if session_id in self._active_turns:
                return False
            self._active_turns[session_id] = turn_id
            return True

    async def release_turn(self, session_id: str, turn_id: str) -> None:
        async with self._lock:
            if self._active_turns.get(session_id) == turn_id:
                self._active_turns.pop(session_id, None)


@router.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    store: BridgeStore = websocket.app.state.store
    runtime: AgentRuntime = websocket.app.state.runtime
    hub: ConnectionHub = websocket.app.state.ws_hub

    # First-frame authentication so device tokens never appear in proxy URL query logs
    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT_SEC)
    except Exception:
        await websocket.close(code=4401, reason="Auth timeout or invalid frame")
        return

    if not isinstance(auth_msg, dict) or auth_msg.get("type") != "auth":
        await websocket.send_json(
            WsStreamEvent(
                type="auth.error",
                data={"detail": "Expected first frame with type 'auth'"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
        await websocket.close(code=4401, reason="Expected auth frame")
        return

    device = authenticate_token(store, str(auth_msg.get("token") or ""))
    if device is None:
        await websocket.send_json(
            WsStreamEvent(
                type="auth.error",
                data={"detail": "Invalid or revoked device token"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
        await websocket.close(code=4401, reason="Unauthorized")
        return

    client = ConnectedClient(ws=websocket, device_id=device.id)
    await hub.register(client)
    await client.send_json_safe(
        WsStreamEvent(
            type="auth.ok",
            data={"deviceId": device.id},
            timestampMs=now_ms(),
        ).model_dump(mode="json")
    )

    try:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            msg_type = str(msg.get("type") or "")

            if msg_type == "ping":
                await client.send_json_safe(
                    WsStreamEvent(type="pong", timestampMs=now_ms()).model_dump(mode="json")
                )
            elif msg_type == "session.subscribe":
                await _handle_subscribe(client, msg, store)
            elif msg_type == "session.unsubscribe":
                sid = str(msg.get("sessionId") or "")
                client.subscriptions.discard(sid)
            elif msg_type == "turn.send":
                await _handle_turn_send(client, msg, store, runtime, hub)
            elif msg_type == "turn.cancel":
                await _handle_turn_cancel(client, msg, runtime)
            else:
                await client.send_json_safe(
                    WsStreamEvent(
                        type="error",
                        data={"code": "UNKNOWN_MESSAGE", "detail": f"Unsupported type: {msg_type}"},
                        timestampMs=now_ms(),
                    ).model_dump(mode="json")
                )
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(client)


async def _handle_subscribe(
    client: ConnectedClient, msg: dict[str, Any], store: BridgeStore
) -> None:
    session_id = str(msg.get("sessionId") or "").strip()
    session = store.get_session(session_id)
    if session is None:
        await client.send_json_safe(
            WsStreamEvent(
                type="error",
                sessionId=session_id,
                data={"code": "SESSION_NOT_FOUND", "detail": "Session does not exist"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
        return

    client.subscriptions.add(session_id)
    await client.send_json_safe(
        WsStreamEvent(
            type="session.subscribed",
            sessionId=session_id,
            seq=session.lastSeq,
            data={"status": session.status},
            timestampMs=now_ms(),
        ).model_dump(mode="json")
    )

    last_seq = msg.get("lastSeq")
    if isinstance(last_seq, int) and last_seq >= 0 and last_seq < session.lastSeq:
        missed = store.list_stream_events_after(session_id, last_seq)
        for ev in missed:
            await client.send_json_safe(ev)


async def _handle_turn_send(
    client: ConnectedClient,
    msg: dict[str, Any],
    store: BridgeStore,
    runtime: AgentRuntime,
    hub: ConnectionHub,
) -> None:
    session_id = str(msg.get("sessionId") or "").strip()
    prompt = str(msg.get("text") or "").strip()
    raw_att_ids = msg.get("attachmentIds")
    attachment_ids = (
        [str(a) for a in raw_att_ids if isinstance(a, str) and a.strip()]
        if isinstance(raw_att_ids, list)
        else []
    )
    model_override = str(msg.get("model")).strip() if msg.get("model") else None
    turn_id = str(msg.get("turnId") or "").strip() or f"turn_{uuid.uuid4().hex[:12]}"

    session = store.get_session(session_id)
    if session is None:
        await client.send_json_safe(
            WsStreamEvent(
                type="error",
                sessionId=session_id,
                turnId=turn_id,
                data={"code": "SESSION_NOT_FOUND", "detail": "Session does not exist"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
        return

    if not prompt and not attachment_ids:
        await client.send_json_safe(
            WsStreamEvent(
                type="error",
                sessionId=session_id,
                turnId=turn_id,
                data={"code": "EMPTY_PROMPT", "detail": "Message text or image attachment is required"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
        return

    project = store.get_project(session.projectId)
    if project is None:
        await client.send_json_safe(
            WsStreamEvent(
                type="error",
                sessionId=session_id,
                turnId=turn_id,
                data={"code": "PROJECT_NOT_FOUND", "detail": "Parent project not found"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
        return

    if not await hub.try_claim_turn(session_id, turn_id):
        await client.send_json_safe(
            WsStreamEvent(
                type="error",
                sessionId=session_id,
                turnId=turn_id,
                data={"code": "TURN_IN_PROGRESS", "detail": "Another turn is already running in this session"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
        return

    client.subscriptions.add(session_id)
    attachments = store.get_attachments_for_session(session_id, attachment_ids)
    user_msg = store.create_message(
        session_id=session_id,
        turn_id=turn_id,
        role="user",
        content=prompt,
        attachment_ids=[a.info.id for a in attachments],
        status="completed",
    )
    asst_msg = store.create_message(
        session_id=session_id,
        turn_id=turn_id,
        role="assistant",
        content="",
        thoughts="",
        status="streaming",
    )
    store.update_session(session_id, status="running")

    async def emit_and_persist(event_type: str, data: dict[str, Any]) -> None:
        if event_type == "thought.delta":
            store.append_message_delta(asst_msg.id, thought_delta=str(data.get("delta") or ""))
        elif event_type == "text.delta":
            store.append_message_delta(asst_msg.id, text_delta=str(data.get("delta") or ""))
        elif event_type in ("tool.call", "tool.result"):
            tool_rec = ToolEventRecord(
                id=str(data.get("id") or f"tool_{uuid.uuid4().hex[:8]}"),
                name=str(data.get("name") or "tool"),
                args=data.get("args") if isinstance(data.get("args"), dict) else {},
                status="completed" if event_type == "tool.result" else "running",
                outputPreview=(
                    str(data["outputPreview"])[:500]
                    if data.get("outputPreview") is not None
                    else None
                ),
            )
            store.append_message_delta(asst_msg.id, tool_event=tool_rec)
        elif event_type == "turn.done":
            final_status = str(data.get("status") or "completed")
            err_msg = str(data["error"]) if data.get("error") else None
            store.finalize_message(asst_msg.id, status=final_status, error_message=err_msg)
            store.update_session(
                session_id,
                status="error" if final_status == "error" else "idle",
            )
            await hub.release_turn(session_id, turn_id)

        envelope = WsStreamEvent(
            type=event_type,  # type: ignore[arg-type]
            sessionId=session_id,
            turnId=turn_id,
            messageId=asst_msg.id,
            data=data,
            timestampMs=now_ms(),
        ).model_dump(mode="json")
        seq = store.append_stream_event(session_id, envelope)
        envelope["seq"] = seq
        await hub.broadcast_session(session_id, envelope)

    # Notify subscribers immediately that the turn has started
    await emit_and_persist(
        "turn.started",
        {
            "userMessageId": user_msg.id,
            "assistantMessageId": asst_msg.id,
            "prompt": prompt,
            "attachments": [a.info.model_dump(mode="json") for a in attachments],
        },
    )

    system_context, _ = store.get_session_runtime_context(session_id)
    try:
        await runtime.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            workspace_dir=Path(project.path),
            prompt=prompt,
            attachments=attachments,
            model=model_override or session.model,
            emit=emit_and_persist,
            system_instructions=system_context,
        )
    except Exception as exc:
        await emit_and_persist("turn.done", {"status": "error", "error": str(exc)})


async def _handle_turn_cancel(
    client: ConnectedClient, msg: dict[str, Any], runtime: AgentRuntime
) -> None:
    session_id = str(msg.get("sessionId") or "").strip()
    turn_id = str(msg.get("turnId") or "").strip() or None
    cancelled = await runtime.cancel_turn(session_id, turn_id)
    if not cancelled:
        await client.send_json_safe(
            WsStreamEvent(
                type="error",
                sessionId=session_id,
                turnId=turn_id,
                data={"code": "NO_ACTIVE_TURN", "detail": "No running turn to cancel"},
                timestampMs=now_ms(),
            ).model_dump(mode="json")
        )
