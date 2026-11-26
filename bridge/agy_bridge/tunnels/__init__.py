"""LAN discovery, Tailscale detection, Cloudflare Tunnel manager, and pairing URI helpers."""

from .cloudflared import CloudflaredManager, validate_cloudflare_tunnel_token
from .lan import build_pairing_uri, discover_lan_endpoints, render_ascii_qr
from .tailscale import detect_tailscale_endpoints, is_tailscale_installed

__all__ = [
    "CloudflaredManager",
    "build_pairing_uri",
    "detect_tailscale_endpoints",
    "discover_lan_endpoints",
    "is_tailscale_installed",
    "render_ascii_qr",
    "validate_cloudflare_tunnel_token",
]
