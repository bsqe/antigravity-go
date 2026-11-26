"""Tests for pairing handshake, hashed device tokens, revocation, and workspace boundaries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def test_ping_is_unauthenticated(bridge_env: dict[str, Any]) -> None:
    client: TestClient = bridge_env["client"]
    res = client.get("/api/v1/ping")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["hostName"] == "TestPC"
    assert data["service"] == "antigravity-go-bridge"


def test_protected_routes_reject_without_token(bridge_env: dict[str, Any]) -> None:
    client: TestClient = bridge_env["client"]
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/sessions").status_code == 401
    assert client.get("/api/v1/channels").status_code == 401


def test_pairing_stores_only_token_hash_and_supports_revocation(
    bridge_env: dict[str, Any],
) -> None:
    client: TestClient = bridge_env["client"]
    cfg_mgr = bridge_env["cfg_mgr"]

    # Wrong pairing token rejected
    bad = client.post(
        "/api/v1/auth/pair",
        json={"pairingToken": "0" * 48, "deviceName": "Attacker"},
    )
    assert bad.status_code == 403

    # Valid pairing token succeeds
    ok = client.post(
        "/api/v1/auth/pair",
        json={"pairingToken": cfg_mgr.config.pairing_token, "deviceName": "My Phone", "platform": "android"},
    )
    assert ok.status_code == 200
    payload = ok.json()
    raw_token = payload["deviceToken"]
    device_id = payload["deviceId"]
    assert raw_token.startswith("agygo_")

    # Verify raw token is NOT stored in SQLite
    conn = sqlite3.connect(str(cfg_mgr.db_path))
    rows = conn.execute("SELECT token_hash FROM devices WHERE id = ?", (device_id,)).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] != raw_token
    assert len(rows[0][0]) == 64

    headers = {"Authorization": f"Bearer {raw_token}"}
    assert client.get("/api/v1/auth/devices", headers=headers).status_code == 200

    # Revoke device and verify immediate 401
    rev = client.delete(f"/api/v1/auth/devices/{device_id}", headers=headers)
    assert rev.status_code == 200
    assert client.get("/api/v1/auth/devices", headers=headers).status_code == 401


def test_workspace_boundary_validation(
    bridge_env: dict[str, Any], paired_headers: dict[str, str], tmp_path: Path
) -> None:
    client: TestClient = bridge_env["client"]
    auth = {"Authorization": paired_headers["Authorization"]}
    demo_dir: Path = bridge_env["demo_project_dir"]

    # Allowed workspace succeeds
    res = client.post(
        "/api/v1/projects",
        headers=auth,
        json={"name": "Demo Repo", "path": str(demo_dir)},
    )
    assert res.status_code == 201
    proj = res.json()
    assert proj["name"] == "Demo Repo"

    # Outside allowed root rejected
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    bad_outside = client.post(
        "/api/v1/projects",
        headers=auth,
        json={"name": "Outside", "path": str(outside)},
    )
    assert bad_outside.status_code == 400

    # Sensitive directory inside allowed root rejected
    ssh_dir = bridge_env["workspace_root"] / ".ssh"
    ssh_dir.mkdir()
    bad_ssh = client.post(
        "/api/v1/projects",
        headers=auth,
        json={"name": "SSH", "path": str(ssh_dir)},
    )
    assert bad_ssh.status_code == 400
