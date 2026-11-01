"""Transcript lines to search-index rows (ports ``SWECore/Search``).

The Swift app and :mod:`swe.sessions.search_writer` write the same
index file, so the rows they produce for one transcript have to agree. This
module is the Python side of ``SearchMessage.swift`` (text caps),
``SearchContent.swift`` (content blocks), ``SearchChunker.swift`` (FTS row
sizes), ``SearchIdentifiers.swift`` (identifier shadow text) and the
per-harness decoders in ``SearchLineDecoders.swift``,
``SearchMoreLineDecoders.swift``, ``SearchPlainLineDecoders.swift`` and
``SearchGraphLineDecoders.swift``.
Keep the rules in step with those files.

Every cap counts UTF-16 code units, the unit the Swift caps count. Swift
cuts on ``Character`` (grapheme) boundaries; the stdlib has no grapheme
segmentation, so cuts here land on code points. The two differ only when a
cap falls inside a combining sequence, which changes one row's last
character and nothing a search would notice.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

USER = "user"
ASSISTANT = "assistant"
TOOL = "tool"

# SearchText.messageLimit: a safety bound so one pasted blob can't dominate.
MESSAGE_LIMIT = 256 * 1024
# SearchChunker.toolRowLimit / chunkLimit.
TOOL_ROW_LIMIT = 3072
CHUNK_LIMIT = 8000
# SearchContent.toolArgumentLimit.
TOOL_ARGUMENT_LIMIT = 2000


@dataclass
class Message:
    role: str
    text: str
    ts: str | None = None


@dataclass
class Hints:
    """Per-line session metadata; the indexer keeps the latest non-None."""

    session_id: str | None = None
    cwd: str | None = None
    title: str | None = None
    # Claude's isMeta and known injected turns: index the text, never use
    # it as the first-prompt title.
    injected_user_turn: bool = False


# -- text helpers (SearchText) -------------------------------------------


def _units(ch: str) -> int:
    return 2 if ord(ch) > 0xFFFF else 1


def utf16_len(text: str) -> int:
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def truncate(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` UTF-16 units, never inside a code point."""
    if len(text) <= limit // 2 or utf16_len(text) <= limit:
        return text
    used = 0
    for index, ch in enumerate(text):
        used += _units(ch)
        if used > limit:
            return text[:index]
    return text


def clean(text: str) -> str:
    """``text`` without lone surrogates. JSON can spell one (``"\\ud800"``)
    and ``json.loads`` keeps it, but neither UTF-8 nor SQLite takes it."""
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")


def bounded(text: str, limit: int = MESSAGE_LIMIT) -> str | None:
    trimmed = clean(truncate(text, limit)).strip()
    return trimmed or None


def json_string(value) -> str | None:
    """``JSONValue.string``: a non-blank string, trimmed."""
    if not isinstance(value, str):
        return None
    trimmed = clean(value).strip()
    return trimmed or None


def title_candidate(text: str, limit: int = 256) -> str | None:
    return bounded(" ".join(text.split()), limit)


# -- timestamps (SearchStamp) --------------------------------------------


def stamp_ms(value) -> float | None:
    """Epoch milliseconds of an ISO-8601 stamp, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # ISO8601FormatStyle rejects a stamp with no zone.
        return None
    return parsed.astimezone(timezone.utc).timestamp() * 1000


def iso_seconds(seconds: float) -> str:
    """Epoch seconds as ``Date.ISO8601FormatStyle(includingFractionalSeconds:
    true)`` spells them: UTC, milliseconds cut (never rounded) from the
    double's own fraction, so both writers store the same string."""
    whole = math.floor(seconds)
    millis = math.floor((seconds - whole) * 1000)
    try:
        moment = datetime.fromtimestamp(whole, timezone.utc)
    except (OverflowError, OSError) as exc:
        # Past what datetime holds: one ValueError for every caller, which
        # the indexer already treats as a bad record, not a failed pass.
        raise ValueError(f"timestamp out of range: {seconds}") from exc
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def stamp_iso(value) -> str | None:
    """``SearchStamp.iso``: a string is kept verbatim when it isn't blank; a
    number is epoch seconds, or milliseconds past ``1e12``."""
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    raw = float(value)
    if not math.isfinite(raw) or raw <= 0:
        return None
    try:
        return iso_seconds(raw / 1000 if raw > 1_000_000_000_000 else raw)
    except ValueError:
        return None  # a junk stamp past year 9999 says nothing


# -- injected turns (SearchInjectedTurn) ---------------------------------

