"""The MCP servers page: the Mac app's McpServersPage, read only.

``GET /v1/app/mcp`` is ``swe mcp list --json`` built in process (the same
``server_inventory`` rows), plus the managed servers folder the Mac app
scans beside it (``McpServerRepo``: which checkouts sit in
``~/.quiver/mcp/servers``, which servers run from them, their git state).
``GET /v1/app/mcp?health=1`` adds ``swe mcp status``'s health probe per
server, run in parallel under one deadline, and only when asked.

Nothing secret leaves here. swe already sends env and header names only
and scrubs known credentials out of commands, arguments and URLs; the
row's command, args and url are then replaced by ``summary``, the one line
the Mac app shows, redacted again the way ``McpRedaction`` does it, so a
token swe does not recognise still never reaches the browser.

Adding, removing, syncing servers and setting secret values are writes;
they stay in ``swe mcp`` and the Mac app (see docs/web-app/mcp.md).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import unquote, urlsplit

# A health probe HEADs each URL with its own five-second timeout; the whole
# check gets this long before a server still being probed reads "unknown".
HEALTH_DEADLINE_S = 30.0
HEALTH_WORKERS = 8
# McpServerRepoModel.gitBatch, and McpServerRepo.gitState's timeout.
GIT_WORKERS = 4
GIT_TIMEOUT_S = 5.0


def get(ctx, args: dict) -> dict:
    health = str(args.get("health", "")) in {"1", "true"}
    harnesses: list[str] = []
    pairs: list[tuple[dict, object]] = []
    error = None
    try:
        harnesses, pairs = inventory()
    except (OSError, ValueError) as exc:
        # The Mac app shows the error and still lists the folder rows.
        error = str(exc) or exc.__class__.__name__
    if health and pairs:
        for (row, _), (state, detail) in zip(pairs, probe_all(pairs)):
            row["health"], row["detail"] = state, detail
    folders = folder_scan(_servers_root(ctx), [row for row, _ in pairs])
    return {
        "harnesses": harnesses,
        "servers": [page_row(row) for row, _ in pairs],
        "health": bool(health and pairs),
        "error": error,
        "folders": folders,
        "machine": (ctx.machine or {}).get("name"),
    }


# ---------------------------------------------------------------- inventory


def inventory() -> tuple[list[str], list[tuple[dict, object]]]:
    """``swe mcp list --json`` for every harness, as ``(harnesses, [(row,
    first_raw_config)])``. The registry is read without the legacy
    migration write, so loading the page never changes a file."""
    from quiver.harness.registry import load_registry_readonly
    from quiver.mcp import cli as mcp_cli
    from quiver.mcp.inventory import server_inventory

    tools = mcp_cli._display_tools(mcp_cli.get_mcp_tools(load_registry_readonly()))
    tool_data = {name: mcp_cli.get_tool_servers(name) for name in tools}
    return list(tool_data), server_inventory(tool_data, include_hub=True)


def page_row(row: dict) -> dict:
    """The swe row without its command line or URL, which become the
    redacted ``summary``; env and header values were never in it."""
    out = {k: v for k, v in row.items() if k not in {"command", "args", "url"}}
    out["summary"] = summary(row.get("command"), row.get("args") or [], row.get("url"))
    return out


def probe_all(pairs: list[tuple[dict, object]],
              deadline: float = HEALTH_DEADLINE_S) -> list[tuple[str, str | None]]:
    """``swe mcp status``'s probe for every server, in parallel. A command
    is looked up on PATH, never started; a URL gets one HEAD request. A
    probe still running at the deadline reads unknown."""
    from quiver.mcp.cli import probe_server_health

    def one(row: dict, cfg) -> tuple[str, str | None]:
        if not isinstance(cfg, dict):
            return "unknown", "no command or url"
        try:
            return probe_server_health(row["name"], cfg)
        except Exception as exc:  # a malformed entry must not sink the page
            return "error", f"check failed: {exc.__class__.__name__}"

    pool = ThreadPoolExecutor(max_workers=HEALTH_WORKERS, thread_name_prefix="mcp-health")
    try:
        futures = [pool.submit(one, row, cfg) for row, cfg in pairs]
        wait(futures, timeout=deadline)
        return [f.result() if f.done() else ("unknown", "check timed out")
                for f in futures]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------- redaction
# A port of QuiverCore's McpRedaction and SecretText.maskingKeys: the parts
# of a command line or URL that may hold a secret, hidden as "…".

_SECRET_FLAG_ENDINGS = ("token", "key", "secret", "password", "passwd", "credential",
                        "credentials", "auth", "authorization", "header")
_KEY_PREFIXES = ("sk-", "sk_", "pk_", "rk_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
                 "github_pat_", "glpat-", "xoxa-", "xoxb-", "xoxp-", "xoxs-", "xapp-",
                 "AIza", "AKIA", "ASIA", "hf_", "gsk_", "xai-", "pplx-", "r8_", "npm_")
_KEY_RUN = re.compile(r"[A-Za-z0-9_\-]{16,}")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_URL_START = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_QUOTES = "\"'"


def summary(command: str | None, args: list, url: str | None) -> str:
    """The command line or URL on one line, with anything that may be a
    secret hidden (``McpServer.summary``)."""
    if url:
        return redact_url(str(url))
    words = command_words(str(command or ""), [str(a) for a in args])
    return " ".join(redact_args(words)).strip()


def looks_like_key(text: str) -> bool:
    """16+ characters with a vendor prefix, mixed case, or digits among
    letters; an upper-case NAME_WITH_UNDERSCORES is a variable name."""
    if len(text) < 16 or not any(ch.isalpha() for ch in text):
        return False
    if text.startswith(_KEY_PREFIXES):
        return True
    lower = any(ch.islower() for ch in text)
    upper = any(ch.isupper() for ch in text)
    digit = any(ch.isdigit() for ch in text)
    return (lower and upper) or (lower and digit) or (digit and "_" not in text)


def masking_keys(text: str) -> str:
    return _KEY_RUN.sub(lambda m: "…" if looks_like_key(m.group(0)) else m.group(0), text)


def _bare(flag: str) -> str:
    return "".join(ch for ch in flag if ch not in "\"'\\")


def is_secret_flag(flag: str) -> bool:
    flag = _bare(flag)
    if not flag.startswith("-"):
        return False
    if flag == "-H":
        return True
    name = "".join(ch for ch in flag if ch not in "-_").lower()
    return bool(name) and name.endswith(_SECRET_FLAG_ENDINGS)


def shell_words(line: str) -> list[str]:
    """Words split at unquoted whitespace, quotes and escapes kept; with an
    unclosed quote every space and quote splits, so no word hides a flag."""
    words: list[str] = []
    word = ""
    quote = None
    escaped = False
    for ch in line:
        if escaped:
            word += ch
            escaped = False
        elif ch == "\\" and quote != "'":
            word += ch
            escaped = True
        elif quote is not None:
            word += ch
            if ch == quote:
                quote = None
        elif ch in _QUOTES:
            word += ch
            quote = ch
        elif ch.isspace():
            if word:
                words.append(word)
            word = ""
        else:
            word += ch
    if quote is not None:
        return loose_words(line)
    if word:
        words.append(word)
    return words


def loose_words(line: str) -> list[str]:
    return [w for w in re.split(r"[\s\"']+", line) if w]


def command_words(command: str, args: list[str]) -> list[str]:
    """The command split like a shell, then each argument; an argument
    holding a flag and its value (``--token abc``) becomes two words."""
    words = shell_words(command)
    for arg in args:
        space = next((i for i, ch in enumerate(arg) if ch.isspace()), None)
        if arg.startswith("-") and space is not None and "=" not in arg[:space]:
            words.append(arg[:space])
            words.append(arg[space:].strip())
        else:
            words.append(arg)
    return words


def _secret_value(value: str, flag: str) -> str:
    flag = _bare(flag)
    header = flag == "-H" or flag.lower().endswith("header")
    if header and ":" in value:
        return value[:value.index(":") + 1] + "…"
    return "…"


def _quoted(arg: str) -> bool:
    return len(arg) >= 2 and arg[0] in _QUOTES and arg[-1] == arg[0]


def _url_word(arg: str) -> str | None:
    quote, inner = "", arg
    if _quoted(arg):
        quote, inner = arg[0], arg[1:-1]
    if not _URL_START.match(inner):
        return None
    space = next((i for i, ch in enumerate(inner) if ch.isspace()), None)
    if space is None:
        return quote + redact_url(inner) + quote
    return quote + redact_url(inner[:space]) + " …" + quote


def _line(text: str) -> str:
    words = shell_words(text)
    if words == [text]:
        words = loose_words(text)
    return " ".join(redact_args(words))


def redact_args(args: list[str]) -> list[str]:
    out: list[str] = []
    hide_next = None
    for arg in args:
        if hide_next is not None:
            out.append(_secret_value(arg, hide_next))
            hide_next = None
        elif "=" in arg and is_secret_flag(arg[:arg.index("=")]):
            flag = arg[:arg.index("=")]
            out.append(flag + "=" + _secret_value(arg[arg.index("=") + 1:], flag))
        elif is_secret_flag(arg):
            out.append(arg)
            hide_next = arg
        elif (assign := _ENV_ASSIGN.match(arg)) is not None:
            # An environment assignment, as `env NAME=value cmd` takes.
            out.append(assign.group(0) + "…")
        elif (redacted := _url_word(arg)) is not None:
            out.append(redacted)
        elif _quoted(arg):
            # A quoted word may be a whole command, as `bash -c '…'` takes.
            out.append(arg[0] + _line(arg[1:-1]) + arg[0])
        elif any(ch.isspace() for ch in arg):
            out.append(_line(arg))
        elif "://" in arg:
            out.append(redact_url(arg))
        else:
            out.append(masking_keys(arg))
    return out


def redact_url(url: str) -> str:
    """The URL with userinfo, query values and fragment hidden, and any
    key-like path segment too."""
    rest, out = url, ""
    if "://" in rest:
        cut = rest.index("://") + 3
        out, rest = rest[:cut], rest[cut:]
    end = next((i for i, ch in enumerate(rest) if ch in "/?#"), len(rest))
    authority, rest = rest[:end], rest[end:]
    if "@" in authority:
        out += "…@"
        authority = authority[authority.rindex("@") + 1:]
    out += authority
    fragment = None
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
    query = None
    if "?" in rest:
        rest, query = rest.split("?", 1)

    def segment(seg: str) -> str:
        decoded = unquote(seg)
        if decoded != seg and masking_keys(decoded) != decoded:
            return "…"
        return masking_keys(seg)

    out += "/".join(segment(s) for s in rest.split("/"))
    if query is not None:
        out += "?" + "&".join(
            "" if not pair else (pair[:pair.index("=") + 1] + "…" if "=" in pair else "…")
            for pair in query.split("&"))
    if fragment:
        out += "#…"
    return out


# ---------------------------------------------------------------- folders
# A port of QuiverCore's McpServerRepo: the server checkouts in the managed
# folder, which inventory servers run from each, and each one's git state.

SERVER_MARKERS = (".git", "package.json", "pyproject.toml", "go.mod", "Cargo.toml",
                  "Dockerfile", "setup.py", "requirements.txt", "server.py", "main.py",
                  "index.js")


def _servers_root(ctx) -> Path:
    from quiver.paths import mcp_servers_dir_for

    return mcp_servers_dir_for(getattr(ctx, "home", None))


def _display_path(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


def _child_folders(path: Path) -> list[Path]:
    """The non-hidden folders right inside ``path``. A symlink is judged by
    itself, not its target, as the Mac app's directory listing does."""
    out = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    out.append(Path(entry.path))
            except OSError:
                continue
    return out


