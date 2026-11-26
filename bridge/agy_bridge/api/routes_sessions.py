"""Session CRUD, paginated message history, and validated image attachment upload/download."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from ..auth import get_config_manager, get_store, require_device_auth
from ..config import MAX_ATTACHMENT_BYTES, ConfigManager
from ..protocol import (
    AttachmentInfo,
    MessagesPageResponse,
    SessionCreate,
    SessionInfo,
    SessionUpdate,
)
from ..store import BridgeStore, DeviceRecord

router = APIRouter(prefix="/api/v1", tags=["sessions"])

ALLOWED_IMAGE_MIMES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def detect_image_mime(raw: bytes) -> str | None:
    """Verify image type by magic header bytes rather than trusting client headers alone."""
    if len(raw) < 12:
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    projectId: str | None = Query(default=None),
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> list[SessionInfo]:
    return store.list_sessions(project_id=projectId)


@router.post("/sessions", response_model=SessionInfo, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> SessionInfo:
    session = store.create_session(
        project_id=body.projectId,
        title=body.title.strip() or "New Conversation",
        model=body.model.strip() if body.model else None,
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return session


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
async def update_session(
    session_id: str,
    body: SessionUpdate,
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> SessionInfo:
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    updated = store.update_session(
        session_id,
        title=body.title.strip() if body.title is not None else None,
        model=body.model.strip() if body.model is not None else None,
    )
    assert updated is not None
    return updated


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> dict[str, bool]:
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await request.app.state.runtime.close_session(session_id)
    store.delete_session(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages", response_model=MessagesPageResponse)
async def get_session_messages(
    session_id: str,
    beforeSeq: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> MessagesPageResponse:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    messages, has_more = store.list_messages_page(
        session_id, before_seq=beforeSeq, limit=limit
    )
    return MessagesPageResponse(
        sessionId=session_id,
        messages=messages,
        hasMore=has_more,
        lastSeq=session.lastSeq,
    )


@router.post(
    "/sessions/{session_id}/attachments",
    response_model=AttachmentInfo,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> AttachmentInfo:
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    raw = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image upload")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Attachment exceeds maximum size of {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB",
        )

    detected_mime = detect_image_mime(raw)
    if detected_mime is None or detected_mime not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PNG, JPEG, WEBP, and GIF images are supported",
        )

    ext = ALLOWED_IMAGE_MIMES[detected_mime]
    raw_name = Path(file.filename or f"image{ext}").name
    safe_name = SAFE_FILENAME_RE.sub("_", raw_name).strip("._") or f"image{ext}"
    if not safe_name.lower().endswith(ext):
        safe_name = f"{safe_name}{ext}"

    session_dir = cfg_mgr.attachments_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    disk_name = f"{uuid.uuid4().hex}{ext}"
    disk_path = (session_dir / disk_name).resolve()
    disk_path.relative_to(cfg_mgr.attachments_dir.resolve())
    disk_path.write_bytes(raw)

    digest = hashlib.sha256(raw).hexdigest()
    return store.create_attachment(
        session_id=session_id,
        filename=safe_name[:120],
        mime_type=detected_mime,
        size_bytes=len(raw),
        disk_path=disk_path,
        sha256=digest,
    )


@router.get("/attachments/{attachment_id}")
async def download_attachment(
    attachment_id: str,
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> FileResponse:
    rec = store.get_attachment(attachment_id)
    if rec is None or not rec.disk_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    resolved = rec.disk_path.resolve()
    try:
        resolved.relative_to(cfg_mgr.attachments_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid path") from exc
    return FileResponse(
        path=resolved,
        media_type=rec.info.mimeType,
        filename=rec.info.filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )
