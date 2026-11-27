"""Read-only scanner for desktop Antigravity transcripts and context handoff pack builder.

Reads documented JSONL transcripts at `<brain_dir>/<conversation_id>/.system_generated/logs/transcript.jsonl`
(plus selectively resolving truncated lines from `transcript_full.jsonl`) and top-level `.md` artifacts
without modifying any desktop Antigravity state or database files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .protocol import (
    DesktopArtifactInfo,
    DesktopConversationSummary,
    MessageRecord,
    ProjectInfo,
    ToolEventRecord,
)

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL)
STRIP_TAGS_RE = re.compile(
    r"<(?:ADDITIONAL_METADATA|CONTEXT_SUMMARY|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>.*?</(?:ADDITIONAL_METADATA|CONTEXT_SUMMARY|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>",
    re.DOTALL,
)
SAFE_CONV_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
IGNORED_BRAIN_DIRS = frozenset({"tempmediastorage", "cache", "tmp"})

MAX_HANDOFF_CHARS = 16_000
MAX_ARTIFACT_EXCERPT_CHARS = 2_000
MAX_HANDOFF_TURNS = 12


@dataclass(slots=True)
class ParsedDesktopMessage:
    """Normalized turn-level message parsed from a desktop Antigravity transcript."""

    step_index: int
    turn_id: str
    role: str  # "user" | "assistant"
    content: str
    thoughts: str = ""
    tool_events: list[ToolEventRecord] = field(default_factory=list)
    created_at_ms: int = 0
    updated_at_ms: int = 0


@dataclass(slots=True)
class ParsedDesktopConversation:
    """Full read-only view of one desktop Antigravity conversation."""

    conversation_id: str
    brain_dir: Path
    title: str
    preview: str
    messages: list[ParsedDesktopMessage]
    artifacts: list[DesktopArtifactInfo]
    artifact_excerpts: list[tuple[str, Path, str]]
    step_count: int
    max_step_index: int
    updated_at_ms: int
    referenced_paths: list[str]


def _parse_iso_ms(raw: Any, fallback_ms: int) -> int:
    if not isinstance(raw, str) or not raw.strip():
        return fallback_ms
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return fallback_ms


def _clean_user_content(raw_content: str, media: Any) -> str:
    """Extract clean user prompt text from Antigravity `<USER_REQUEST>` envelopes."""
    match = USER_REQUEST_RE.search(raw_content or "")
    if match:
        cleaned = match.group(1).strip()
    else:
        cleaned = STRIP_TAGS_RE.sub("", raw_content or "").strip()

    media_list = media if isinstance(media, list) else []
    media_count = len(media_list)
    if not cleaned and media_count > 0:
        return f"[图片附件 × {media_count}]"
    if cleaned and media_count > 0:
        return f"{cleaned}\n\n[附带 {media_count} 个媒体附件]"
    return cleaned


def _truncate_text(text: str, limit: int) -> str:
    single = re.sub(r"\s+", " ", text).strip()
    if len(single) <= limit:
        return single
    return single[: max(1, limit - 1)].rstrip() + "…"


def _load_full_transcript_lines(
    full_path: Path, line_indices: set[int]
) -> dict[int, dict[str, Any]]:
    """Selectively read only the specific lines marked truncated in `transcript.jsonl`."""
    if not line_indices or not full_path.is_file():
        return {}
    needed = set(line_indices)
    resolved: dict[int, dict[str, Any]] = {}
    try:
        with full_path.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx not in needed:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        resolved[idx] = obj
                except json.JSONDecodeError:
                    pass
                needed.discard(idx)
                if not needed:
                    break
    except OSError:
        return {}
    return resolved


def _extract_paths_from_tool_args(args: Any, out: list[str]) -> None:
    if not isinstance(args, dict):
        return
    for key in ("Path", "path", "DirectoryPath", "SearchPath", "TargetFile", "cwd"):
        val = args.get(key)
        if isinstance(val, str):
            cleaned = val.strip().strip('"').strip("'")
            if len(cleaned) >= 3 and (":\\" in cleaned or ":/" in cleaned or cleaned.startswith("/")):
                out.append(cleaned)


def _discover_artifacts(conv_dir: Path) -> tuple[list[DesktopArtifactInfo], list[tuple[str, Path, str]]]:
    """List top-level Markdown artifacts in `<brain>/<conv_id>/*.md`."""
    artifacts: list[DesktopArtifactInfo] = []
    excerpts: list[tuple[str, Path, str]] = []
    try:
        md_files = sorted(
            (p for p in conv_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return [], []

    for md_path in md_files[:12]:
        try:
            st = md_path.stat()
        except OSError:
            continue
        summary = ""
        meta_path = conv_dir / f"{md_path.name}.metadata.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(meta, dict):
                    summary = str(meta.get("summary") or meta.get("Summary") or "").strip()
            except (OSError, json.JSONDecodeError):
                pass
        artifacts.append(
            DesktopArtifactInfo(
                name=md_path.name,
                sizeBytes=st.st_size,
                updatedAtMs=int(st.st_mtime * 1000),
                summary=summary or None,
            )
        )
        try:
            body = md_path.read_text(encoding="utf-8", errors="replace").strip()
            if len(body) > MAX_ARTIFACT_EXCERPT_CHARS:
                body = body[:MAX_ARTIFACT_EXCERPT_CHARS].rstrip() + "\n...(truncated)"
            excerpts.append((md_path.name, md_path, body))
        except OSError:
            pass
    return artifacts, excerpts


def infer_matching_project(
    referenced_paths: list[str], projects: list[ProjectInfo]
) -> tuple[str | None, str | None]:
    """Match paths referenced in tool calls against registered Bridge projects."""
    if not referenced_paths or not projects:
        return None, None

    normalized_projects: list[tuple[str, str, ProjectInfo]] = []
    for proj in projects:
        norm = str(Path(proj.path)).replace("\\", "/").rstrip("/").lower()
        normalized_projects.append((norm, proj.path, proj))
    # Prefer the most specific (longest) project path first
    normalized_projects.sort(key=lambda item: len(item[0]), reverse=True)

    counts: dict[str, int] = {}
    matched_proj: dict[str, ProjectInfo] = {}
    for raw in referenced_paths:
        norm_ref = raw.replace("\\", "/").rstrip("/").lower()
        for norm_proj, _, proj in normalized_projects:
            if norm_ref == norm_proj or norm_ref.startswith(norm_proj + "/"):
                counts[proj.id] = counts.get(proj.id, 0) + 1
                matched_proj[proj.id] = proj
                break

    if not counts:
        return None, None
    best_id = max(counts, key=lambda pid: counts[pid])
    best = matched_proj[best_id]
    return best.id, best.path


def parse_desktop_conversation(
    brain_root: Path, conversation_id: str
) -> ParsedDesktopConversation | None:
    """Parse one desktop conversation directory safely (strictly read-only)."""
    if not SAFE_CONV_ID_RE.match(conversation_id):
        return None
    if conversation_id.lower() in IGNORED_BRAIN_DIRS:
        return None

    conv_dir = (brain_root / conversation_id).resolve()
    try:
        conv_dir.relative_to(brain_root.resolve())
    except ValueError:
        return None

    transcript_path = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript_path.is_file():
        return None

    try:
        stat = transcript_path.stat()
        mtime_ms = int(stat.st_mtime * 1000)
        raw_lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    entries: list[tuple[int, dict[str, Any]]] = []
    truncated_content_lines: set[int] = set()
    has_explicit_user = False

    for line_idx, raw_line in enumerate(raw_lines):
        if not raw_line.strip():
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        entries.append((line_idx, obj))
        if obj.get("source") == "USER_EXPLICIT" and obj.get("type") == "USER_INPUT":
            has_explicit_user = True
        trunc = obj.get("truncated_fields")
        if (
            obj.get("type") in ("USER_INPUT", "PLANNER_RESPONSE")
            and isinstance(trunc, list)
            and "content" in trunc
        ):
            truncated_content_lines.add(line_idx)

    # Filter out internal subagent conversations that have no direct user turns
    if not has_explicit_user:
        return None

    full_path = conv_dir / ".system_generated" / "logs" / "transcript_full.jsonl"
    full_lines = _load_full_transcript_lines(full_path, truncated_content_lines)

    messages: list[ParsedDesktopMessage] = []
    referenced_paths: list[str] = []
    turn_counter = 0
    current_turn_id = "desktop_turn_0"
    pending_assistant: ParsedDesktopMessage | None = None
    max_step_index = -1

    def flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant is None:
            return
        if (
            pending_assistant.content.strip()
            or pending_assistant.thoughts.strip()
            or pending_assistant.tool_events
        ):
            messages.append(pending_assistant)
        pending_assistant = None

    for line_idx, obj in entries:
        step_index = obj.get("step_index")
        if not isinstance(step_index, int):
            step_index = line_idx
        if step_index > max_step_index:
            max_step_index = step_index

        source = str(obj.get("source") or "")
        ev_type = str(obj.get("type") or "")
        ts_ms = _parse_iso_ms(obj.get("created_at"), mtime_ms)
        full_obj = full_lines.get(line_idx, obj)

        if source == "USER_EXPLICIT" and ev_type == "USER_INPUT":
            flush_assistant()
            turn_counter += 1
            current_turn_id = f"desktop_turn_{turn_counter}"
            raw_content = str(full_obj.get("content") or obj.get("content") or "")
            cleaned = _clean_user_content(raw_content, obj.get("media"))
            if not cleaned:
                continue
            messages.append(
                ParsedDesktopMessage(
                    step_index=step_index,
                    turn_id=current_turn_id,
                    role="user",
                    content=cleaned,
                    created_at_ms=ts_ms,
                    updated_at_ms=ts_ms,
                )
            )
            continue

        if source == "MODEL" and ev_type == "PLANNER_RESPONSE":
            content_piece = str(full_obj.get("content") or obj.get("content") or "").strip()
            thinking_piece = str(obj.get("thinking") or "").strip()
            raw_tools = obj.get("tool_calls")
            tool_events: list[ToolEventRecord] = []
            if isinstance(raw_tools, list):
                for idx, tc in enumerate(raw_tools):
                    if not isinstance(tc, dict):
                        continue
                    tc_name = str(tc.get("name") or "tool")
                    tc_args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                    _extract_paths_from_tool_args(tc_args, referenced_paths)
                    tool_events.append(
                        ToolEventRecord(
                            id=f"dstep_{step_index}_{idx}",
                            name=tc_name,
                            args=tc_args,
                            status="completed",
                        )
                    )

            if not content_piece and not thinking_piece and not tool_events:
                continue

            if pending_assistant is None:
                pending_assistant = ParsedDesktopMessage(
                    step_index=step_index,
                    turn_id=current_turn_id,
                    role="assistant",
                    content=content_piece,
                    thoughts=thinking_piece,
                    tool_events=tool_events,
                    created_at_ms=ts_ms,
                    updated_at_ms=ts_ms,
                )
            else:
                pending_assistant.step_index = step_index
                pending_assistant.updated_at_ms = ts_ms
                if content_piece:
                    pending_assistant.content = (
                        f"{pending_assistant.content}\n\n{content_piece}"
                        if pending_assistant.content
                        else content_piece
                    )
                if thinking_piece:
                    pending_assistant.thoughts = (
                        f"{pending_assistant.thoughts}\n\n{thinking_piece}"
                        if pending_assistant.thoughts
                        else thinking_piece
                    )
                if tool_events:
                    pending_assistant.tool_events.extend(tool_events)

    flush_assistant()
    if not messages:
        return None

    first_user = next((m.content for m in messages if m.role == "user" and m.content.strip()), "")
    first_line = next((ln.strip() for ln in first_user.splitlines() if ln.strip()), "")
    title = _truncate_text(first_line or f"Desktop {conversation_id[:8]}", 80)

    last_msg = next((m.content for m in reversed(messages) if m.content.strip()), "")
    preview = _truncate_text(last_msg, 140)

    artifacts, artifact_excerpts = _discover_artifacts(conv_dir)
    return ParsedDesktopConversation(
        conversation_id=conversation_id,
        brain_dir=conv_dir,
        title=title,
        preview=preview,
        messages=messages,
        artifacts=artifacts,
        artifact_excerpts=artifact_excerpts,
        step_count=len(entries),
        max_step_index=max_step_index,
        updated_at_ms=mtime_ms,
        referenced_paths=referenced_paths,
    )


def list_desktop_conversations(
    brain_root: Path,
    *,
    exclude_ids: set[str],
    linked_by_desktop_id: dict[str, str],
    projects: list[ProjectInfo],
    limit: int = 30,
) -> list[DesktopConversationSummary]:
    """Scan desktop Antigravity conversations sorted by most recently updated."""
    if not brain_root.exists() or not brain_root.is_dir():
        return []

    candidates: list[tuple[float, str]] = []
    try:
        for entry in brain_root.iterdir():
            if not entry.is_dir():
                continue
            cid = entry.name
            if cid in exclude_ids or cid.lower() in IGNORED_BRAIN_DIRS:
                continue
            if not SAFE_CONV_ID_RE.match(cid):
                continue
            tpath = entry / ".system_generated" / "logs" / "transcript.jsonl"
            if not tpath.is_file():
                continue
            try:
                candidates.append((tpath.stat().st_mtime, cid))
            except OSError:
                continue
    except OSError:
        return []

    candidates.sort(key=lambda item: item[0], reverse=True)
    results: list[DesktopConversationSummary] = []
    clamped = max(1, min(limit, 100))

    for _, cid in candidates:
        if len(results) >= clamped:
            break
        parsed = parse_desktop_conversation(brain_root, cid)
        if parsed is None:
            continue
        inferred_pid, inferred_path = infer_matching_project(parsed.referenced_paths, projects)
        turn_count = sum(1 for m in parsed.messages if m.role == "user")
        results.append(
            DesktopConversationSummary(
                id=parsed.conversation_id,
                title=parsed.title,
                preview=parsed.preview,
                turnCount=turn_count,
                messageCount=len(parsed.messages),
                stepCount=parsed.step_count,
                artifacts=parsed.artifacts,
                inferredProjectId=inferred_pid,
                inferredWorkspacePath=inferred_path,
                linkedSessionId=linked_by_desktop_id.get(parsed.conversation_id),
                updatedAtMs=parsed.updated_at_ms,
            )
        )
    return results


def to_protocol_messages(
    conversation_id: str,
    parsed_messages: list[ParsedDesktopMessage],
    *,
    limit: int = 80,
) -> list[MessageRecord]:
    """Convert parsed desktop messages to Bridge `MessageRecord` objects for preview."""
    sliced = parsed_messages[-max(1, min(limit, 300)) :]
    out: list[MessageRecord] = []
    for idx, msg in enumerate(sliced, start=1):
        out.append(
            MessageRecord(
                id=f"dmsg_{conversation_id[:8]}_{msg.step_index}_{idx}",
                sessionId=conversation_id,
                turnId=msg.turn_id,
                role=msg.role,  # type: ignore[arg-type]
                content=msg.content,
                thoughts=msg.thoughts,
                toolEvents=msg.tool_events,
                attachments=[],
                status="completed",
                errorMessage=None,
                seq=idx,
                createdAtMs=msg.created_at_ms,
                updatedAtMs=msg.updated_at_ms,
            )
        )
    return out


def build_handoff_context(
    parsed: ParsedDesktopConversation,
    *,
    workspace_path: str,
    max_chars: int = MAX_HANDOFF_CHARS,
    max_turns: int = MAX_HANDOFF_TURNS,
) -> str:
    """Build a bounded structured handoff prompt appended to SDK `system_instructions`."""
    header = (
        "# Desktop Antigravity Conversation Handoff\n"
        "You are seamlessly continuing an existing desktop Google Antigravity conversation "
        "from the user's mobile Antigravity-Go client.\n"
        f"- Source Desktop Conversation ID: `{parsed.conversation_id}`\n"
        f"- Active Local Workspace: `{workspace_path}`\n"
        "- Treat the prior transcript excerpt and artifacts below as established history. "
        "Continue directly from where the desktop session left off without asking the user "
        "to repeat already-completed work.\n"
    )

    artifact_section = ""
    if parsed.artifact_excerpts:
        art_parts = ["\n## Desktop Artifacts"]
        for name, full_path, excerpt in parsed.artifact_excerpts[:4]:
            art_parts.append(f"### {name} (`{full_path}`)\n{excerpt}")
        artifact_section = "\n\n".join(art_parts) + "\n"
        if len(artifact_section) > max_chars // 3:
            artifact_section = artifact_section[: max_chars // 3].rstrip() + "\n...(artifacts truncated)\n"

    # Keep the latest N user/assistant pairs (up to 2 * max_turns messages)
    recent = parsed.messages[-(max_turns * 2) :]
    history_header = "\n## Recent Desktop Conversation Transcript\n"
    budget = max(1_500, max_chars - len(header) - len(artifact_section) - len(history_header))

    rendered_blocks: list[str] = []
    used = 0
    for msg in reversed(recent):
        role_label = "User" if msg.role == "user" else "Assistant"
        tool_note = ""
        if msg.tool_events:
            tool_names = ", ".join(dict.fromkeys(t.name for t in msg.tool_events[:10]))
            tool_note = f" [tools: {tool_names}]"
        body = msg.content.strip() or "(no visible text)"
        if len(body) > 2_000:
            body = body[:2_000].rstrip() + "…"
        block = f"### {role_label}{tool_note}\n{body}\n"
        if used + len(block) > budget and rendered_blocks:
            break
        rendered_blocks.append(block)
        used += len(block)

    rendered_blocks.reverse()
    combined = header + artifact_section + history_header + "\n".join(rendered_blocks)
    if len(combined) > max_chars:
        combined = combined[:max_chars].rstrip() + "\n...(truncated)"
    return combined
