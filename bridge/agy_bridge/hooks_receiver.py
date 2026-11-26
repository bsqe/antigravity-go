"""Loopback ingress for Antigravity lifecycle hooks (`SessionStart`, `PostToolUse`, `Stop`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .auth import require_loopback_hook_auth
from .protocol import HookEventPayload, WsStreamEvent
from .store import now_ms

router = APIRouter(prefix="/api/v1/internal", tags=["hooks"])


@router.post("/hooks")
async def ingest_hook_event(
    payload: HookEventPayload,
    request: Request,
    _: None = Depends(require_loopback_hook_auth),
) -> dict[str, bool]:
    hub = request.app.state.ws_hub
    event = WsStreamEvent(
        type="hook.event",
        sessionId=payload.conversationId,
        data={
            "hookEventName": payload.hookEventName,
            "conversationId": payload.conversationId,
            "cwd": payload.cwd,
            "modelName": payload.modelName,
            "toolCall": payload.toolCall,
            "terminationReason": payload.terminationReason,
            "error": payload.error,
        },
        timestampMs=now_ms(),
    )
    await hub.broadcast_global(event.model_dump(mode="json"))
    return {"ok": True}
