"""The Compute page's data on a Linux hub: what the starred harnesses run,
what it costs the machine, and a short history for the trend card.

A port of the Mac app's ``QuiverCore/Compute`` (ProcessSampler,
ProcessAttribution, CPUMeter, ComputeHistory) onto Linux's own sources,
stdlib only:

- processes: ``/proc/<pid>/{stat,status,cmdline,exe,cwd,cgroup}``, the
  hub user's own processes only, as the Mac reads only the user's;
- the machine: ``/proc/stat`` (CPU % between two samples),
  ``/proc/meminfo``, ``/proc/loadavg``;
- temperatures and fans: ``/sys/class/hwmon``, then
  ``/sys/class/thermal`` for what hwmon left out;
- the GPU: ``nvidia-smi --query-gpu`` for each device and
  ``nvidia-smi pmon`` for each process's share, when it is installed,
  with a timeout.

Memory per process is private memory (``RssAnon`` plus ``VmSwap``), the
nearest Linux figure to the Mac's physical footprint: shared library
pages are not counted once per process.

Sampling happens on request, at most every ``MIN_SAMPLE`` seconds for
every viewer together. The first request also starts one daemon thread
that samples every ``TICK`` seconds for the life of the hub, because the
Mac trend card shows history from before the page was opened — and auto
clean, which quits orphan trees older than the configured age, starts
the ticker at hub boot while it is enabled, since asking after a restart
would mean the orphans it was set to stop get to keep running.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

# ProcessAttribution.scriptRunners: a harness CLI can be a script of these.
SCRIPT_RUNNERS = {"node", "bun", "deno", "python", "python3"}
# OrphanTree.runners: what a left-behind dev or MCP server runs under.
ORPHAN_RUNNERS = {
    "node", "npm", "npx", "pnpm", "yarn", "bun", "deno",
    "python", "python3", "uv", "uvx", "tsx", "vite",
}
# CPUMeter: at or above 80% for two minutes counts as busy.
BUSY_PERCENT = 80.0
BUSY_SPAN = 120.0

# Every viewer shares one sample at most this often; the page polls every 3 s.
MIN_SAMPLE = 2.0
# The background sampler's cadence: the Mac's default trend interval.
TICK = 10.0
# TrendInterval's choices, in seconds, and ComputeHistory.window.
INTERVALS = (3, 10, 30, 60, 300)
DEFAULT_INTERVAL = 10
WINDOW = 120
# ComputeLimits: the orphan-age field's default and bounds — a minute to
# a year — shared by the sheet and the auto-clean threshold.
DEFAULT_ORPHAN_AGE = 24 * 3600
ORPHAN_AGE_RANGE = (60, 365 * 86400)
# ComputeModel.autoCleanGrace: a force-cleaned orphan gets this long to
# exit after SIGTERM before the SIGKILL follows.
AUTOCLEAN_GRACE = 10.0
# Two rings: every 3 s for the 3 s view (six minutes), every 10 s for
# the rest (ten hours, the 5 minute view's window).
FINE_STEP, FINE_KEEP = 3.0, WINDOW * 3 + 30
COARSE_STEP, COARSE_KEEP = 10.0, WINDOW * 300 + 300

NVIDIA_TIMEOUT = 3.0

# Sensor chip (hwmon ``name``) or thermal zone ``type`` to the Mac's
# ThermalComponent ids. Anything else (Wi-Fi, ACPI board zones) is left out.
_COMPONENT_BY_SENSOR = {
    "k10temp": "cpu", "coretemp": "cpu", "zenpower": "cpu", "cpu_thermal": "cpu",
    "cpu-thermal": "cpu", "x86_pkg_temp": "cpu", "soc_thermal": "cpu", "cpu": "cpu",
    "amdgpu": "gpu", "radeon": "gpu", "nouveau": "gpu", "gpu_thermal": "gpu", "gpu-thermal": "gpu",
    "jc42": "memory", "spd5118": "memory",
    "nvme": "ssd", "drivetemp": "ssd",
    "battery": "battery",
}
# FanSummary.upMargin: 15% over its minimum counts as a fan running up.
FAN_UP_MARGIN = 0.15


def _shown(words: list[str]) -> str:
    """A process's arguments as the browser may see them: an MCP server
    or helper started with ``--token=…`` or a key in its URL would put
    that secret on every paired device's screen, so the MCP page's
    redaction runs over them first."""
    from quiver.webapp.pages import mcp
    return " ".join(mcp.redact_args(list(words))) if words else ""


def supported() -> bool:
    """Whether this hub has the Linux sources the page reads."""
    return platform.system() == "Linux" and Path("/proc/stat").is_file()


# ---------------------------------------------------------------- processes


@dataclass
class Proc:
    """One running process, like the Mac's ProcessSample."""

    pid: int
    ppid: int
    path: str = ""
    arguments: list[str] = field(default_factory=list)
    started_at: float = 0.0
    threads: int = 0
    memory_bytes: int = 0
    cpu_ns: int = 0
    cwd: str | None = None
    cgroup: str = ""

    @property
    def name(self) -> str:
        if self.path:
            return os.path.basename(self.path)
        if self.arguments and self.arguments[0]:
            return os.path.basename(self.arguments[0])
        return f"pid {self.pid}"

    @property
    def runner_name(self) -> str:
        lower = self.name.lower()
        return "python" if lower.startswith("python") else lower

    @property
    def command_line(self) -> str:
        return " ".join(self.arguments) if self.arguments else self.path


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _readlink(path: Path) -> str | None:
    try:
        return os.readlink(path)
    except OSError:
        return None


