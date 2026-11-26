"""Tests for pairing URI, Tailscale/Cloudflare tunnel parsing, and Antigravity plugin/hooks."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from agy_bridge.protocol import ChannelEndpoint
from agy_bridge.tunnels import (
    build_pairing_uri,
    validate_cloudflare_tunnel_token,
)
from agy_bridge.tunnels.tailscale import parse_tailscale_status_json

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pairing_uri_roundtrip() -> None:
    endpoints = [
        ChannelEndpoint(kind="lan", label="LAN (Wi-Fi)", baseUrl="http://192.168.1.25:18790"),
        ChannelEndpoint(kind="tailscale", label="Tailscale IP", baseUrl="http://100.88.1.2:18790"),
        ChannelEndpoint(
            kind="cloudflare",
            label="Cloudflare Quick Tunnel",
            baseUrl="https://abc-xyz.trycloudflare.com",
        ),
    ]
    uri = build_pairing_uri(
        host_name="My Windows PC",
        pairing_token="a" * 48,
        endpoints=endpoints,
    )
    parsed = urlparse(uri)
    assert parsed.scheme == "agygo"
    assert parsed.netloc == "pair"
    qs = parse_qs(parsed.query)
    assert qs["v"] == ["1"]
    assert qs["name"] == ["My Windows PC"]
    assert qs["token"] == ["a" * 48]

    raw_eps = qs["eps"][0]
    padded = raw_eps + "=" * ((4 - len(raw_eps) % 4) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    assert len(decoded) == 3
    assert decoded[0]["k"] == "lan"
    assert decoded[2]["u"] == "https://abc-xyz.trycloudflare.com"


def test_tailscale_status_json_parsing() -> None:
    sample = json.dumps(
        {
            "BackendState": "Running",
            "Self": {
                "DNSName": "workstation.tail1234.ts.net.",
                "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"],
            },
        }
    )
    eps = parse_tailscale_status_json(sample, 18790)
    assert len(eps) == 2
    assert eps[0].baseUrl == "http://workstation.tail1234.ts.net:18790"
    assert eps[1].baseUrl == "http://100.101.102.103:18790"

    stopped = json.dumps({"BackendState": "Stopped", "Self": {"TailscaleIPs": ["100.101.102.103"]}})
    assert parse_tailscale_status_json(stopped, 18790) == []


def test_cloudflare_token_validation() -> None:
    valid_payload = base64.b64encode(
        json.dumps({"a": "acct123", "t": "tun456", "s": "sec789"}).encode("utf-8")
    ).decode("ascii")
    ok, token, err = validate_cloudflare_tunnel_token(
        f"cloudflared.exe service install {valid_payload}"
    )
    assert ok is True
    assert token == valid_payload
    assert err == ""

    bad_ok, _, bad_err = validate_cloudflare_tunnel_token("not-a-token")
    assert bad_ok is False
    assert bad_err != ""


def test_plugin_manifests_are_valid_json() -> None:
    plugin_dir = REPO_ROOT / "plugin"
    plugin_json = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    sidecar_json = json.loads(
        (plugin_dir / "sidecars" / "bridge" / "sidecar.json").read_text(encoding="utf-8")
    )
    hooks_json = json.loads((plugin_dir / "hooks.json").read_text(encoding="utf-8"))

    assert plugin_json["name"] == "antigravity-go"
    assert sidecar_json["command"] == "python"
    assert sidecar_json["restart_policy"] in ("always", "on-failure", "never")
    assert "antigravity-go-observer" in hooks_json
    assert "SessionStart" in hooks_json["antigravity-go-observer"]
    assert "PostToolUse" in hooks_json["antigravity-go-observer"]
    assert "Stop" in hooks_json["antigravity-go-observer"]


def test_hook_forward_script_fails_open_when_bridge_offline(tmp_path: Path) -> None:
    script = REPO_ROOT / "plugin" / "scripts" / "hook_forward.py"
    env = {**os.environ, "AGY_GO_HOME": str(tmp_path / "offline_home")}
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps({"hookEventName": "Stop", "conversationId": "c1"}),
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout.strip()) == {}


def test_hook_ingress_broadcasts_to_authenticated_ws(
    bridge_env: dict[str, Any], paired_headers: dict[str, str]
) -> None:
    client: TestClient = bridge_env["client"]
    hook_secret = bridge_env["cfg_mgr"].config.hook_secret

    # Bad hook secret rejected
    bad = client.post(
        "/api/v1/internal/hooks",
        headers={"X-Agy-Hook-Secret": "wrong"},
        json={"hookEventName": "Stop", "conversationId": "conv_9"},
    )
    assert bad.status_code == 401

    # Valid hook secret broadcasts event over WebSocket
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": paired_headers["_device_token"]})
        assert ws.receive_json()["type"] == "auth.ok"

        res = client.post(
            "/api/v1/internal/hooks",
            headers={"X-Agy-Hook-Secret": hook_secret},
            json={
                "hookEventName": "Stop",
                "conversationId": "conv_9",
                "terminationReason": "NO_TOOL_CALL",
            },
        )
        assert res.status_code == 200
        pushed = ws.receive_json()
        assert pushed["type"] == "hook.event"
        assert pushed["data"]["hookEventName"] == "Stop"
        assert pushed["data"]["terminationReason"] == "NO_TOOL_CALL"
