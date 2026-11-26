"""Tailscale local node IP (100.64.0.0/10) and MagicDNS discovery."""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import sys
from pathlib import Path

from ..protocol import ChannelEndpoint

TAILSCALE_CANDIDATES = (
    "tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
)


def find_tailscale_binary() -> str | None:
    for candidate in TAILSCALE_CANDIDATES:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        p = Path(candidate)
        if p.is_file():
            return str(p)
    return None


def is_tailscale_installed() -> bool:
    return find_tailscale_binary() is not None


def parse_tailscale_status_json(raw_json: str, port: int) -> list[ChannelEndpoint]:
    """Parse `tailscale status --json` into Tailscale endpoints (MagicDNS + IPv4)."""
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        return []
    backend_state = str(data.get("BackendState", "")).strip()
    if backend_state and backend_state.lower() != "running":
        return []

    self_node = data.get("Self")
    if not isinstance(self_node, dict):
        return []

    endpoints: list[ChannelEndpoint] = []
    dns_name = str(self_node.get("DNSName", "")).strip().rstrip(".")
    if dns_name:
        endpoints.append(
            ChannelEndpoint(
                kind="tailscale",
                label="Tailscale MagicDNS",
                baseUrl=f"http://{dns_name}:{port}",
                available=True,
                detail=dns_name,
            )
        )

    ips = self_node.get("TailscaleIPs") or []
    for raw_ip in ips:
        try:
            ip_obj = ipaddress.IPv4Address(str(raw_ip).strip())
        except ValueError:
            continue
        if ip_obj in ipaddress.IPv4Network("100.64.0.0/10"):
            endpoints.append(
                ChannelEndpoint(
                    kind="tailscale",
                    label="Tailscale IP",
                    baseUrl=f"http://{ip_obj}:{port}",
                    available=True,
                    detail=str(ip_obj),
                )
            )
            break

    return endpoints


def detect_tailscale_endpoints(port: int, timeout_sec: float = 2.5) -> list[ChannelEndpoint]:
    binary = find_tailscale_binary()
    if not binary:
        return []
    try:
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": True,
            "timeout": timeout_sec,
            "check": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.run([binary, "status", "--json"], **kwargs)  # type: ignore[arg-type]
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        return parse_tailscale_status_json(proc.stdout, port)
    except Exception:
        return []