def boot_time(proc: Path) -> float:
    """``btime`` from /proc/stat: when the machine booted, epoch seconds."""
    raw = _read(proc / "stat") or b""
    for line in raw.decode(errors="replace").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    return 0.0


def ticks() -> int:
    """``SC_CLK_TCK`` where the OS reports it, 100 elsewhere (the common
    Linux value, and what the tests use)."""
    return os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _status_kb(text: str, key: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def read_process(proc: Path, pid: int, boot: float, tick: int) -> Proc | None:
    """One process, or ``None`` when it exited mid-read, is a zombie or a
    kernel thread. Anything else unreadable costs that field only."""
    base = proc / str(pid)
    raw = _read(base / "stat")
    if not raw:
        return None
    text = raw.decode(errors="replace")
    # The name in parentheses may hold spaces and parentheses itself.
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) < 20 or fields[0] == "Z":
        return None
    try:
        ppid = int(fields[1])
        cpu_ticks = int(fields[11]) + int(fields[12])
        threads = int(fields[17])
        start_ticks = int(fields[19])
    except ValueError:
        return None
    raw_args = _read(base / "cmdline") or b""
    arguments = [a.decode(errors="replace") for a in raw_args.split(b"\0")]
    while arguments and arguments[-1] == "":
        arguments.pop()
    arguments = arguments[:256]
    if not arguments and ppid == 2:
        return None  # a kernel thread
    status = (_read(base / "status") or b"").decode(errors="replace")
    anon = _status_kb(status, "RssAnon")
    if anon is None:
        anon = _status_kb(status, "VmRSS") or 0
    memory = (anon + (_status_kb(status, "VmSwap") or 0)) * 1024
    exe = _readlink(base / "exe") or ""
    if exe.endswith(" (deleted)"):
        exe = exe[: -len(" (deleted)")]
    # ProcessSampler.resolvedPath: an unreadable executable falls back to
    # an absolute argv[0].
    if not exe and arguments and arguments[0].startswith("/"):
        exe = arguments[0]
    return Proc(
        pid=pid, ppid=ppid, path=exe, arguments=arguments,
        started_at=boot + start_ticks / tick, threads=threads,
        memory_bytes=memory, cpu_ns=cpu_ticks * 1_000_000_000 // tick,
    )


def read_processes(proc: Path = Path("/proc"), uid: int | None = None) -> list[Proc]:
    """The user's own processes, pid 1 left out, as the Mac's sampler."""
    uid = os.getuid() if uid is None else uid
    tick = ticks()
    boot = boot_time(proc)
    out: list[Proc] = []
    try:
        entries = list(os.scandir(proc))
    except OSError:
        return out
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid <= 1:
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
        except OSError:
            continue
        p = read_process(proc, pid, boot, tick)
        if p is not None:
            out.append(p)
    return out


def fill_cwd(p: Proc, proc: Path) -> None:
    """Read the working directory and cgroup only for the processes that
    need them (orphan candidates), not the whole table."""
    p.cwd = _readlink(proc / str(p.pid) / "cwd") or None
    p.cgroup = (_read(proc / str(p.pid) / "cgroup") or b"").decode(errors="replace").strip()


# ---------------------------------------------------------------- attribution


@dataclass
class Matcher:
    """HarnessMatcher without the app bundle, which Linux does not have."""

    harness: str
    commands: set[str]
    home_folder: str
    # The home folder with symlinks resolved: /proc/<pid>/exe always
    # names the real file, so a ~/.claude linked elsewhere matches too.
    real_home_folder: str = ""


def load_matchers(home: Path) -> list[Matcher]:
    """The starred harnesses in registry order (swe list's), with their
    command, aliases and ``~/.<name>`` home folder. Read once and without
    migration, so viewing the page never rewrites a legacy registry."""
    from quiver.harness import registry

    entries = registry.load_registry_readonly()
    if registry.HARNESS_FILE.exists() or not registry.TOOLS_FILE.exists():
        starred = set(registry.starred_names(entries))
    else:
        # A legacy install not migrated yet: its stars and archive still
        # live in stars.json and archived.json. Read them as the migration
        # would, without running it.
        starred = set(registry._read_legacy_stars()) - set(registry._read_legacy_archive())
    out = []
    for name, info in entries.items():
        if name not in starred:
            continue
        commands = {a for a in (info.get("aliases") or []) if a}
        command = os.path.basename(str(info.get("command") or ""))
        if command:
            commands.add(command)
        folder = home / f".{name}"
        try:
            real = str(folder.resolve())
        except (OSError, RuntimeError):
            real = ""
        out.append(Matcher(name, commands, str(folder), real if real != str(folder) else ""))
    return out


def is_root(p: Proc, m: Matcher) -> bool:
    """ProcessAttribution.isRoot, plus argv[0]: a Node CLI that sets
    ``process.title`` (Claude Code does) rewrites /proc/<pid>/cmdline to
    its bare name, so argv[1] no longer names the script."""
    if os.path.basename(p.path) in m.commands:
        return True
    for folder in (m.home_folder, m.real_home_folder):
        if folder and p.path.startswith(folder + "/"):
            return True
    if p.runner_name in SCRIPT_RUNNERS and len(p.arguments) > 1 \
            and os.path.basename(p.arguments[1]) in m.commands:
        return True
    return bool(p.arguments) and os.path.basename(p.arguments[0]) in m.commands