_INJECTED_TAGS = frozenset({
    "agent-message", "bash-input", "bash-stderr", "bash-stdout",
    "command-args", "command-message", "command-name",
    "cross-session-message", "fork-boilerplate", "local-command-caveat",
    "local-command-stderr", "local-command-stdout", "mcp-polling-update",
    "mcp-resource-update", "system-reminder", "task-notification",
    "teammate-message", "user-memory-input", "user-prompt-submit-hook",
})
_INJECTED_PREFIXES = (
    "<channel source=",
    "[request interrupted",
    "a message arrived from ",
    "another claude session sent a message",
    "no response requested.",
    "caveat: the messages below were generated by the user while running local commands",
    "this session is being continued from a previous conversation",
)
_ASCII_SPACE = "\t\n\x0b\x0c\r "


def _leading_tag(head: str) -> str | None:
    if not head.startswith("<"):
        return None
    name = []
    for ch in head[1:]:
        if ch == ">" or ch.isspace():
            break
        if not (ch.isascii() and (ch.isalnum() or ch == "-")):
            return None
        name.append(ch)
    if not name or not name[0].isalpha():
        return None
    return "".join(name)


def is_injected_turn(text: str) -> bool:
    start = 0
    while start < min(len(text), 64) and text[start] in _ASCII_SPACE:
        start += 1
    head = truncate(text[start:start + 256], 256).lower()
    if not head:
        return False
    tag = _leading_tag(head)
    if tag is not None and tag in _INJECTED_TAGS:
        return True
    return head.startswith(_INJECTED_PREFIXES)


# -- content blocks (SearchContent) --------------------------------------

_TEXT_BLOCK_TYPES = frozenset({"text", "input_text", "output_text", "thinking", "reasoning"})
_TOOL_CALL_TYPES = frozenset({"tool_use", "toolcall", "tool_call", "function_call"})
_TOOL_RESULT_TYPES = frozenset({"tool_result", "toolresult", "function_call_output"})
_TOOL_INPUT_KEYS = ("command", "cmd", "file_path", "path", "pattern", "query", "description")


def _joined(parts: list[str]) -> str | None:
    if len(parts) == 1:
        return bounded(parts[0])
    return bounded("\n".join(parts))


def tool_call_text(name, tool_input) -> str | None:
    tool_name = json_string(name)
    argument = None
    if isinstance(tool_input, dict):
        for key in _TOOL_INPUT_KEYS:
            argument = json_string(tool_input.get(key))
            if argument is not None:
                break
    else:
        argument = json_string(tool_input)
    if argument is not None:
        argument = truncate(argument, TOOL_ARGUMENT_LIMIT)
    if tool_name and argument:
        return f"{tool_name}: {argument}"
    return tool_name or argument


def tool_result_text(content) -> str | None:
    if isinstance(content, str):
        return bounded(content)
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    length = 0
    for item in content:
        text = item if isinstance(item, str) else (
            item.get("text") if isinstance(item, dict) else None)
        if not isinstance(text, str) or not text:
            continue
        parts.append(text)
        length += utf16_len(text)
        if length >= MESSAGE_LIMIT:
            break
    return _joined(parts)


def append_tool(messages: list[Message], text: str | None, ts: str | None) -> None:
    if text is None:
        return
    body = bounded(text)
    if body is not None:
        messages.append(Message(TOOL, body, ts))


def content_messages(role: str | None, content, ts: str | None) -> list[Message]:
    """A provider ``content`` value split into messages. ``role`` None
    drops the record's own text (developer/system context) but keeps its
    tool blocks."""
    if isinstance(content, str):
        text = bounded(content)
        return [Message(role, text, ts)] if role and text else []
    if isinstance(content, list):
        blocks = content
    elif content is not None:
        blocks = [content]
    else:
        blocks = []
    messages: list[Message] = []
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        lowered = kind.lower() if isinstance(kind, str) else None
        if lowered in _TOOL_CALL_TYPES:
            tool_input = block.get("input")
            if tool_input is None:
                tool_input = block.get("arguments")
            append_tool(messages, tool_call_text(block.get("name"), tool_input), ts)
        elif lowered in _TOOL_RESULT_TYPES:
            body = block.get("content")
            if body is None:
                body = block.get("output")
            append_tool(messages, tool_result_text(body), ts)
        else:
            if isinstance(kind, str) and lowered not in _TEXT_BLOCK_TYPES:
                continue
            text = block.get("text")
            if text is None:
                text = block.get("content")
            if isinstance(text, str) and text:
                parts.append(text)
    if role:
        text = _joined(parts)
        if text is not None:
            messages.insert(0, Message(role, text, ts))
    return messages


# -- decoders ------------------------------------------------------------


