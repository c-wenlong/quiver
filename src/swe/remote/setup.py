"""``swe remote setup``: get a machine from nothing to a paired phone.

Remote control needs a chain of things before a phone can reach the hub:
Tailscale installed and signed in, MagicDNS and HTTPS certificates on in
the tailnet, a certificate this machine can actually fetch, the hub
running in the background on loopback, ``tailscale serve`` in front of
it, and a paired device. Each link used to be a terminal step someone
had to know about. This module checks them in order and says, for each,
whether it is done, something SWE can do itself (``--apply``), or
something only a person can do, with the page to open.

``gather`` does the I/O and ``evaluate`` turns what it found into the
report, so tests drive ``evaluate`` with made-up facts. ``apply`` loops
gather, evaluate, act until nothing is left that SWE can do.

The certificate is checked before the hub moves behind ``tailscale
serve``: a hub moved there without one is unreachable from every device
(the TLS handshake just hangs), while the hub it replaced, bound to the
tailnet IP, was working.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from swe.paths import atomic_write_text, read_json_dict, remote_dir_for
from swe.remote import launchd, pairing
from swe.remote.tsid import tailscale_exe

DEFAULT_PORT = 8477

# States a step can be in.
OK = "ok"
TODO = "todo"            # SWE does it with --apply
NEEDS_YOU = "needs_you"  # a person must act; the step names where
FAILED = "failed"        # checked and broken, with the reason
BLOCKED = "blocked"      # waits for an earlier step

ADMIN_DNS_URL = "https://login.tailscale.com/admin/dns"
DOWNLOAD_URL = {
    "darwin": "https://tailscale.com/download/mac",
    "linux": "https://tailscale.com/download/linux",
}

# A certificate fetch that worked is good for a day (the cert lasts 90);
# one that failed is retried after a minute, so a fixed DNS shows up fast
# without every status poll waiting out a 45 s timeout.
CERT_OK_TTL_S = 24 * 3600
CERT_FAIL_TTL_S = 60
CERT_TIMEOUT_S = 90

# How long --apply waits for a freshly installed hub to answer.
HUB_WAIT_S = 20

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class Facts:
    """What ``gather`` found; ``evaluate`` reads nothing else."""

    platform: str = field(default_factory=lambda: sys.platform)
    port: int = DEFAULT_PORT
    exe: str | None = None
    version: str = ""
    # `tailscale status --json`, or None when tailscaled didn't answer.
    status: dict | None = None
    status_error: str = ""
    # `tailscale serve status --json`.
    serve: dict = field(default_factory=dict)
    # (ok, error) of a certificate fetch, or None when not checked.
    cert: tuple[bool, str] | None = None
    # The hub's background service (LaunchAgent on a Mac, systemd user
    # unit on Linux): its command line, and whether it runs.
    agent_args: list[str] | None = None
    agent_running: bool = False
    # Linux: a systemd user manager to install into, and whether user
    # services outlive logout (None when loginctl can't say).
    service_available: bool = False
    linger: bool | None = None
    hub_config: dict = field(default_factory=dict)
    loopback_ok: bool = False
    https_ok: bool = False
    https_error: str = ""
    # When each paired device that isn't revoked was paired.
    device_times: list[float] = field(default_factory=list)
    # When --apply last moved the hub to a new address; devices paired
    # before it can't reach the hub any more.
    moved_at: float | None = None
    satellites: int = 0

    @property
    def devices(self) -> int:
        """Devices that can still reach the hub."""
        if self.moved_at is None:
            return len(self.device_times)
        return sum(1 for t in self.device_times if t >= self.moved_at)

    @property
    def dns_name(self) -> str:
        """This node's MagicDNS name, without the trailing dot."""
        me = (self.status or {}).get("Self") or {}
        return str(me.get("DNSName") or "").rstrip(".")

    @property
    def public_url(self) -> str:
        return f"https://{self.dns_name}:{self.port}" if self.dns_name else ""


def _step(sid: str, title: str, state: str, detail: str = "",
          action: dict | None = None) -> dict:
    return {"id": sid, "title": title, "state": state, "detail": detail,
            "action": action}


def _open(url: str, label: str) -> dict | None:
    """An ``open`` action for a web address only: the page puts the URL in
    an href, and the ones a status read reports are someone else's text."""
    if not url.lower().startswith(("https://", "http://")):
        return None
    return {"kind": "open", "label": label, "url": url}


