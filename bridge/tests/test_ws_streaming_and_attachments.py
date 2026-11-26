"""End-to-end tests for image uploads, WebSocket streaming, replay, and mid-turn cancellation."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

# Minimal valid 1x1 PNG bytes
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _create_project_and_session(
    bridge_env: dict[str, Any], paired_headers: dict[str, str]
) -> tuple[str, str]:
    client: TestClient = bridge_env["client"]
    auth = {"Authorization": paired_headers["Authorization"]}
    proj = client.post(
        "/api/v1/projects",
        headers=auth,
        json={"name": "Demo", "path": str(bridge_env["demo_project_dir"])},
    ).json()
    ses = client.post(
        "/api/v1/sessions",
        headers=auth,
        json={"projectId": proj["id"], "title": "Streaming Test"},
    ).json()
    return proj["id"], ses["id"]


def test_ws_rejects_bad_first_frame_auth(bridge_env: dict[str, Any]) -> None:
    client: TestClient = bridge_env["client"]
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": "agygo_invalid"})
        resp = ws.receive_json()
        assert resp["type"] == "auth.error"


def test_image_upload_validation_and_download(
    bridge_env: dict[str, Any], paired_headers: dict[str, str]
) -> None:
    client: TestClient = bridge_env["client"]
    auth = {"Authorization": paired_headers["Authorization"]}
    _, session_id = _create_project_and_session(bridge_env, paired_headers)

    # Disguised non-image rejected with 415
    bad = client.post(
        f"/api/v1/sessions/{session_id}/attachments",
        headers=auth,
        files={"file": ("evil.png", b"<html>not a png</html>", "image/png")},
    )
    assert bad.status_code == 415

    # Valid PNG succeeds and downloads cleanly
    ok = client.post(
        f"/api/v1/sessions/{session_id}/attachments",
        headers=auth,
        files={"file": ("../shot.png", VALID_PNG_BYTES, "image/png")},
    )
    assert ok.status_code == 201
    att = ok.json()
    assert att["mimeType"] == "image/png"
    assert ".." not in att["filename"]

    dl = client.get(f"/api/v1/attachments/{att['id']}", headers=auth)
    assert dl.status_code == 200
    assert dl.content == VALID_PNG_BYTES
    assert dl.headers.get("x-content-type-options") == "nosniff"


def test_streaming_thoughts_text_tools_attachments_and_replay(
    bridge_env: dict[str, Any], paired_headers: dict[str, str]
) -> None:
    client: TestClient = bridge_env["client"]
    auth = {"Authorization": paired_headers["Authorization"]}
    _, session_id = _create_project_and_session(bridge_env, paired_headers)

    att = client.post(
        f"/api/v1/sessions/{session_id}/attachments",
        headers=auth,
        files={"file": ("screen.png", VALID_PNG_BYTES, "image/png")},
    ).json()

    events: list[dict[str, Any]] = []
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": paired_headers["_device_token"]})
        assert ws.receive_json()["type"] == "auth.ok"

        ws.send_json(
            {
                "type": "turn.send",
                "sessionId": session_id,
                "turnId": "turn_1",
                "text": "Explain this error",
                "attachmentIds": [att["id"]],
            }
        )

        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "turn.done":
                break

    event_types = [e["type"] for e in events]
    assert event_types[0] == "turn.started"
    assert "thought.delta" in event_types
    assert "tool.call" in event_types
    assert "tool.result" in event_types
    assert "text.delta" in event_types
    assert events[-1]["data"]["status"] == "completed"

    # Strictly increasing sequence numbers
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    # Messages persisted in SQLite with thoughts, toolEvents, and attachments
    page = client.get(f"/api/v1/sessions/{session_id}/messages", headers=auth).json()
    assert len(page["messages"]) == 2
    user_msg, asst_msg = page["messages"]
    assert user_msg["role"] == "user"
    assert len(user_msg["attachments"]) == 1
    assert asst_msg["role"] == "assistant"
    assert "Planning step 1." in asst_msg["thoughts"]
    assert "Echo: Explain this error [images=1]" in asst_msg["content"]
    assert len(asst_msg["toolEvents"]) == 1
    assert asst_msg["toolEvents"][0]["status"] == "completed"

    # Reconnect and replay events after seq of turn.started
    first_seq = events[0]["seq"]
    with client.websocket_connect("/api/v1/ws") as ws2:
        ws2.send_json({"type": "auth", "token": paired_headers["_device_token"]})
        assert ws2.receive_json()["type"] == "auth.ok"
        ws2.send_json({"type": "session.subscribe", "sessionId": session_id, "lastSeq": first_seq})
        sub = ws2.receive_json()
        assert sub["type"] == "session.subscribed"
        replayed = [ws2.receive_json() for _ in range(len(events) - 1)]
        assert [e["seq"] for e in replayed] == seqs[1:]


def test_mid_turn_cancellation(
    bridge_env: dict[str, Any], paired_headers: dict[str, str]
) -> None:
    client: TestClient = bridge_env["client"]
    auth = {"Authorization": paired_headers["Authorization"]}
    bridge_env["runtime"].slow_mode = True
    _, session_id = _create_project_and_session(bridge_env, paired_headers)

    terminal_event: dict[str, Any] | None = None
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "auth", "token": paired_headers["_device_token"]})
        assert ws.receive_json()["type"] == "auth.ok"

        ws.send_json(
            {
                "type": "turn.send",
                "sessionId": session_id,
                "turnId": "turn_slow",
                "text": "Generate a very long output",
            }
        )

        # Wait until first text.delta arrives, then send turn.cancel
        sent_cancel = False
        while True:
            ev = ws.receive_json()
            if ev["type"] == "text.delta" and not sent_cancel:
                ws.send_json({"type": "turn.cancel", "sessionId": session_id, "turnId": "turn_slow"})
                sent_cancel = True
            if ev["type"] == "turn.done":
                terminal_event = ev
                break

    assert terminal_event is not None
    assert terminal_event["data"]["status"] == "cancelled"

    # Verify session returned to idle and message recorded as cancelled
    sessions = client.get("/api/v1/sessions", headers=auth).json()
    assert sessions[0]["status"] == "idle"
    page = client.get(f"/api/v1/sessions/{session_id}/messages", headers=auth).json()
    assert page["messages"][-1]["status"] == "cancelled"