@dataclass
class Attribution:
    by_harness: dict[str, list[Proc]]
    orphans: list[tuple[Proc, list[Proc]]]


def _ancestors(pid: int, by_pid: dict[int, Proc]) -> set[int]:
    out: set[int] = set()
    node = by_pid.get(pid)
    for _ in range(64):
        parent = by_pid.get(node.ppid) if node else None
        if parent is None or parent.pid == node.pid:
            break
        out.add(parent.pid)
        node = parent
    return out


def attribute(samples: list[Proc], matchers: list[Matcher], proc: Path = Path("/proc"),
              self_pid: int | None = None) -> Attribution:
    """ProcessAttribution.attribute on Linux. Each process goes to the
    harness of its nearest self-or-ancestor root. Of the rest, a script
    runner whose parent is gone is an orphan tree's root: its parent is
    pid 1 or the user's ``systemd --user`` (a subreaper, where Linux
    reparents orphans in a user session), it works somewhere other than
    ``/``, and it is not a systemd service (whose cgroup ends in
    ``.service``, Linux's launchd-managed agents). The hub itself and its
    ancestors are never orphans."""
    self_pid = os.getpid() if self_pid is None else self_pid
    by_pid = {p.pid: p for p in samples}
    by_harness: dict[str, list[Proc]] = {}
    rest: list[Proc] = []
    for p in samples:
        harness = None
        node = p
        for _ in range(64):
            match = next((m for m in matchers if is_root(node, m)), None)
            if match:
                harness = match.harness
                break
            parent = by_pid.get(node.ppid) if node.pid > 1 else None
            if parent is None or parent.pid == node.pid:
                break
            node = parent
        if harness:
            by_harness.setdefault(harness, []).append(p)
        else:
            rest.append(p)

    reapers = {1} | {p.pid for p in samples if p.name == "systemd"}
    protected = {self_pid} | _ancestors(self_pid, by_pid)
    roots: dict[int, Proc] = {}
    for p in rest:
        if p.ppid not in reapers or p.runner_name not in ORPHAN_RUNNERS or p.pid in protected:
            continue
        fill_cwd(p, proc)
        leaf = p.cgroup.rsplit("/", 1)[-1]
        if p.cwd and p.cwd != "/" and not leaf.endswith(".service"):
            roots[p.pid] = p
    trees: dict[int, list[Proc]] = {}
    for p in rest:
        if p.pid in roots:
            continue
        node = p
        for _ in range(64):
            if node.pid in roots:
                trees.setdefault(node.pid, []).append(p)
                break
            parent = by_pid.get(node.ppid)
            if parent is None or parent.pid == node.pid:
                break
            node = parent
    orphans = [(roots[pid], trees.get(pid, [])) for pid in sorted(roots)]
    return Attribution(by_harness, orphans)


# ---------------------------------------------------------------- auto clean


def send_signal(pid: int, sig: int, started_at: float | None = None,
                proc: Path = Path("/proc")) -> bool:
    """``os.kill`` as a boolean, the ComputeModel's signaller seam.

    Through a pidfd where the kernel offers one: the descriptor pins
    the process the pid meant at open, so the signal can never land on
    a recycled pid, and re-reading ``stat`` first means a process that
    already exited is not signalled at all. ``started_at`` is the start
    time the caller sampled; a pidfd-less kernel falls back to a bare
    ``os.kill`` — the same window the manual quit paths already run."""
    if started_at is not None and hasattr(os, "pidfd_open") \
            and hasattr(os, "pidfd_send_signal"):
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            return False
        try:
            current = read_process(proc, pid, boot_time(proc), ticks())
            if current is None or current.started_at != started_at:
                return False
            os.pidfd_send_signal(fd, sig)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def _clamp_age(seconds) -> int:
    """The field's value clamped into the a-minute-to-a-year range; a
    missing or unreadable one answers the default. ``1e309`` reads as
    infinity and ``int`` raises ``OverflowError`` on it, so bad numbers
    must not crash the boot-time settings read."""
    try:
        age = int(seconds)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_ORPHAN_AGE
    return min(max(age, ORPHAN_AGE_RANGE[0]), ORPHAN_AGE_RANGE[1]) if age > 0 \
        else DEFAULT_ORPHAN_AGE


def clean_settings(home: Path | None) -> dict:
    """The ``compute`` section of the hub's ``config.json`` as the auto
    clean knobs. ``enabled``/``force`` are strictly bool — a mistyped
    ``swe config set`` leaves them off rather than kills things; the age
    clamps to the sheet's range, the day default when absent."""
    from quiver.configuration import load_config
    from quiver.paths import config_file_for

    section = load_config(config_file_for(home)).get("compute")
    section = section if isinstance(section, dict) else {}
    return {
        "enabled": section.get("auto_clean") is True,
        "force": section.get("auto_clean_force") is True,
        "orphan_age_seconds": _clamp_age(section.get("orphan_age_seconds")),
        "grace_seconds": AUTOCLEAN_GRACE,
    }


# The read-modify-write in save_clean_settings holds this lock so two
# page writes can never interleave a read against a replace.
_config_lock = threading.Lock()