def _claude(obj: dict) -> tuple[list[Message], Hints]:
    hints = Hints(
        session_id=json_string(obj.get("sessionId")),
        cwd=json_string(obj.get("cwd")),
    )
    ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None
    kind = obj.get("type")
    if kind == "custom-title":
        hints.title = json_string(obj.get("customTitle"))
    elif kind == "ai-title":
        hints.title = json_string(obj.get("aiTitle"))
    elif kind in ("user", "assistant"):
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        messages = content_messages(USER if kind == "user" else ASSISTANT, content, ts)
        if kind == "user":
            first = next((m for m in messages if m.role == USER), None)
            hints.injected_user_turn = obj.get("isMeta") is True or (
                first is not None and is_injected_turn(first.text))
        return messages, hints
    return [], hints


@dataclass
class _CodexState:
    saw_session_meta: bool = False
    history_mode: str | None = None


def _codex(state: _CodexState, obj: dict) -> tuple[list[Message], Hints]:
    hints = Hints()
    ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    kind = obj.get("type")
    if kind == "session_meta":
        # Only the first counts: a fork copies its parent's in after its own.
        if state.saw_session_meta:
            return [], hints
        state.saw_session_meta = True
        state.history_mode = json_string(payload.get("history_mode"))
        hints.session_id = json_string(payload.get("id")) or json_string(payload.get("session_id"))
        hints.cwd = json_string(payload.get("cwd"))
        hints.title = (json_string(payload.get("title"))
                       or json_string(payload.get("thread_name"))
                       or json_string(payload.get("threadName")))
        return [], hints
    if kind == "turn_context":
        hints.cwd = json_string(payload.get("cwd"))
        return [], hints
    if kind == "response_item":
        if state.history_mode == "paginated":
            return [], hints
        return _codex_response_item(payload, ts), hints
    if kind == "event_msg":
        return _codex_event(state, payload, ts), hints
    return [], hints


def _first_present(record: dict, *keys):
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _codex_response_item(payload: dict, ts: str | None) -> list[Message]:
    kind = payload.get("type")
    if kind == "message":
        role = payload.get("role")
        return content_messages(role if role in (USER, ASSISTANT, TOOL) else None,
                                payload.get("content"), ts)
    if kind in ("function_call", "custom_tool_call", "local_shell_call"):
        tool_input = _first_present(payload, "arguments", "input", "action")
        # `action` wraps shell calls as {"type":"exec","command":[...]}.
        if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), list):
            tool_input = {**tool_input, "command": " ".join(
                part for part in tool_input["command"] if isinstance(part, str))}
        name = payload.get("name")
        messages: list[Message] = []
        append_tool(messages, tool_call_text("tool" if name is None else name, tool_input), ts)
        return messages
    if kind in ("function_call_output", "custom_tool_call_output"):
        output = payload.get("output")
        if isinstance(output, dict):
            output = _first_present(output, "content", "output")
        messages = []
        append_tool(messages, tool_result_text(output), ts)
        return messages
    return []


def _codex_event(state: _CodexState, payload: dict, ts: str | None) -> list[Message]:
    kind = payload.get("type")
    if kind == "item_completed":
        if state.history_mode != "paginated":
            return []
        item = payload.get("item")
        return _codex_completed(item if isinstance(item, dict) else None, ts)
    if kind == "user_message":
        return content_messages(USER, payload.get("message"), ts)
    if kind == "agent_message":
        return content_messages(ASSISTANT, payload.get("message"), ts)
    return []


def _codex_completed(item: dict | None, ts: str | None) -> list[Message]:
    if item is None:
        return []
    kind = item.get("type")
    if kind in ("UserMessage", "user_message"):
        return content_messages(USER, item.get("content"), ts)
    if kind in ("AgentMessage", "agent_message"):
        return content_messages(ASSISTANT, item.get("content"), ts)
    messages: list[Message] = []
    if kind in ("CommandExecution", "command_execution"):
        command = item.get("command")
        if isinstance(command, list):
            command = " ".join(part for part in command if isinstance(part, str))
        append_tool(messages, tool_call_text("shell", command), ts)
        append_tool(messages, tool_result_text(
            _first_present(item, "aggregated_output", "aggregatedOutput")), ts)
    elif kind in ("FileChange", "file_change"):
        changes = item.get("changes")
        for path, value in (changes.items() if isinstance(changes, dict) else ()):
            change = value if isinstance(value, dict) else {}
            append_tool(messages, tool_call_text("apply_patch", {"path": path}), ts)
            append_tool(messages, tool_result_text(
                _first_present(change, "unified_diff", "content")), ts)
    return messages


def _first_key(record: dict, *keys):
    """Swift's ``a ?? b`` over JSON values: the first key that is present,
    even when it holds JSON null (``NSNull`` is not nil)."""
    for key in keys:
        if key in record:
            return record[key]
    return None


@dataclass
class _DevinState:
    # tool_call_id -> call name, for `role: "tool"` chat_message rows.
    call_names: dict = field(default_factory=dict)


