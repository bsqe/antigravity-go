#!/usr/bin/env python3
"""Antigravity Hook forwarder for Antigravity-Go Bridge.

Reads the camelCase JSON payload provided by Antigravity on stdin, forwards a
compact observation event to the local Bridge on 127.0.0.1 using the shared
hook secret, and ALWAYS outputs `{}` on stdout so it never blocks the agent
loop when the Bridge is stopped or unreachable.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def resolve_config_path() -> Path:
    env_home = os.environ.get("AGY_GO_HOME", "").strip()
    base = Path(env_home).expanduser() if env_home else (Path.home() / ".antigravity-go")
    return base / "bridge-config.json"


def load_bridge_target() -> tuple[int, str] | None:
    cfg_path = resolve_config_path()
    if not cfg_path.is_file():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        port = int(cfg.get("port", 18790))
        secret = str(cfg.get("hook_secret", "")).strip()
        if not secret or port <= 0 or port > 65535:
            return None
        return port, secret
    except Exception:
        return None


def forward_event(payload: dict) -> None:
    target = load_bridge_target()
    if target is None:
        return
    port, secret = target
    body = json.dumps(
        {
            "hookEventName": str(payload.get("hookEventName") or "Unknown"),
            "conversationId": payload.get("conversationId"),
            "cwd": payload.get("cwd"),
            "modelName": payload.get("modelName"),
            "toolCall": payload.get("toolCall"),
            "terminationReason": payload.get("terminationReason"),
            "error": payload.get("error"),
            "raw": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        url=f"http://127.0.0.1:{port}/api/v1/internal/hooks",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agy-Hook-Secret": secret,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5):
            pass
    except Exception:
        # Fail-open by design: never disrupt the Antigravity agent loop.
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
            if isinstance(payload, dict):
                forward_event(payload)
    except Exception:
        pass
    sys.stdout.write("{}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