def scan(root: Path) -> list[dict]:
    """The server folders under ``root``: a child with no marker file is a
    group folder whose children are the servers; any other child is a
    server. Ungrouped first, then groups, names case-insensitively.
    A missing root is empty; a file, or a listing that fails, raises."""
    if not root.exists():
        return []
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is a file, not a folder.")
    try:
        children = _child_folders(root)
    except OSError as exc:
        raise OSError(f"Couldn't read {root}: {exc.strerror or exc}.") from exc
    found = []
    for child in children:
        if not any((child / marker).exists() for marker in SERVER_MARKERS):
            try:
                members = _child_folders(child)
            except OSError:
                members = []  # an unreadable group just shows no servers
            found += [{"name": m.name, "group": child.name, "path": m} for m in members]
        else:
            found.append({"name": child.name, "group": None, "path": child})
    found.sort(key=lambda f: (f["group"] is not None, (f["group"] or "").casefold(),
                              f["name"].casefold()))
    return found


def _unquote_word(word: str) -> str:
    """A word as exec receives it: quotes removed, backslash escapes undone
    (literal inside single quotes)."""
    out, quote, escaped = "", None, False
    for ch in word:
        if escaped:
            out += ch
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote is not None:
            if ch == quote:
                quote = None
            else:
                out += ch
        elif ch in _QUOTES:
            quote = ch
        else:
            out += ch
    if escaped:
        out += "\\"
    return out


