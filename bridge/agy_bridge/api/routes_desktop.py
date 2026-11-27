"""REST endpoints for desktop Antigravity conversation sync, handoff continuation, and tool settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..auth import get_config_manager, get_store, require_device_auth
from ..config import ConfigManager
from ..desktop_sync import (
    build_handoff_context,
    infer_matching_project,
    list_desktop_conversations,
    parse_desktop_conversation,
    to_protocol_messages,
)
from ..protocol import (
    BridgeSettingsResponse,
    BridgeSettingsUpdateRequest,
    DesktopContinueRequest,
    DesktopContinueResponse,
    DesktopConversationDetailResponse,
    DesktopConversationSummary,
    DesktopSyncResponse,
)
from ..store import BridgeStore, DeviceRecord

router = APIRouter(prefix="/api/v1", tags=["desktop", "settings"])


@router.get("/desktop/conversations", response_model=list[DesktopConversationSummary])
async def get_desktop_conversations(
    limit: int = Query(default=30, ge=1, le=100),
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> list[DesktopConversationSummary]:
    brain_root = cfg_mgr.config.effective_desktop_brain_dir()
    exclude_ids = store.get_all_session_ids()
    linked_map = store.list_linked_desktop_sessions()
    projects = store.list_projects()
    return list_desktop_conversations(
        brain_root,
        exclude_ids=exclude_ids,
        linked_by_desktop_id=linked_map,
        projects=projects,
        limit=limit,
    )


@router.get(
    "/desktop/conversations/{conv_id}",
    response_model=DesktopConversationDetailResponse,
)
async def get_desktop_conversation_detail(
    conv_id: str,
    limit: int = Query(default=80, ge=1, le=300),
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> DesktopConversationDetailResponse:
    brain_root = cfg_mgr.config.effective_desktop_brain_dir()
    parsed = parse_desktop_conversation(brain_root, conv_id)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Desktop conversation not found",
        )
    projects = store.list_projects()
    inferred_pid, inferred_path = infer_matching_project(parsed.referenced_paths, projects)
    linked_session = store.find_session_by_desktop_id(parsed.conversation_id)
    summary = DesktopConversationSummary(
        id=parsed.conversation_id,
        title=parsed.title,
        preview=parsed.preview,
        turnCount=sum(1 for m in parsed.messages if m.role == "user"),
        messageCount=len(parsed.messages),
        stepCount=parsed.step_count,
        artifacts=parsed.artifacts,
        inferredProjectId=inferred_pid,
        inferredWorkspacePath=inferred_path,
        linkedSessionId=linked_session.id if linked_session else None,
        updatedAtMs=parsed.updated_at_ms,
    )
    return DesktopConversationDetailResponse(
        summary=summary,
        messages=to_protocol_messages(parsed.conversation_id, parsed.messages, limit=limit),
    )


@router.post(
    "/desktop/conversations/{conv_id}/continue",
    response_model=DesktopContinueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def continue_desktop_conversation(
    conv_id: str,
    body: DesktopContinueRequest,
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> DesktopContinueResponse:
    brain_root = cfg_mgr.config.effective_desktop_brain_dir()
    parsed = parse_desktop_conversation(brain_root, conv_id)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Desktop conversation not found",
        )

    project = store.get_project(body.projectId)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    handoff_context = build_handoff_context(parsed, workspace_path=project.path)

    existing = (
        store.find_session_by_desktop_id(parsed.conversation_id, project_id=project.id)
        if body.reuseExisting
        else None
    )
    if existing is not None:
        added = store.import_desktop_messages(
            existing.id,
            parsed.messages,
            max_step=parsed.max_step_index,
            system_context=handoff_context,
        )
        refreshed = store.get_session(existing.id)
        assert refreshed is not None
        return DesktopContinueResponse(
            session=refreshed,
            importedMessages=added,
            reused=True,
        )

    title = f"[接力] {parsed.title}"
    session = store.create_session(
        project_id=project.id,
        title=title[:200],
        model=body.model.strip() if body.model else None,
        source_desktop_id=parsed.conversation_id,
        system_context=handoff_context,
        synced_desktop_step=-1,
    )
    assert session is not None
    added = store.import_desktop_messages(
        session.id,
        parsed.messages,
        max_step=parsed.max_step_index,
        system_context=handoff_context,
    )
    refreshed = store.get_session(session.id)
    assert refreshed is not None
    return DesktopContinueResponse(
        session=refreshed,
        importedMessages=added,
        reused=False,
    )


@router.post(
    "/sessions/{session_id}/sync-desktop",
    response_model=DesktopSyncResponse,
)
async def sync_session_from_desktop(
    session_id: str,
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> DesktopSyncResponse:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if not session.sourceDesktopId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is not linked to a desktop conversation",
        )
    project = store.get_project(session.projectId)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    brain_root = cfg_mgr.config.effective_desktop_brain_dir()
    parsed = parse_desktop_conversation(brain_root, session.sourceDesktopId)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Linked desktop conversation transcript is no longer available",
        )

    handoff_context = build_handoff_context(parsed, workspace_path=project.path)
    added = store.import_desktop_messages(
        session.id,
        parsed.messages,
        max_step=parsed.max_step_index,
        system_context=handoff_context,
    )
    _, synced_step = store.get_session_runtime_context(session.id)
    return DesktopSyncResponse(
        sessionId=session.id,
        sourceDesktopId=session.sourceDesktopId,
        addedMessages=added,
        syncedStep=synced_step,
    )


@router.get("/settings", response_model=BridgeSettingsResponse)
async def get_bridge_settings(
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
) -> BridgeSettingsResponse:
    cfg = cfg_mgr.config
    brain_dir = cfg.effective_desktop_brain_dir()
    return BridgeSettingsResponse(
        toolPolicy=cfg.effective_tool_policy(),
        defaultModel=cfg.default_model,
        desktopBrainDir=str(brain_dir),
        desktopSyncAvailable=brain_dir.is_dir(),
    )


@router.post("/settings", response_model=BridgeSettingsResponse)
async def update_bridge_settings(
    body: BridgeSettingsUpdateRequest,
    request: Request,
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
) -> BridgeSettingsResponse:
    cfg = cfg_mgr.config
    updates: dict[str, object] = {}
    if body.toolPolicy is not None:
        updates["sdk_tool_policy"] = body.toolPolicy
        updates["sdk_allow_write_tools"] = body.toolPolicy == "full"
    if body.defaultModel is not None:
        updates["default_model"] = body.defaultModel.strip()

    if updates:
        cfg = cfg_mgr.save(cfg.model_copy(update=updates))
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is not None and hasattr(runtime, "update_config"):
            runtime.update_config(cfg)

    brain_dir = cfg.effective_desktop_brain_dir()
    return BridgeSettingsResponse(
        toolPolicy=cfg.effective_tool_policy(),
        defaultModel=cfg.default_model,
        desktopBrainDir=str(brain_dir),
        desktopSyncAvailable=brain_dir.is_dir(),
    )
