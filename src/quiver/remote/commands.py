"""``swe remote serve|pair|status|devices`` — argv + print only.

The serve command runs the hub in the foreground (a launchd/systemd
unit or ``nohup`` keeps it alive); ``pair`` mints a one-time token into
``pair_tokens.json`` that the running hub honors on ``POST /v1/pair``;
``status`` probes the hub and reports reachability, machines and paired
devices.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from quiver.console import c
from quiver.flags import expand_value_flags
from quiver.json_output import emit, error_stream, take_json_flag
from quiver.paths import remote_dir_for
from quiver.remote import catalog as catalog_mod
from quiver.remote import pairing
from quiver.remote.hub import DEFAULT_PORT, HubState, serve

_SUBCOMMANDS = {"serve", "pair", "status", "devices"}

_VALUE_FLAGS = {"--host", "--port", "--name", "--tail-days", "--revoke"}

_USAGE = f"""\
  {c('cyan', 'swe remote serve [--host=H] [--port=N]')}   Run the hub (REST + SSE)
  {c('cyan', 'swe remote pair [--name=N] [--host=H] [--port=N]')}
                                  Print a pairing URL + token for a phone
  {c('cyan', 'swe remote status [--host=H] [--port=N]')}  Probe the hub + list machines
  {c('cyan', 'swe remote devices [--revoke=<id>]')}       List or revoke paired devices
"""


def _tailscale_ip() -> str:
    """This machine's tailnet IPv4, or '' when Tailscale is absent."""
    exe = shutil.which("tailscale")
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, "ip", "-4"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""


def _parse_flags(args):
    """Returns (subcommand, flags dict, positionals) or raises ValueError."""
    args, json_view = take_json_flag(args)
    try:
        args = expand_value_flags(args, _VALUE_FLAGS)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    flags: dict[str, str] = {}
    positionals: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _VALUE_FLAGS:
            if i + 1 >= len(args):
                raise ValueError(f"{arg} takes a value: write it as {arg}=<value>")
            flags[arg] = args[i + 1]
            i += 2
        elif arg.startswith("-") and arg != "-":
            raise ValueError(f"unknown flag: {arg}")
        else:
            positionals.append(arg)
            i += 1
    sub = positionals[0] if positionals else ""
    return sub, flags, positionals[1:], json_view


def _host_port(flags: dict) -> tuple[str, int]:
    host = flags.get("--host") or ""
    try:
        port = int(flags.get("--port") or DEFAULT_PORT)
    except ValueError:
        raise ValueError("--port must be a number")
    return host, port


def _probe(host: str, port: int, timeout: float = 3.0) -> dict | None:
    url = f"http://{host}:{port}/v1/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _cmd_serve(flags: dict) -> int:
    host, port = _host_port(flags)
    if not host:
        host = _tailscale_ip() or "127.0.0.1"
        if host == "127.0.0.1":
            print(
                c("yellow",
                  "no Tailscale address found; binding localhost only"),
                file=sys.stderr,
            )
    state = HubState()
    print(
        f"remote hub on {c('cyan', f'http://{host}:{port}')} "
        f"as {c('bold', state.machine['name'])} ({state.machine_id})"
    )
    print(c("dim", f"state: {remote_dir_for()}"))
    try:
        return serve(state, host, port)
    except OSError as exc:
        print(c("red", f"cannot bind {host}:{port} — {exc}"),
              file=sys.stderr)
        return 1


def _cmd_pair(flags: dict) -> int:
    host, port = _host_port(flags)
    if not host:
        host = _tailscale_ip() or "127.0.0.1"
    token = pairing.mint_pair_token()
    url = pairing.pair_url(host, port, token)
    payload = pairing.pair_payload(host, port, token)
    name = flags.get("--name") or ""
    print(f"Pair a device ({int(pairing.TOKEN_TTL_S / 60)} min window):\n")
    print(f"  {c('cyan', url)}")
    print(f"  {c('dim', payload)}\n")
    qrencode = shutil.which("qrencode")
    if qrencode:
        try:
            subprocess.run(
                [qrencode, "-t", "ANSIUTF8", url], check=False, timeout=10
            )
            print()
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        print(c("dim", "  (install qrencode for a QR; the URL works too)"))
    if name:
        print(c("dim", f"  device name on exchange: {name}"))
    print(c("dim", f"  raw token: {token}"))
    return 0