def _apply(label: str) -> dict:
    return {"kind": "apply", "label": label}


def evaluate(f: Facts) -> dict:
    """The report: every step in order, each with its state and action."""
    steps: list[dict] = []
    darwin = f.platform == "darwin"

    def blocked_by(title: str) -> str:
        return f"Waits for: {title}"

    # 1. Tailscale installed.
    t_installed = "Tailscale installed"
    if f.exe:
        steps.append(_step("tailscale.installed", t_installed, OK,
                           f"Tailscale {f.version}".strip()))
    else:
        url = DOWNLOAD_URL.get(f.platform, DOWNLOAD_URL["linux"])
        steps.append(_step(
            "tailscale.installed", t_installed, NEEDS_YOU,
            "Your phone reaches this machine over Tailscale, a private "
            "network between your own devices. Install it, then come back.",
            _open(url, "Download Tailscale")))

    # 2. Signed in and connected.
    t_signed = "Signed in to Tailscale"
    backend = str((f.status or {}).get("BackendState") or "")
    if not f.exe:
        steps.append(_step("tailscale.signed_in", t_signed, BLOCKED, blocked_by(t_installed)))
    elif backend == "Running":
        tailnet = ((f.status or {}).get("CurrentTailnet") or {}).get("Name") or ""
        detail = f"{tailnet} as {f.dns_name.split('.')[0]}" if tailnet and f.dns_name else ""
        steps.append(_step("tailscale.signed_in", t_signed, OK, detail))
    elif backend == "NeedsLogin" and (f.status or {}).get("AuthURL"):
        steps.append(_step("tailscale.signed_in", t_signed, NEEDS_YOU,
                           "Sign in to your tailnet in the browser.",
                           _open(str(f.status["AuthURL"]), "Sign in")))
    elif darwin:
        detail = ("Tailscale is off. Turn it on from its menu bar icon."
                  if backend in {"Stopped", "NeedsLogin"} else
                  "Tailscale isn't running. Open it and sign in.")
        steps.append(_step("tailscale.signed_in", t_signed, NEEDS_YOU, detail,
                           {"kind": "launch", "label": "Open Tailscale", "app": "Tailscale"}))
    else:
        steps.append(_step(
            "tailscale.signed_in", t_signed, NEEDS_YOU,
            "Tailscale isn't connected. Run `sudo tailscale up` and sign in.",
            {"kind": "command", "label": "Copy command", "command": "sudo tailscale up"}))
    signed_in = steps[-1]["state"] == OK

    # 3. MagicDNS, so the machine has a name a certificate can be for.
    t_magic = "MagicDNS on"
    if not signed_in:
        steps.append(_step("tailscale.magicdns", t_magic, BLOCKED, blocked_by(t_signed)))
    elif ((f.status or {}).get("CurrentTailnet") or {}).get("MagicDNSEnabled") and f.dns_name:
        suffix = (f.status or {}).get("MagicDNSSuffix") or ""
        steps.append(_step("tailscale.magicdns", t_magic, OK,
                           f"Names end in {suffix}" if suffix else ""))
    else:
        steps.append(_step(
            "tailscale.magicdns", t_magic, NEEDS_YOU,
            "Gives this machine a name like mac.your-tailnet.ts.net. A tailnet "
            "admin turns it on under DNS in the Tailscale admin console.",
            _open(ADMIN_DNS_URL, "Open admin console")))
    magic = steps[-1]["state"] == OK

    # 4. HTTPS certificates enabled for the tailnet.
    t_https = "HTTPS certificates on"
    cert_domains = (f.status or {}).get("CertDomains") or []
    if not magic:
        steps.append(_step("tailscale.https", t_https, BLOCKED, blocked_by(t_magic)))
    elif f.dns_name in cert_domains:
        steps.append(_step("tailscale.https", t_https, OK))
    else:
        steps.append(_step(
            "tailscale.https", t_https, NEEDS_YOU,
            "Lets the hub use https, which browsers need for copy, install "
            "and notifications. A tailnet admin clicks Enable HTTPS under "
            "DNS in the admin console.",
            _open(ADMIN_DNS_URL, "Open admin console")))
    https_on = steps[-1]["state"] == OK

    # 5. A certificate this machine can fetch.
    t_cert = f"Certificate for {f.dns_name.split('.')[0]}" if f.dns_name else "Certificate"
    if not https_on:
        steps.append(_step("tailscale.cert", t_cert, BLOCKED, blocked_by(t_https)))
    elif f.https_ok or (f.cert is not None and f.cert[0]):
        steps.append(_step("tailscale.cert", t_cert, OK, "From Let's Encrypt, renewed by Tailscale"))
    elif f.cert is None:
        steps.append(_step("tailscale.cert", t_cert, TODO, "Not fetched yet",
                           _apply("Fetch certificate")))
    else:
        steps.append(_step("tailscale.cert", t_cert, FAILED, cert_advice(f.cert[1]),
                           _open(ADMIN_DNS_URL, "Open admin console")))
    cert_ok = steps[-1]["state"] == OK

    # 6. The hub, in the background, on loopback with the public URL.
    t_hub = "Hub running in the background"
    want = f.public_url
    if not cert_ok:
        steps.append(_step("hub.service", t_hub, BLOCKED, blocked_by(t_cert)))
    elif (darwin or f.service_available) and f.loopback_ok and not f.agent_running:
        # Something else answers on the port, most likely a hub started
        # from a shell: the background one would fail to bind.
        steps.append(_step(
            "hub.service", t_hub, NEEDS_YOU,
            f"A hub started outside the background service answers on 127.0.0.1:{f.port}, "
            "so the background one can't start. Stop it, then check again.",
            {"kind": "command", "label": "Copy command", "command": "pkill -f 'swe remote serve'"}))
    elif darwin or f.service_available:
        args = f.agent_args or []
        right = f"--public-url={want}" in args and "--host=127.0.0.1" in args
        if right and f.agent_running and f.loopback_ok:
            if darwin:
                detail = "Starts at login and restarts if it stops"
            elif f.linger is False:
                detail = ("Runs only while you're logged in. To keep it after "
                          "logout, run `sudo loginctl enable-linger $USER`.")
            elif f.linger is None:
                detail = ("Restarts if it stops. loginctl couldn't say whether it outlives "
                          "logout; to be sure, run `sudo loginctl enable-linger $USER`.")
            else:
                detail = "Starts at boot and restarts if it stops"
            steps.append(_step("hub.service", t_hub, OK, detail))
        elif right and f.agent_running:
            steps.append(_step(
                "hub.service", t_hub, FAILED,
                "The hub keeps stopping. Its log is ~/.swe/data/remote/serve.log.",
                _apply("Restart hub")))
        else:
            detail = move_note(f) if args else "Not installed yet"
            steps.append(_step("hub.service", t_hub, TODO, detail,
                               _apply("Move hub" if args else "Start hub")))
    elif f.loopback_ok and f.hub_config.get("public_url") == want:
        steps.append(_step("hub.service", t_hub, OK))
    else:
        port_flag = f"--port={f.port} " if f.port != DEFAULT_PORT else ""
        cmd = f"swe remote serve {port_flag}--public-url={want}"
        steps.append(_step(
            "hub.service", t_hub, NEEDS_YOU,
            f"No systemd user manager here. Run `{cmd}` under this machine's "
            "init system so it starts at boot.",
            {"kind": "command", "label": "Copy command", "command": cmd}))
    hub_ok = steps[-1]["state"] == OK

    # 7. tailscale serve in front, and https reaching the hub.
    t_reach = f"Reachable at {want}" if want else "Reachable over https"
    if not hub_ok:
        steps.append(_step("hub.reachable", t_reach, BLOCKED, blocked_by(t_hub)))
    elif f.https_ok:
        steps.append(_step("hub.reachable", t_reach, OK, "Through tailscale serve"))
    elif not serve_configured(f.serve, f.dns_name, f.port):
        steps.append(_step("hub.reachable", t_reach, TODO, "tailscale serve isn't set up yet",
                           _apply("Set up tailscale serve")))
    else:
        steps.append(_step("hub.reachable", t_reach, FAILED,
                           f"tailscale serve is set up, but https didn't answer: {f.https_error}"
                           if f.https_error else "tailscale serve is set up, but https didn't answer"))
    reach_ok = steps[-1]["state"] == OK

    # 8. A device paired.
    t_paired = "A phone paired"
    stranded = len(f.device_times) - f.devices
    if f.devices:
        steps.append(_step("phone.paired", t_paired, OK,
                           "1 device" if f.devices == 1 else f"{f.devices} devices"))
    elif not reach_ok:
        steps.append(_step("phone.paired", t_paired, BLOCKED, blocked_by(t_reach)))
    elif stranded:
        steps.append(_step(
            "phone.paired", t_paired, NEEDS_YOU,
            f"The hub moved to {want}. Pair your devices again there: "
            f"{'the one' if stranded == 1 else f'the {stranded}'} paired at the old "
            "address can't reach it.", {"kind": "pair", "label": "Pair a phone"}))
    else:
        steps.append(_step("phone.paired", t_paired, NEEDS_YOU,
                           "Scan a code with the Quiver app, or open a link in a browser.",
                           {"kind": "pair", "label": "Pair a phone"}))

    return {
        "ready": all(s["state"] == OK for s in steps),
        "platform": f.platform,
        "url": want,
        "steps": steps,
    }


