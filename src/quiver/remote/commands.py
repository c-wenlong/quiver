"""``swe remote serve|pair|status|devices|attach`` — argv + print only.

The serve command runs the hub in the foreground; ``serve --install``
hands it to a LaunchAgent that restarts it after a crash or a reboot
(``launchd.py``), and a systemd unit does the same on Linux. ``pair`` mints a one-time token into
``pair_tokens.json`` that the running hub honors on ``POST /v1/pair``;
``status`` probes the hub and reports reachability, machines and paired
devices.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from quiver.console import c
from quiver.flags import expand_value_flags
from quiver.json_output import emit, error_stream, take_json_flag
from quiver.paths import remote_dir_for
from quiver.remote import agent as agent_mod
from quiver.remote import catalog as catalog_mod
from quiver.remote import launchd, notify, pairing, protocol, supervise, tail
from quiver.remote import setup as setup_mod
from quiver.remote.hub import (
    DEFAULT_PORT,
    HubState,
    check_public_url,
    load_hub_config,
    serve,
)

_SUBCOMMANDS = {"serve", "pair", "status", "devices", "attach", "agent",
                "notify", "setup"}

_VALUE_FLAGS = {
    "--host", "--port", "--name", "--tail-days", "--revoke", "--mode",
    "--hub", "--token", "--ntfy", "--topic", "--ntfy-token",
    "--approve", "--deny", "--public-url",
}
_BOOL_FLAGS = {"--on", "--off", "--app", "--requests", "--install",
               "--uninstall", "--tailnet", "--apply", "--recheck"}

_USAGE = f"""\
  {c('cyan', 'swe remote serve [--host=H] [--port=N]')}   Run the hub (REST + SSE)
  {c('cyan', 'swe remote serve --public-url=URL')}      Run behind tailscale serve (binds 127.0.0.1)
  {c('cyan', 'swe remote serve --install | --uninstall')}
                                  Keep the hub running as a LaunchAgent
  {c('cyan', 'swe remote setup [--apply] [--recheck] [--json]')}
                                  Check every step to a paired phone; --apply does Quiver's part
  {c('cyan', 'swe remote agent --hub=URL [--token=T]')}   Satellite: dial out to the hub
  {c('cyan', 'swe remote pair [--name=N] [--host=H] [--port=N] [--app] [--json]')}
                                  Print a pairing URL + token for a phone
  {c('cyan', 'swe remote status [--host=H] [--port=N]')}  Probe the hub + list machines
  {c('cyan', 'swe remote devices [--revoke=<id>] [--requests] [--approve=<code>] [--deny=<code>]')}
                                  List/revoke devices and approve pair requests
  {c('cyan', 'swe remote attach <session> [--mode=tmux|headless]')}
                                  Move an observed session under supervision
  {c('cyan', 'swe remote notify [--ntfy=URL --topic=T] [--on|--off]')}
                                  Configure ntfy push notifications
"""


_tailscale_ip = catalog_mod.tailscale_ip


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
        elif arg in _BOOL_FLAGS:
            flags[arg] = "1"
            i += 1
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


def _saved_hub(flags: dict) -> dict:
    """The last ``serve``'s address, when no --host/--port overrides it."""
    if flags.get("--host") or flags.get("--port"):
        return {}
    return load_hub_config()


def _phone_address(flags: dict) -> tuple[str, int, str]:
    """``(host, port, scheme)`` a phone should dial: the hub's public URL
    when it runs behind ``tailscale serve``, else the flags or the
    tailnet IP."""
    public = str(_saved_hub(flags).get("public_url") or "")
    if public:
        parts = urlsplit(public)
        default = 443 if parts.scheme == "https" else 80
        return parts.hostname or "", parts.port or default, parts.scheme
    host, port = _host_port(flags)
    return host or _tailscale_ip() or "127.0.0.1", port, "http"


def _public_url(flags: dict) -> str:
    """``--public-url`` normalised, or ""; raises ValueError when bad."""
    raw = flags.get("--public-url")
    return check_public_url(raw) if raw else ""


