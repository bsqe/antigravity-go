"""Workspace project CRUD endpoints with strict boundary validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import get_config_manager, get_store, require_device_auth
from ..config import ConfigManager, validate_workspace_path
from ..protocol import ProjectCreate, ProjectInfo
from ..store import BridgeStore, DeviceRecord

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("", response_model=list[ProjectInfo])
async def list_projects(
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> list[ProjectInfo]:
    return store.list_projects()


@router.post("", response_model=ProjectInfo, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> ProjectInfo:
    try:
        validated = validate_workspace_path(body.path, cfg_mgr.config)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return store.create_project(name=body.name.strip(), path=str(validated))


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    request: Request,
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> dict[str, bool]:
    sessions = store.list_sessions(project_id=project_id)
    runtime = request.app.state.runtime
    for ses in sessions:
        await runtime.close_session(ses.id)
    deleted = store.delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return {"ok": True}