def move_note(f: Facts) -> str:
    """What moving a hub that already runs elsewhere does to its clients."""
    note = f"Runs on its tailnet address today. Moving it to {f.public_url} changes its address"
    who = []
    if f.device_times:
        n = len(f.device_times)
        who.append("the paired device" if n == 1 else f"the {n} paired devices")
    if f.satellites:
        who.append("the satellite" if f.satellites == 1 else f"the {f.satellites} satellites")
    if not who:
        return note + "."
    return f"{note}, so {' and '.join(who)} must be paired or pointed at it again."


def cert_advice(error: str) -> str:
    """What to do about a failed certificate fetch, in plain words."""
    low = error.lower()
    if "lookup" in low and ("timeout" in low or "no such host" in low or "server misbehaving" in low):
        return ("Tailscale can't look up Let's Encrypt, so it can't get a "
                "certificate. Add a DNS server it can reach (for example "
                "1.1.1.1) to this machine's network settings, or turn on "
                "Override DNS servers in the admin console.")
    if "not enabled" in low or "https" in low and "disabled" in low:
        return "HTTPS certificates are off for this tailnet. Turn them on under DNS in the admin console."
    first = error.strip().splitlines()[-1] if error.strip() else "unknown error"
    return f"Tailscale couldn't get a certificate: {first}"