def _devin(state: _DevinState, obj: dict) -> tuple[list[Message], Hints]:
    """``SearchDevinDecoder``: a whole ATIF document, one ATIF step, or one
    ``message_nodes.chat_message`` record."""
    hints = Hints()
    steps = obj.get("steps")
    if isinstance(steps, list):
        hints.session_id = json_string(obj.get("session_id")) or json_string(obj.get("sessionId"))
        hints.cwd = json_string(obj.get("working_directory"))
        messages: list[Message] = []
        for step in steps:
            messages += _devin_step(step if isinstance(step, dict) else None)
        return messages, hints
    if "role" in obj:
        return _devin_chat_message(state, obj), hints
    if "source" in obj or "step_id" in obj:
        return _devin_step(obj), hints
    return [], hints


def _devin_step_text(step: dict):
    message = step.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return _first_key(message, "content", "text")
    return _first_key(step, "message", "content", "text")


def _devin_step(step: dict | None) -> list[Message]:
    """One ATIF step: system steps drop, ``is_user_input`` overrides
    ``source``, and an agent step decodes its text, its calls and the
    results its ``observation`` embeds."""
    if step is None:
        return []
    metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else None
    ts = step.get("timestamp")
    if not isinstance(ts, str):
        created = metadata.get("created_at") if metadata else None
        ts = created if isinstance(created, str) else None
    source = step.get("source") if isinstance(step.get("source"), str) else None
    if source == "system":
        return []
    if source == "user" or (metadata is not None and metadata.get("is_user_input") is True):
        return content_messages(USER, _devin_step_text(step), ts)
    if not (source == "agent" or step.get("role") == "assistant" or "tool_calls" in step):
        return []
    messages = content_messages(ASSISTANT, _devin_step_text(step), ts)
    calls = step.get("tool_calls")
    for call in calls if isinstance(calls, list) else ():
        if isinstance(call, dict):
            append_tool(messages, tool_call_text(
                _first_key(call, "function_name", "name"), call.get("arguments")), ts)
    observation = step.get("observation")
    results = observation.get("results") if isinstance(observation, dict) else None
    for result in results if isinstance(results, list) else ():
        body = result.get("content") if isinstance(result, dict) else None
        append_tool(messages, tool_result_text(result if body is None else body), ts)
    return messages


def _devin_chat_message(state: _DevinState, record: dict) -> list[Message]:
    metadata = record.get("metadata")
    created = metadata.get("created_at") if isinstance(metadata, dict) else None
    ts = created if isinstance(created, str) else None
    role = record.get("role")
    if role == "user":
        return content_messages(USER, record.get("content"), ts)
    if role == "assistant":
        messages = content_messages(ASSISTANT, record.get("content"), ts)
        calls = record.get("tool_calls")
        for call in calls if isinstance(calls, list) else ():
            if not isinstance(call, dict):
                continue
            call_id, name = json_string(call.get("id")), json_string(call.get("name"))
            if call_id is not None and name is not None:
                state.call_names[call_id] = name
            append_tool(messages, tool_call_text(call.get("name"), call.get("arguments")), ts)
        return messages
    if role == "tool":
        text = tool_result_text(record.get("content"))
        if text is None:
            return []
        call_id = json_string(record.get("tool_call_id"))
        name = state.call_names.get(call_id) if call_id is not None else None
        messages = []
        append_tool(messages, f"{name}: {text}" if name else text, ts)
        return messages
    return []


def _role(value) -> str | None:
    """``SearchRole(rawValue:)``."""
    return value if value in (USER, ASSISTANT, TOOL) else None


def _pi(obj: dict) -> tuple[list[Message], Hints]:
    """``SearchPiDecoder``: ``session`` and ``session_info`` records carry
    the id, cwd and ``/name`` title; ``toolResult`` is a role of its own
    that names the call it answers."""
    hints = Hints()
    kind = obj.get("type")
    if kind == "session":
        hints.session_id = json_string(obj.get("id"))
        hints.cwd = json_string(obj.get("cwd")) or json_string(obj.get("workspace"))
    elif kind == "session_info":
        hints.title = json_string(obj.get("name"))
    elif kind == "message":
        message = obj.get("message")
        if not isinstance(message, dict):
            return [], hints
        ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None
        if message.get("role") == "toolResult":
            text = tool_result_text(message.get("content"))
            if text is None:
                return [], hints
            name = json_string(message.get("toolName"))
            messages: list[Message] = []
            append_tool(messages, f"{name}: {text}" if name else text, ts)
            return messages, hints
        return content_messages(_role(message.get("role")), message.get("content"), ts), hints
    return [], hints


