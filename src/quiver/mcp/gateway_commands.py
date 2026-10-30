"""``swe mcp gateway``: run shared MCP servers once for every harness.

See ``quiver.mcp.gateway`` for what is shared and ``gateway_server`` for
how one server process serves many sessions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from quiver.console import c, cpad
from quiver.flags import expand_value_flags
from quiver.json_output import emit, take_json_flag
from quiver.mcp import gateway as gw
from quiver.remote import launchd

LABEL = "com.cwenlong.quiver.mcp-gateway"


def _hub_data() -> dict:
    from quiver.mcp.cli import MCP_SOURCE_FILE, load_json

    return load_json(MCP_SOURCE_FILE) or {}


def _save_hub_data(data: dict) -> None:
    from datetime import datetime

    from quiver.mcp.cli import MCP_SOURCE_FILE, save_json

    data["updated"] = datetime.now().isoformat()
    save_json(MCP_SOURCE_FILE, data)


def current_settings() -> dict:
    return gw.settings_of(_hub_data())


def _save_settings(**changes) -> dict:
    data = _hub_data()
    merged = {**gw.settings_of(data), **changes}
    data[gw.GATEWAY_KEY] = merged
    _save_hub_data(data)
    return merged


def _fetch(port: int, path: str, timeout: float = 2.0):
    """GET a gateway endpoint as JSON, or None when nothing answers."""
    try:
        with urllib.request.urlopen(f"http://{gw.HOST}:{port}{path}", timeout=timeout) as resp:
            return json.loads(resp.read() or b"null")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _parse_port(args: list[str]) -> int | None:
    """``--port=N`` from argv, or None; raises ValueError on a bad one."""
    expanded = expand_value_flags(args, {"--port"})
    unknown = [a for a in expanded if a.startswith("-") and a != "--port"]
    if unknown:
        raise ValueError(f"Unknown flag(s): {', '.join(unknown)}")
    if "--port" not in expanded:
        return None
    value = expanded[expanded.index("--port") + 1]
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise ValueError(f"--port must be 1-65535, not {value!r}")
    return int(value)


# -- serve ---------------------------------------------------------------


def _serve(args: list[str]) -> int:
    from quiver.mcp.gateway_server import serve

    try:
        port = _parse_port(args) or current_settings()["port"]
    except ValueError as exc:
        print(c("red", str(exc)), file=sys.stderr)
        return 1
    try:
        return serve(port, gw.log_dir_for())
    except OSError as exc:
        print(c("red", f"cannot bind {gw.HOST}:{port}: {exc}"), file=sys.stderr, flush=True)
        return 1


# -- install / uninstall --------------------------------------------------


def _supports_gateway(swe: str) -> bool:
    try:
        res = subprocess.run([swe, "mcp", "help", "gateway"], capture_output=True,
                             text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def gateway_program(port: int) -> tuple[list[str], str | None]:
    """The LaunchAgent's command line, or an error saying why there is none.

    Quiver.app's bundled swe first, as for the remote hub (a launchd agent
    can't read a checkout in ~/Desktop), but only once it knows this
    command; an app built before the gateway would exit at every start.
    """
    bundled = launchd.bundled_swe()
    if bundled and _supports_gateway(str(bundled)):
        swe = str(bundled)
    else:
        swe = os.path.abspath(sys.argv[0])
        if not os.access(swe, os.X_OK) or not _supports_gateway(swe):
            return [], (f"cannot find a swe with `mcp gateway` to run ({sys.argv[0]}); "
                        "run this as `swe mcp gateway install`")
        folder = launchd.protected_dir(swe)
        if folder:
            return [], (f"{swe} is in ~/{folder}, which macOS blocks a LaunchAgent from "
                        "reading. Install Quiver.app (app/mac/script/install-app.sh) and "
                        "run install again: the agent then runs the app's bundled swe.")
    # Always spelled out: an agent left to read the port from mcp.json
    # could start before install saves the new one and bind the old.
    return [swe, "mcp", "gateway", "serve", f"--port={port}"], None


def _install(args: list[str]) -> int:
    if sys.platform != "darwin":
        print(c("red", "install runs the gateway under launchd, so macOS only for now; "
                       "elsewhere run `swe mcp gateway serve` under your init system"),
              file=sys.stderr)
        return 1
    try:
        port = _parse_port(args) or current_settings()["port"]
    except ValueError as exc:
        print(c("red", str(exc)), file=sys.stderr)
        return 1
    program, err = gateway_program(port)
    if err:
        print(c("red", err), file=sys.stderr)
        return 1
    log_path = gw.log_dir_for() / "gateway.log"
    try:
        # Standard, not the hub's Background: macOS throttles a background
        # job and every server it starts, and a large tools/list (X's is
        # 6 MB) then took 10-18 s, past Claude Code's fetch timeout.
        path = launchd.install(program, log_path, os.environ.get("PATH", ""), label=LABEL,
                               process_type="Standard")
    except RuntimeError as exc:
        print(c("red", f"launchctl could not load the gateway: {exc}"), file=sys.stderr)
        return 1
    _save_settings(enabled=True, port=port)
    print(f"installed {path}")
    print(f"  runs  {' '.join(program)}")
    print(f"  log   {log_path}")
    for _ in range(20):
        if _fetch(port, "/health", timeout=1.0):
            print(c("green", f"  ✓ answering on http://{gw.HOST}:{port}"))
            break
        time.sleep(0.5)
    else:
        print(c("yellow", f"  not answering on port {port} yet; see {log_path}"), file=sys.stderr)
    _sync_hint()
    return 0


def _uninstall(args: list[str]) -> int:
    if sys.platform == "darwin":
        try:
            removed = launchd.uninstall(label=LABEL)
        except RuntimeError as exc:
            print(c("red", f"could not stop the gateway: {exc}"), file=sys.stderr)
            return 1
        print(f"stopped the gateway and removed {launchd.plist_path(label=LABEL)}" if removed
              else c("dim", "no gateway LaunchAgent was installed"))
    was = current_settings()["enabled"]
    _save_settings(enabled=False)
    if was:
        # Harnesses still point at the gateway's URLs until synced back.
        _sync_hint()
    return 0


def _sync_hint(names: list[str] | None = None) -> None:
    only = f" --only={','.join(names)}" if names else ""
    print(c("dim", f"  point harnesses at it: swe mcp sync quiver --all{only} --force"))


# -- share / unshare ------------------------------------------------------


def _share(args: list[str], shared: bool) -> int:
    from quiver.mcp.cli import get_hub_servers, save_hub_servers

    force = "--force" in args
    names = [a for a in args if not a.startswith("-")]
    verb = "share" if shared else "unshare"
    if not names:
        print(f"Usage: {c('cyan', f'swe mcp gateway {verb} <server...>')}")
        return 1
    servers = get_hub_servers()
    unknown = [n for n in names if n not in servers]
    if unknown:
        print(c("red", f"not in the hub: {', '.join(unknown)}"), file=sys.stderr)
        return 1
    if shared:
        remote = [n for n in names if not servers[n].get("command") or servers[n].get("url")]
        if remote:
            print(c("red", f"only stdio servers can be shared; reached at a URL: "
                           f"{', '.join(remote)}"), file=sys.stderr)
            return 1
        risky = [n for n in names if gw.looks_stateful(n, servers[n])]
        if risky and not force:
            print(c("red", f"{', '.join(risky)} looks like it keeps state per session (a "
                           "browser or REPL), and one shared copy would mix sessions up. "
                           "Pass --force to share it anyway."), file=sys.stderr)
            return 1
    changed = []
    for name in names:
        entry = dict(servers[name])
        if gw.is_shared(entry) == shared:
            continue
        if shared:
            entry[gw.SHARED_KEY] = True
        else:
            entry.pop(gw.SHARED_KEY, None)
        servers[name] = entry
        changed.append(name)
    if changed:
        save_hub_servers(servers)
    print(f"{'shared' if shared else 'unshared'}: {', '.join(changed)}" if changed
          else c("dim", f"nothing to change: already {'shared' if shared else 'unshared'}"))
    settings = current_settings()
    if changed and settings["enabled"]:
        _sync_hint(changed)
    elif changed and shared:
        print(c("dim", "  the gateway is not installed yet: swe mcp gateway install"))
    return 0


# -- status ---------------------------------------------------------------


def gateway_status() -> dict:
    """The ``swe mcp gateway status --json`` payload."""
    from quiver.mcp.cli import get_hub_servers

    settings = current_settings()
    installed = launchd.plist_path(label=LABEL).exists() if sys.platform == "darwin" else False
    pid = launchd.loaded_pid(label=LABEL)[1] if installed else None
    live = _fetch(settings["port"], "/status")
    running = {row["name"]: row for row in (live or {}).get("servers", [])
               if isinstance(row, dict) and "name" in row}
    rows = []
    for name, server in sorted(get_hub_servers().items()):
        if not gw.is_shared(server):
            continue
        row = running.get(name) or {"state": "idle" if live else "down", "pid": None,
                                    "uptime_s": None, "starts": 0, "error": None}
        rows.append({"name": name, "url": gw.url_for(name, settings["port"]),
                     **{k: row.get(k) for k in ("state", "pid", "uptime_s", "starts", "error")}})
    return {"enabled": settings["enabled"], "port": settings["port"],
            "installed": installed, "pid": pid, "reachable": live is not None,
            "error": (live or {}).get("error"), "servers": rows}


def _status(args: list[str]) -> int:
    args, json_view = take_json_flag(list(args))
    status = gateway_status()
    if json_view:
        return emit(status)
    port = status["port"]
    if status["reachable"]:
        head = c("green", f"running on http://{gw.HOST}:{port}")
    elif status["installed"]:
        head = c("yellow", f"installed but not answering on port {port}")
    else:
        head = c("dim", "not installed")
    print(f"\n  {c('bold', 'MCP gateway')}  {head}")
    if status["error"]:
        print(c("yellow", f"  {status['error']}"))
    if status["enabled"] != status["installed"] and sys.platform == "darwin":
        note = ("sync points harnesses here, but no LaunchAgent runs it: swe mcp gateway install"
                if status["enabled"] else "installed, but sync still writes commands; "
                "run swe mcp gateway install again")
        print(c("yellow", f"  {note}"))
    if not status["servers"]:
        print(c("dim", "  no shared servers yet: swe mcp gateway share <server...>\n"))
        return 0
    print()
    for row in status["servers"]:
        state = row["state"]
        colour = {"running": "green", "error": "red", "down": "yellow"}.get(state, "dim")
        detail = (f"pid {row['pid']}, up {row['uptime_s']}s" if state == "running"
                  else row.get("error") or "")
        print(f"  {row['name']:<24} {cpad(colour, state, 9)} {c('dim', detail)}")
    print()
    return 0


def cmd_gateway(args: list[str]) -> int:
    from quiver.mcp.cli import MCP_HELP

    sub = args[0] if args else "status"
    rest = args[1:]
    if sub in ("-h", "--help", "help") or "--help" in rest or "-h" in rest:
        print(MCP_HELP["gateway"])
        return 0
    handlers = {
        "status": _status,
        "serve": _serve,
        "install": _install,
        "uninstall": _uninstall,
        "share": lambda a: _share(a, True),
        "unshare": lambda a: _share(a, False),
    }
    if sub not in handlers:
        print(f"Unknown gateway command: {sub}")
        print(f"Available: {', '.join(handlers)}")
        return 1
    return handlers[sub](rest)