def serve_configured(serve: dict, dns_name: str, port: int) -> bool:
    """Whether ``tailscale serve`` proxies https on ``port`` to the hub."""
    tcp = (serve.get("TCP") or {}).get(str(port)) or {}
    if not tcp.get("HTTPS"):
        return False
    web = (serve.get("Web") or {}).get(f"{dns_name}:{port}") or {}
    proxy = str(((web.get("Handlers") or {}).get("/") or {}).get("Proxy") or "")
    return proxy.rstrip("/") in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


# ── gathering ────────────────────────────────────────────────────


def _run_json(run: Runner, argv: list[str], timeout: float = 10) -> tuple[dict | None, str]:
    try:
        res = run(argv, capture_output=True, text=True, timeout=timeout,
                  stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if res.returncode != 0:
        return None, (res.stderr or res.stdout or "").strip()
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError:
        return None, "unreadable JSON"
    return (data if isinstance(data, dict) else None), ""


def _get(url: str, timeout: float) -> tuple[bool, str]:
    """GET ``url``. https retries against the OS CA bundle when Python's
    own store has none (python.org builds on macOS), as satellites do."""
    from swe.remote.agent import _urlopen

    try:
        with _urlopen(urllib.request.Request(url), timeout) as resp:
            return 200 <= resp.status < 300, ""
    except Exception as exc:  # noqa: BLE001 - any failure is "not reachable"
        return False, str(getattr(exc, "reason", exc))


def _agent(home: Path | None, run: Runner) -> tuple[list[str] | None, bool]:
    return launchd.status(home, run)


def _state_file(home: Path | None) -> Path:
    return remote_dir_for(home) / "setup-state.json"


def load_state(home: Path | None = None) -> dict:
    return read_json_dict(_state_file(home))


def record_move(home: Path | None, url: str, now: float | None = None) -> None:
    """Remember that the hub moved to ``url``, so devices paired before
    it are no longer counted as able to reach it."""
    try:
        path = _state_file(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"moved_at": time.time() if now is None else now, "url": url}))
    except OSError:
        pass