def _cmd_status(flags: dict, json_view: bool) -> int:
    host, port = _host_port(flags)
    if not host:
        host = _tailscale_ip() or "127.0.0.1"
    info = _probe(host, port)
    devices = pairing.load_devices()
    machines = catalog_mod.load_machines()
    if json_view:
        return emit({
            "hub": info or {"ok": False},
            "hub_addr": f"{host}:{port}",
            "machines": list(machines.values()),
            "devices": [
                {
                    "id": did,
                    "name": row.get("name"),
                    "created_at": row.get("created_at"),
                    "last_seen_at": row.get("last_seen_at"),
                    "revoked": bool(row.get("revoked_at")),
                }
                for did, row in devices.items()
            ],
        })
    if info is None:
        print(c("red", f"hub unreachable at {host}:{port}"))
        print(c("dim", f"  start it: swe remote serve --host={host}"))
    else:
        machine = info.get("machine") or {}
        print(
            f"hub {c('green', 'online')} at {c('cyan', f'{host}:{port}')} "
            f"· up {info.get('uptime_s', '?')}s · swe {info.get('version')}"
        )
        print(f"  machine: {machine.get('name', '?')} ({machine.get('id', '?')})")
    print(f"  devices paired: {len(devices)}")
    if machines:
        print("  machines:")
        for mid, row in machines.items():
            state = c("green", "online") if row.get("online") else c("dim", "offline")
            print(f"    {row.get('name', mid)} {state} ({mid})")
    return 0 if info is not None else 1


def _cmd_devices(flags: dict, json_view: bool) -> int:
    revoke = flags.get("--revoke")
    if revoke:
        if pairing.revoke_device(None, revoke):
            print(f"revoked {revoke}")
            return 0
        print(c("red", f"no such device: {revoke}"),
              file=error_stream(json_view))
        return 1
    devices = pairing.load_devices()
    if json_view:
        return emit({
            "devices": [
                {
                    "id": did,
                    "name": row.get("name"),
                    "created_at": row.get("created_at"),
                    "last_seen_at": row.get("last_seen_at"),
                    "revoked": bool(row.get("revoked_at")),
                }
                for did, row in devices.items()
            ]
        })
    if not devices:
        print(c("dim", "no paired devices — swe remote pair"))
        return 0
    for did, row in devices.items():
        mark = c("red", "revoked") if row.get("revoked_at") else c("green", "active")
        seen = row.get("last_seen_at")
        seen_s = time.strftime("%Y-%m-%d %H:%M", time.localtime(seen)) if seen else "never"
        print(f"  {did}  {row.get('name', 'device')}  {mark}  last seen {seen_s}")
    return 0


def cmd_remote(args: list[str]) -> int:
    try:
        sub, flags, rest, json_view = _parse_flags(list(args))
    except ValueError as exc:
        print(c("red", str(exc)), file=sys.stderr)
        return 1
    if not sub or sub not in _SUBCOMMANDS:
        print(_USAGE)
        if sub:
            print(c("red", f"unknown subcommand: {sub}"), file=sys.stderr)
            return 1
        return 0
    if sub == "serve":
        if rest:
            print(c("red", f"unexpected args: {' '.join(rest)}"), file=sys.stderr)
            return 1
        return _cmd_serve(flags)
    if sub == "pair":
        if rest:
            print(c("red", f"unexpected args: {' '.join(rest)}"), file=sys.stderr)
            return 1
        return _cmd_pair(flags)
    if sub == "status":
        if rest:
            print(c("red", f"unexpected args: {' '.join(rest)}"), file=sys.stderr)
            return 1
        return _cmd_status(flags, json_view)
    if sub == "devices":
        return _cmd_devices(flags, json_view)
    return 0
