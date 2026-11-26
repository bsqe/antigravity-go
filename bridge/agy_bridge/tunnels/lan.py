"""LAN IPv4 interface ranking, fused pairing URI encoding, and terminal QR rendering."""

from __future__ import annotations

import base64
import ipaddress
import io
import json
import socket
from urllib.parse import quote

from ..protocol import ChannelEndpoint

VIRTUAL_IFACE_HINTS = (
    "vethernet",
    "wsl",
    "hyper-v",
    "docker",
    "vmware",
    "virtualbox",
    "vbox",
    "utun",
    "tailscale",
    "zerotier",
    "tun",
    "tap",
    "loopback",
    "npcap",
    "bridge",
)


def _is_tailscale_ip(ip_obj: ipaddress.IPv4Address) -> bool:
    return ip_obj in ipaddress.IPv4Network("100.64.0.0/10")


def _score_interface(name: str, ip_str: str) -> int:
    lower = name.lower()
    score = 100
    if any(hint in lower for hint in VIRTUAL_IFACE_HINTS):
        score -= 60
    if any(k in lower for k in ("wi-fi", "wifi", "wlan", "en0", "ethernet", "eth")):
        score += 30
    if ip_str.startswith("192.168."):
        score += 20
    elif ip_str.startswith("10."):
        score += 10
    return score


def discover_lan_endpoints(port: int) -> list[ChannelEndpoint]:
    """Return routable LAN IPv4 endpoints sorted with physical NICs first."""
    candidates: list[tuple[int, str, str]] = []
    seen_ips: set[str] = set()

    try:
        import psutil  # type: ignore[import-untyped]

        for iface_name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family != socket.AF_INET:
                    continue
                ip_str = addr.address
                try:
                    ip_obj = ipaddress.IPv4Address(ip_str)
                except ValueError:
                    continue
                if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
                    continue
                if _is_tailscale_ip(ip_obj):
                    continue
                if not ip_obj.is_private:
                    continue
                if ip_str in seen_ips:
                    continue
                seen_ips.add(ip_str)
                candidates.append((_score_interface(iface_name, ip_str), iface_name, ip_str))
    except Exception:
        pass

    # Fallback probe via UDP socket route table if psutil unavailable or returned nothing
    if not candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip_str = s.getsockname()[0]
                ip_obj = ipaddress.IPv4Address(ip_str)
                if not ip_obj.is_loopback and not _is_tailscale_ip(ip_obj):
                    candidates.append((100, "default", ip_str))
        except OSError:
            pass

    candidates.sort(key=lambda item: (-item[0], item[2]))
    return [
        ChannelEndpoint(
            kind="lan",
            label=f"LAN ({iface})",
            baseUrl=f"http://{ip_str}:{port}",
            available=True,
            detail=iface,
        )
        for _, iface, ip_str in candidates
    ]


def build_pairing_uri(
    *,
    host_name: str,
    pairing_token: str,
    endpoints: list[ChannelEndpoint],
) -> str:
    """Encode all discovered endpoints into one compact `agygo://pair` QR payload."""
    ep_payload = [
        {"k": ep.kind, "u": ep.baseUrl, "l": ep.label}
        for ep in endpoints
        if ep.available and ep.baseUrl
    ]
    raw_json = json.dumps(ep_payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded_eps = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    safe_name = quote(host_name.strip() or "Desktop", safe="")
    return f"agygo://pair?v=1&name={safe_name}&token={pairing_token}&eps={encoded_eps}"


def render_ascii_qr(data: str) -> str:
    """Render a compact terminal-friendly QR code string."""
    try:
        import qrcode  # type: ignore[import-untyped]

        qr = qrcode.QRCode(border=1)
        qr.add_data(data)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        return buf.getvalue()
    except Exception:
        return f"[QR unavailable - copy pairing URI manually]\n{data}\n"