def _cert_cache(home: Path | None) -> Path:
    return remote_dir_for(home) / "setup-cert.json"


def check_cert(exe: str, name: str, home: Path | None = None, run: Runner = subprocess.run,
               now: float | None = None, fresh: bool = False) -> tuple[bool, str]:
    """Fetch this node's certificate once, to see that it can be fetched.

    The files go to a private temp folder that is removed at once:
    tailscaled keeps its own copy for ``tailscale serve``. Results are
    cached (see ``CERT_OK_TTL_S``) so a status poll stays quick.
    """
    now = time.time() if now is None else now
    cache = _cert_cache(home)
    if not fresh:
        try:
            got = json.loads(cache.read_text(encoding="utf-8"))
            ttl = CERT_OK_TTL_S if got.get("ok") else CERT_FAIL_TTL_S
            if got.get("name") == name and now - float(got.get("at", 0)) < ttl:
                return bool(got.get("ok")), str(got.get("error") or "")
        except (OSError, ValueError, TypeError):
            pass
    with tempfile.TemporaryDirectory() as tmp:
        try:
            res = run([exe, "cert", f"--cert-file={tmp}/c.crt", f"--key-file={tmp}/c.key", name],
                      capture_output=True, text=True, timeout=CERT_TIMEOUT_S,
                      stdin=subprocess.DEVNULL)
            ok, error = res.returncode == 0, "" if res.returncode == 0 else (res.stderr or res.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok, error = False, str(exc)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cache, json.dumps({"name": name, "ok": ok, "error": error, "at": now}))
    except OSError:
        pass
    return ok, error


def gather(home: Path | None = None, port: int | None = None, run: Runner = subprocess.run,
           get: Callable[[str, float], tuple[bool, str]] = _get,
           fetch_cert: bool = False, fresh_cert: bool = False) -> Facts:
    """Look at this machine. ``fetch_cert`` also checks the certificate
    when https doesn't already prove it works."""
    from swe.remote.hub import load_hub_config

    cfg = load_hub_config(home)
    f = Facts(port=int(port or cfg.get("port") or DEFAULT_PORT), hub_config=cfg)
    f.exe = tailscale_exe()
    if f.exe:
        f.status, f.status_error = _run_json(run, [f.exe, "status", "--json"])
        f.version = str((f.status or {}).get("Version") or "").split("-")[0]
        if f.dns_name:
            f.serve = _run_json(run, [f.exe, "serve", "status", "--json"])[0] or {}
    if f.platform == "darwin":
        f.agent_args, f.agent_running = _agent(home, run)
    elif f.platform.startswith("linux"):
        from swe.remote import systemd

        f.service_available = systemd.available(run)
        if f.service_available:
            f.agent_args, f.agent_running = systemd.status(home, run)
            f.linger = systemd.lingering(run)
    f.loopback_ok = get(f"http://127.0.0.1:{f.port}/v1/status", 2)[0]
    if f.public_url:
        f.https_ok, f.https_error = get(f"{f.public_url}/v1/status", 5)
    certs_on = f.dns_name and f.dns_name in ((f.status or {}).get("CertDomains") or [])
    if fetch_cert and certs_on and not f.https_ok and f.exe:
        f.cert = check_cert(f.exe, f.dns_name, home, run, fresh=fresh_cert)
    elif certs_on and not f.https_ok and f.exe:
        # A cached answer still shows; only --apply or a recheck fetches.
        f.cert = _cached_cert(home, f.dns_name)
    f.device_times = [float(row.get("created_at") or 0) for row in pairing.load_devices(home).values()
                      if not row.get("revoked_at")]
    moved = load_state(home)
    if moved.get("url") == f.public_url and moved.get("moved_at"):
        f.moved_at = float(moved["moved_at"])
    from swe.remote.catalog import load_machine_id, load_machines

    # The hub registers its own row in the same table; only the others
    # are satellites.
    own = load_machine_id(home)
    f.satellites = sum(mid != own for mid in load_machines(home))
    return f


