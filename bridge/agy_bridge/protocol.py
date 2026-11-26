"""Typed REST and WebSocket protocol schemas shared by Bridge and agy_client."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Auth & Pairing ────────────────────────────────────────────────────────────


class PingResponse(BaseModel):
    ok: bool = True
    service: str = "antigravity-go-bridge"
    version: str
    hostName: str
    timestampMs: int


class PairRequest(BaseModel):
    pairingToken: str = Field(min_length=24, max_length=128)
    deviceName: str = Field(default="Mobile Device", min_length=1, max_length=80)
    platform: str = Field(default="unknown", max_length=32)


class PairResponse(BaseModel):
    deviceId: str
    deviceToken: str
    hostName: str
    version: str


class DeviceInfo(BaseModel):
    id: str
    name: str
    platform: str
    createdAtMs: int
    lastSeenAtMs: int
    revoked: bool = False


# ── Projects & Sessions ───────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=1024)


class ProjectInfo(BaseModel):
    id: str
    name: str
    path: str
    createdAtMs: int
    sessionCount: int = 0


class SessionCreate(BaseModel):
    projectId: str = Field(min_length=1, max_length=64)
    title: str = Field(default="New Conversation", min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=120)


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=120)


class SessionInfo(BaseModel):
    id: str
    projectId: str
    projectName: str
    title: str
    model: str | None = None
    status: Literal["idle", "running", "error"] = "idle"
    lastSeq: int = 0
    createdAtMs: int
    updatedAtMs: int


# ── Messages & Attachments ────────────────────────────────────────────────────


class AttachmentInfo(BaseModel):
    id: str
    sessionId: str
    filename: str
    mimeType: str
    sizeBytes: int
    sha256: str
    createdAtMs: int


class ToolEventRecord(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "completed", "error"] = "running"
    outputPreview: str | None = None


class MessageRecord(BaseModel):
    id: str
    sessionId: str
    turnId: str
    role: Literal["user", "assistant", "system"]
    content: str = ""
    thoughts: str = ""
    toolEvents: list[ToolEventRecord] = Field(default_factory=list)
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    status: Literal["streaming", "completed", "cancelled", "error"] = "completed"
    errorMessage: str | None = None
    seq: int
    createdAtMs: int
    updatedAtMs: int


class MessagesPageResponse(BaseModel):
    sessionId: str
    messages: list[MessageRecord]
    hasMore: bool
    lastSeq: int


# ── Channels & Tunnels ────────────────────────────────────────────────────────


class ChannelEndpoint(BaseModel):
    kind: Literal["lan", "tailscale", "cloudflare"]
    label: str
    baseUrl: str
    available: bool = True
    detail: str | None = None


class ChannelsStatusResponse(BaseModel):
    hostName: str
    port: int
    pairingUri: str
    endpoints: list[ChannelEndpoint]
    tailscaleInstalled: bool
    cloudflaredInstalled: bool
    cloudflaredMode: Literal["off", "quick", "named"]
    cloudflaredRunning: bool
    hasNamedTunnelToken: bool


class TunnelConfigRequest(BaseModel):
    mode: Literal["off", "quick", "named"]
    token: str | None = None
    hostname: str | None = None


# ── Antigravity Hooks ─────────────────────────────────────────────────────────


class HookEventPayload(BaseModel):
    hookEventName: str
    conversationId: str | None = None
    cwd: str | None = None
    modelName: str | None = None
    toolCall: dict[str, Any] | None = None
    terminationReason: str | None = None
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


# ── WebSocket Envelopes ───────────────────────────────────────────────────────


class WsStreamEvent(BaseModel):
    type: Literal[
        "auth.ok",
        "auth.error",
        "pong",
        "session.subscribed",
        "turn.started",
        "thought.delta",
        "text.delta",
        "tool.call",
        "tool.result",
        "turn.done",
        "hook.event",
        "error",
    ]
    sessionId: str | None = None
    turnId: str | None = None
    messageId: str | None = None
    seq: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestampMs: int