def save_clean_settings(home: Path | None, *, enabled: bool,
                        force: bool | None = None,
                        orphan_age_seconds=None) -> None:
    """Write the knobs through ``swe config``'s own path: the file is
    validated on the way out, so a corrupt ``config.json`` refuses the
    write instead of being clobbered. A knob left as ``None`` keeps its
    saved value, and the fill-in read happens under the same lock as the
    save, so two hub writes cannot lose each other. Raises
    ``ConfigurationError`` (and ``OSError`` if the file cannot be
    written)."""
    from quiver.configuration import load_config, save_config, set_value
    from quiver.paths import config_file_for

    with _config_lock:
        path = config_file_for(home)
        config = load_config(path)
        saved = clean_settings(home)
        if force is None:
            force = saved["force"]
        age = saved["orphan_age_seconds"] if orphan_age_seconds is None \
            else _clamp_age(orphan_age_seconds)
        config = set_value(config, "compute.auto_clean", enabled)
        config = set_value(config, "compute.auto_clean_force", force)
        config = set_value(config, "compute.orphan_age_seconds", age)
        save_config(config, path)


def _descendants_of(pid: int, children: dict[int, list[Proc]]) -> list[Proc]:
    """Every process under ``pid``, walking the sample's own ppid links."""
    out: list[Proc] = []
    frontier = [pid]
    visited = {pid}
    for _ in range(64):
        if not frontier:
            break
        nxt = [c for f in frontier for c in children.get(f, []) if c.pid not in visited]
        visited.update(c.pid for c in nxt)
        out.extend(nxt)
        frontier = [c.pid for c in nxt]
    return out


def kill_order(root: Proc, descendants: list[Proc], samples: list[Proc],
               self_pid: int, ancestors: set[int]) -> list[int]:
    """ComputeSummary.killOrder's orphan-tree branch: the members still
    alive, deepest first, then the root, so children do not die as
    zombies waiting on a parent that already went. Never pid 1, the hub
    or its ancestors; a pid the tree recorded counts only while the
    sample still has it with the same start time — a pid that died and
    got reused is somebody else's process now. The tree's membership
    comes from the current sample, so a worker the root spawned after the
    mark still dies, and so does a recorded child that outlived its
    root."""
    by_pid = {p.pid: p for p in samples}
    children: dict[int, list[Proc]] = {}
    for p in samples:
        children.setdefault(p.ppid, []).append(p)

    def live(p: Proc) -> bool:
        current = by_pid.get(p.pid)
        return current is not None and current.started_at == p.started_at

    members = _descendants_of(root.pid, children) if live(root) else []
    survivors = [d for d in descendants if live(d)]
    members += survivors
    for s in survivors:
        members += _descendants_of(s.pid, children)
    # Depth is the distance down from the root, so deeper members die
    # first; a member reparented off the tree still gets its hop count.
    depth = {root.pid: 0}
    for member in members:
        node, d = member, 0
        while node.pid != root.pid and d < 64 and node.ppid in by_pid:
            node = by_pid[node.ppid]
            d += 1
        depth[member.pid] = d
    members.sort(key=lambda p: depth[p.pid], reverse=True)
    ordered = members + ([root] if live(root) else [])
    out: list[int] = []
    seen: set[int] = set()
    for p in ordered:
        if p.pid <= 1 or p.pid == self_pid or p.pid in ancestors or p.pid in seen:
            continue
        out.append(p.pid)
        seen.add(p.pid)
    return out


class CPUMeter:
    """CPUMeter: cumulative CPU time to a percentage over all cores (so it
    can pass 100), and the busy streak. A pid gets none on its first
    sighting, or when its start time moved (the pid was reused)."""

    def __init__(self) -> None:
        self._previous: dict[int, tuple[int, float, float, float | None]] = {}

    def update(self, samples: list[Proc], now: float) -> dict[int, float]:
        percent: dict[int, float] = {}
        nxt: dict[int, tuple[int, float, float, float | None]] = {}
        for s in samples:
            busy_since = None
            old = self._previous.get(s.pid)
            if old and old[2] == s.started_at:
                used = max(0, s.cpu_ns - old[0])
                wall = now - old[1]
                if wall > 0:
                    p = used / (wall * 1e9) * 100
                    percent[s.pid] = p
                    busy_since = (old[3] if old[3] is not None else old[1]) if p >= BUSY_PERCENT else None
            nxt[s.pid] = (s.cpu_ns, now, s.started_at, busy_since)
        self._previous = nxt
        return percent

    def is_busy(self, pid: int) -> bool:
        entry = self._previous.get(pid)
        return bool(entry and entry[3] is not None and entry[1] - entry[3] >= BUSY_SPAN)


# ---------------------------------------------------------------- machine


def read_cpu_times(proc: Path = Path("/proc")) -> tuple[int, int] | None:
    """(busy, total) jiffies from /proc/stat's ``cpu`` line. Guest time is
    already inside user time, so only the first eight columns count."""
    raw = _read(proc / "stat") or b""
    for line in raw.decode(errors="replace").splitlines():
        if line.startswith("cpu "):
            try:
                values = [int(v) for v in line.split()[1:9]]
            except ValueError:
                return None
            if len(values) < 4:
                return None
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            return total - idle, total
    return None


