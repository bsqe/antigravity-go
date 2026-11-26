"""Cloudflare Quick Tunnel (`trycloudflare.com`) and Named Tunnel lifecycle manager."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..protocol import ChannelEndpoint

TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
TOKEN_EXTRACT_RE = re.compile(r"\beyJ[A-Za-z0-9+/_-]{12,}=*(?:\.[A-Za-z0-9+/_-]+=*)*")

CLOUDFLARED_CANDIDATES = (
    "cloudflared",
    r"C:\Program Files\cloudflared\cloudflared.exe",
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "/opt/homebrew/bin/cloudflared",
    "/usr/local/bin/cloudflared",
    "/usr/bin/cloudflared",
)


def find_cloudflared_binary() -> str | None:
    for candidate in CLOUDFLARED_CANDIDATES:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        p = Path(candidate)
        if p.is_file():
            return str(p)
    return None


def validate_cloudflare_tunnel_token(raw: str) -> tuple[bool, str, str]:
    """Validate and normalize a Cloudflare Tunnel token (or `service install` snippet)."""
    text = raw.strip()
    if not text:
        return False, "", "Cloudflare Tunnel token is empty."
    match = TOKEN_EXTRACT_RE.search(text)
    if not match:
        return False, "", "Could not find a valid Cloudflare Tunnel token starting with 'eyJ'."
    token = match.group(0)

    if "." not in token:
        norm = token.rstrip("=").replace("-", "+").replace("_", "/")
        pad = (4 - len(norm) % 4) % 4
        try:
            decoded = json.loads(base64.b64decode(norm + "=" * pad).decode("utf-8"))
        except Exception:
            return False, "", "Malformed base64 Cloudflare Tunnel token."
        if not isinstance(decoded, dict) or not all(
            isinstance(decoded.get(k), str) and decoded[k].strip() for k in ("a", "t", "s")
        ):
            return False, "", "Cloudflare Tunnel token is missing required fields (a/t/s)."
        return True, token, ""

    parts = token.split(".")
    if len(parts) not in (2, 3) or any(not p for p in parts):
        return False, "", "Malformed JWT Cloudflare Tunnel token."
    return True, token, ""


@dataclass(slots=True)
class TunnelState:
    mode: Literal["off", "quick", "named"] = "off"
    running: bool = False
    public_url: str = ""
    last_error: str = ""


class CloudflaredManager:
    """Supervises an optional `cloudflared` child process forwarding to `127.0.0.1:<port>`."""

    def __init__(self, local_port: int) -> None:
        self.local_port = local_port
        self.state = TunnelState()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._url_ready = asyncio.Event()

    @property
    def installed(self) -> bool:
        return find_cloudflared_binary() is not None

    def current_endpoint(self) -> ChannelEndpoint | None:
        if not self.state.running or not self.state.public_url:
            return None
        label = (
            "Cloudflare Tunnel"
            if self.state.mode == "named"
            else "Cloudflare Quick Tunnel"
        )
        return ChannelEndpoint(
            kind="cloudflare",
            label=label,
            baseUrl=self.state.public_url.rstrip("/"),
            available=True,
            detail=self.state.mode,
        )

    async def start_quick(self, timeout_sec: float = 15.0) -> TunnelState:
        await self.stop()
        binary = find_cloudflared_binary()
        if not binary:
            self.state = TunnelState(
                mode="quick",
                running=False,
                last_error="cloudflared binary not found in PATH",
            )
            return self.state

        self._url_ready.clear()
        self.state = TunnelState(mode="quick", running=False)
        spawn_kwargs: dict[str, object] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if sys.platform == "win32":
            spawn_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._proc = await asyncio.create_subprocess_exec(
            binary,
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"http://127.0.0.1:{self.local_port}",
            **spawn_kwargs,  # type: ignore[arg-type]
        )
        self._reader_task = asyncio.create_task(self._consume_output(expect_quick_url=True))
        try:
            await asyncio.wait_for(self._url_ready.wait(), timeout=timeout_sec)
        except TimeoutError:
            self.state.last_error = "Timed out waiting for trycloudflare.com URL"
        return self.state

    async def start_named(self, token: str, hostname: str) -> TunnelState:
        await self.stop()
        ok, clean_token, err = validate_cloudflare_tunnel_token(token)
        if not ok:
            self.state = TunnelState(mode="named", running=False, last_error=err)
            return self.state

        clean_host = hostname.strip().removeprefix("https://").removeprefix("http://").strip("/")
        if not clean_host or "." not in clean_host:
            self.state = TunnelState(
                mode="named",
                running=False,
                last_error="A valid public hostname (e.g. agy.example.com) is required for named tunnels.",
            )
            return self.state

        binary = find_cloudflared_binary()
        if not binary:
            self.state = TunnelState(
                mode="named",
                running=False,
                last_error="cloudflared binary not found in PATH",
            )
            return self.state

        self.state = TunnelState(
            mode="named",
            running=True,
            public_url=f"https://{clean_host}",
        )
        spawn_kwargs: dict[str, object] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if sys.platform == "win32":
            spawn_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._proc = await asyncio.create_subprocess_exec(
            binary,
            "tunnel",
            "--no-autoupdate",
            "run",
            "--token",
            clean_token,
            **spawn_kwargs,  # type: ignore[arg-type]
        )
        self._reader_task = asyncio.create_task(self._consume_output(expect_quick_url=False))
        return self.state

    async def stop(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._proc is not None:
            if self._proc.returncode is None:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=4.0)
                except TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            self._proc = None

        self.state = TunnelState(mode="off", running=False, public_url="", last_error="")

    async def _consume_output(self, *, expect_quick_url: bool) -> None:
        assert self._proc is not None
        stream = self._proc.stdout
        if stream is None:
            return
        try:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if expect_quick_url:
                    match = TRYCLOUDFLARE_RE.search(line)
                    if match:
                        self.state.public_url = match.group(0)
                        self.state.running = True
                        self.state.last_error = ""
                        self._url_ready.set()
        except asyncio.CancelledError:
            raise
        finally:
            if self._proc is not None and self._proc.returncode is not None:
                self.state.running = False
