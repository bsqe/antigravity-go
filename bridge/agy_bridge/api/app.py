"""FastAPI application assembly and lifespan wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import __version__
from ..config import ConfigManager
from ..hooks_receiver import router as hooks_router
from ..runtime.antigravity_sdk import AntigravitySdkRuntime
from ..runtime.base import AgentRuntime
from ..store import BridgeStore
from ..tunnels import CloudflaredManager
from .routes_auth import router as auth_router
from .routes_channels import router as channels_router
from .routes_projects import router as projects_router
from .routes_sessions import router as sessions_router
from .ws_stream import ConnectionHub, router as ws_router


def create_app(
    *,
    config_manager: ConfigManager | None = None,
    store: BridgeStore | None = None,
    runtime: AgentRuntime | None = None,
    tunnel_manager: CloudflaredManager | None = None,
    auto_start_tunnel: bool = True,
) -> FastAPI:
    cfg_mgr = config_manager or ConfigManager()
    db_store = store or BridgeStore(cfg_mgr.db_path)
    agent_runtime = runtime or AntigravitySdkRuntime(
        cfg_mgr.config, cfg_mgr.data_dir / "sdk_sessions"
    )
    cf_manager = tunnel_manager or CloudflaredManager(cfg_mgr.config.port)
    hub = ConnectionHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if auto_start_tunnel:
            cfg = cfg_mgr.config
            if cfg.cloudflared_mode == "quick" and cf_manager.installed:
                await cf_manager.start_quick()
            elif (
                cfg.cloudflared_mode == "named"
                and cf_manager.installed
                and cfg.cloudflared_token
                and cfg.cloudflared_hostname
            ):
                await cf_manager.start_named(cfg.cloudflared_token, cfg.cloudflared_hostname)
        try:
            yield
        finally:
            await agent_runtime.shutdown()
            await cf_manager.stop()
            if store is None:
                db_store.close()

    app = FastAPI(
        title="Antigravity-Go Bridge",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.config_manager = cfg_mgr
    app.state.store = db_store
    app.state.runtime = agent_runtime
    app.state.tunnel_manager = cf_manager
    app.state.ws_hub = hub

    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(sessions_router)
    app.include_router(channels_router)
    app.include_router(hooks_router)
    app.include_router(ws_router)
    return app