def _cached_cert(home: Path | None, name: str) -> tuple[bool, str] | None:
    try:
        got = json.loads(_cert_cache(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if got.get("name") != name:
        return None
    ttl = CERT_OK_TTL_S if got.get("ok") else CERT_FAIL_TTL_S
    if time.time() - float(got.get("at", 0)) >= ttl:
        return None
    return bool(got.get("ok")), str(got.get("error") or "")


# ── doing ────────────────────────────────────────────────────────


@dataclass
class Actions:
    """What ``apply`` calls to change the machine; tests swap them."""

    install_hub: Callable[[str, int], str | None]
    start_serve: Callable[[str, int], str | None]
    wait_hub: Callable[[int], bool]


def apply(home: Path | None = None, port: int | None = None, actions: Actions | None = None,
          gather_fn: Callable[..., Facts] | None = None, log: Callable[[str], None] = lambda _m: None,
          max_steps: int = 8, fresh_cert: bool = False,
          record: Callable[[str], None] | None = None) -> dict:
    """Do every step SWE can do, in order, and return the final report.

    Stops at the first step that needs a person or failed: later steps
    depend on it. Each action is followed by a fresh look, so a step
    that didn't take is reported as it is, never assumed done.
    """
    gather_fn = gather_fn or gather
    actions = actions or default_actions(home)
    record = record or (lambda url: record_move(home, url))
    report: dict = {}
    tried: set[str] = set()
    for i in range(max_steps):
        facts = gather_fn(home=home, port=port, fetch_cert=True, fresh_cert=fresh_cert and i == 0)
        report = evaluate(facts)
        nxt = next((s for s in report["steps"] if s["state"] != OK), None)
        if nxt is None:
            return report
        # A hub that keeps stopping is restarted once; anything else that
        # failed or needs a person ends the run.
        restart = nxt["state"] == FAILED and nxt["id"] == "hub.service" and nxt["id"] not in tried
        if nxt["state"] != TODO and not restart:
            return report
        sid = nxt["id"]
        tried.add(sid)
        if sid == "tailscale.cert":
            # gather fetched it; a TODO here means the fetch didn't run.
            return report
        if sid == "hub.service":
            moving = bool(facts.agent_args) and not restart
            log(f"{'moving' if moving else 'starting'} the hub to {facts.public_url}")
            err = actions.install_hub(facts.public_url, facts.port)
            if err is None and not actions.wait_hub(facts.port):
                err = f"the hub didn't answer on 127.0.0.1:{facts.port}"
            if err is None and moving:
                record(facts.public_url)
        elif sid == "hub.reachable":
            log(f"setting up tailscale serve on :{facts.port}")
            err = actions.start_serve(facts.exe or "tailscale", facts.port)
        else:
            return report
        if err:
            nxt["state"] = FAILED
            nxt["detail"] = err
            nxt["action"] = serve_fix(err) if sid == "hub.reachable" else nxt.get("action")
            report["ready"] = False
            return report
    return report


def serve_fix(err: str) -> dict | None:
    """Linux keeps ``tailscale serve`` for root unless an operator is set."""
    if "access denied" in err.lower() or "operator" in err.lower():
        cmd = "sudo tailscale set --operator=$USER"
        return {"kind": "command", "label": "Copy command", "command": cmd}
    return None


def default_actions(home: Path | None = None, run: Runner = subprocess.run) -> Actions:
    def install_hub(public_url: str, port: int) -> str | None:
        from swe.remote.commands import hub_program, install_service

        program, err = hub_program("127.0.0.1", port, public_url)
        if err:
            return err
        try:
            install_service(program, remote_dir_for(home) / "serve.log", home=home, run=run)
        except RuntimeError as exc:
            return str(exc)
        return None

    def start_serve(exe: str, port: int) -> str | None:
        try:
            res = run([exe, "serve", "--bg", f"--https={port}", f"http://127.0.0.1:{port}"],
                      capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return str(exc)
        if res.returncode != 0:
            return (res.stderr or res.stdout or "").strip() or f"tailscale serve exited {res.returncode}"
        return None

    def wait_hub(port: int) -> bool:
        for _ in range(HUB_WAIT_S):
            if _get(f"http://127.0.0.1:{port}/v1/status", 1)[0]:
                return True
            time.sleep(1)
        return False

    return Actions(install_hub=install_hub, start_serve=start_serve, wait_hub=wait_hub)
