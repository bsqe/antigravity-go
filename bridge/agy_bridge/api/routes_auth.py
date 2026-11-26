"""Health ping, QR pairing handshake, and device token revocation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import __version__
from ..auth import (
    get_config_manager,
    get_store,
    mint_device_token,
    require_device_auth,
    verify_pairing_token,
)
from ..config import ConfigManager
from ..protocol import DeviceInfo, PairRequest, PairResponse, PingResponse
from ..store import BridgeStore, DeviceRecord, now_ms

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.get("/ping", response_model=PingResponse)
async def ping(cfg_mgr: ConfigManager = Depends(get_config_manager)) -> PingResponse:
    return PingResponse(
        ok=True,
        version=__version__,
        hostName=cfg_mgr.config.host_name,
        timestampMs=now_ms(),
    )


@router.post("/auth/pair", response_model=PairResponse)
async def pair_device(
    body: PairRequest,
    cfg_mgr: ConfigManager = Depends(get_config_manager),
    store: BridgeStore = Depends(get_store),
) -> PairResponse:
    if not verify_pairing_token(body.pairingToken, cfg_mgr.config.pairing_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid pairing token",
        )
    plaintext, token_hash = mint_device_token()
    device = store.create_device(
        name=body.deviceName.strip(),
        platform=body.platform.strip() or "unknown",
        token_hash=token_hash,
    )
    return PairResponse(
        deviceId=device.id,
        deviceToken=plaintext,
        hostName=cfg_mgr.config.host_name,
        version=__version__,
    )


@router.get("/auth/devices", response_model=list[DeviceInfo])
async def list_devices(
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> list[DeviceInfo]:
    return store.list_devices()


@router.delete("/auth/devices/{device_id}")
async def revoke_device(
    device_id: str,
    request: Request,
    _: DeviceRecord = Depends(require_device_auth),
    store: BridgeStore = Depends(get_store),
) -> dict[str, bool]:
    revoked = store.revoke_device(device_id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    hub = getattr(request.app.state, "ws_hub", None)
    if hub is not None:
        await hub.kick_device(device_id)
    return {"ok": True}
