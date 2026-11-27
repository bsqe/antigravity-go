"""Tests for desktop Antigravity transcript scanning, handoff continuation, incremental sync, and tool policies."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import google.antigravity as ag

from agy_bridge.config import BridgeConfig
from agy_bridge.runtime.antigravity_sdk import AntigravitySdkRuntime
from agy_bridge.store import BridgeStore


def _write_desktop_transcript(
    brain_root: Path,
    conv_id: str,
    entries: list[dict[str, Any]],
    *,
    full_entries: list[dict[str, Any]] | None = None,
    artifacts: dict[str, str] | None = None,
) -> Path:
    conv_dir = brain_root / conv_id
    logs_dir = conv_dir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    (logs_dir / "transcript.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    if full_entries is not None:
        (logs_dir / "transcript_full.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in full_entries) + "\n",
            encoding="utf-8",
        )
    if artifacts:
        for name, body in artifacts.items():
            (conv_dir / name).write_text(body, encoding="utf-8")
            (conv_dir / f"{name}.metadata.json").write_text(
                json.dumps({"summary": f"Summary for {name}"}),
                encoding="utf-8",
            )
    return conv_dir


def test_desktop_conversations_list_detail_continue_and_incremental_sync(
    bridge_env: dict[str, Any], paired_headers: dict[str, str], tmp_path: Path
) -> None:
    client = bridge_env["client"]
    cfg_mgr = bridge_env["cfg_mgr"]
    runtime = bridge_env["runtime"]
    demo_dir: Path = bridge_env["demo_project_dir"]

    brain_root = tmp_path / "desktop_brain"
    brain_root.mkdir()
    cfg_mgr.save(cfg_mgr.config.model_copy(update={"desktop_brain_dir": str(brain_root)}))

    # Register demo project
    proj_res = client.post(
        "/api/v1/projects",
        headers=paired_headers,
        json={"name": "DemoRepo", "path": str(demo_dir)},
    )
    assert proj_res.status_code == 201
    proj_id = proj_res.json()["id"]

    # Create a Bridge-owned session and drop a matching folder in brain_root (must be excluded!)
    own_sess = client.post(
        "/api/v1/sessions",
        headers=paired_headers,
        json={"projectId": proj_id, "title": "Bridge Own Session"},
    ).json()
    _write_desktop_transcript(
        brain_root,
        own_sess["id"],
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": "2026-11-27T01:00:00Z",
                "content": "<USER_REQUEST>\nBridge internal\n</USER_REQUEST>",
            }
        ],
    )

    # Create a subagent-only folder (no USER_EXPLICIT -> must be filtered out!)
    _write_desktop_transcript(
        brain_root,
        "subagent-1111-2222-3333-444444444444",
        [
            {
                "step_index": 0,
                "source": "SYSTEM",
                "type": "USER_INPUT",
                "created_at": "2026-11-27T01:05:00Z",
                "content": "Subagent task prompt",
            }
        ],
    )

    # Create a realistic desktop conversation with truncated content, tool calls, media, and artifacts
    desktop_id = "desktop-aaaa-bbbb-cccc-1234567890ab"
    base_entries = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-11-27T02:00:00Z",
            "content": "<USER_REQUEST>\n帮我检查 DemoRepo 的配置文件\n</USER_REQUEST>\n<ADDITIONAL_METADATA>ignored</ADDITIONAL_METADATA>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-11-27T02:00:05Z",
            "thinking": "Let me inspect the demo repo.",
            "tool_calls": [
                {"name": "read_file", "args": {"Path": str(demo_dir / "README.md")}},
            ],
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "GENERIC",
            "created_at": "2026-11-27T02:00:06Z",
            "content": "Raw tool output omitted from chat bubbles",
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-11-27T02:00:10Z",
            "thinking": "Looks clean.",
            "content": "truncated preview...",
            "truncated_fields": ["content"],
        },
        {
            "step_index": 4,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-11-27T02:01:00Z",
            "content": "<USER_REQUEST>\n\n</USER_REQUEST>",
            "media": [{"mime_type": "image/png", "uri": "C:/tmp/screen.png"}],
        },
        {
            "step_index": 5,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-11-27T02:01:08Z",
            "content": "我已经看到你的截图了，下一步可以直接改代码。",
        },
    ]
    full_entries = list(base_entries)
    full_entries[3] = {
        **base_entries[3],
        "content": "完整未截断回复：DemoRepo 的配置已经全部核查完毕，发现 2 处优化点。",
        "truncated_fields": [],
    }
    _write_desktop_transcript(
        brain_root,
        desktop_id,
        base_entries,
        full_entries=full_entries,
        artifacts={"implementation_plan.md": "# Plan\nStep 1: Add desktop sync.\nStep 2: Ship."},
    )

    # 1. List desktop conversations
    list_res = client.get("/api/v1/desktop/conversations", headers=paired_headers)
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 1
    item = items[0]
    assert item["id"] == desktop_id
    assert item["title"] == "帮我检查 DemoRepo 的配置文件"
    assert item["turnCount"] == 2
    assert item["messageCount"] == 4
    assert item["inferredProjectId"] == proj_id
    assert item["linkedSessionId"] is None
    assert len(item["artifacts"]) == 1
    assert item["artifacts"][0]["name"] == "implementation_plan.md"
    assert item["artifacts"][0]["summary"] == "Summary for implementation_plan.md"

    # 2. Detail view resolves full untruncated content & grouped tool calls
    detail_res = client.get(f"/api/v1/desktop/conversations/{desktop_id}", headers=paired_headers)
    assert detail_res.status_code == 200
    msgs = detail_res.json()["messages"]
    assert len(msgs) == 4
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "帮我检查 DemoRepo 的配置文件"
    assert msgs[1]["role"] == "assistant"
    assert "完整未截断回复" in msgs[1]["content"]
    assert "Let me inspect" in msgs[1]["thoughts"]
    assert msgs[1]["toolEvents"][0]["name"] == "read_file"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"] == "[图片附件 × 1]"

    # 3. Continue on phone creates linked session and imports messages
    cont_res = client.post(
        f"/api/v1/desktop/conversations/{desktop_id}/continue",
        headers=paired_headers,
        json={"projectId": proj_id},
    )
    assert cont_res.status_code == 201
    cont_body = cont_res.json()
    assert cont_body["reused"] is False
    assert cont_body["importedMessages"] == 4
    session_id = cont_body["session"]["id"]
    assert cont_body["session"]["sourceDesktopId"] == desktop_id

    page_res = client.get(f"/api/v1/sessions/{session_id}/messages", headers=paired_headers)
    assert page_res.status_code == 200
    assert len(page_res.json()["messages"]) == 4

    # 4. Sending a turn via WS passes desktop handoff context into runtime.start_turn
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": paired_headers["_device_token"]})
        assert ws.receive_json()["type"] == "auth.ok"
        ws.send_json({"type": "turn.send", "sessionId": session_id, "text": "继续第二步"})
        while True:
            ev = ws.receive_json()
            if ev["type"] == "turn.done":
                break

    assert len(runtime.received_calls) == 1
    sys_inst = runtime.received_calls[0]["system_instructions"]
    assert sys_inst is not None
    assert desktop_id in sys_inst
    assert "implementation_plan.md" in sys_inst
    assert "完整未截断回复" in sys_inst

    # 5. Append a new turn on desktop and incrementally sync without duplicates
    updated_entries = [
        *base_entries,
        {
            "step_index": 6,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-11-27T02:05:00Z",
            "content": "<USER_REQUEST>\n我在电脑上又加了一条需求\n</USER_REQUEST>",
        },
        {
            "step_index": 7,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-11-27T02:05:06Z",
            "content": "好的，电脑端已经记下了这条新需求。",
        },
    ]
    _write_desktop_transcript(brain_root, desktop_id, updated_entries, full_entries=full_entries)

    sync_res = client.post(f"/api/v1/sessions/{session_id}/sync-desktop", headers=paired_headers)
    assert sync_res.status_code == 200
    assert sync_res.json()["addedMessages"] == 2
    assert sync_res.json()["syncedStep"] == 7

    # Calling sync a second time is strictly idempotent
    sync_again = client.post(f"/api/v1/sessions/{session_id}/sync-desktop", headers=paired_headers)
    assert sync_again.status_code == 200
    assert sync_again.json()["addedMessages"] == 0

    # Continuing again reuses the existing session cleanly
    reuse_res = client.post(
        f"/api/v1/desktop/conversations/{desktop_id}/continue",
        headers=paired_headers,
        json={"projectId": proj_id, "reuseExisting": True},
    )
    assert reuse_res.status_code == 201
    assert reuse_res.json()["reused"] is True
    assert reuse_res.json()["session"]["id"] == session_id
    assert reuse_res.json()["importedMessages"] == 0


def test_legacy_sqlite_migration_preserves_existing_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            created_at_ms INTEGER NOT NULL
        );
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            model TEXT,
            status TEXT NOT NULL DEFAULT 'idle',
            last_seq INTEGER NOT NULL DEFAULT 0,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );
        INSERT INTO projects VALUES ('proj_old', 'OldProj', '/tmp/old', 100);
        INSERT INTO sessions VALUES ('11111111-2222-3333-4444-555555555555', 'proj_old', 'Legacy Chat', NULL, 'idle', 5, 100, 200);
        """
    )
    conn.commit()
    conn.close()

    store = BridgeStore(db_path)
    try:
        sess = store.get_session("11111111-2222-3333-4444-555555555555")
        assert sess is not None
        assert sess.title == "Legacy Chat"
        assert sess.sourceDesktopId is None
        sys_ctx, step = store.get_session_runtime_context(sess.id)
        assert sys_ctx is None
        assert step == -1
    finally:
        store.close()


