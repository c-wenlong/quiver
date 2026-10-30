"""Which hub servers the MCP gateway runs, and what sync writes for them.

Every harness session starts its own copy of every stdio server it is
configured with: a dozen Claude Code sessions with 17 servers each is
about 200 processes and 8 GB. A server that keeps no per-session state
(an API client: GitHub, Linear, Notion, Brave) can run once and serve
them all. The gateway (``gateway_server.py``) does that: it starts each
shared server once and serves it as streamable HTTP on 127.0.0.1, and
``swe mcp sync`` points harnesses at that URL instead of the command.

A server is shared only when its hub entry says ``"shared": true``
(``swe mcp gateway share``); a browser or REPL server keeps state per
session and stays stdio. The gateway's own settings sit beside the
servers in mcp.json:

    {"gateway": {"enabled": true, "port": 8478}, "mcpServers": {...}}

``enabled`` is set by ``swe mcp gateway install`` and cleared by
``uninstall``, so sync never points a harness at a gateway that isn't
installed. Logic only: callers print.
"""

from __future__ import annotations

from pathlib import Path

from quiver.paths import cache_dir_for

GATEWAY_KEY = "gateway"
SHARED_KEY = "shared"
HOST = "127.0.0.1"
# Beside the remote hub's 8477.
DEFAULT_PORT = 8478
PATH_PREFIX = "/mcp/"

# Names that point at a server holding per-session state. Sharing one
# would hand every session the same browser or REPL, so ``share`` refuses
# them unless told otherwise.
STATEFUL_HINTS = ("playwright", "puppeteer", "browser", "chrome",
                  "computer-use", "computer_use", "repl")


def log_dir_for(home: Path | None = None) -> Path:
    """``~/.quiver/cache/mcp-gateway``: the gateway's log and one per server."""
    return cache_dir_for(home) / "mcp-gateway"


def settings_of(hub_data: dict | None) -> dict:
    """``{"enabled": bool, "port": int}`` from mcp.json's ``gateway`` key."""
    raw = (hub_data or {}).get(GATEWAY_KEY)
    raw = raw if isinstance(raw, dict) else {}
    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        port = DEFAULT_PORT
    return {"enabled": raw.get("enabled") is True, "port": port}


def is_shared(server) -> bool:
    return isinstance(server, dict) and server.get(SHARED_KEY) is True


def without_flags(server):
    """The hub entry minus the gateway's flag: what a harness would hold."""
    if not isinstance(server, dict) or SHARED_KEY not in server:
        return server
    return {k: v for k, v in server.items() if k != SHARED_KEY}


def runs_on_gateway(server, settings: dict) -> bool:
    """Whether sync points harnesses at the gateway for this hub entry.

    Only stdio servers: a URL server is already one process somewhere.
    """
    return (settings.get("enabled") is True and is_shared(server)
            and bool(server.get("command")) and not server.get("url"))


def url_for(name: str, port: int) -> str:
    return f"http://{HOST}:{port}{PATH_PREFIX}{name}"


def entry_for(name: str, port: int) -> dict:
    """The canonical definition sync writes for a server on the gateway."""
    return {"url": url_for(name, port)}


def is_gateway_entry(name: str, canonical, settings: dict | None = None) -> bool:
    """Whether a harness's canonical definition is a gateway URL for ``name``.

    Any loopback port counts, not just today's: a harness can still hold
    the URL from before a port change, until it is synced again.
    """
    if not isinstance(canonical, dict) or canonical.get("command"):
        return False
    url = canonical.get("url")
    if not isinstance(url, str):
        return False
    head = f"http://{HOST}:"
    if not url.startswith(head):
        return False
    port, sep, path = url[len(head):].partition("/")
    return port.isdigit() and sep == "/" and "/" + path == PATH_PREFIX + name


def matches_hub(name: str, hub_server, canonical, settings: dict) -> bool:
    """Whether a harness's definition is what sync writes for the hub's.

    That is the hub's own definition, or a gateway URL for a hub server
    that has a command. ``discover`` uses it so a synced gateway URL never
    reads as an edit to pull back into the hub. That holds after
    ``unshare``, ``uninstall`` or a port change too, until the harness is
    synced back: importing the URL would replace the command with a URL
    that points at the gateway, which runs that very command.
    """
    if without_flags(hub_server) == canonical:
        return True
    return (isinstance(hub_server, dict) and bool(hub_server.get("command"))
            and is_gateway_entry(name, canonical, settings))


def looks_stateful(name: str, server) -> bool:
    text = name.lower()
    if isinstance(server, dict):
        text += " " + str(server.get("command", "")).lower()
        text += " " + " ".join(str(a).lower() for a in server.get("args") or [])
    return any(hint in text for hint in STATEFUL_HINTS)
