"""Pairing handshake, hashed device token verification, and loopback hook guards."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from .config import ConfigManager
from .store import BridgeStore, DeviceRecord


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def verify_pairing_token(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def verify_hook_secret(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def mint_device_token() -> tuple[str, str]:
    """Return `(plaintext_token, sha256_hash)`."""
    raw = f"agygo_{secrets.token_urlsafe(32)}"
    return raw, hash_token(raw)


def extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def authenticate_token(store: BridgeStore, raw_token: str | None) -> DeviceRecord | None:
    if not raw_token:
        return None
    device = store.get_active_device_by_hash(hash_token(raw_token))
    if device is None:
        return None
    store.touch_device(device.id)
    return device


def get_store(request: Request) -> BridgeStore:
    return request.app.state.store


def get_config_manager(request: Request) -> ConfigManager:
    return request.app.state.config_manager


async def require_device_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> DeviceRecord:
    store: BridgeStore = request.app.state.store
    token = extract_bearer(authorization)
    device = authenticate_token(store, token)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked device token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return device


async def require_loopback_hook_auth(
    request: Request,
    x_agy_hook_secret: Annotated[str | None, Header()] = None,
) -> None:
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hook ingress is restricted to loopback",
        )
    cfg_mgr: ConfigManager = request.app.state.config_manager
    if not verify_hook_secret(x_agy_hook_secret or "", cfg_mgr.config.hook_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid hook secret",
        )