def test_bridge_settings_and_sdk_tool_policies(
    bridge_env: dict[str, Any], paired_headers: dict[str, str], tmp_path: Path
) -> None:
    client = bridge_env["client"]

    # Default is read_only
    get_res = client.get("/api/v1/settings", headers=paired_headers)
    assert get_res.status_code == 200
    assert get_res.json()["toolPolicy"] == "read_only"

    # Update to edit_files then full
    for mode in ("edit_files", "full", "read_only"):
        upd = client.post(
            "/api/v1/settings",
            headers=paired_headers,
            json={"toolPolicy": mode},
        )
        assert upd.status_code == 200
        assert upd.json()["toolPolicy"] == mode

    # Verify real SDK LocalAgentConfig contract for all 3 policies + system_instructions
    sid = "12345678-1234-4234-8234-1234567890ab"

    ro_rt = AntigravitySdkRuntime(BridgeConfig(sdk_tool_policy="read_only"), tmp_path / "s1")
    ro_cfg = ro_rt._build_local_config(
        session_id=sid,
        workspace_dir=tmp_path,
        model=None,
        system_instructions="# Handoff context",
    )
    assert ag.BuiltinTools.VIEW_FILE in (ro_cfg.capabilities.enabled_tools or [])
    assert ag.BuiltinTools.EDIT_FILE not in (ro_cfg.capabilities.enabled_tools or [])
    assert ag.BuiltinTools.RUN_COMMAND not in (ro_cfg.capabilities.enabled_tools or [])
    assert ro_cfg.system_instructions == "# Handoff context"

    ef_rt = AntigravitySdkRuntime(BridgeConfig(sdk_tool_policy="edit_files"), tmp_path / "s2")
    ef_cfg = ef_rt._build_local_config(session_id=sid, workspace_dir=tmp_path, model=None)
    assert ag.BuiltinTools.EDIT_FILE in (ef_cfg.capabilities.enabled_tools or [])
    assert ag.BuiltinTools.CREATE_FILE in (ef_cfg.capabilities.enabled_tools or [])
    assert ag.BuiltinTools.RUN_COMMAND not in (ef_cfg.capabilities.enabled_tools or [])

    full_rt = AntigravitySdkRuntime(BridgeConfig(sdk_tool_policy="full"), tmp_path / "s3")
    full_cfg = full_rt._build_local_config(session_id=sid, workspace_dir=tmp_path, model=None)
    assert full_cfg.policies is not None and len(full_cfg.policies) > 0