def _cursor(obj: dict) -> tuple[list[Message], Hints]:
    """``SearchCursorDecoder``: Claude-shaped content blocks under
    ``message.content``. Id, title, cwd and times come from the chat's
    ``meta.json`` (``cursor_meta_fields``)."""
    role = json_string(obj.get("role"))
    if role not in (USER, ASSISTANT):
        return [], Hints()
    message = obj.get("message")
    if isinstance(message, dict) and "content" in message:
        content = message["content"]
    else:
        content = obj.get("content")
    ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None
    return content_messages(role, content, ts), Hints()


@dataclass
class _QoderState:
    # The first cwd wins (``updateLatestLocation``), so it emits once.
    cwd_emitted: bool = False


def _qoder(state: _QoderState, obj: dict) -> tuple[list[Message], Hints]:
    """``SearchQoderDecoder``: Claude's records with ``message.content``
    cut to its ``text`` blocks, and ``workspace-directories`` as the cwd
    when nothing earlier gave one."""
    filtered = obj
    message = obj.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        trimmed = {**message, "content": [
            block for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "text"]}
        filtered = {**obj, "message": trimmed}
    messages, hints = _claude(filtered)
    if hints.cwd is not None:
        if state.cwd_emitted:
            hints.cwd = None
        else:
            state.cwd_emitted = True
    directories = obj.get("directories")
    if (obj.get("type") == "workspace-directories" and not state.cwd_emitted
            and isinstance(directories, list) and directories):
        cwd = json_string(directories[0])
        if cwd is not None:
            hints.cwd = cwd
            state.cwd_emitted = True
    return messages, hints


@dataclass
class _CodebuddyState:
    cwd_emitted: bool = False
    # A `summary` outranks `ai-title` for good.
    saw_summary_title: bool = False


def _codebuddy_text(content) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = []
    for block in content:
        if (isinstance(block, dict)
                and json_string(block.get("type")) in ("input_text", "output_text", "text")):
            text = json_string(block.get("text"))
            if text is not None:
                parts.append(text)
    return "\n\n".join(parts) if parts else None


def _codebuddy(state: _CodebuddyState, obj: dict) -> tuple[list[Message], Hints]:
    """``SearchCodebuddyDecoder``: flat ``sessionId``/``cwd``; ``summary``
    then ``ai-title`` are the titles; ``message`` records hold a top-level
    ``role`` and text blocks."""
    hints = Hints(session_id=json_string(obj.get("sessionId")))
    if not state.cwd_emitted:
        cwd = json_string(obj.get("cwd"))
        if cwd is not None:
            hints.cwd = cwd
            state.cwd_emitted = True
    ts = stamp_iso(obj.get("timestamp"))
    kind = obj.get("type")
    if kind == "summary":
        summary = json_string(obj.get("summary"))
        title = title_candidate(summary, 96) if summary else None
        if title is not None:
            hints.title = title
            state.saw_summary_title = True
    elif kind == "ai-title":
        ai_title = json_string(obj.get("aiTitle"))
        if not state.saw_summary_title and ai_title:
            hints.title = title_candidate(ai_title, 96)
    elif kind == "message":
        role = json_string(obj.get("role"))
        text = bounded(_codebuddy_text(obj.get("content")) or "")
        if text is None:
            return [], hints
        if role == USER:
            provider = obj.get("providerData")
            hints.injected_user_turn = (
                isinstance(provider, dict) and provider.get("skipRun") is True
            ) or is_injected_turn(text)
            return [Message(USER, text, ts)], hints
        if role == ASSISTANT:
            return [Message(ASSISTANT, text, ts)], hints
    return [], hints


def _hermes(obj: dict) -> tuple[list[Message], Hints]:
    """``SearchHermesDecoder``: one whole ``session_*.json`` document."""
    hints = Hints(session_id=json_string(obj.get("session_id")), cwd=json_string(obj.get("cwd")))
    messages: list[Message] = []
    records = obj.get("messages")
    for record in records if isinstance(records, list) else ():
        if isinstance(record, dict) and record.get("role") in (USER, ASSISTANT):
            messages += content_messages(record["role"], record.get("content"), None)
    return messages, hints