def _inside(word: str, base: str) -> bool:
    if word.startswith("file://"):
        try:
            word = unquote(urlsplit(word).path)
        except ValueError:
            return False
    if not word.startswith("/"):
        return False
    path = os.path.normpath(word)
    return path == base or path.startswith(base.rstrip("/") + "/")


def hub_usage(folders: list[dict], servers: list[dict]) -> dict[str, list[str]]:
    """Each folder path -> the servers whose command or an argument is an
    absolute path at or under it, so ``node …/linear-mcp/build/index.js``
    matches while ``mcp-hnx`` never answers for ``mcp-hn``."""
    usage: dict[str, list[str]] = {}
    for folder in folders:
        base = os.path.normpath(str(folder["path"]))
        for server in servers:
            words = [str(a) for a in server.get("args") or []]
            command = server.get("command")
            if command:
                words.append(str(command))
                words += [_unquote_word(w) for w in shell_words(str(command))]
            if any(_inside(w, base) for w in words):
                usage.setdefault(base, []).append(server["name"])
    return usage


def parse_git(porcelain: str) -> dict:
    """``git status --porcelain=v1 --branch`` boiled down to a row's needs."""
    lines = [ln for ln in porcelain.split("\n") if ln]
    if not lines or not lines[0].startswith("## "):
        return git_state(is_repo=False)
    head = lines[0][3:]
    ahead = behind = 0
    if head.endswith("]") and " [" in head:
        open_at = head.rindex(" [")
        for part in head[open_at + 2:-1].split(","):
            part = part.strip()
            if part.startswith("ahead "):
                ahead = _int(part[6:])
            if part.startswith("behind "):
                behind = _int(part[7:])
        head = head[:open_at]
    changes = len(lines) > 1
    if head.startswith("HEAD (no branch)"):
        return git_state(None, ahead, behind, changes)
    if head.startswith("No commits yet on "):
        return git_state(head[18:], ahead, behind, changes)
    if "..." in head:
        head = head[:head.index("...")]
    return git_state(head, ahead, behind, changes)


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def git_state(branch: str | None = None, ahead: int = 0, behind: int = 0,
              has_changes: bool = False, is_repo: bool = True) -> dict:
    return {"branch": branch, "ahead": ahead, "behind": behind,
            "has_changes": has_changes, "is_repo": is_repo}


