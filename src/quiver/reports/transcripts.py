"""Normalize local harness transcripts into a common, semantic message stream."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from quiver.sessions.models import Session


@dataclass(frozen=True)
class NormalizedMessage:
    """A semantic transcript message, independent of a harness wire format."""

    role: str
    text: str
    kind: str = "message"
    timestamp: float | None = None


@dataclass
class NormalizedTranscript:
    """Normalized transcript plus provenance and a graceful read status."""

    tool_name: str
    session_id: str
    project_path: str
    messages: list[NormalizedMessage] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)
    readable: bool = True
    error: str = ""

    @property
    def normalized_text(self) -> str:
        return "\n\n".join(f"{m.role}: {m.text}" for m in self.messages)

    @property
    def digest(self) -> str:
        payload = {
            "tool": self.tool_name,
            "session": self.session_id,
            "path": self.project_path,
            "messages": [
                {"role": m.role, "kind": m.kind, "text": m.text}
                for m in self.messages
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
        return hashlib.sha256(encoded).hexdigest()


Reader = Callable[[Session], NormalizedTranscript]
READER_REGISTRY: dict[str, Reader] = {}


def register_reader(*tool_names: str) -> Callable[[Reader], Reader]:
    def decorate(reader: Reader) -> Reader:
        for tool_name in tool_names:
            READER_REGISTRY[tool_name] = reader
        return reader

    return decorate


def read_transcript(session: Session) -> NormalizedTranscript:
    """Read one session without allowing malformed local state to escape."""

    reader = READER_REGISTRY.get(session.tool_name)
    if reader is None:
        return _unreadable(session, f"no transcript reader for {session.tool_name}")
    try:
        transcript = reader(session)
        transcript.messages = _deduplicate(transcript.messages)
        return transcript
    except Exception as exc:
        return _unreadable(session, f"{type(exc).__name__}: {exc}")


def _new(session: Session, paths: Iterable[Path] = ()) -> NormalizedTranscript:
    return NormalizedTranscript(
        tool_name=session.tool_name,
        session_id=session.session_id,
        project_path=session.path,
        source_paths=[str(path) for path in paths],
    )


def _unreadable(session: Session, error: str, paths: Iterable[Path] = ()) -> NormalizedTranscript:
    transcript = _new(session, paths)
    transcript.readable = False
    transcript.error = error
    return transcript


_ENVELOPE_TAGS = (
    "app-context",
    "environment_context",
    "permissions instructions",
    "recommended_plugins",
    "local-command-caveat",
    "command-name",
    "command-message",
    "local-command-stdout",
)
_ENVELOPE_RE = re.compile(
    r"<(?P<envelope_tag>"
    + "|".join(re.escape(tag) for tag in _ENVELOPE_TAGS)
    + r")\b[^>]*>.*?</(?P=envelope_tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----.*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)
_AUTHORIZATION_RE = re.compile(
    r"(?im)(\b(?:proxy-)?authorization\s*:\s*)"
    r"(?:(?:bearer|basic|token)\s+)?[^\s\"',;]+"
)
_SENSITIVE_NAME = (
    r"(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|BEARER_TOKEN|CLIENT_SECRET|"
    r"PRIVATE_KEY|SECRET_KEY|PASSWORD|PASSWD|TOKEN)"
)
_ENV_ASSIGNMENT_RE = re.compile(
    r"(?im)(\b(?:export\s+)?(?:[A-Z][A-Z0-9_]*_)?"
    + _SENSITIVE_NAME
    + r"\s*=\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s#;]+)"
)
_JSON_SECRET_RE = re.compile(
    r"(?i)([\"'](?:[A-Z][A-Z0-9_]*_)?"
    + _SENSITIVE_NAME
    + r"[\"']\s*:\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*')"
)
_KNOWN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[opusr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")(?![A-Za-z0-9])"
)


def _redact_secrets(text: str) -> str:
    """Remove credential material while preserving useful transcript context."""

    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _AUTHORIZATION_RE.sub(r"\1[REDACTED]", text)
    text = _ENV_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = _JSON_SECRET_RE.sub(r"\1\"[REDACTED]\"", text)
    return _KNOWN_TOKEN_RE.sub("[REDACTED]", text)


def _clean_text(value: Any) -> str:
    """Extract prose from structured content without flattening protocol JSON."""

    if value is None:
        return ""
    if isinstance(value, str):
        text = _ENVELOPE_RE.sub("", value)
        text = _redact_secrets(text)
        text = text.replace("\x00", "").strip()
        return text
    if isinstance(value, list):
        parts = [_clean_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if not isinstance(value, dict):
        return str(value).strip()

    block_type = str(value.get("type") or "").lower()
    if block_type in {"image", "image_url", "thinking", "reasoning", "redacted_thinking"}:
        return ""
    for key in ("text", "content", "message", "output", "result"):
        if key in value:
            text = _clean_text(value[key])
            if text:
                return text
    return ""


def _role(value: Any) -> str:
    role = str(value or "").lower()
    if role in {"user", "human"}:
        return "human"
    if role in {"assistant", "agent", "model", "ai"}:
        return "assistant"
    if role in {"tool", "function", "tool_result", "toolresult"}:
        return "tool"
    return ""


def _message(role: str, content: Any, *, kind: str = "message", timestamp: Any = None) -> NormalizedMessage | None:
    if role not in {"human", "assistant", "tool"}:
        return None
    text = _clean_text(content)
    if not text:
        return None
    ts: float | None = None
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        ts = float(timestamp)
    return NormalizedMessage(role=role, text=text, kind=kind, timestamp=ts)


def _messages_from_content(role: str, content: Any, timestamp: Any = None) -> list[NormalizedMessage]:
    if not isinstance(content, list):
        msg = _message(role, content, timestamp=timestamp)
        return [msg] if msg else []
    messages: list[NormalizedMessage] = []
    text_blocks: list[Any] = []
    for block in content:
        if not isinstance(block, dict):
            text_blocks.append(block)
            continue
        block_type = str(block.get("type") or "").lower()
        if block_type in {"tool_use", "tool-call", "function_call"}:
            msg = _tool_message(block.get("name"), block.get("input") or block.get("arguments"))
            if msg:
                messages.append(msg)
        elif block_type in {"tool_result", "tool-result", "function_call_output"}:
            msg = _tool_message(block.get("name") or "tool result", output=block.get("content") or block.get("output"))
            if msg:
                messages.append(msg)
        else:
            text_blocks.append(block)
    msg = _message(role, text_blocks, timestamp=timestamp)
    if msg:
        messages.insert(0, msg)
    return messages


def _tool_message(name: Any, arguments: Any = None, output: Any = None) -> NormalizedMessage | None:
    label = str(name or "tool").strip()
    if output is None and isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
            if isinstance(decoded, (dict, list)):
                arguments = decoded
        except json.JSONDecodeError:
            pass
    content = output if output is not None else arguments
    text = _clean_text(content)
    if isinstance(arguments, dict) and output is None:
        evidence = next(
            (
                arguments.get(key)
                for key in ("command", "cmd", "path", "file_path", "filePath", "filename")
                if arguments.get(key)
            ),
            None,
        )
        text = _clean_text(evidence) or text
    if not text:
        return None
    return NormalizedMessage("tool", f"{label}: {text}", kind="tool")


def _messages_from_record(record: Any) -> list[NormalizedMessage]:
    if not isinstance(record, dict):
        return []
    text_envelope = record.get("text") if isinstance(record.get("text"), dict) else None
    if text_envelope is not None and text_envelope.get("role"):
        return _messages_from_content(
            _role(text_envelope.get("role")),
            text_envelope.get("content"),
            record.get("timestamp"),
        )
    if isinstance(record.get("payload"), dict):
        payload = record["payload"]
        payload_type = str(payload.get("type") or "").lower()
        if record.get("type") == "response_item" and payload_type == "message":
            return _messages_from_content(
                _role(payload.get("role")), payload.get("content"), record.get("timestamp")
            )
        if record.get("type") == "response_item" and payload_type in {"function_call", "custom_tool_call"}:
            msg = _tool_message(payload.get("name"), payload.get("arguments") or payload.get("input"))
            return [msg] if msg else []
        if record.get("type") == "response_item" and payload_type in {"function_call_output", "custom_tool_call_output"}:
            msg = _tool_message(payload.get("name") or "tool result", output=payload.get("output"))
            return [msg] if msg else []

    event_type = str(record.get("type") or "").lower()
    if event_type in {"session_meta", "session_start", "system", "developer", "progress", "summary"}:
        return []
    nested = record.get("message")
    if isinstance(nested, dict):
        nested_text = nested.get("text") if isinstance(nested.get("text"), dict) else {}
        role = _role(nested.get("role") or nested_text.get("role") or record.get("role"))
        return _messages_from_content(
            role,
            nested.get("content", nested_text.get("content", nested.get("text"))),
            record.get("timestamp"),
        )
    role = _role(record.get("role") or record.get("type"))
    return _messages_from_content(
        role,
        record.get("content", record.get("text", record.get("message"))),
        record.get("timestamp"),
    )


def _read_jsonl(path: Path) -> list[NormalizedMessage]:
    messages: list[NormalizedMessage] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            messages.extend(_messages_from_record(record))
    return messages


def _read_json_messages(path: Path) -> list[NormalizedMessage]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = (
            data.get("messages")
            or data.get("history")
            or data.get("conversation")
            or data.get("logs")
            or []
        )
    else:
        records = []
    messages: list[NormalizedMessage] = []
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("message"), dict) and "role" not in record:
            messages.extend(_messages_from_record(record))
        else:
            messages.extend(_messages_from_record(record))
    return messages


def _deduplicate(messages: list[NormalizedMessage]) -> list[NormalizedMessage]:
    result: list[NormalizedMessage] = []
    for message in messages:
        if result and (message.role, message.kind, message.text) == (
            result[-1].role,
            result[-1].kind,
            result[-1].text,
        ):
            continue
        result.append(message)
    return result


def _find_session_file(root: Path, session_id: str, patterns: tuple[str, ...] = ("*.jsonl",)) -> Path | None:
    if session_id:
        direct = Path(session_id).expanduser()
        if direct.is_file():
            return direct
    if not root.exists():
        return None
    sid = Path(session_id).stem if session_id else ""
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(root.glob(f"**/{pattern}"))
    if sid:
        for path in candidates:
            if sid == path.stem or sid in path.parts:
                return path
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def _jsonl_reader(root: str, patterns: tuple[str, ...] = ("*.jsonl",)) -> Reader:
    def read(session: Session) -> NormalizedTranscript:
        path = _find_session_file(Path(os.path.expanduser(root)), session.session_id, patterns)
        if path is None:
            return _unreadable(session, "transcript file not found")
        transcript = _new(session, [path])
        transcript.messages = _read_jsonl(path)
        return transcript

    return read


for _name, _root, _patterns in (
    ("claude", "~/.claude/projects", ("*.jsonl",)),
    ("droid", "~/.factory/sessions", ("*.jsonl",)),
    ("codex", "~/.codex/sessions", ("*.jsonl",)),
    ("pi", "~/.pi/agent/sessions", ("*.jsonl",)),
    ("kimi", "~/.kimi/sessions", ("context.jsonl", "*.jsonl")),
    ("cursor", "~/.cursor/projects", ("*.jsonl",)),
):
    READER_REGISTRY[_name] = _jsonl_reader(_root, _patterns)


@register_reader("tau")
def _read_tau(session: Session) -> NormalizedTranscript:
    root = Path(os.path.expanduser("~/.tau/sessions"))
    path: Path | None = None
    for index in root.glob("*/index.jsonl") if root.exists() else ():
        try:
            with index.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    entry = json.loads(line)
                    if str(entry.get("id") or "") != session.session_id:
                        continue
                    candidate = entry.get("path")
                    path = Path(candidate) if candidate else index.parent / f"{session.session_id}.jsonl"
                    if not path.is_absolute():
                        path = index.parent / path
                    break
        except (OSError, json.JSONDecodeError):
            continue
        if path:
            break
    if path is None or not path.exists():
        path = _find_session_file(root, session.session_id)
    if path is None:
        return _unreadable(session, "Tau transcript not found")
    transcript = _new(session, [path])
    transcript.messages = _read_jsonl(path)
    return transcript


@register_reader("freebuff")
def _read_freebuff(session: Session) -> NormalizedTranscript:
    root = Path(os.path.expanduser("~/.config/manicode/projects"))
    chat = _find_session_file(root, session.session_id, ("log.jsonl",))
    if chat is None:
        return _unreadable(session, "Freebuff log not found")
    transcript = _new(session, [chat])
    state = chat.parent / "run-state.json"
    if state.exists():
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            history = data.get("sessionState", {}).get("mainAgentState", {}).get("messageHistory", [])
            transcript.messages = [m for row in history for m in _messages_from_record(row)]
            transcript.source_paths.append(str(state))
        except (OSError, json.JSONDecodeError):
            pass
    if not transcript.messages:
        transcript.messages = _read_jsonl(chat)
    return transcript


@register_reader("grok")
def _read_grok(session: Session) -> NormalizedTranscript:
    root = Path(os.path.expanduser("~/.grok/sessions"))
    path = _find_session_file(root, session.session_id, ("chat_history.jsonl",))
    if path is None:
        return _unreadable(session, "Grok chat history not found")
    transcript = _new(session, [path])
    transcript.messages = _read_jsonl(path)
    return transcript


@register_reader("continue")
def _read_continue(session: Session) -> NormalizedTranscript:
    path = Path(os.path.expanduser(f"~/.continue/sessions/{session.session_id}.json"))
    if not path.exists():
        return _unreadable(session, "Continue session not found")
    transcript = _new(session, [path])
    transcript.messages = _read_json_messages(path)
    return transcript


@register_reader("cline")
def _read_cline(session: Session) -> NormalizedTranscript:
    root = Path(os.path.expanduser(f"~/.cline/data/tasks/{session.session_id}"))
    paths = [root / "api_conversation_history.json", root / "ui_messages.json"]
    existing = [path for path in paths if path.exists()]
    if not existing:
        return _unreadable(session, "Cline task transcript not found")
    transcript = _new(session, existing)
    for path in existing:
        try:
            transcript.messages.extend(_read_json_messages(path))
        except (OSError, json.JSONDecodeError):
            continue
    return transcript


def _file_json_reader(root: str, filename: Callable[[Session], str]) -> Reader:
    def read(session: Session) -> NormalizedTranscript:
        path = Path(os.path.expanduser(root)) / filename(session)
        if not path.exists():
            return _unreadable(session, "JSON transcript not found")
        transcript = _new(session, [path])
        transcript.messages = _read_json_messages(path)
        return transcript

    return read


READER_REGISTRY["amp"] = _file_json_reader("~/.local/share/amp/threads", lambda s: f"{s.session_id}.json")
READER_REGISTRY["hermes"] = _file_json_reader("~/.hermes/sessions", lambda s: f"{s.session_id}.json" if s.session_id.startswith("session_") else f"session_{s.session_id}.json")


@register_reader("gemini")
def _read_gemini(session: Session) -> NormalizedTranscript:
    index = Path(os.path.expanduser("~/.gemini/projects.json"))
    try:
        projects = json.loads(index.read_text(encoding="utf-8")).get("projects", {})
        hash_dir = projects.get(session.path) or projects.get(session.session_id)
    except (OSError, json.JSONDecodeError, AttributeError):
        hash_dir = None
    if not hash_dir:
        return _unreadable(session, "Gemini project mapping not found")
    path = Path(os.path.expanduser(f"~/.gemini/tmp/{hash_dir}/logs.json"))
    if not path.exists():
        return _unreadable(session, "Gemini logs not found")
    transcript = _new(session, [path])
    transcript.messages = _read_json_messages(path)
    return transcript


@register_reader("antigravity")
def _read_antigravity(session: Session) -> NormalizedTranscript:
    root = Path(os.path.expanduser("~/.gemini/antigravity/brain"))
    directories = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
    if session.session_id:
        directories = [p for p in directories if p.name == session.session_id]
    if not directories:
        return _unreadable(session, "Antigravity brain session not found")
    directory = min(directories, key=lambda p: abs((p.stat().st_mtime * 1000) - session.timestamp))
    paths = list(directory.glob("*.metadata.json"))
    overview = directory / ".system_generated/logs/overview.txt"
    if overview.exists():
        paths.append(overview)
    transcript = _new(session, paths)
    for path in paths:
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                transcript.messages.extend(_messages_from_record(data))
            elif path.name == "overview.txt":
                text = path.read_text(encoding="utf-8", errors="replace")
                transcript.messages.extend(_antigravity_overview_messages(text))
        except (OSError, json.JSONDecodeError):
            continue
    return transcript


def _antigravity_overview_messages(text: str) -> list[NormalizedMessage]:
    messages: list[NormalizedMessage] = []
    field_re = re.compile(
        r'"(?P<field>Prompt|Response|Message)":"(?P<content>(?:\\.|[^"\\])*)"'
    )
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = None
        if isinstance(record, dict) and record.get("content") is not None:
            source = str(record.get("source") or "").upper()
            role = "human" if source.startswith("USER") else "assistant" if source == "MODEL" else ""
            msg = _message(role, record.get("content"), timestamp=record.get("created_at"))
            if msg:
                messages.append(msg)
                continue
        for match in field_re.finditer(line):
            decoded = json.loads(f'"{match.group("content")}"')
            role = "human" if match.group("field") == "Prompt" else "assistant"
            msg = _message(role, decoded)
            if msg:
                messages.append(msg)
    return messages


def _open_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _sqlite_json_parts(session: Session, db_path: str) -> NormalizedTranscript:
    path = Path(os.path.expanduser(db_path))
    conn = _open_ro(path)
    if conn is None:
        return _unreadable(session, "SQLite store not found", [path])
    transcript = _new(session, [path])
    try:
        roles: dict[str, str] = {}
        for message_id, raw in conn.execute("SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created", (session.session_id,)):
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            roles[str(message_id)] = _role(data.get("role"))
            direct = _message(roles[str(message_id)], data.get("content") or data.get("text"))
            if direct:
                transcript.messages.append(direct)
        for message_id, raw in conn.execute("SELECT message_id, data FROM part WHERE session_id = ? ORDER BY time_created", (session.session_id,)):
            try:
                part = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type in {"tool", "tool-invocation", "tool_result"}:
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                msg = _tool_message(part.get("tool"), state.get("input"), state.get("output"))
            else:
                msg = _message(roles.get(str(message_id), "assistant"), part.get("text") or part.get("content"))
            if msg:
                transcript.messages.append(msg)
    except sqlite3.Error as exc:
        return _unreadable(session, f"SQLite schema error: {exc}", [path])
    finally:
        conn.close()
    return transcript


@register_reader("opencode")
def _read_opencode(session: Session) -> NormalizedTranscript:
    return _sqlite_json_parts(session, "~/.local/share/opencode/opencode.db")


@register_reader("mimo")
def _read_mimo(session: Session) -> NormalizedTranscript:
    return _sqlite_json_parts(session, "~/.local/share/mimocode/mimocode.db")


@register_reader("copilot")
def _read_copilot(session: Session) -> NormalizedTranscript:
    path = Path(os.path.expanduser("~/.copilot/session-store.db"))
    conn = _open_ro(path)
    if conn is None:
        return _unreadable(session, "Copilot store not found", [path])
    transcript = _new(session, [path])
    try:
        rows = conn.execute("SELECT user_message, assistant_response FROM turns WHERE session_id = ? ORDER BY turn_index", (session.session_id,))
        for user, assistant in rows:
            for role, content in (("human", user), ("assistant", assistant)):
                msg = _message(role, content)
                if msg:
                    transcript.messages.append(msg)
    except sqlite3.Error as exc:
        return _unreadable(session, f"SQLite schema error: {exc}", [path])
    finally:
        conn.close()
    return transcript


@register_reader("forge")
def _read_forge(session: Session) -> NormalizedTranscript:
    path = Path(os.path.expanduser("~/.forge/.forge.db"))
    conn = _open_ro(path)
    if conn is None:
        return _unreadable(session, "Forge store not found", [path])
    transcript = _new(session, [path])
    try:
        row = conn.execute("SELECT context FROM conversations WHERE conversation_id = ?", (session.session_id,)).fetchone()
        context = json.loads(row[0]) if row and row[0] else {}
        records = context.get("messages", []) if isinstance(context, dict) else []
        transcript.messages = [message for record in records for message in _messages_from_record(record.get("message", record) if isinstance(record, dict) else record)]
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        return _unreadable(session, f"Forge transcript error: {exc}", [path])
    finally:
        conn.close()
    return transcript


@register_reader("crush")
def _read_crush(session: Session) -> NormalizedTranscript:
    index = Path(os.path.expanduser("~/.local/share/crush/projects.json"))
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
        projects = data.get("projects", data) if isinstance(data, dict) else data
    except (OSError, json.JSONDecodeError):
        projects = []
    for project in projects if isinstance(projects, list) else []:
        if not isinstance(project, dict) or project.get("path") != session.path:
            continue
        db_path = Path(os.path.expanduser(str(project.get("data_dir") or ""))) / "crush.db"
        conn = _open_ro(db_path)
        if conn is None:
            continue
        transcript = _new(session, [db_path])
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "messages" in tables:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
                role_col = "role" if "role" in columns else "sender"
                text_col = "content" if "content" in columns else "message"
                for role, content in conn.execute(f"SELECT {role_col}, {text_col} FROM messages WHERE session_id = ? ORDER BY rowid", (session.session_id,)):
                    msg = _message(_role(role), content)
                    if msg:
                        transcript.messages.append(msg)
            return transcript
        except sqlite3.Error as exc:
            return _unreadable(session, f"Crush transcript error: {exc}", [db_path])
        finally:
            conn.close()
    return _unreadable(session, "Crush project store not found")


EXPECTED_READER_TOOLS = frozenset(
    {
        "opencode", "claude", "gemini", "antigravity", "codex", "pi", "cursor",
        "freebuff", "droid", "copilot", "continue", "crush", "amp", "kimi",
        "hermes", "grok", "cline", "forge", "mimo", "tau",
    }
)
