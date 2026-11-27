"""LAN / Tailscale / Cloudflare Tunnel discovery and lifecycle control endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import get_config_manager, require_device_auth
from ..config import ConfigManager
from ..protocol import ChannelEndpoint, ChannelsStatusResponse, TunnelConfigRequest
from ..store import DeviceRecord
from ..tunnels import (
    CloudflaredManager,
    build_pairing_uri,
    detect_tailscale_endpoints,
    discover_lan_endpoints,
    is_tailscale_installed,
    validate_cloudflare_tunnel_token,
)

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])


def collect_channel_status(
    cfg_mgr: ConfigManager, tunnel_mgr: CloudflaredManager
) -> ChannelsStatusResponse:
    cfg = cfg_mgr.config
    endpoints: list[ChannelEndpoint] = []

    if cfg.lan_enabled:
        endpoints.extend(discover_lan_endpoints(cfg.port))

    ts_installed = is_tailscale_installed()
    if cfg.tailscale_enabled and ts_installed:
        endpoints.extend(detect_tailscale_endpoints(cfg.port))

    cf_ep = tunnel_mgr.current_endpoint()
    if cf_ep is not None:
        endpoints.append(cf_ep)

    pairing_uri = build_pairing_uri(
        host_name=cfg.host_name,
        pairing_token=cfg.pairing_token,
        endpoints=endpoints,
    )
    effective_cf_mode = tunnel_mgr.state.mode if tunnel_mgr.state.running else cfg.cloudflared_mode
    return ChannelsStatusResponse(
        hostName=cfg.host_name,
        port=cfg.port,
        pairingUri=pairing_uri,
        endpoints=endpoints,
        tailscaleInstalled=ts_installed,
        cloudflaredInstalled=tunnel_mgr.installed,
        cloudflaredMode=effective_cf_mode,
        cloudflaredRunning=tunnel_mgr.state.running,
        hasNamedTunnelToken=bool(cfg.cloudflared_token),
    )


@router.get("", response_model=ChannelsStatusResponse)
async def get_channels_status(
    request: Request,
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
) -> ChannelsStatusResponse:
    tunnel_mgr: CloudflaredManager = request.app.state.tunnel_manager
    return collect_channel_status(cfg_mgr, tunnel_mgr)


@router.post("/tunnel", response_model=ChannelsStatusResponse)
async def configure_tunnel(
    body: TunnelConfigRequest,
    request: Request,
    _: DeviceRecord = Depends(require_device_auth),
    cfg_mgr: ConfigManager = Depends(get_config_manager),
) -> ChannelsStatusResponse:
    tunnel_mgr: CloudflaredManager = request.app.state.tunnel_manager
    cfg = cfg_mgr.config

    if body.mode == "off":
        await tunnel_mgr.stop()
        cfg_mgr.save(cfg.model_copy(update={"cloudflared_mode": "off"}))
        return collect_channel_status(cfg_mgr, tunnel_mgr)

    if body.mode == "quick":
        state = await tunnel_mgr.start_quick()
        cfg_mgr.save(cfg.model_copy(update={"cloudflared_mode": "quick"}))
        if not state.running and state.last_error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=state.last_error,
            )
        return collect_channel_status(cfg_mgr, tunnel_mgr)

    # Named tunnel
    effective_token = (body.token or "").strip() or cfg.cloudflared_token
    effective_host = (body.hostname or "").strip() or cfg.cloudflared_hostname
    ok, clean_token, err = validate_cloudflare_tunnel_token(effective_token)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    state = await tunnel_mgr.start_named(clean_token, effective_host)
    if not state.running and state.last_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=state.last_error,
        )
    cfg_mgr.save(
        cfg.model_copy(
            update={
                "cloudflared_mode": "named",
                "cloudflared_token": clean_token,
                "cloudflared_hostname": effective_host,
            }
        )
    )
    return collect_channel_status(cfg_mgr, tunnel_mgr)
