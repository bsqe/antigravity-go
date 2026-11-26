"""Command-line and double-click launcher entry point for Antigravity-Go Bridge."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
from pathlib import Path

import uvicorn

from . import __version__
from .api.app import create_app
from .api.routes_channels import collect_channel_status
from .config import ConfigManager, validate_workspace_path
from .store import BridgeStore
from .tunnels import CloudflaredManager, render_ascii_qr


def _ensure_stdio() -> None:
    """Guard against None stdio handles and GBK encoding errors on Windows."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "a", encoding="utf-8", buffering=1)
    elif hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "a", encoding="utf-8", buffering=1)
    elif hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _print_pairing_banner(cfg_mgr: ConfigManager, cf_mgr: CloudflaredManager) -> None:
    status = collect_channel_status(cfg_mgr, cf_mgr)
    print(f"\n=== Antigravity-Go Bridge v{__version__} ===")
    print(f"Host Name   : {status.hostName}")
    print(f"Listen Port : {status.port}")
    print(f"Auth Mode   : {cfg_mgr.config.auth_mode}")
    print(f"API Key Set : {'Yes' if cfg_mgr.config.effective_api_key() else 'No'}")
    print(f"Data Dir    : {cfg_mgr.data_dir}")
    print("Endpoints   :")
    if status.endpoints:
        for ep in status.endpoints:
            print(f"  - [{ep.kind}] {ep.baseUrl} ({ep.label})")
    else:
        print(f"  - [loopback] http://127.0.0.1:{status.port}")
    print("\nScan this QR code in the Antigravity-Go mobile app:")
    print(render_ascii_qr(status.pairingUri))
    print(f"Pairing URI : {status.pairingUri}\n")


def _interactive_first_run_setup(cfg_mgr: ConfigManager) -> None:
    """Friendly one-click setup when double-clicking AntigravityGoBridge.exe."""
    if not (sys.stdin is not None and sys.stdin.isatty()):
        return

    cfg = cfg_mgr.config
    if cfg.auth_mode == "api_key" and not cfg.effective_api_key():
        print("\n[首次启动引导] 未检测到 Gemini / Vertex Express API Key。")
        try:
            entered = input("请输入 API Key (AIza... 或 AQ....，直接回车可稍后配置): ").strip()
        except EOFError:
            entered = ""
        if entered:
            cfg = cfg_mgr.save(
                cfg.model_copy(update={"gemini_api_key": entered, "auth_mode": "api_key"})
            )
            print(f"已保存 API Key 到 {cfg_mgr.config_path} (模式: api_key)。\n")

    store = BridgeStore(cfg_mgr.db_path)
    try:
        existing_projects = store.list_projects()
        if not existing_projects:
            print("[首次启动引导] 尚未登记项目工作区目录。")
            try:
                raw_dir = input("请输入要给手机访问的本地项目路径 (直接回车跳过): ").strip().strip('"')
            except EOFError:
                raw_dir = ""
            if raw_dir:
                candidate = Path(raw_dir).expanduser().resolve()
                if candidate.is_dir():
                    roots = list(cfg.allowed_roots)
                    if str(candidate) not in roots:
                        roots.append(str(candidate))
                        cfg = cfg_mgr.save(cfg.model_copy(update={"allowed_roots": roots}))
                    validated = validate_workspace_path(str(candidate), cfg)
                    proj = store.create_project(name=validated.name, path=str(validated))
                    print(f"已登记项目: {proj.name} -> {proj.path}\n")
                else:
                    print(f"目录不存在，已跳过: {raw_dir}\n")
    finally:
        store.close()