def read_memory(proc: Path = Path("/proc")) -> dict | None:
    """MemTotal, MemAvailable and swap from /proc/meminfo, in bytes."""
    text = (_read(proc / "meminfo") or b"").decode(errors="replace")
    total = _status_kb(text, "MemTotal")
    if not total:
        return None
    available = _status_kb(text, "MemAvailable")
    if available is None:
        available = (_status_kb(text, "MemFree") or 0) + (_status_kb(text, "Cached") or 0)
    swap_total = _status_kb(text, "SwapTotal") or 0
    swap_free = _status_kb(text, "SwapFree") or 0
    return {
        "total_bytes": total * 1024,
        "used_bytes": max(0, total - available) * 1024,
        "swap_used_bytes": max(0, swap_total - swap_free) * 1024,
        "swap_total_bytes": swap_total * 1024,
    }


def read_load(proc: Path = Path("/proc")) -> list[float] | None:
    parts = (_read(proc / "loadavg") or b"").decode(errors="replace").split()
    try:
        return [float(v) for v in parts[:3]] if len(parts) >= 3 else None
    except ValueError:
        return None


def _plausible(celsius: float) -> bool:
    # TemperatureReading.plausible: anything outside is a sensor with nothing on it.
    return 10 < celsius < 150


def _mean(values: list[float]) -> float | None:
    kept = [v for v in values if _plausible(v)]
    return sum(kept) / len(kept) if kept else None


def _component(sensor: str) -> str | None:
    name = sensor.strip().lower()
    if name.startswith("bat"):
        return "battery"
    return _COMPONENT_BY_SENSOR.get(name)


def read_temperatures(sys_root: Path = Path("/sys")) -> tuple[dict[str, float], dict | None]:
    """One value per component (the mean of its sensors), hwmon first and
    thermal zones for what hwmon left out, plus a fan summary (the
    fastest fan, and whether one runs 15% over its minimum) or ``None``
    without fans."""
    groups: dict[str, list[float]] = {}
    fans: list[tuple[float, float]] = []
    hwmon = sys_root / "class" / "hwmon"
    for chip in sorted(hwmon.glob("hwmon*")):
        name = (_read(chip / "name") or b"").decode(errors="replace")
        component = _component(name)
        for sensor in sorted(chip.glob("temp*_input")):
            if component is None:
                break
            try:
                groups.setdefault(component, []).append(int((_read(sensor) or b"").strip()) / 1000)
            except ValueError:
                continue
        for fan in sorted(chip.glob("fan*_input")):
            try:
                rpm = float(int((_read(fan) or b"").strip()))
            except ValueError:
                continue
            minimum = _read(fan.with_name(fan.name.replace("_input", "_min"))) or b"0"
            try:
                fans.append((rpm, float(int(minimum.strip()))))
            except ValueError:
                fans.append((rpm, 0.0))
    values = {c: v for c, vs in groups.items() if (v := _mean(vs)) is not None}
    zones: dict[str, list[float]] = {}
    for zone in sorted((sys_root / "class" / "thermal").glob("thermal_zone*")):
        component = _component((_read(zone / "type") or b"").decode(errors="replace"))
        if component is None or component in values:
            continue
        try:
            zones.setdefault(component, []).append(int((_read(zone / "temp") or b"").strip()) / 1000)
        except ValueError:
            continue
    for c, vs in zones.items():
        v = _mean(vs)
        if v is not None:
            values[c] = v
    summary = None
    if fans:
        summary = {
            "rpm": max(rpm for rpm, _ in fans),
            "up": any(lo > 0 and rpm > lo * (1 + FAN_UP_MARGIN) for rpm, lo in fans),
        }
    return values, summary


_NVIDIA_FIELDS = ("index", "name", "utilization.gpu", "memory.used", "memory.total",
                  "temperature.gpu", "power.draw")


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None  # "[N/A]", "[Not Supported]"


def parse_nvidia(text: str) -> list[dict]:
    """``nvidia-smi --query-gpu=... --format=csv,noheader,nounits`` rows."""
    gpus = []
    for line in text.splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != len(_NVIDIA_FIELDS):
            continue
        index = _number(cols[0])
        if index is None:
            continue
        used, total = _number(cols[3]), _number(cols[4])
        gpus.append({
            "index": int(index),
            "name": cols[1],
            "utilization_percent": _number(cols[2]),
            "memory_used_bytes": int(used * 1024 * 1024) if used is not None else None,
            "memory_total_bytes": int(total * 1024 * 1024) if total is not None else None,
            "temperature_c": _number(cols[5]),
            "power_watts": _number(cols[6]),
        })
    return gpus


def parse_nvidia_pmon(text: str) -> dict[int, float]:
    """``nvidia-smi pmon -c 1 -s u`` rows to each pid's SM (compute)
    utilisation, summed over its GPUs like CPU % over cores. The columns
    come from the header, since drivers add some (``jpg``, ``ofa``); a
    ``-`` is a process the driver saw no work from in its last sample."""
    pid_at, sm_at = 1, 3
    out: dict[int, float] = {}
    for line in text.splitlines():
        cols = line.split()
        if not cols:
            continue
        if cols[0].startswith("#"):
            names = line.lstrip("#").split()
            if "pid" in names and "sm" in names:
                pid_at, sm_at = names.index("pid"), names.index("sm")
            continue
        if len(cols) <= max(pid_at, sm_at) or not cols[pid_at].isdigit():
            continue  # "-" where a GPU runs nothing
        pid = int(cols[pid_at])
        out[pid] = out.get(pid, 0.0) + (_number(cols[sm_at]) or 0.0)
    return out