def _swift_json(value) -> str:
    """``JSONSerialization.data(withJSONObject:)`` text: compact, with
    ``/`` escaped. Key order can still differ: Foundation keeps none."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).replace("/", "\\/")


def _kiro_blocks(content) -> list:
    """``toPreviewBlocks``: Kiro's ``{kind, data}`` blocks restated as the
    Claude-shaped blocks ``content_messages`` reads."""
    blocks: list = []
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict):
            continue
        kind = block.get("kind")
        data = block.get("data")
        if kind == "text":
            if isinstance(data, str) and data:
                blocks.append({"type": "text", "text": data})
        elif kind == "json":
            # Foundation serializes only an object or array at the top.
            if isinstance(data, (dict, list)):
                blocks.append({"type": "text", "text": _swift_json(data)})
        elif kind == "toolUse":
            tool = data if isinstance(data, dict) else None
            use: dict = {"type": "tool_use"}
            name = json_string(tool.get("name")) if tool else None
            if name is not None:
                use["name"] = name
            if tool is not None and "input" in tool:
                use["input"] = tool["input"]
            blocks.append(use)
        elif kind == "toolResult":
            blocks.append({"type": "tool_result", "content": _kiro_blocks(
                data.get("content") if isinstance(data, dict) else None)})
    return blocks


def _kiro(obj: dict) -> tuple[list[Message], Hints]:
    """``SearchKiroDecoder``: a transcript record (``kind``) or the
    ``<uuid>.json`` manifest."""
    hints = Hints()
    kind = obj.get("kind")
    if isinstance(kind, str):
        data = obj.get("data")
        if not isinstance(data, dict):
            return [], hints
        blocks = _kiro_blocks(data.get("content"))
        if kind == "Prompt":
            meta = data.get("meta")
            ts = stamp_iso(meta.get("timestamp")) if isinstance(meta, dict) else None
            return content_messages(USER, blocks, ts), hints
        if kind == "AssistantMessage":
            return content_messages(ASSISTANT, blocks, None), hints
        if kind == "ToolResults":
            return content_messages(TOOL, blocks, None), hints
        return [], hints
    hints.cwd = json_string(obj.get("cwd"))
    title = json_string(obj.get("title"))
    hints.title = title_candidate(title, 96) if title else None
    return [], hints


def _grok_flatten(value) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    parts = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            kind = item.get("type")
            if isinstance(kind, str) and kind != "text":
                continue
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
    return "\n".join(parts) if parts else None


_QUERY_OPEN = re.compile(re.escape("<user_query>"), re.IGNORECASE)
_QUERY_CLOSE = re.compile(re.escape("</user_query>"), re.IGNORECASE)


def grok_strip_query(text: str) -> str:
    """``stripUserQueryEnvelope``: the ``<user_query>`` body, or the text
    as it is; a missing closer still yields the tail."""
    start = _QUERY_OPEN.search(text)
    if start is None:
        return text
    body = text[start.end():]
    end = _QUERY_CLOSE.search(body)
    return (body[:end.start()] if end else body).strip()


def _grok_bootstrap(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized.startswith("<user_info>"):
        return False
    end = normalized.find("</user_info>")
    if end < 0:
        return "<user_query>" not in normalized
    remainder = normalized[end + len("</user_info>"):].strip()
    return not remainder or (remainder.startswith("<git_status>")
                             and remainder.endswith("</git_status>"))


def _grok_prompt(value) -> str | None:
    raw = _grok_flatten(value)
    if raw is None or _grok_bootstrap(raw):
        return None
    trimmed = grok_strip_query(raw).strip()
    if not trimmed or _grok_bootstrap(trimmed):
        return None
    head = trimmed.lower()
    if head.startswith(("<user_info>", "<user_info ")) and "<user_query>" not in head:
        return None
    return trimmed


def _grok(obj: dict) -> tuple[list[Message], Hints]:
    """``SearchGrokDecoder``: a ``chat_history.jsonl`` record or the
    ``summary.json`` document."""
    hints = Hints()
    kind = obj.get("type")
    if kind in (USER, ASSISTANT):
        ts = stamp_iso(obj.get("timestamp"))
        content = obj.get("content")
        if kind == USER:
            body = _grok_prompt(content)
            text = bounded(body) if body is not None else None
            return ([Message(USER, text, ts)] if text else []), hints
        if isinstance(content, str):
            text = bounded(grok_strip_query(content))
            return ([Message(ASSISTANT, text, ts)] if text else []), hints
        return content_messages(ASSISTANT, content, ts), hints
    info = obj.get("info") if isinstance(obj.get("info"), dict) else {}
    hints.session_id = json_string(info.get("id"))
    hints.cwd = json_string(info.get("cwd"))
    generated = json_string(obj.get("generated_title"))
    summary = json_string(obj.get("session_summary"))
    hints.title = ((title_candidate(generated, 96) if generated else None)
                   or (title_candidate(summary, 96) if summary else None))
    return [], hints


# The cheap byte gates (`markers`) run before json.loads.
_MARKERS = {
    "claude": (b'"type":"user"', b'"type":"assistant"', b'"custom-title"', b'"ai-title"'),
    "codex": (b'"response_item"', b'"event_msg"', b'"session_meta"', b'"turn_context"'),
    "devin": (b'"steps"', b'"role"', b'"source"', b'"step_id"'),
    # Pi writes `"type": "x"` with a space; `"session` covers both
    # `"session"` and `"session_info"`.
    "pi": (b'"message"', b'"session'),
    "cursor": (b'"role":"user"', b'"role":"assistant"'),
    "qoder": (b'"type":"user"', b'"type":"assistant"', b'"type": "user"', b'"type": "assistant"',
              b'"custom-title"', b'"ai-title"', b'"workspace-directories"'),
    "codebuddy": (b'"message"', b'"summary"', b'"ai-title"', b'"sessionId"', b'"cwd"',
                  b'"gitBranch"'),
    "hermes": (b'"session_id"', b'"messages"', b'"cwd"', b'"session_start"'),
    "kiro": (b'"kind"', b'"session_id"', b'"session_state"', b'"cwd"', b'"title"',
             b'"parent_session_id"'),
    "grok": (b'"type"', b'"info"', b'"generated_title"', b'"session_summary"', b'"head_branch"',
             b'"user_query"', b'"user_info"', b'"content"'),
}

HARNESSES = tuple(_MARKERS)


@dataclass
class Decoder:
    """One file's decoder (``SearchDecoder``): per-harness state plus the
    first-prompt title bookkeeping."""

    harness: str
    prompt_title_sent: bool = False
    explicit_title_seen: bool = False
    _codex: _CodexState = field(default_factory=_CodexState)
    _devin: _DevinState = field(default_factory=_DevinState)
    _qoder: _QoderState = field(default_factory=_QoderState)
    _codebuddy: _CodebuddyState = field(default_factory=_CodebuddyState)

    def wants(self, line: bytes) -> bool:
        return any(marker in line for marker in _MARKERS[self.harness])

    def decode_line(self, line: bytes) -> tuple[list[Message], Hints]:
        """``decode(line:)``: the marker gate, then the parse. For a whole
        JSON document (Devin's ``transcripts/*.json``) the "line" is the
        file."""
        if not self.wants(line):
            return [], Hints()
        try:
            obj = json.loads(line)
        except (ValueError, RecursionError):
            return [], Hints()
        if not isinstance(obj, dict):
            return [], Hints()
        return self.decode_object(obj)

    def decode_object(self, obj: dict) -> tuple[list[Message], Hints]:
        if self.harness == "claude":
            messages, hints = _claude(obj)
        elif self.harness == "codex":
            messages, hints = _codex(self._codex, obj)
        elif self.harness == "devin":
            messages, hints = _devin(self._devin, obj)
        elif self.harness == "pi":
            messages, hints = _pi(obj)
        elif self.harness == "cursor":
            messages, hints = _cursor(obj)
        elif self.harness == "qoder":
            messages, hints = _qoder(self._qoder, obj)
        elif self.harness == "codebuddy":
            messages, hints = _codebuddy(self._codebuddy, obj)
        elif self.harness == "hermes":
            messages, hints = _hermes(obj)
        elif self.harness == "kiro":
            messages, hints = _kiro(obj)
        else:
            messages, hints = _grok(obj)
        if hints.title is not None:
            self.explicit_title_seen = True
        elif not (self.prompt_title_sent or self.explicit_title_seen
                  or hints.injected_user_turn):
            first = next((m for m in messages if m.role == USER), None)
            candidate = title_candidate(first.text) if first else None
            if candidate is not None:
                self.prompt_title_sent = True
                hints.title = candidate
        return messages, hints

    def prime(self, obj: dict) -> None:
        """Feed a line from before an append's offset for its state only.

        Swift starts an append with a fresh decoder, which forgets codex's
        ``history_mode`` and indexes a paginated rollout's tail in the
        wrong shape. Reading the head again for state costs a few lines."""
        if self.harness == "codex" and obj.get("type") == "session_meta":
            _codex(self._codex, obj)


# -- rows (SearchChunker + identifierShadowText) -------------------------


def _is_token_char(ch: str) -> bool:
    if ch in "_.-/+":
        return True
    if ch.isalpha() or ch.isnumeric():
        return True
    return unicodedata.category(ch) == "Co"


def _is_boundary(ch: str) -> bool:
    if _is_token_char(ch):
        return False
    # A lone combining mark folds onto its neighbour rather than breaking.
    return unicodedata.category(ch) not in ("Mn", "Mc", "Me")


def chunks(text: str) -> list[str]:
    """``text`` split into pieces of at most ``CHUNK_LIMIT`` UTF-16 units,
    cut at the last token boundary in each window's second half."""
    if utf16_len(text) <= CHUNK_LIMIT:
        return [text]
    offsets = []
    offset = 0
    for ch in text:
        offsets.append(offset)
        offset += _units(ch)
    rows: list[str] = []
    start = 0
    count = len(text)
    while start < count:
        window_end = offsets[start] + CHUNK_LIMIT
        end = start
        while end < count and offsets[end] + _units(text[end]) <= window_end:
            end += 1
        if end == start:
            end = start + 1
        if end < count:
            floor = offsets[start] + CHUNK_LIMIT // 2
            index = end - 1
            while index > start and offsets[index] >= floor:
                if _is_boundary(text[index]):
                    end = index + 1
                    break
                index -= 1
        rows.append(text[start:end])
        start = end
    return rows


def _is_sep(b: int) -> bool:
    return b in (0x5F, 0x2E, 0x2F, 0x2D)  # _ . / -


def _is_upper(b: int) -> bool:
    return 65 <= b <= 90


def _is_lower(b: int) -> bool:
    return 97 <= b <= 122


def _is_digit(b: int) -> bool:
    return 48 <= b <= 57


def _is_token_byte(b: int) -> bool:
    return _is_upper(b) or _is_lower(b) or _is_digit(b) or _is_sep(b)


def _interesting(token: bytes) -> bool:
    previous_low_or_digit = False
    for b in token:
        if _is_sep(b):
            return True
        if _is_upper(b) and previous_low_or_digit:
            return True
        previous_low_or_digit = _is_lower(b) or _is_digit(b)
    head = 0
    while head < len(token) and _is_upper(token[head]):
        head += 1
    if head < 2:
        return False
    return all(_is_digit(b) or b == 0x5F for b in token[head:])


def _pieces(token: bytes) -> list[bytes]:
    out = []
    index = 0
    while index < len(token):
        if _is_sep(token[index]):
            index += 1
            continue
        end = index + 1
        while end < len(token) and not _is_sep(token[end]):
            end += 1
        out.append(token[index:end])
        index = end
    return out


def _camel_pieces(piece: bytes) -> list[bytes]:
    out = []
    index = 0
    n = len(piece)
    while index < n:
        if _is_upper(piece[index]):
            end = index + 1
            while end < n and _is_upper(piece[end]):
                end += 1
            if end < n and _is_lower(piece[end]):
                if end - index == 1:
                    tail = end
                    while tail < n and (_is_lower(piece[tail]) or _is_digit(piece[tail])):
                        tail += 1
                    out.append(piece[index:tail])
                    index = tail
                else:
                    out.append(piece[index:end - 1])
                    index = end - 1
            else:
                out.append(piece[index:end])
                index = end
        else:
            end = index + 1
            while end < n and (_is_lower(piece[end]) or _is_digit(piece[end])):
                end += 1
            out.append(piece[index:end])
            index = end
    return out


def identifier_shadow_text(text: str, limit: int = 4000) -> str:
    """Interesting identifiers split on ``_./-`` and camel humps,
    lowercased and deduplicated, so ``jsonlFiles`` hits ``jsonl``."""
    data = text.encode("utf-8")
    out: list[str] = []
    seen: set[str] = set()

    def add(piece: bytes) -> None:
        lowered = piece.decode("ascii").lower()
        if len(lowered) >= 2 and lowered not in seen:
            seen.add(lowered)
            out.append(lowered)

    index = 0
    n = len(data)
    while index < n and len(out) < limit:
        if not _is_token_byte(data[index]):
            index += 1
            continue
        end = index + 1
        while end < n and _is_token_byte(data[end]):
            end += 1
        token = data[index:end]
        index = end
        if not (3 <= len(token) <= 120 and _interesting(token)):
            continue
        for piece in _pieces(token):
            if len(out) >= limit:
                break
            add(piece)
            if any(_is_lower(b) for b in piece) and any(_is_upper(b) for b in piece):
                for sub in _camel_pieces(piece):
                    if len(out) >= limit:
                        break
                    add(sub)
    return " ".join(out)


@dataclass
class Row:
    """One ``messages`` row plus its FTS row (``SearchMessageRow``)."""

    role: str
    ts: str | None
    user_text: str = ""
    assistant_text: str = ""
    tool_text: str = ""
    identifiers: str = ""


def message_rows(messages: list[Message]) -> list[Row]:
    rows: list[Row] = []
    for message in messages:
        if not message.text.strip():
            continue
        if message.role == TOOL:
            pieces = [truncate(message.text, TOOL_ROW_LIMIT)]
        else:
            pieces = chunks(message.text)
        for piece in pieces:
            row = Row(role=message.role, ts=message.ts,
                      identifiers=identifier_shadow_text(piece))
            if message.role == USER:
                row.user_text = piece
            elif message.role == ASSISTANT:
                row.assistant_text = piece
            else:
                row.tool_text = piece
            rows.append(row)
    return rows