def read_git(path: Path) -> dict:
    """The folder's git state. Only a folder with its own ``.git`` counts,
    or ``git status`` would answer for an enclosing repository. Optional
    locks are off, so the read never rewrites the index. Anything off
    (git missing, an error, five seconds gone) is not a repo."""
    if not (path / ".git").exists():
        return git_state(is_repo=False)
    git = shutil.which("git")
    if not git:
        return git_state(is_repo=False)
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        done = subprocess.run(
            [git, "-C", str(path), "status", "--porcelain=v1", "--branch"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S, env=env,
            stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return git_state(is_repo=False)
    if done.returncode != 0 or "not a git repository" in done.stderr:
        return git_state(is_repo=False)
    return parse_git(done.stdout)


def folder_scan(root: Path, servers: list[dict]) -> dict:
    """The servers folder as the page shows it: each checkout with its git
    state and the servers that run from it (``used_by``), or why it could
    not be read. ``servers`` is the whole inventory, so a server the page
    filters out still marks its folder as used."""
    out = {"root": _display_path(root), "exists": root.exists(), "error": None,
           "items": []}
    try:
        found = scan(root)
    except OSError as exc:
        out["error"] = str(exc)
        return out
    usage = hub_usage(found, servers)
    with ThreadPoolExecutor(max_workers=GIT_WORKERS, thread_name_prefix="mcp-git") as pool:
        states = list(pool.map(lambda f: read_git(f["path"]), found))
    for folder, git in zip(found, states):
        path = os.path.normpath(str(folder["path"]))
        out["items"].append({
            "name": folder["name"],
            "group": folder["group"],
            "path": _display_path(Path(path)),
            "git": git,
            "used_by": usage.get(path, []),
        })
    return out