def cmd_start(args: argparse.Namespace) -> int:
    cfg_mgr = ConfigManager(args.data_dir)
    if getattr(args, "interactive_setup", False):
        _interactive_first_run_setup(cfg_mgr)

    cfg = cfg_mgr.config
    updates: dict[str, object] = {}

    if args.port is not None:
        updates["port"] = args.port
    if args.local_only:
        updates["bind_host"] = "127.0.0.1"
    elif args.host is not None:
        updates["bind_host"] = args.host
    if args.auth_mode is not None:
        updates["auth_mode"] = args.auth_mode
    if args.cloudflared is not None:
        updates["cloudflared_mode"] = args.cloudflared
    if args.allow_write_tools:
        updates["sdk_allow_write_tools"] = True

    if updates:
        cfg = cfg_mgr.save(cfg.model_copy(update=updates))

    cf_mgr = CloudflaredManager(cfg.port)
    if not args.managed:
        _print_pairing_banner(cfg_mgr, cf_mgr)

    logging.basicConfig(
        level=logging.WARNING if args.managed else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    app = create_app(config_manager=cfg_mgr, tunnel_manager=cf_mgr)
    uvicorn.run(
        app,
        host=cfg.bind_host,
        port=cfg.port,
        use_colors=False,
        log_config=None,
    )
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    cfg_mgr = ConfigManager(args.data_dir)
    cf_mgr = CloudflaredManager(cfg_mgr.config.port)
    _print_pairing_banner(cfg_mgr, cf_mgr)
    return 0


def cmd_reset_token(args: argparse.Namespace) -> int:
    cfg_mgr = ConfigManager(args.data_dir)
    new_token = cfg_mgr.rotate_pairing_token()
    print(f"Rotated pairing token (first 8 chars): {new_token[:8]}...")
    print("Existing QR codes are now invalid. Run `agy-bridge pair` to show the new QR code.")
    return 0


def cmd_add_project(args: argparse.Namespace) -> int:
    cfg_mgr = ConfigManager(args.data_dir)
    if args.trust_root:
        candidate = str(Path(args.path).expanduser().resolve())
        roots = list(cfg_mgr.config.allowed_roots)
        if candidate not in roots:
            roots.append(candidate)
            cfg_mgr.save(cfg_mgr.config.model_copy(update={"allowed_roots": roots}))
    validated = validate_workspace_path(args.path, cfg_mgr.config)
    store = BridgeStore(cfg_mgr.db_path)
    try:
        name = args.name or Path(validated).name
        proj = store.create_project(name=name, path=str(validated))
        print(f"Registered project: {proj.id} ({proj.name}) -> {proj.path}")
    finally:
        store.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg_mgr = ConfigManager(args.data_dir)
    cf_mgr = CloudflaredManager(cfg_mgr.config.port)
    status = collect_channel_status(cfg_mgr, cf_mgr)
    store = BridgeStore(cfg_mgr.db_path)
    try:
        projects = store.list_projects()
        devices = store.list_devices()
    finally:
        store.close()
    print(f"Antigravity-Go Bridge v{__version__}")
    print(f"Config      : {cfg_mgr.config_path}")
    print(f"Host        : {status.hostName} ({cfg_mgr.config.bind_host}:{status.port})")
    print(f"SDK Auth    : {cfg_mgr.config.auth_mode} (API key set: {bool(cfg_mgr.config.effective_api_key())})")
    print(f"Tailscale   : {'installed' if status.tailscaleInstalled else 'not found'}")
    print(f"Cloudflared : {'installed' if status.cloudflaredInstalled else 'not found'} (mode={status.cloudflaredMode})")
    print(f"Projects    : {len(projects)}")
    print(f"Devices     : {len([d for d in devices if not d.revoked])} active / {len(devices)} total")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="AntigravityGoBridge",
        description="Antigravity-Go desktop bridge (official google-antigravity SDK)",
    )
    parser.add_argument("--data-dir", default=None, help="Override state directory (default: ~/.antigravity-go)")
    sub = parser.add_subparsers(dest="command", required=False)

    p_start = sub.add_parser("start", help="Run the bridge HTTP/WebSocket server")
    p_start.add_argument("--host", default=None, help="Bind host (default: 0.0.0.0)")
    p_start.add_argument("--port", type=int, default=None, help="Bind port (default: 18790)")
    p_start.add_argument("--local-only", action="store_true", help="Bind strictly to 127.0.0.1")
    p_start.add_argument("--managed", action="store_true", help="Run quietly under Antigravity Sidecar")
    p_start.add_argument("--auth-mode", choices=["api_key", "vertex"], default=None)
    p_start.add_argument("--cloudflared", choices=["off", "quick", "named"], default=None)
    p_start.add_argument("--allow-write-tools", action="store_true", help="Enable SDK file-write/command tools")
    p_start.set_defaults(func=cmd_start, interactive_setup=False)

    p_pair = sub.add_parser("pair", help="Display current pairing QR code and endpoints")
    p_pair.set_defaults(func=cmd_pair)

    p_reset = sub.add_parser("reset-token", help="Rotate the 48-char pairing token")
    p_reset.set_defaults(func=cmd_reset_token)

    p_proj = sub.add_parser("add-project", help="Register an allowed local workspace directory")
    p_proj.add_argument("path", help="Path to local workspace folder")
    p_proj.add_argument("--name", default=None, help="Display name for the project")
    p_proj.add_argument(
        "--trust-root",
        action="store_true",
        help="Automatically add this directory to allowed_roots in bridge-config.json",
    )
    p_proj.set_defaults(func=cmd_add_project)

    p_status = sub.add_parser("status", help="Show bridge configuration and channel diagnostics")
    p_status.set_defaults(func=cmd_status)
    return parser


def cli(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    _ensure_stdio()
    raw_args = sys.argv[1:] if argv is None else argv
    double_click_mode = len(raw_args) == 0

    parser = build_parser()
    if double_click_mode:
        args = parser.parse_args(["start"])
        args.interactive_setup = True
    else:
        args = parser.parse_args(raw_args)
        if not hasattr(args, "func"):
            args = parser.parse_args([*raw_args, "start"])

    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nBridge stopped.")
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Bridge failed to start: {exc}")
        if double_click_mode and sys.stdin is not None and sys.stdin.isatty():
            try:
                input("\n按回车键退出...")
            except EOFError:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
