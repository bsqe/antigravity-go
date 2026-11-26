"""Persistent bridge configuration, secrets, and workspace boundary guards."""

from __future__ import annotations

import json
import os
import secrets
import socket
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_PORT = 18790
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

SENSITIVE_DIR_NAMES = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".kube",
        ".azure",
        ".config/gcloud",
        "system32",
        "syswow64",
    }
)


def resolve_data_dir(override: str | Path | None = None) -> Path:
    """Resolve the bridge state directory (`AGY_GO_HOME` or `~/.antigravity-go`)."""
    if override is not None:
        base = Path(override).expanduser().resolve()
    elif env_dir := os.environ.get("AGY_GO_HOME", "").strip():
        base = Path(env_dir).expanduser().resolve()
    else:
        base = (Path.home() / ".antigravity-go").resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


class BridgeConfig(BaseModel):
    """On-disk configuration persisted at `<data_dir>/bridge-config.json`."""

    version: int = 1
    host_name: str = Field(default_factory=lambda: socket.gethostname() or "Desktop")
    bind_host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    pairing_token: str = Field(default_factory=lambda: secrets.token_hex(24))
    hook_secret: str = Field(default_factory=lambda: secrets.token_hex(16))

    # Official google-antigravity SDK authentication mode
    auth_mode: Literal["api_key", "vertex"] = "api_key"
    gemini_api_key: str = ""
    default_model: str = ""
    sdk_allow_write_tools: bool = False

    # Workspace boundary roots (defaults to user home when empty)
    allowed_roots: list[str] = Field(default_factory=list)

    # Cross-network channels
    lan_enabled: bool = True
    tailscale_enabled: bool = True
    cloudflared_mode: Literal["off", "quick", "named"] = "off"
    cloudflared_token: str = ""
    cloudflared_hostname: str = ""

    def effective_api_key(self) -> str:
        return (
            os.environ.get("GEMINI_API_KEY", "").strip()
            or os.environ.get("GOOGLE_API_KEY", "").strip()
            or self.gemini_api_key.strip()
        )

    def effective_allowed_roots(self) -> list[Path]:
        if self.allowed_roots:
            return [Path(p).expanduser().resolve() for p in self.allowed_roots if p.strip()]
        return [Path.home().resolve()]


def is_sensitive_workspace(target: Path) -> bool:
    """Reject OS system directories and credential folders as project roots."""
    resolved = target.expanduser().resolve()
    parts_lower = [part.lower() for part in resolved.parts]
    joined_posix = "/".join(parts_lower)

    for forbidden in SENSITIVE_DIR_NAMES:
        if "/" in forbidden:
            if forbidden in joined_posix:
                return True
        elif forbidden in parts_lower:
            return True

    # Reject root filesystem or drive root (e.g., C:\ or /)
    if resolved == resolved.parent:
        return True

    # Protect ~/.gemini internal credentials/daemon directories
    gemini_dir = (Path.home() / ".gemini").resolve()
    try:
        resolved.relative_to(gemini_dir)
        return True
    except ValueError:
        pass

    return False


def validate_workspace_path(raw_path: str, config: BridgeConfig) -> Path:
    """Validate that `raw_path` exists, is a directory, and lies within allowed roots."""
    cleaned = raw_path.strip()
    if not cleaned:
        raise ValueError("Workspace path cannot be empty.")

    target = Path(cleaned).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise ValueError(f"Workspace directory does not exist: {target}")

    if is_sensitive_workspace(target):
        raise ValueError(f"Workspace path is restricted for security: {target}")

    roots = config.effective_allowed_roots()
    if not any(_is_within(target, root) for root in roots):
        allowed_desc = ", ".join(str(r) for r in roots)
        raise ValueError(f"Workspace path must be inside allowed roots: {allowed_desc}")

    return target


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


class ConfigManager:
    """Loads, persists, and rotates `bridge-config.json` safely."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = resolve_data_dir(data_dir)
        self.config_path = self.data_dir / "bridge-config.json"
        self.attachments_dir = self.data_dir / "attachments"
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "bridge.sqlite3"
        self._config = self._load_or_create()

    @property
    def config(self) -> BridgeConfig:
        return self._config

    def _load_or_create(self) -> BridgeConfig:
        if self.config_path.exists():
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            cfg = BridgeConfig.model_validate(raw)
            changed = False
            if len(cfg.pairing_token) < 32:
                cfg.pairing_token = secrets.token_hex(24)
                changed = True
            if len(cfg.hook_secret) < 16:
                cfg.hook_secret = secrets.token_hex(16)
                changed = True
            if changed:
                self._write(cfg)
            return cfg

        cfg = BridgeConfig()
        self._write(cfg)
        return cfg

    def _write(self, cfg: BridgeConfig) -> None:
        tmp = self.config_path.with_suffix(".json.tmp")
        payload = json.dumps(cfg.model_dump(mode="json"), indent=2, ensure_ascii=False)
        tmp.write_text(payload, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.config_path)
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass

    def save(self, updated: BridgeConfig) -> BridgeConfig:
        self._write(updated)
        self._config = updated
        return self._config

    def rotate_pairing_token(self) -> str:
        updated = self._config.model_copy(update={"pairing_token": secrets.token_hex(24)})
        self.save(updated)
        return updated.pairing_token