# How long --install waits for the new hub to answer: start-up plus a
# slow `tailscale ip` lookup and one launchd retry.
_INSTALL_WAIT_S = 20


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _cmd_serve(flags: dict) -> int:
    if flags.get("--install") or flags.get("--uninstall"):
        return _cmd_serve_launchd(flags)
    try:
        host, port = _host_port(flags)
        public_url = _public_url(flags)
    except ValueError as exc:
        print(c("red", str(exc)), file=sys.stderr)
        return 1
    # Behind tailscale serve the hub listens on loopback only; serve is
    # the one door in.
    if public_url and not host:
        host = "127.0.0.1"
    if not host:
        host = _tailscale_ip() or "127.0.0.1"
        if host == "127.0.0.1" and flags.get("--tailnet"):
            # Under launchd at login Tailscale may not be up yet: exit so
            # launchd retries, rather than serve a hub the phone can't reach.
            print(f"{_stamp()}  no Tailscale address yet; exiting so launchd retries",
                  file=sys.stderr, flush=True)
            return 1
        if host == "127.0.0.1":
            print(
                c("yellow",
                  "no Tailscale address found; binding localhost only"),
                file=sys.stderr,
            )
    state = HubState()
    # Timestamped and flushed: the log is often a file, and a hub killed
    # by a restart otherwise leaves no trace of when it ran.
    print(
        f"{_stamp()}  remote hub on {c('cyan', f'http://{host}:{port}')} "
        f"as {c('bold', state.machine['name'])} ({state.machine_id})",
        flush=True,
    )
    if public_url:
        print(f"phones reach it at {c('cyan', public_url)}", flush=True)
    print(c("dim", f"state: {remote_dir_for()}"), flush=True)
    try:
        rc = serve(state, host, port, public_url)
    except OSError as exc:
        print(c("red", f"{_stamp()}  cannot bind {host}:{port} — {exc}"),
              file=sys.stderr, flush=True)
        return 1
    print(f"{_stamp()}  remote hub stopped", flush=True)
    return rc


def hub_program(host: str, port: int | None, public_url: str) -> tuple[list[str], str | None]:
    """The LaunchAgent's command line, or an error saying why it can't run.

    It runs Quiver.app's bundled swe when the app is installed, else this
    swe, unless that sits where macOS keeps launchd agents out. ``port``
    is left off the command line when None (the default port).
    """
    bundled = launchd.bundled_swe()
    swe = str(bundled) if bundled else os.path.abspath(sys.argv[0])
    if not bundled:
        if not os.access(swe, os.X_OK):
            return [], (f"cannot find the swe executable ({sys.argv[0]}); "
                        "run this as `swe remote serve --install`")
        folder = launchd.protected_dir(swe)
        if folder:
            return [], (f"{swe} is in ~/{folder}, which macOS blocks a LaunchAgent "
                        "from reading, so the hub would fail at every start. Install "
                        "Quiver.app (app/script/install-app.sh) and run --install "
                        "again: the agent then runs the app's bundled swe.")
    program = [swe, "remote", "serve"]
    program += [f"--host={host}"] if host else ["--tailnet"]
    if port is not None and port != DEFAULT_PORT:
        program.append(f"--port={port}")
    if public_url:
        program.append(f"--public-url={public_url}")
    return program, None


def _cmd_serve_launchd(flags: dict) -> int:
    if sys.platform != "darwin":
        print(c("red", "--install and --uninstall manage a macOS LaunchAgent; "
                       "on Linux run `swe remote serve` under a systemd unit"),
              file=sys.stderr)
        return 1
    if flags.get("--uninstall"):
        try:
            removed = launchd.uninstall()
        except RuntimeError as exc:
            print(c("red", f"launchctl could not stop the hub: {exc}"), file=sys.stderr)
            return 1
        if removed:
            print(f"stopped the hub and removed {launchd.plist_path()}")
        else:
            print(c("dim", "no hub LaunchAgent was installed"))
        return 0
    try:
        host, port = _host_port(flags)
        public_url = _public_url(flags)
    except ValueError as exc:
        print(c("red", str(exc)), file=sys.stderr)
        return 1
    if public_url and not host:
        host = "127.0.0.1"
    program, err = hub_program(host, port if flags.get("--port") else None, public_url)
    if err:
        print(c("red", err), file=sys.stderr)
        return 1
    # A hub started from a shell would keep the port, and launchd's copy
    # would fail to bind on every retry.
    probe_host = host or _tailscale_ip() or "127.0.0.1"
    loaded, _ = launchd.loaded_pid()
    stray = not loaded and _probe(probe_host, port, timeout=1.0) is not None
    log_path = remote_dir_for() / "serve.log"
    try:
        path = launchd.install(program, log_path, os.environ.get("PATH", ""))
    except RuntimeError as exc:
        print(c("red", f"launchctl could not load the hub: {exc}"), file=sys.stderr)
        return 1
    print(f"installed {path}")
    print(f"  runs  {' '.join(program)}")
    print(f"  log   {log_path}")
    print(c("dim", "  starts at login and restarts if it exits; "
                   "run --install again to restart it, --uninstall to stop it"))
    if not stray:
        # A hub that can't start exits and launchd only retries, so wait
        # for it to answer rather than leave the phone offline unsaid.
        for _ in range(_INSTALL_WAIT_S):
            if _probe(probe_host, port, timeout=1.0) is not None:
                print(c("green", f"  hub answering on {probe_host}:{port}"))
                break
            time.sleep(1)
        else:
            print(c("yellow", f"the hub is not answering on {probe_host}:{port} after "
                              f"{_INSTALL_WAIT_S} s; see {log_path}"), file=sys.stderr)
    if stray:
        print(c("yellow",
                f"a hub started outside launchd already answers on {probe_host}:{port}. "
                "Stop it (pgrep -f 'swe remote serve') and launchd starts its own "
                f"within {launchd.THROTTLE_S} s."),
              file=sys.stderr)
    return 0