def read_nvidia_processes() -> dict[int, float] | None:
    """Each pid's GPU utilisation from ``nvidia-smi pmon``, or ``None``
    when there is no ``nvidia-smi``, it fails (a GPU without process
    monitoring) or does not answer within ``NVIDIA_TIMEOUT``."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        done = subprocess.run([exe, "pmon", "-c", "1", "-s", "u"],
                              capture_output=True, text=True, timeout=NVIDIA_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return parse_nvidia_pmon(done.stdout)


def read_nvidia() -> list[dict] | None:
    """Every NVIDIA GPU's figures, or ``None`` when there is no
    ``nvidia-smi`` or it does not answer within ``NVIDIA_TIMEOUT``."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        done = subprocess.run(
            [exe, "--query-gpu=" + ",".join(_NVIDIA_FIELDS), "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=NVIDIA_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return parse_nvidia(done.stdout)


# ---------------------------------------------------------------- history


@dataclass
class Tick:
    """One sample's trend point: per harness (memory, CPU %, GPU %), the
    machine's temperatures and fans. GPU is ``None`` when ``nvidia-smi
    pmon`` gave no reading, so the line skips it rather than dip to 0."""

    at: float
    harnesses: dict[str, tuple[int, float, float | None]]
    temperatures: dict[str, float]
    fans: dict | None


class History:
    """ComputeHistory as two rings, so ten hours of the 5 minute view and
    six minutes of the 3 second view both fit without keeping every
    3 second point for ten hours."""

    def __init__(self) -> None:
        self.fine: deque[Tick] = deque()
        self.coarse: deque[Tick] = deque()
        self.last_tick: float | None = None

    def record(self, tick: Tick) -> None:
        self.last_tick = max(self.last_tick or tick.at, tick.at)
        # Half a second of slack, so a 3 s poll landing at 2.9 s still counts.
        for ring, step, keep in ((self.fine, FINE_STEP, FINE_KEEP),
                                 (self.coarse, COARSE_STEP, COARSE_KEEP)):
            if not ring or tick.at - ring[-1].at >= step - 0.5:
                ring.append(tick)
            while ring and ring[0].at < tick.at - keep:
                ring.popleft()

    def window(self, interval: int) -> list[Tick]:
        """The ticks in the ``WINDOW`` intervals ending at the newest tick,
        thinned from the newest back to one per interval, oldest first."""
        if self.last_tick is None:
            return []
        ring = self.fine if interval < COARSE_STEP else self.coarse
        start = self.last_tick - WINDOW * interval
        kept: list[Tick] = []
        for tick in reversed(ring):
            if tick.at < start:
                break
            if not kept or kept[-1].at - tick.at >= interval * 0.9:
                kept.append(tick)
        kept.reverse()
        return kept

    def peaks(self) -> dict[str, int]:
        """Each harness's highest memory at any retained tick, for the
        card's "gone above the flag" rule (ComputeHistory.shown)."""
        out: dict[str, int] = {}
        for ring in (self.fine, self.coarse):
            for tick in ring:
                for harness, (memory, _cpu, _gpu) in tick.harnesses.items():
                    out[harness] = max(out.get(harness, 0), memory)
        return out


# ---------------------------------------------------------------- model


class ComputeModel:
    """The page's state between requests: the last sample, the meters and
    the history, behind one lock (the hub serves requests on threads)."""

    def __init__(self, proc: Path = Path("/proc"), sys_root: Path = Path("/sys"),
                 nvidia=read_nvidia, nvidia_processes=read_nvidia_processes,
                 clock=time.time, self_pid: int | None = None,
                 signaller=None) -> None:
        self.proc, self.sys_root, self.nvidia, self.clock = proc, sys_root, nvidia, clock
        self.nvidia_processes = nvidia_processes
        self.self_pid = os.getpid() if self_pid is None else self_pid
        # The seam takes (pid, sig, sampled start time); the default pins
        # the signal through a pidfd against this model's /proc.
        self.signaller = signaller or (
            lambda pid, sig, started_at: send_signal(pid, sig, started_at, self.proc))
        self.lock = threading.Lock()
        # One sample at a time, held across the nvidia-smi calls but
        # apart from ``lock``, so a payload read never waits on the GPU
        # and two due samples don't both run the commands.
        self._sample_lock = threading.Lock()
        self.meter = CPUMeter()
        self.history = History()
        self.home: Path = Path.home()
        self.matchers: list[Matcher] = []
        self.matchers_loaded = False
        self.error: str | None = None
        self.sampled_at: float | None = None
        self.samples: list[Proc] = []
        self.cpu: dict[int, float] = {}
        self.attribution = Attribution({}, [])
        self.system: dict = {}
        self.gpus: list[dict] | None = None
        self.gpu_by_pid: dict[int, float] | None = None
        self.gpu_available = False
        self.temperature_available = False
        self.temperatures: dict[str, float] = {}
        self.fans: dict | None = None
        self._cpu_times: tuple[int, int] | None = None
        # Auto clean's ledger by orphan root pid: the tree as it was when
        # SIGTERM went out, when, and whether the grace escalation to
        # SIGKILL already ran. The recorded start times do the same work
        # as `_signalled`: a pid the kernel recycled answers for a
        # different process and is judged again on its own age.
        self._cleaned: dict[int, tuple[float, float, tuple[Proc, list[Proc]], bool]] = {}
        # Each pid auto clean signalled -> (at, started_at), for the
        # "quitting" mark on the orphan row. Pruned like the Mac's
        # signalledAt: a pid whose process changed forgets the mark.
        self._signalled: dict[int, tuple[float, float]] = {}

    def _load_matchers(self) -> None:
        try:
            self.matchers = load_matchers(self.home)
            self.matchers_loaded = True
            self.error = None
        except (OSError, ValueError) as exc:
            self.error = f"Couldn't read the harness registry: {exc}"

    def sample(self, force: bool = False) -> None:
        """Take a fresh sample unless the last one is under
        ``MIN_SAMPLE`` old, and record it in the history.

        ``nvidia-smi`` runs before ``lock`` is taken: two calls of up to
        ``NVIDIA_TIMEOUT`` each must not hold up every Compute read. A
        second caller waits on ``_sample_lock`` and then finds the first
        caller's sample fresh."""
        with self._sample_lock:
            with self.lock:
                if not force and self._fresh():
                    return
            gpus = self.nvidia()
            gpu_by_pid = self.nvidia_processes()
            self._take_sample(gpus, gpu_by_pid)

    def _take_sample(self, gpus, gpu_by_pid) -> None:
        with self.lock:
            self._load_matchers()
            samples = read_processes(self.proc)
            at = self.clock()
            self.samples = samples
            self.cpu = self.meter.update(samples, at)
            self.attribution = attribute(samples, self.matchers, self.proc, self.self_pid)
            self.sampled_at = at
            self._auto_clean(at)
            if self._signalled:
                by_pid = {p.pid: p for p in samples}
                self._signalled = {
                    pid: v for pid, v in self._signalled.items()
                    if pid in by_pid and by_pid[pid].started_at == v[1]
                }
            self._sample_machine(gpus, gpu_by_pid)
            self._record(at)

    def _fresh(self) -> bool:
        return (self.sampled_at is not None
                and self.clock() - self.sampled_at < MIN_SAMPLE)

    def _sample_machine(self, gpus: list[dict] | None,
                        gpu_by_pid: dict[int, float] | None) -> None:
        times = read_cpu_times(self.proc)
        cpu_percent = None
        if times and self._cpu_times:
            busy = times[0] - self._cpu_times[0]
            total = times[1] - self._cpu_times[1]
            if total > 0:
                cpu_percent = max(0.0, min(100.0, busy / total * 100))
        if times:
            self._cpu_times = times
        self.system = {
            "cpu_percent": cpu_percent,
            "cores": os.cpu_count() or 0,
            "memory": read_memory(self.proc),
            "load": read_load(self.proc),
        }
        self.gpus = gpus
        # The GPU tab plots harnesses, so only per-process readings make
        # it available; the device totals feed the machine line.
        self.gpu_by_pid = gpu_by_pid
        if self.gpu_by_pid is not None:
            self.gpu_available = True
        self.temperatures, self.fans = read_temperatures(self.sys_root)
        gpu_temps = [g["temperature_c"] for g in self.gpus or [] if g.get("temperature_c") is not None]
        if "gpu" not in self.temperatures and gpu_temps:
            mean = _mean(gpu_temps)
            if mean is not None:
                self.temperatures["gpu"] = mean
        if self.temperatures:
            self.temperature_available = True

    def _record(self, at: float) -> None:
        gpu = self.gpu_by_pid
        harnesses = {
            harness: (sum(p.memory_bytes for p in procs), sum(self.cpu.get(p.pid, 0.0) for p in procs),
                      None if gpu is None else sum(gpu.get(p.pid, 0.0) for p in procs))
            for harness, procs in self.attribution.by_harness.items() if procs
        }
        self.history.record(Tick(at, harnesses, dict(self.temperatures),
                                 dict(self.fans) if self.fans else None))

    # ------------------------------------------------------------ auto clean

    def _auto_clean(self, now: float) -> None:
        """The Mac model's autoClean, run over every accepted sample —
        the ticker and page requests alike, so it works with no browser
        open. An orphan tree older than the saved age gets one SIGTERM,
        through the same kill plan and guards a manual Quit uses on the
        Mac; a tree already signalled is left alone. With ``force`` a
        marked tree whose members are still alive ``AUTOCLEAN_GRACE``
        later gets SIGKILL once. Marks prune once nothing in the tree
        answers the recorded start times, so a tree that died — or a pid
        handed to somebody new — is forgotten.

        It waits for the matcher list to load: an empty one would read
        a harness's own helpers as orphans, and auto clean kills what it
        reads."""
        cfg = clean_settings(self.home)
        if not cfg["enabled"] or not self.matchers_loaded:
            return
        marked = self._marked_pids()
        for root, descendants in self.attribution.orphans:
            if now - root.started_at <= cfg["orphan_age_seconds"]:
                continue
            mark = self._cleaned.get(root.pid)
            if (mark is not None and mark[0] == root.started_at) \
                    or (root.pid, root.started_at) in marked:
                continue
            self._signal_tree(root, descendants, force=False)
            self._cleaned[root.pid] = (root.started_at, now, (root, descendants), False)
        ancestors = _ancestors(self.self_pid, {p.pid: p for p in self.samples})
        for pid, (started, at, tree, forced) in list(self._cleaned.items()):
            order = kill_order(tree[0], tree[1], self.samples, self.self_pid, ancestors)
            if not order:
                del self._cleaned[pid]
            elif cfg["force"] and not forced and now - at >= AUTOCLEAN_GRACE:
                self._signal_tree(tree[0], tree[1], force=True)
                self._cleaned[pid] = (started, at, tree, True)

    def _marked_pids(self) -> set[tuple[int, float]]:
        """Every ``(pid, started_at)`` inside a recorded tree, so a member
        that outlived its root and reparented into a new orphan is not
        signalled all over again as the new tree's root."""
        out: set[tuple[int, float]] = set()
        for started, _at, (root, descendants), _forced in self._cleaned.values():
            out.add((root.pid, started))
            out.update((d.pid, d.started_at) for d in descendants)
        return out

    def _signal_tree(self, root: Proc, descendants: list[Proc], *, force: bool) -> None:
        ancestors = _ancestors(self.self_pid, {p.pid: p for p in self.samples})
        started = {p.pid: p.started_at for p in self.samples}
        sig = signal.SIGKILL if force else signal.SIGTERM
        for pid in kill_order(root, descendants, self.samples, self.self_pid, ancestors):
            if self.signaller(pid, sig, started[pid]):
                self._signalled[pid] = (self.clock(), started[pid])

    # ------------------------------------------------------------ payload

    def payload(self, interval: int) -> dict:
        with self.lock:
            return self._payload(interval)

    def _proc_row(self, p: Proc) -> dict:
        return {
            "pid": p.pid,
            "name": p.name,
            "command_line": _shown(p.arguments) or p.path,
            "threads": p.threads,
            "memory_bytes": p.memory_bytes,
            "cpu_percent": self.cpu.get(p.pid),
            "started_at": p.started_at,
            "busy": self.meter.is_busy(p.pid),
        }

    def _payload(self, interval: int) -> dict:
        by = self.attribution.by_harness
        order = [m.harness for m in self.matchers]
        harnesses = []
        for harness in order:
            procs = by.get(harness) or []
            if not procs:
                continue
            rows = sorted(procs, key=lambda p: p.memory_bytes, reverse=True)
            harnesses.append({
                "harness": harness,
                "process_count": len(procs),
                "threads": sum(p.threads for p in procs),
                "memory_bytes": sum(p.memory_bytes for p in procs),
                "cpu_percent": sum(self.cpu.get(p.pid, 0.0) for p in procs),
                "running_since": min(p.started_at for p in procs),
                "processes": [self._proc_row(p) for p in rows],
            })
        orphans = []
        for root, descendants in self.attribution.orphans:
            words = [os.path.basename(root.arguments[0])] + root.arguments[1:3] if root.arguments else [root.name]
            orphans.append({
                "pid": root.pid,
                "command": _shown(words),
                "command_line": _shown(root.arguments) or root.path,
                "cwd": root.cwd,
                "started_at": root.started_at,
                "memory_bytes": root.memory_bytes + sum(d.memory_bytes for d in descendants),
                "descendants": len(descendants),
                "quitting": root.pid in self._signalled,
            })
        orphans.sort(key=lambda o: o["memory_bytes"], reverse=True)
        everyone = [p for procs in by.values() for p in procs] + [
            p for root, ds in self.attribution.orphans for p in [root, *ds]]
        return {
            "supported": True,
            "sampled_at": self.sampled_at,
            "matchers_loaded": self.matchers_loaded,
            "starred": order,
            "error": self.error,
            "autoclean": clean_settings(self.home),
            "totals": {
                "memory_bytes": sum(p.memory_bytes for p in everyone),
                "cpu_percent": sum(self.cpu.get(p.pid, 0.0) for p in everyone),
                "threads": sum(p.threads for p in everyone),
                "process_count": len(everyone),
            },
            "harnesses": harnesses,
            "not_running": [h for h in order if not by.get(h)],
            "orphans": orphans,
            "system": self.system,
            "gpus": self.gpus or [],
            "temperatures": self.temperatures,
            "fans": self.fans,
            "trend": self._trend(interval),
        }

    def _trend(self, interval: int) -> dict:
        ticks = self.history.window(interval)
        series: dict[str, list] = {}
        temperatures = []
        for t in ticks:
            for harness, (memory, cpu, gpu) in t.harnesses.items():
                series.setdefault(harness, []).append(
                    [round(t.at, 3), memory, round(cpu, 2), None if gpu is None else round(gpu, 2)])
            if t.temperatures or t.fans:
                temperatures.append({"at": round(t.at, 3),
                                     "values": {k: round(v, 1) for k, v in t.temperatures.items()},
                                     "fans": t.fans})
        return {
            "interval": interval,
            "window": WINDOW,
            "last_tick": self.history.last_tick,
            "harnesses": series,
            "peaks": self.history.peaks(),
            "gpu_available": self.gpu_available,
            "temperatures": temperatures,
            "temperature_available": self.temperature_available,
        }


# ---------------------------------------------------------------- the hub's one model

_model: ComputeModel | None = None
_ticker: threading.Thread | None = None
_guard = threading.Lock()


def model() -> ComputeModel:
    global _model
    with _guard:
        if _model is None:
            _model = ComputeModel()
        return _model


def start_ticker() -> None:
    """Start the background sampler once per hub process. It runs as a
    daemon, so it never holds the hub open at exit."""
    global _ticker
    with _guard:
        if _ticker is not None:
            return
        _ticker = threading.Thread(target=_tick_forever, name="quiver-compute", daemon=True)
        _ticker.start()


def _tick_forever() -> None:
    while True:
        time.sleep(TICK)
        try:
            model().sample()
        except Exception:  # noqa: BLE001 - a bad tick must not end the sampler
            continue
