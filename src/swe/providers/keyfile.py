"""The provider keys file as the Mac app edits it.

A port of ``APIKeysFile`` and ``APIKeysStore`` (app/mac/Sources/QuiverCore/
APIKeys/APIKeys.swift), so the web app's Settings › Providers › Inference
reads and writes ``~/.swe/secrets/providers/.api_keys`` exactly as the
Mac app does: ``export NAME="value"`` lines grouped under ``# Provider``
comment headings, every other line left byte for byte.

``KeysFile`` parses and edits the text; ``load`` and ``save`` read and
write it with the app's rules: the previous version copied to
``~/.swe/backups/api-keys/api_keys.bak`` (folder mode 700), the new one
written mode 600 through a temp file and a rename, ``~/.swe/secrets``
sealed with its own ``.gitignore`` of ``*``, and a save refused when the
file changed since it was read. Error messages name variables, never values.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from swe.paths import SECRETS_GITIGNORE_BODY, swe_dir_for, secrets_dir_for


class KeyFileError(Exception):
    """A refused edit or save, worded for the page (``APIKeysError``)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPORT_RE = re.compile(r"^export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_UNSAFE_RE = re.compile(r"^export\s+([A-Za-z_][A-Za-z0-9_]*)=")
_EXPORT_WORD_RE = re.compile(r"\b(?i:export)\s+[A-Za-z_]")
_BARE_FORBIDDEN = set("$`\"'\\;&|<>()")
_LABEL_PUNCT = set(" ().,&/-")


@dataclass(frozen=True)
class Entry:
    provider: str
    name: str
    value: str
    line: int  # zero-based


def is_valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def is_label(text: str) -> bool:
    """Short words of letters and digits with light punctuation, as a
    heading reads: "Anthropic", "Kimi (Moonshot AI)", "ZAI (01.ai)"."""
    return (
        bool(text) and len(text) <= 60 and any(c.isalpha() for c in text)
        and all(c.isalpha() or c.isdigit() or c in _LABEL_PUNCT for c in text)
        and not any(len(word) >= 16 for word in text.split(" "))
        and not _EXPORT_WORD_RE.search(text)
    )


def _comment_text(line: str) -> str | None:
    trimmed = line.strip(" \t")
    if not trimmed.startswith("#"):
        return None
    return trimmed.lstrip("#").strip(" \t")


def heading(line: str) -> str | None:
    text = _comment_text(line)
    return text if text is not None and is_label(text) else None


def unquote(raw: str) -> str | None:
    """The value, or None when the line carries anything after it other
    than a comment: saving such a line would drop it. None too for a
    double-quoted ``$`` or backtick the shell expands (``"$OTHER"``): the
    text isn't the value, and saving it back escaped would turn the
    reference into a literal."""
    if raw.startswith('"'):
        value, escaped, closed = [], False, None
        for index in range(1, len(raw)):
            char = raw[index]
            if escaped:
                # In double quotes only these four take a backslash.
                if char not in '"\\$`':
                    value.append("\\")
                value.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                closed = index
                break
            elif char in "$`":
                return None
            else:
                value.append(char)
        if closed is None:
            return None
        rest = raw[closed + 1:]
        value = "".join(value)
    elif raw.startswith("'"):
        end = raw.find("'", 1)
        if end < 0:
            return None
        value, rest = raw[1:end], raw[end + 1:]
    else:
        end = next((i for i, c in enumerate(raw) if c.isspace()), len(raw))
        value = raw[:end]
        if any(c in _BARE_FORBIDDEN for c in value):
            return None
        rest = raw[end:]
    trailing = rest.strip(" \t")
    if trailing and not (trailing.startswith("#") and rest[:1].isspace()):
        return None
    return value


def export(line: str) -> tuple[str, str] | None:
    """``export NAME=value``, double-quoted, single-quoted or bare."""
    match = _EXPORT_RE.match(line.strip(" \t"))
    if not match:
        return None
    value = unquote(match.group(2).strip(" \t"))
    return None if value is None else (match.group(1), value)


def render(name: str, value: str) -> str:
    escaped = "".join("\\" + c if c in '"\\$`' else c for c in value)
    return f'export {name}="{escaped}"'


class KeysFile:
    def __init__(self, text: str):
        self.ends_with_newline = text.endswith("\n")
        lines = text.split("\n")
        if self.ends_with_newline:
            lines.pop()
        self.lines = [] if not text else lines

    @property
    def text(self) -> str:
        tail = "\n" if self.ends_with_newline and self.lines else ""
        return "\n".join(self.lines) + tail

    @property
    def entries(self) -> list[Entry]:
        provider, out = "", []
        for index, line in enumerate(self.lines):
            head = heading(line)
            if head is not None:
                provider = head
                continue
            pair = export(line)
            if pair:
                out.append(Entry(provider, pair[0], pair[1], index))
        return out

    @property
    def keys(self) -> list[Entry]:
        """One per variable, in first-seen order, holding the value a
        shell ends up with (the later line, for a name exported twice).
        A name whose last assignment is a line we can't read has no known
        value, so it isn't listed."""
        last = {e.name: e for e in self.entries}
        unsafe = self.unsafe_lines
        seen, out = set(), []
        for entry in self.entries:
            if entry.name not in seen:
                seen.add(entry.name)
                if unsafe.get(entry.name, -1) < last[entry.name].line:
                    out.append(last[entry.name])
        return out

    @property
    def unsafe_lines(self) -> dict[str, int]:
        """The last line index of each name an ``export`` line we can't
        edit safely assigns (``export A="$OTHER"``, ``export A=x; ...``)."""
        found = {}
        for index, line in enumerate(self.lines):
            if export(line) is None:
                match = _UNSAFE_RE.match(line.strip(" \t"))
                if match:
                    found[match.group(1)] = index
        return found

    def set(self, name: str, value: str, provider: str) -> None:
        """In place if it exists, else at the end of its provider's group,
        else in a new group at the end of the file."""
        if not is_valid_name(name):
            raise KeyFileError("bad_name", f"{name} isn't a valid variable name. Use letters, digits and underscores, not starting with a digit.")
        if "\n" in value or "\r" in value:
            raise KeyFileError("bad_value", "A key can't span more than one line.")
        if "\n" in provider or "\r" in provider:
            raise KeyFileError("bad_provider", _BAD_PROVIDER)
        line = render(name, value)
        entries = self.entries
        matches = [e for e in entries if e.name == name]
        if len(matches) > 1:
            raise KeyFileError("duplicate", _duplicate(name))
        # A value set in place still wins when it comes after the unsafe
        # line; a new line, or one the unsafe line overrides, wouldn't.
        unsafe = self.unsafe_lines.get(name)
        if unsafe is not None and (matches[0].line if matches else -1) < unsafe:
            raise KeyFileError("duplicate", _unsafe(name))
        if matches:
            self.lines[matches[0].line] = line
            return
        provider = provider.strip(" \t")
        group = [e for e in entries if e.provider == provider]
        if group:
            self.lines.insert(group[-1].line + 1, line)
            return
        # A heading the file wouldn't read back would file the key under
        # whichever provider came before it.
        if not is_label(provider):
            raise KeyFileError("bad_provider", _BAD_PROVIDER)
        if self.lines and self.lines[-1] != "":
            self.lines.append("")
        self.lines += [f"# {provider}", line]
        # A new file ends with a newline, as an editor would leave it.
        if len(self.lines) == 2:
            self.ends_with_newline = True

    def remove(self, name: str) -> None:
        """Drops a key, and its heading once nothing is left under it."""
        entries = self.entries
        matches = [e for e in entries if e.name == name]
        if len(matches) > 1:
            raise KeyFileError("duplicate", _duplicate(name))
        if not matches:
            raise KeyFileError("not_found", f"{name} isn't in the file any more.")
        if name in self.unsafe_lines:
            raise KeyFileError("duplicate", _unsafe(name))
        entry = matches[0]
        siblings = [e for e in entries if e.provider == entry.provider and e.name != name]
        del self.lines[entry.line]
        if siblings or not entry.provider:
            return
        above = self.lines[:min(entry.line, len(self.lines))]
        found = [i for i, line in enumerate(above) if heading(line) == entry.provider]
        if not found:
            return
        at = found[-1]
        del self.lines[at]
        # Leave one blank line between groups, not two.
        if 0 < at < len(self.lines) and self.lines[at - 1] == "" and self.lines[at] == "":
            del self.lines[at]


_BAD_PROVIDER = "A provider name has to be a short label: up to 60 letters, digits, spaces and ( ) . , & / -."


def _unsafe(name: str) -> str:
    return (f"{name} is also set on a line SWE can't edit safely. "
            "Change or remove that line in an editor, then edit it here.")


def _duplicate(name: str) -> str:
    return f"{name} is exported more than once in the file. Remove the extra line in an editor, then edit it here."


# -- the store ---------------------------------------------------------------


def backup_dir_for(home: Path | None = None) -> Path:
    """Where the Mac app keeps one backup of the previous version."""
    return swe_dir_for(home) / "backups" / "api-keys"


def _target(path: Path) -> Path:
    """The real file behind a symlink, so a save doesn't replace the link.
    A symlink loop raises RuntimeError before Python 3.13; later ones hand
    back a path that is still a link, which a save would replace."""
    try:
        target = path.resolve()
        looped = target.is_symlink()
    except (OSError, RuntimeError) as exc:
        raise KeyFileError("unreadable", f"Couldn't read the keys file: {exc}") from None
    if looped:
        raise KeyFileError("unreadable", f"Couldn't read the keys file: {path} is a symlink loop")
    return target


def load(path: Path) -> tuple[KeysFile, bytes | None]:
    """The file and its bytes as read (None when there is none yet)."""
    target = _target(path)
    try:
        data = target.read_bytes()
    except FileNotFoundError:
        return KeysFile(""), None
    except OSError as exc:
        raise KeyFileError("unreadable", f"Couldn't read the keys file: {exc.strerror or exc}") from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # Lossy decoding would rewrite bytes on lines no one edited.
        raise KeyFileError("unreadable", "Couldn't read the keys file: it isn't valid UTF-8, so SWE won't rewrite it") from None
    return KeysFile(text), data


def _current(target: Path) -> bytes | None:
    try:
        return target.read_bytes()
    except OSError:
        return None


def save(path: Path, keys: KeysFile, stamp: bytes | None, home: Path | None = None) -> None:
    """Writes ``keys`` mode 600 after backing up the previous version,
    unless the file no longer holds ``stamp``. Both files are prepared
    first and the file is checked again just before they are swapped in."""
    target = _target(path)
    changed = KeyFileError("changed_on_disk", "The file changed since it was read. Reload to see the new version, then make the change again.")
    if _current(target) != stamp:
        raise changed
    prepared: list[Path] = []
    try:
        backup = backup_dir_for(home) / "api_keys.bak"
        backup_temp = None
        if stamp is not None:
            backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # It may already exist with looser permissions; it holds secrets.
            os.chmod(backup.parent, 0o700)
            backup_temp = _private_temp(stamp, backup)
            prepared.append(backup_temp)
        _seal(target, home)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target_temp = _private_temp(keys.text.encode("utf-8"), target)
        prepared.append(target_temp)
        if _current(target) != stamp:
            raise changed
        if backup_temp is not None:
            os.replace(backup_temp, backup)
        os.replace(target_temp, target)
        prepared.clear()
    except KeyFileError:
        raise
    except OSError as exc:
        raise KeyFileError("unwritable", f"Couldn't save the keys file: {exc.strerror or exc}") from None
    finally:
        for temp in prepared:
            try:
                temp.unlink()
            except OSError:
                pass


def _seal(target: Path, home: Path | None) -> None:
    """A save into ~/.swe/secrets first gives the folder its own
    ``.gitignore`` of ``*``, which git lets win over the hub's root one."""
    try:
        folder = secrets_dir_for(home).resolve()
    except (OSError, RuntimeError):
        return
    if folder not in target.parents:
        return
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    seal = folder / ".gitignore"
    try:
        if seal.read_text(encoding="utf-8") == SECRETS_GITIGNORE_BODY:
            return
    except OSError:
        pass
    temp = _private_temp(SECRETS_GITIGNORE_BODY.encode("utf-8"), seal)
    os.replace(temp, seal)


def _private_temp(data: bytes, destination: Path) -> Path:
    """A temp file created 600 beside ``destination``, so a secret is
    never readable by others even for a moment."""
    temp = destination.parent / f".{destination.name}.swe-{uuid.uuid4().hex}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except OSError:
        # A half-written temp file may hold some of the keys.
        temp.unlink(missing_ok=True)
        raise
    return temp