def _cmd_pair(flags: dict, json_view: bool) -> int:
    try:
        host, port, scheme = _phone_address(flags)
    except ValueError as exc:
        print(c("red", str(exc)), file=error_stream(json_view))
        return 1
    name = flags.get("--name") or ""
    token = pairing.mint_pair_token()
    url = pairing.pair_url(host, port, token, name, scheme)
    payload = pairing.pair_payload(host, port, token, name, scheme)
    if json_view:
        return emit({
            "host": host,
            "port": port,
            "token": token,
            "name": name,
            "url": url,
            "deep_link": payload,
            "expires_in_s": int(pairing.TOKEN_TTL_S),
        })
    print(f"Pair a device ({int(pairing.TOKEN_TTL_S / 60)} min window):\n")
    print(f"  {c('cyan', url)}")
    print(f"  {c('dim', payload)}\n")
    qrencode = shutil.which("qrencode")
    if qrencode:
        try:
            # --app encodes the quiver:// deep link so the Camera app
            # opens the iOS wrapper instead of Safari/the PWA.
            qr = payload if flags.get("--app") else url
            subprocess.run(
                [qrencode, "-t", "ANSIUTF8", qr], check=False, timeout=10
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
    try:
        host, port = _host_port(flags)
    except ValueError as exc:
        print(c("red", str(exc)), file=error_stream(json_view))
        return 1
    saved = _saved_hub(flags)
    public_url = str(saved.get("public_url") or "")
    if saved.get("host") and isinstance(saved.get("port"), int):
        # Probe where the hub listens: behind serve that is loopback,
        # which the tailnet IP default would miss.
        host = saved["host"]
        port = saved["port"]
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
    if not host:
        host = _tailscale_ip() or "127.0.0.1"
    info = _probe(host, port)
    devices = pairing.load_devices()
    machines = catalog_mod.load_machines()
    if json_view:
        return emit({
            "hub": info or {"ok": False},
            "hub_addr": f"{host}:{port}",
            "public_url": public_url or None,
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
        if public_url:
            print(f"  phones: {c('cyan', public_url)}")
    print(f"  devices paired: {len(devices)}")
    if machines:
        print("  machines:")
        for mid, row in machines.items():
            state = c("green", "online") if row.get("online") else c("dim", "offline")
            print(f"    {row.get('name', mid)} {state} ({mid})")
    return 0 if info is not None else 1


def _cmd_devices(flags: dict, json_view: bool) -> int:
    if flags.get("--requests"):
        rows = pairing.list_requests()
        if json_view:
            return emit({"requests": rows})
        if not rows:
            print(c("dim", "no pending pair requests"))
            return 0
        for r in rows:
            print(
                f"  {r['request_id']}  {r.get('name', 'device')}  "
                f"{r.get('node_name') or '?'}  "
                f"{r.get('login') or 'no login'}  "
                f"{r.get('peer_ip', '?')}  expires in {r['expires_in_s']}s"
            )
        return 0
    approve = flags.get("--approve")
    if approve is not None:
        code = "".join(ch for ch in approve if ch.isdigit())
        res = pairing.approve_request(None, code)
        if json_view:
            if res is None:
                emit({"ok": False})
                return 1
            return emit({**res, "ok": True})
        if res is None:
            print(c("red", "no pending request matches that code"),
                  file=error_stream(json_view))
            return 1
        print(
            f"approved {res['name']} ({res.get('node_name', '?')}) "
            f"→ device {res['device_id']}"
        )
        return 0
    deny = flags.get("--deny")
    if deny is not None:
        code = "".join(ch for ch in deny if ch.isdigit())
        ok = pairing.deny_request(None, code)
        if json_view:
            return emit({"ok": ok})
        if not ok:
            print(c("red", "no pending request matches that code"),
                  file=error_stream(json_view))
            return 1
        print("denied")
        return 0
    revoke = flags.get("--revoke")
    if revoke:
        ok = pairing.revoke_device(None, revoke)
        if json_view:
            return emit({"revoked": revoke, "ok": ok})
        if ok:
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


def _cmd_attach(flags: dict, rest: list[str]) -> int:
    """Local attach: mark + relaunch; the hub reads the registry."""
    if not rest:
        print(c("red", "attach needs a session key or id"),
              file=sys.stderr)
        return 1
    want = rest[0]
    mode = flags.get("--mode") or "auto"
    if mode not in supervise.ATTACH_MODES:
        print(c("red", "--mode must be one of "
                + ", ".join(sorted(supervise.ATTACH_MODES))),
              file=sys.stderr)
        return 1
    from quiver.sessions.aggregator import get_all_sessions
    from quiver.sessions.status import session_statuses

    sessions = get_all_sessions(limit=None, use_cache=True)
    matches = [
        s for s in sessions
        if protocol.session_key(s.tool_name, s.session_id, s.path) == want
        or s.session_id == want
    ]
    if not matches:
        # Permit a unique substring match on the session id.
        matches = [s for s in sessions if want in (s.session_id or "")]
    if not matches:
        print(c("red", f"no session matches {want}"), file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(c("red", f"{want} matches {len(matches)} sessions; use the full key"),
              file=sys.stderr)
        return 1
    session = matches[0]
    key = protocol.session_key(session.tool_name, session.session_id, session.path)
    status = session_statuses([session])[0]
    transcript = tail.resolve_jsonl_path(session)
    last_append = None
    try:
        last_append = transcript.stat().st_mtime if transcript else None
    except OSError:
        pass
    ok, note = supervise.attach(
        None, key, session,
        last_append_at=last_append, status=status, mode=mode,
    )
    if not ok:
        print(c("red", note), file=sys.stderr)
        return 1
    print(f"{c('green', 'attached')} {key} — {note}")
    return 0


def _cmd_agent(flags: dict) -> int:
    """Satellite: pair on first run (``--token``), then dial the hub."""
    hub = (flags.get("--hub") or "").rstrip("/")
    token = flags.get("--token") or ""
    conf = agent_mod.load_agent()
    if token:
        if not hub:
            print(c("red", "--token needs --hub=http://<host>:<port>"),
                  file=sys.stderr)
            return 1
        conf = agent_mod.pair_agent(
            hub, token, flags.get("--name") or ""
        )
        if conf is None:
            print(c("red", "pairing failed — check the token and hub"),
                  file=sys.stderr)
            return 1
        print(c("green", f"paired with {conf['hub']}"))
    elif hub:
        conf = dict(conf or {}, hub=hub)
    if not conf or not conf.get("credential") or not conf.get("hub"):
        print(c("red",
                "no hub credential — run: "
                "swe remote agent --hub=http://<host>:<port> --token=T"),
              file=sys.stderr)
        return 1
    machine = catalog_mod.local_machine()
    print(
        f"remote agent {c('cyan', conf['hub'])} "
        f"as {c('bold', machine['name'])} ({machine['id']})"
    )
    return agent_mod.serve(conf["hub"], conf["credential"])


def _cmd_notify(flags: dict, json_view: bool) -> int:
    """Configure the ntfy fan-out the running hub reads at startup."""
    updates = {}
    if "--ntfy" in flags:
        updates["url"] = flags["--ntfy"]
    if "--topic" in flags:
        updates["topic"] = flags["--topic"]
    if "--ntfy-token" in flags:
        updates["ntfy_token"] = flags["--ntfy-token"]
    if "--on" in flags:
        updates["enabled"] = True
    if "--off" in flags:
        updates["enabled"] = False
    if "--ntfy" in flags or "--topic" in flags:
        updates.setdefault("enabled", True)  # configuring implies on
    cfg = notify.configure(None, **updates) if updates else \
        notify.load_config()
    if json_view:
        redacted = dict(cfg)
        for k in ("ntfy_token", "action_credential"):
            if redacted.get(k):
                redacted[k] = "***"
        return emit({"notify": redacted})
    if not cfg.get("url") or not cfg.get("topic"):
        print(c("dim", "ntfy not configured — swe remote notify "
                      "--ntfy=<url> --topic=<t>"))
        return 0
    state = c("green", "on") if cfg.get("enabled") else c("dim", "off")
    print(f"ntfy {state}  {cfg['url']}/{cfg['topic']}")
    if not cfg.get("enabled"):
        print(c("dim", "  enable: swe remote notify --on"))
    print(c("dim", "  changes apply on the next `swe remote serve`"))
    return 0


_SETUP_MARK = {
    setup_mod.OK: ("green", "✓"),
    setup_mod.TODO: ("cyan", "○"),
    setup_mod.NEEDS_YOU: ("yellow", "!"),
    setup_mod.FAILED: ("red", "✗"),
    setup_mod.BLOCKED: ("dim", "·"),
}


def _cmd_setup(flags: dict, json_view: bool) -> int:
    """``swe remote setup``: every step from nothing to a paired phone."""
    try:
        port = int(flags["--port"]) if flags.get("--port") else None
    except ValueError:
        port = 0
    if port is not None and not 1 <= port <= 65535:
        # Checked before anything changes: --apply would stop a working
        # hub and install one that can't start.
        print(c("red", "--port must be a number from 1 to 65535"), file=error_stream(json_view))
        return 1
    if flags.get("--apply"):
        say = (lambda _m: None) if json_view else (lambda m: print(c("dim", f"… {m}"), flush=True))
        report = setup_mod.apply(port=port, log=say, fresh_cert=bool(flags.get("--recheck")))
    else:
        report = setup_mod.evaluate(setup_mod.gather(
            port=port, fetch_cert=bool(flags.get("--recheck")),
            fresh_cert=bool(flags.get("--recheck"))))
    if json_view:
        return emit(report)
    for step in report["steps"]:
        color, mark = _SETUP_MARK.get(step["state"], ("dim", "?"))
        line = f"  {c(color, mark)} {step['title']}"
        if step["detail"]:
            line += c("dim", f"  {step['detail']}")
        print(line)
        action = step.get("action") or {}
        if step["state"] in {setup_mod.NEEDS_YOU, setup_mod.FAILED}:
            if action.get("url"):
                print(c("dim", f"      open {action['url']}"))
            elif action.get("command"):
                print(c("dim", f"      run  {action['command']}"))
            elif action.get("kind") == "launch":
                print(c("dim", f"      open the {action.get('app')} app"))
            elif action.get("kind") == "pair":
                print(c("dim", "      run  swe remote pair --app"))
    if report["ready"]:
        print(c("green", f"\nReady: phones reach this hub at {report['url']}"))
        return 0
    if any(s["state"] == setup_mod.TODO for s in report["steps"]):
        print(c("dim", "\nRun `swe remote setup --apply` to do the steps marked ○."))
    return 0 if not flags.get("--apply") else 1


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
        return _cmd_pair(flags, json_view)
    if sub == "status":
        if rest:
            print(c("red", f"unexpected args: {' '.join(rest)}"), file=sys.stderr)
            return 1
        return _cmd_status(flags, json_view)
    if sub == "devices":
        return _cmd_devices(flags, json_view)
    if sub == "attach":
        return _cmd_attach(flags, rest)
    if sub == "agent":
        if rest:
            print(c("red", f"unexpected args: {' '.join(rest)}"),
                  file=sys.stderr)
            return 1
        return _cmd_agent(flags)
    if sub == "setup":
        if rest:
            print(c("red", f"unexpected args: {' '.join(rest)}"), file=sys.stderr)
            return 1
        return _cmd_setup(flags, json_view)
    if sub == "notify":
        if rest:
            print(c("red", f"unexpected args: {' '.join(rest)}"), file=sys.stderr)
            return 1
        return _cmd_notify(flags, json_view)
    return 0
