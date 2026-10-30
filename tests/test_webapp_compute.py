"""The Compute page's data: /proc and /sys parsing over fake trees,
attribution, the meters, the history rings, auto clean and the route."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.configuration import load_config
from quiver.paths import config_file_for
from quiver.webapp import compute
from quiver.webapp.pages import PageError
from quiver.webapp.pages import compute as page

from tests.test_remote_hub import HubBase
from tests.test_webapp import app_js_function

HOME = "/home/u"
TICK = 100


def _stat_line(pid, comm, state, ppid, utime, stime, threads, start):
    rest = [state, ppid, 0, 0, 0, 0, 0, 0, 0, 0, 0, utime, stime, 0, 0, 20, 0, threads, 0, start, 0, 0]
    return f"{pid} ({comm}) " + " ".join(str(x) for x in rest) + "\n"


class FakeMachine:
    """A /proc and /sys under a temp folder, owned by this user."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        self.proc = self.root / "proc"
        self.sys = self.root / "sys"
        self.proc.mkdir()
        self.sys.mkdir()
        self.cpu_line = "cpu  100 0 100 800 0 0 0 0 0 0"
        self.write_stat()

    def write_stat(self):
        (self.proc / "stat").write_text(f"{self.cpu_line}\ncpu0 1 2 3 4\nbtime 1000\n")
        (self.proc / "meminfo").write_text(
            "MemTotal:       8000000 kB\nMemFree:  1 kB\nMemAvailable:   6000000 kB\n"
            "SwapTotal:      1000 kB\nSwapFree:       400 kB\n")
        (self.proc / "loadavg").write_text("0.50 0.25 0.10 1/100 999\n")

    def process(self, pid, ppid, cmdline, exe=None, comm=None, state="S", cpu=(0, 0), threads=1,
                start=500, anon_kb=1024, swap_kb=0, cwd=None, cgroup="0::/user.slice/session-1.scope"):
        d = self.proc / str(pid)
        d.mkdir(exist_ok=True)
        comm = comm or (os.path.basename(cmdline[0]) if cmdline else "kworker")
        (d / "stat").write_text(_stat_line(pid, comm, state, ppid, cpu[0], cpu[1], threads, start))
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in cmdline))
        (d / "status").write_text(f"Name:\t{comm}\nVmRSS:\t9999 kB\nRssAnon:\t{anon_kb} kB\nVmSwap:\t{swap_kb} kB\n")
        (d / "cgroup").write_text(cgroup + "\n")
        for name, target in (("exe", exe), ("cwd", cwd)):
            link = d / name
            if link.is_symlink():
                link.unlink()
            if target:
                os.symlink(target, link)

    def hwmon(self, index, name, temps=(), fans=()):
        d = self.sys / "class" / "hwmon" / f"hwmon{index}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        for i, milli in enumerate(temps, 1):
            (d / f"temp{i}_input").write_text(f"{milli}\n")
        for i, (rpm, lo) in enumerate(fans, 1):
            (d / f"fan{i}_input").write_text(f"{rpm}\n")
            (d / f"fan{i}_min").write_text(f"{lo}\n")

    def zone(self, index, kind, milli):
        d = self.sys / "class" / "thermal" / f"thermal_zone{index}"
        d.mkdir(parents=True)
        (d / "type").write_text(kind + "\n")
        (d / "temp").write_text(f"{milli}\n")


def _matchers():
    return [compute.Matcher("claude", {"claude", "cc"}, f"{HOME}/.claude"),
            compute.Matcher("codex", {"codex"}, f"{HOME}/.codex")]


class TestReadProcesses(unittest.TestCase):
    def setUp(self):
        self.m = FakeMachine()

    def test_fields_memory_and_skips(self):
        self.m.process(10, 1, ["/usr/bin/node", "/x/bin/claude", "--resume"], exe="/usr/bin/node",
                       comm="node (x) y", cpu=(150, 50), threads=7, start=200, anon_kb=2048, swap_kb=1024)
        self.m.process(11, 1, ["zombie"], state="Z")
        self.m.process(12, 2, [])  # a kernel thread
        self.m.process(13, 10, ["/opt/app", "a"], exe="/opt/app (deleted)")
        self.m.process(14, 10, ["/opt/gone"])  # unreadable exe falls back to argv[0]
        procs = {p.pid: p for p in [compute.read_process(self.m.proc, pid, 1000, TICK)
                                    for pid in (10, 11, 12, 13, 14)] if p}
        self.assertEqual(sorted(procs), [10, 13, 14])
        p = procs[10]
        self.assertEqual((p.ppid, p.threads, p.name, p.runner_name), (1, 7, "node", "node"))
        self.assertEqual(p.cpu_ns, 2 * 1_000_000_000)
        self.assertEqual(p.started_at, 1002.0)
        self.assertEqual(p.memory_bytes, 3072 * 1024)
        self.assertEqual(p.command_line, "/usr/bin/node /x/bin/claude --resume")
        self.assertEqual(procs[13].path, "/opt/app")
        self.assertEqual(procs[14].path, "/opt/gone")

    def test_only_own_uid_and_never_pid_1(self):
        self.m.process(1, 0, ["/sbin/init"], exe="/sbin/init")
        self.m.process(20, 1, ["bash"], exe="/bin/bash")
        self.assertEqual([p.pid for p in compute.read_processes(self.m.proc)], [20])
        self.assertEqual(compute.read_processes(self.m.proc, uid=os.getuid() + 1), [])

    def test_python_versions_fold_to_one_runner(self):
        p = compute.Proc(1, 0, path="/usr/bin/python3.12")
        self.assertEqual(p.runner_name, "python")
        self.assertEqual(compute.Proc(5, 0).name, "pid 5")


class TestAttribution(unittest.TestCase):
    def setUp(self):
        self.m = FakeMachine()

    def _procs(self):
        return {p.pid: p for p in compute.read_processes(self.m.proc)}

    def test_roots_descendants_and_rules(self):
        m = self.m
        m.process(100, 1, ["/usr/bin/zsh"], exe="/usr/bin/zsh")
        m.process(101, 100, ["node", "/home/u/.npm/bin/claude"], exe="/usr/bin/node")     # script runner
        m.process(102, 101, ["/bin/bash", "-c", "ls"], exe="/bin/bash")                  # descendant
        m.process(103, 100, ["codex"], exe="/usr/local/bin/codex")                       # command name
        m.process(104, 1, ["srv"], exe=f"{HOME}/.claude/remote/srv/server")              # home folder
        m.process(105, 100, ["claude"], exe="/usr/bin/node")                             # process.title
        m.process(106, 100, ["cc", "--x"], exe="/home/u/.local/share/claude/versions/2.1")  # alias as argv[0]
        m.process(107, 100, ["vim"], exe="/usr/bin/vim")                                 # nobody's
        found = compute.attribute(list(self._procs().values()), _matchers(), m.proc, self_pid=1)
        by = {h: sorted(p.pid for p in ps) for h, ps in found.by_harness.items()}
        self.assertEqual(by, {"claude": [101, 102, 104, 105, 106], "codex": [103]})
        self.assertEqual(found.orphans, [])

    def test_orphans_under_init_or_the_user_manager(self):
        m = self.m
        m.process(50, 1, ["/usr/lib/systemd/systemd", "--user"], exe="/usr/lib/systemd/systemd")
        m.process(200, 1, ["node", "server.js"], exe="/usr/bin/node", cwd="/home/u/app")
        m.process(201, 200, ["esbuild"], exe="/usr/bin/esbuild")
        m.process(202, 201, ["esbuild-child"], exe="/usr/bin/esbuild")
        m.process(210, 50, ["npx", "mcp"], exe="/usr/bin/npx", cwd="/home/u/proj")        # reparented
        m.process(220, 50, ["python3", "svc.py"], exe="/usr/bin/python3", cwd="/home/u",
                  cgroup="0::/user.slice/user-1000.slice/user@1000.service/app.slice/quiver-hub.service")
        m.process(230, 1, ["node", "x"], exe="/usr/bin/node", cwd="/")                    # in /
        m.process(240, 1, ["/usr/bin/tmux"], exe="/usr/bin/tmux", cwd="/home/u")          # not a runner
        m.process(250, 1, ["python3", "-m", "quiver.cli"], exe="/usr/bin/python3", cwd="/home/u")  # the hub
        m.process(260, 100, ["node", "dev"], exe="/usr/bin/node", cwd="/home/u")          # parent alive
        found = compute.attribute(list(self._procs().values()), _matchers(), m.proc, self_pid=250)
        roots = {root.pid: sorted(d.pid for d in ds) for root, ds in found.orphans}
        self.assertEqual(roots, {200: [201, 202], 210: []})
        self.assertEqual(found.orphans[0][0].cwd, "/home/u/app")

    def test_load_matchers_reads_starred_unarchived_in_order(self):
        registry = {"codex": {"command": "/opt/bin/codex", "aliases": ["cx"], "state": "starred", "pin": 2},
                    "claude": {"command": "claude", "aliases": ["cc"], "state": "starred", "pin": 1},
                    "gemini": {"command": "gemini"},
                    "old": {"command": "old", "state": "archived", "archived": {"reason": "x"}}}
        home = Path(tempfile.mkdtemp())
        (home / "real").mkdir()
        os.symlink(home / "real", home / ".claude")
        with patch("quiver.harness.registry.load_registry_readonly", return_value=registry), \
                patch("quiver.harness.registry.load_registry", side_effect=AssertionError("migrates")):
            matchers = compute.load_matchers(home)
        self.assertEqual([m.harness for m in matchers], ["codex", "claude"])
        self.assertEqual(matchers[0].commands, {"codex", "cx"})
        self.assertEqual(matchers[1].home_folder, str(home / ".claude"))
        self.assertEqual(matchers[1].real_home_folder, str((home / "real").resolve()))
        p = compute.Proc(9, 1, path=str((home / "real").resolve() / "bin" / "x"))
        self.assertTrue(compute.is_root(p, matchers[1]))


    def test_legacy_install_reads_stars_without_migrating(self):
        folder = Path(tempfile.mkdtemp())
        tools, stars, archive = folder / "tools.json", folder / "stars.json", folder / "archived.json"
        tools.write_text(json.dumps({"claude": {"command": "claude"}, "codex": {"command": "codex"},
                                     "old": {"command": "old"}}))
        stars.write_text(json.dumps(["codex", "old"]))
        archive.write_text(json.dumps({"old": {"reason": "x"}}))
        harness = folder / "harness.json"
        with patch("quiver.harness.registry.HARNESS_FILE", harness), \
                patch("quiver.harness.registry.TOOLS_FILE", tools), \
                patch("quiver.harness.registry.STARS_FILE", stars), \
                patch("quiver.harness.registry.ARCHIVE_FILE", archive):
            matchers = compute.load_matchers(Path(HOME))
        self.assertEqual([m.harness for m in matchers], ["codex"])
        self.assertFalse(harness.exists())
        self.assertTrue(stars.exists() and tools.exists())


class TestMeters(unittest.TestCase):
    def test_cpu_percent_busy_streak_and_reuse(self):
        meter = compute.CPUMeter()
        p = compute.Proc(1, 0, started_at=10.0, cpu_ns=0)
        self.assertEqual(meter.update([p], 0.0), {})
        t = 0.0
        for _ in range(13):  # 130 s at 90% of one core
            t += 10
            p.cpu_ns += 9 * 10**9
            percent = meter.update([p], t)
        self.assertAlmostEqual(percent[1], 90.0)
        self.assertTrue(meter.is_busy(1))
        p.cpu_ns += 10**8  # 1% breaks the streak
        meter.update([p], t + 10)
        self.assertFalse(meter.is_busy(1))
        reused = compute.Proc(1, 0, started_at=99.0, cpu_ns=p.cpu_ns + 10**12)
        self.assertEqual(meter.update([reused], t + 20), {})

    def test_machine_readers(self):
        m = FakeMachine()
        self.assertEqual(compute.read_cpu_times(m.proc), (200, 1000))
        mem = compute.read_memory(m.proc)
        self.assertEqual(mem["total_bytes"], 8000000 * 1024)
        self.assertEqual(mem["used_bytes"], 2000000 * 1024)
        self.assertEqual(mem["swap_used_bytes"], 600 * 1024)
        self.assertEqual(compute.read_load(m.proc), [0.5, 0.25, 0.1])
        self.assertEqual(compute.boot_time(m.proc), 1000.0)
        empty = Path(tempfile.mkdtemp())
        self.assertIsNone(compute.read_cpu_times(empty))
        self.assertIsNone(compute.read_memory(empty))
        self.assertIsNone(compute.read_load(empty))


class TestSensors(unittest.TestCase):
    def test_components_fans_and_zone_fill(self):
        m = FakeMachine()
        m.hwmon(0, "k10temp", temps=(55000, 61000))
        m.hwmon(1, "iwlwifi_1_0", temps=(38000,))
        m.hwmon(2, "nvme", temps=(40000, -273000, 44000), fans=((2500, 2000), (900, 0)))
        m.zone(0, "x86_pkg_temp", 90000)  # CPU already read from hwmon
        m.zone(1, "battery", 30000)
        values, fans = compute.read_temperatures(m.sys)
        self.assertEqual(values, {"cpu": 58.0, "ssd": 42.0, "battery": 30.0})
        self.assertEqual(fans, {"rpm": 2500.0, "up": True})

    def test_no_sensors(self):
        self.assertEqual(compute.read_temperatures(Path(tempfile.mkdtemp())), ({}, None))

    def test_nvidia_rows(self):
        text = ("0, NVIDIA GeForce GTX 1070, 12, 17, 8192, 45, 10.72\n"
                "1, Tesla T4, [N/A], 0, 15360, [Not Supported], [N/A]\nbad line\n")
        gpus = compute.parse_nvidia(text)
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0]["utilization_percent"], 12.0)
        self.assertEqual(gpus[0]["memory_total_bytes"], 8192 * 1024 * 1024)
        self.assertEqual(gpus[1]["name"], "Tesla T4")
        self.assertIsNone(gpus[1]["utilization_percent"])
        self.assertIsNone(gpus[1]["temperature_c"])

    def test_nvidia_absent_or_failing(self):
        with patch("shutil.which", return_value=None):
            self.assertIsNone(compute.read_nvidia())
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch("subprocess.run", side_effect=compute.subprocess.TimeoutExpired("x", 3)):
            self.assertIsNone(compute.read_nvidia())

    def test_pmon_rows(self):
        # A 580 driver's output (jpg and ofa columns), two GPUs, one pid on both.
        text = ("# gpu         pid   type     sm    mem    enc    dec    jpg    ofa    command \n"
                "# Idx           #    C/G      %      %      %      %      %      %    name \n"
                "    0       1740     G      -      -      -      -      -      -    Xorg           \n"
                "    0       4242     C     35      9      -      -      -      -    python3        \n"
                "    1       4242     C     20      4      -      -      -      -    python3        \n"
                "    1          -     -      -      -      -      -      -      -    -              \n")
        self.assertEqual(compute.parse_nvidia_pmon(text), {1740: 0.0, 4242: 55.0})

    def test_pmon_older_driver_columns(self):
        text = ("# gpu        pid  type    sm   mem   enc   dec   command\n"
                "# Idx          #   C/G     %     %     %     %   name\n"
                "    0       7001     C    12     3     -     -   ollama\n")
        self.assertEqual(compute.parse_nvidia_pmon(text), {7001: 12.0})
        self.assertEqual(compute.parse_nvidia_pmon(""), {})

    def test_pmon_absent_or_failing(self):
        with patch("shutil.which", return_value=None):
            self.assertIsNone(compute.read_nvidia_processes())
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch("subprocess.run", side_effect=compute.subprocess.TimeoutExpired("x", 3)):
            self.assertIsNone(compute.read_nvidia_processes())
        failed = compute.subprocess.CompletedProcess([], 6, "", "pmon not supported")
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch("subprocess.run", return_value=failed):
            self.assertIsNone(compute.read_nvidia_processes())
        done = compute.subprocess.CompletedProcess([], 0, "    0   12   C   7   1   -   -   x\n", "")
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch("subprocess.run", return_value=done) as run:
            self.assertEqual(compute.read_nvidia_processes(), {12: 7.0})
        self.assertEqual(run.call_args[0][0][1:], ["pmon", "-c", "1", "-s", "u"])


class TestHistory(unittest.TestCase):
    def _tick(self, at, memory=1):
        return compute.Tick(at, {"claude": (memory, 1.0, None)}, {"cpu": 50.0}, None)

    def test_rings_window_and_peaks(self):
        h = compute.History()
        for i in range(100):
            h.record(self._tick(i * 3.0, memory=i))
        # Every 3 s point in the fine ring; every 10 s or so in the coarse.
        self.assertEqual(len(h.fine), 100)
        self.assertTrue(all(b.at - a.at >= 9.5 for a, b in zip(h.coarse, list(h.coarse)[1:])))
        three = h.window(3)
        self.assertEqual(three[-1].at, 297.0)
        self.assertEqual(len(three), 100)
        thirty = h.window(30)
        self.assertEqual(thirty[-1].at, h.coarse[-1].at)
        self.assertTrue(all(b.at - a.at >= 27 for a, b in zip(thirty, thirty[1:])))
        self.assertEqual(h.peaks(), {"claude": 99})

    def test_old_points_age_out(self):
        h = compute.History()
        h.record(self._tick(0.0))
        h.record(self._tick(compute.COARSE_KEEP + 100.0))
        self.assertEqual(len(h.coarse), 1)
        self.assertEqual(len(h.fine), 1)
        self.assertEqual(compute.History().window(10), [])


class _Model(unittest.TestCase):
    def setUp(self):
        self.m = FakeMachine()
        m = self.m
        m.process(100, 1, ["/usr/bin/zsh"], exe="/usr/bin/zsh", start=100)
        m.process(101, 100, ["claude"], exe="/usr/bin/node", start=200, anon_kb=3 * 1024 * 1024, threads=12)
        m.process(102, 101, ["node", "mcp.js"], exe="/usr/bin/node", start=300, anon_kb=1024)
        m.process(200, 1, ["node", "server.js", "--port", "3000"], exe="/usr/bin/node", cwd="/home/u/app",
                  start=50, anon_kb=2048)
        m.hwmon(0, "k10temp", temps=(55000,))
        self.now = [5000.0]
        # pmon's reading: claude's two pids on the GPU, someone else's too.
        self.gpu_by_pid = {101: 30.0, 102: 5.0, 4000: 60.0}
        self.model = compute.ComputeModel(
            proc=m.proc, sys_root=m.sys,
            nvidia=lambda: compute.parse_nvidia("0, GTX, 40, 1, 8, 60, 5\n"),
            nvidia_processes=lambda: self.gpu_by_pid,
            clock=lambda: self.now[0], self_pid=999)
        self.model.home = Path(HOME)
        self._patches = [
            patch("quiver.harness.registry.load_registry_readonly",
                  return_value={"claude": {"command": "claude", "state": "starred"},
                                "codex": {"command": "codex", "state": "starred"}}),
        ]
        for p in self._patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self._patches):
            p.stop()


class TestModel(_Model):
    def test_nvidia_smi_runs_off_the_lock(self):
        # Two nvidia-smi calls of up to 3 s each ran under the model's
        # lock, so every Compute read waited for them.
        held = []

        def nvidia():
            held.append(self.model.lock.locked())
            return None

        self.model.nvidia = nvidia
        self.model.nvidia_processes = lambda: held.append(
            self.model.lock.locked())
        self.model.sample()
        self.assertEqual(held, [False, False])

    def test_overlapping_samples_run_nvidia_smi_once(self):
        # A sample paused in nvidia-smi: a second due sample waits for it
        # and then finds it fresh, while payload reads go on meanwhile.
        import threading
        inside, release = threading.Event(), threading.Event()
        calls = []

        def nvidia():
            calls.append(1)
            inside.set()
            release.wait(10)
            return None

        self.model.nvidia = nvidia
        first = threading.Thread(target=self.model.sample)
        second = threading.Thread(target=self.model.sample)
        first.start()
        self.assertTrue(inside.wait(10))
        second.start()
        self.model.payload(10)  # the data lock stays free
        release.set()
        first.join(10)
        second.join(10)
        self.assertEqual(calls, [1])
        self.assertEqual(len(self.model.history.fine), 1)

    def test_a_fresh_sample_skips_nvidia_smi(self):
        calls = []
        self.model.sample()
        self.model.nvidia = lambda: calls.append(1)
        self.now[0] += 1.0
        self.model.sample()  # inside MIN_SAMPLE
        self.assertEqual(calls, [])

    def test_two_samples_make_the_payload(self):
        self.model.sample()
        self.m.process(101, 100, ["claude"], exe="/usr/bin/node", start=200, anon_kb=3 * 1024 * 1024,
                       threads=12, cpu=(300, 100))
        self.m.cpu_line = "cpu  200 0 200 1600 0 0 0 0 0 0"
        self.m.write_stat()
        self.now[0] += 1.0
        self.model.sample()  # inside MIN_SAMPLE: reused
        self.now[0] += 9.0
        self.model.sample()
        body = self.model.payload(10)
        self.assertTrue(body["supported"])
        self.assertEqual(body["starred"], ["claude", "codex"])
        self.assertEqual(body["not_running"], ["codex"])
        [claude] = body["harnesses"]
        self.assertEqual(claude["process_count"], 2)
        self.assertEqual([r["pid"] for r in claude["processes"]], [101, 102])
        self.assertAlmostEqual(claude["processes"][0]["cpu_percent"], 40.0)
        self.assertEqual(claude["running_since"], 1002.0)
        self.assertEqual(claude["threads"], 13)
        [orphan] = body["orphans"]
        self.assertEqual((orphan["pid"], orphan["command"], orphan["cwd"]),
                         (200, "node server.js --port", "/home/u/app"))
        self.assertEqual(body["totals"]["process_count"], 3)
        self.assertEqual(body["system"]["cpu_percent"], 20.0)
        self.assertEqual(body["system"]["load"], [0.5, 0.25, 0.1])
        self.assertEqual(body["temperatures"], {"cpu": 55.0, "gpu": 60.0})
        trend = body["trend"]
        self.assertEqual(len(trend["harnesses"]["claude"]), 2)
        self.assertEqual([pt[3] for pt in trend["harnesses"]["claude"]], [35.0, 35.0])
        self.assertNotIn("gpus", trend)
        self.assertEqual(body["gpus"][0]["utilization_percent"], 40.0)
        self.assertTrue(trend["gpu_available"] and trend["temperature_available"])
        self.assertEqual(trend["peaks"]["claude"], 3 * 1024**3 + 1024 * 1024)
        json.dumps(body)

    def test_command_lines_hide_secrets_before_they_leave_the_hub(self):
        """A harness's MCP child and an orphan started with a token on
        their command line show it as "…" to every paired browser."""
        self.m.process(103, 101, ["node", "mcp.js", "--token=ghp_abcdefghijklmnop1234"],
                       exe="/usr/bin/node", start=310)
        self.m.process(201, 1, ["npx", "--api-key", "sk-live-abcdefghijklmnop", "srv"],
                       exe="/usr/bin/node", cwd="/home/u", start=60)
        self.model.sample()
        self.now[0] += 10.0
        self.model.sample()
        body = json.dumps(self.model.payload(10))
        self.assertNotIn("ghp_abcdefghijklmnop1234", body)
        self.assertNotIn("sk-live-abcdefghijklmnop", body)
        rows = {r["pid"]: r for h in self.model.payload(10)["harnesses"] for r in h["processes"]}
        self.assertEqual(rows[103]["command_line"], "node mcp.js --token=…")
        self.assertEqual(rows[101]["command_line"], "claude")
        orphans = {o["pid"]: o for o in self.model.payload(10)["orphans"]}
        self.assertEqual(orphans[201]["command"], "npx --api-key …")
        self.assertEqual(orphans[201]["command_line"], "npx --api-key … srv")

    def test_gpu_is_none_without_pmon_and_zero_off_the_gpu(self):
        self.gpu_by_pid = None
        self.model.sample()
        self.assertEqual(self.model.payload(10)["trend"]["harnesses"]["claude"][-1][3], None)
        self.assertFalse(self.model.payload(10)["trend"]["gpu_available"])
        self.gpu_by_pid = {4000: 60.0}
        self.now[0] += 10.0
        self.model.sample()
        trend = self.model.payload(10)["trend"]
        self.assertEqual([pt[3] for pt in trend["harnesses"]["claude"]], [None, 0.0])
        self.assertTrue(trend["gpu_available"])

    def test_a_bad_registry_is_an_error_not_a_crash(self):
        with patch("quiver.harness.registry.load_registry_readonly", side_effect=ValueError("bad json")):
            self.model.sample()
        body = self.model.payload(10)
        self.assertFalse(body["matchers_loaded"])
        self.assertIn("bad json", body["error"])
        self.assertEqual(body["harnesses"], [])


class TestAutoClean(_Model):
    """The hub's auto clean: SIGTERM for old orphans, the force switch's
    SIGKILL grace, and the recorded-start-time guards the Mac shares."""

    def _signals(self):
        calls = []
        self.model.signaller = \
            lambda pid, sig, at=None: calls.append((pid, sig)) or True
        return calls

    def _config(self, section):
        home = Path(tempfile.mkdtemp())
        from quiver.configuration import save_config
        save_config({"compute": section}, config_file_for(home))
        self.model.home = home

    def test_no_config_signals_nothing(self):
        calls = self._signals()
        self.model.home = Path(tempfile.mkdtemp())
        self.model.sample()
        self.assertEqual(calls, [])

    def test_off_signals_nothing(self):
        calls = self._signals()
        self._config({"auto_clean": False, "orphan_age_seconds": 60})
        self.model.sample()
        self.assertEqual(calls, [])

    def test_old_orphan_gets_one_sigterm(self):
        calls = self._signals()
        self._config({"auto_clean": True, "orphan_age_seconds": 3600})
        self.model.sample()
        self.assertEqual(calls, [(200, signal.SIGTERM)])
        self.now[0] += 10.0
        self.model.sample()  # marked already: not signalled again
        self.assertEqual(calls, [(200, signal.SIGTERM)])

    def test_descendants_die_before_the_root(self):
        calls = self._signals()
        self.m.process(201, 200, ["esbuild"], exe="/usr/bin/esbuild", start=60)
        self._config({"auto_clean": True, "orphan_age_seconds": 3600})
        self.model.sample()
        self.assertEqual(calls, [(201, signal.SIGTERM), (200, signal.SIGTERM)])

    def test_young_orphan_left_alone(self):
        calls = self._signals()
        self._config({"auto_clean": True})  # the day default; the fixture is ~4000 s
        self.model.sample()
        self.assertEqual(calls, [])

    def test_force_escalates_after_the_grace(self):
        calls = self._signals()
        self._config({"auto_clean": True, "auto_clean_force": True,
                      "orphan_age_seconds": 3600})
        self.model.sample()
        self.assertEqual(calls, [(200, signal.SIGTERM)])
        self.now[0] += compute.AUTOCLEAN_GRACE + 1
        self.model.sample()  # still alive: SIGKILL follows
        self.assertEqual(calls, [(200, signal.SIGTERM), (200, signal.SIGKILL)])
        self.now[0] += 60
        self.model.sample()  # forced once: not over and over
        self.assertEqual(len(calls), 2)

    def test_no_force_no_escalation(self):
        calls = self._signals()
        self._config({"auto_clean": True, "orphan_age_seconds": 3600})
        self.model.sample()
        self.now[0] += 60
        self.model.sample()
        self.assertEqual(calls, [(200, signal.SIGTERM)])

    def test_dead_tree_and_reused_pid_are_left_alone(self):
        calls = self._signals()
        self._config({"auto_clean": True, "auto_clean_force": True,
                      "orphan_age_seconds": 3600})
        self.model.sample()
        self.assertEqual(calls, [(200, signal.SIGTERM)])
        # The tree exited and the kernel recycled pid 200 for a brand-new
        # process: the recorded mark does not carry over to it, and its
        # own age is young, so nothing more is signalled.
        shutil.rmtree(self.m.proc / "200")
        self.m.process(200, 1, ["node", "new.js"], exe="/usr/bin/node",
                       cwd="/home/u/new", start=395000)  # ~60 s before now
        self.now[0] += compute.AUTOCLEAN_GRACE + 1
        self.model.sample()
        self.assertEqual(calls, [(200, signal.SIGTERM)])

    def test_a_surviving_child_still_gets_the_escalation(self):
        calls = self._signals()
        self.m.process(201, 200, ["esbuild"], exe="/usr/bin/esbuild", start=60)
        self._config({"auto_clean": True, "auto_clean_force": True,
                      "orphan_age_seconds": 3600})
        self.model.sample()
        self.assertEqual(calls, [(201, signal.SIGTERM), (200, signal.SIGTERM)])
        # The root died on SIGTERM; the child reparented to init but is
        # the same process the recorded tree saw, so the force pass
        # still reaches it.
        shutil.rmtree(self.m.proc / "200")
        self.m.process(201, 1, ["esbuild"], exe="/usr/bin/esbuild", start=60)
        self.now[0] += compute.AUTOCLEAN_GRACE + 1
        self.model.sample()
        self.assertEqual(calls[-1], (201, signal.SIGKILL))

    def test_payload_marks_quitting_and_exposes_the_settings(self):
        self._signals()
        self._config({"auto_clean": True, "auto_clean_force": True,
                      "orphan_age_seconds": 3600})
        self.model.sample()
        body = self.model.payload(10)
        self.assertTrue(body["autoclean"]["enabled"])
        self.assertTrue(body["autoclean"]["force"])
        self.assertEqual(body["autoclean"]["orphan_age_seconds"], 3600)
        self.assertEqual(body["autoclean"]["grace_seconds"], compute.AUTOCLEAN_GRACE)
        self.assertTrue(body["orphans"][0]["quitting"])

    def test_a_recycled_pid_does_not_keep_the_quitting_mark(self):
        self._signals()
        self._config({"auto_clean": True, "orphan_age_seconds": 3600})
        self.model.sample()
        self.assertTrue(self.model.payload(10)["orphans"][0]["quitting"])
        shutil.rmtree(self.m.proc / "200")
        self.m.process(200, 1, ["node", "new.js"], exe="/usr/bin/node",
                       cwd="/home/u/new", start=395000)
        self.now[0] += 10
        self.model.sample()
        self.assertFalse(self.model.payload(10)["orphans"][0]["quitting"])

    def test_clean_settings_coerces_and_defaults(self):
        home = Path(tempfile.mkdtemp())
        self.assertEqual(compute.clean_settings(home),
                         {"enabled": False, "force": False,
                          "orphan_age_seconds": compute.DEFAULT_ORPHAN_AGE,
                          "grace_seconds": compute.AUTOCLEAN_GRACE})
        from quiver.configuration import save_config
        save_config({"compute": {"auto_clean": "yes", "auto_clean_force": True,
                                 "orphan_age_seconds": "x"}},
                    config_file_for(home))
        got = compute.clean_settings(home)
        # A mistyped "yes" reads as off — the switch never fires on a string.
        self.assertEqual((got["enabled"], got["force"]), (False, True))
        self.assertEqual(got["orphan_age_seconds"], compute.DEFAULT_ORPHAN_AGE)
        # Infinity and NaN in the file answer the default too; int()
        # raises OverflowError on them, which the boot read must survive.
        save_config({"compute": {"orphan_age_seconds": 1e309}},
                    config_file_for(home))
        self.assertEqual(compute.clean_settings(home)["orphan_age_seconds"],
                         compute.DEFAULT_ORPHAN_AGE)

    def test_a_tree_member_that_outlives_its_root_is_not_signalled_twice(self):
        calls = self._signals()
        self.m.process(201, 200, ["esbuild"], exe="/usr/bin/esbuild", start=60)
        self._config({"auto_clean": True, "orphan_age_seconds": 3600})
        self.model.sample()
        self.assertEqual(calls, [(201, signal.SIGTERM), (200, signal.SIGTERM)])
        # The root died on SIGTERM; the child is now its own orphan root
        # but still sits inside the recorded tree's mark, so it is left
        # for the mark's own bookkeeping instead of another SIGTERM.
        shutil.rmtree(self.m.proc / "200")
        self.m.process(201, 1, ["node", "child.js"], exe="/usr/bin/node",
                       cwd="/home/u/app", start=60)
        self.now[0] += 10
        self.model.sample()
        self.assertEqual(calls, [(201, signal.SIGTERM), (200, signal.SIGTERM)])


class TestSettingsPost(_Model):
    def _ctx(self, home):
        class Ctx:
            machine = {"name": "box"}
        ctx = Ctx()
        ctx.home = home
        return ctx

    def test_settings_round_trip_starts_the_ticker(self):
        home = Path(tempfile.mkdtemp())
        with patch.object(compute, "supported", return_value=True), \
                patch.object(compute, "model", return_value=self.model), \
                patch.object(compute, "start_ticker") as ticker:
            body = page.post(self._ctx(home), "settings",
                             {"enabled": True, "force": True,
                              "orphan_age_seconds": 7200})
        self.assertTrue(body["autoclean"]["enabled"])
        self.assertTrue(body["autoclean"]["force"])
        self.assertEqual(body["autoclean"]["orphan_age_seconds"], 7200)
        self.assertTrue(ticker.called)
        self.assertEqual(self.model.home, home)
        cfg = load_config(config_file_for(home))
        self.assertEqual(cfg["compute"],
                         {"auto_clean": True, "auto_clean_force": True,
                          "orphan_age_seconds": 7200})

    def test_disabling_keeps_the_saved_age(self):
        home = Path(tempfile.mkdtemp())
        ctx = self._ctx(home)
        with patch.object(compute, "supported", return_value=True), \
                patch.object(compute, "model", return_value=self.model), \
                patch.object(compute, "start_ticker"):
            page.post(ctx, "settings", {"enabled": True, "orphan_age_seconds": 7200})
            body = page.post(ctx, "settings", {"enabled": False})
        self.assertFalse(body["autoclean"]["enabled"])
        self.assertEqual(body["autoclean"]["orphan_age_seconds"], 7200)

    def test_bad_bodies_are_refused(self):
        ctx = self._ctx(Path(tempfile.mkdtemp()))
        with patch.object(compute, "supported", return_value=True):
            for body, code in (({}, "bad_enabled"),
                               ({"enabled": "yes"}, "bad_enabled")):
                with self.assertRaises(PageError) as cm:
                    page.post(ctx, "settings", body)
                self.assertEqual(cm.exception.code, code)
            with self.assertRaises(PageError) as cm:
                page.post(ctx, "quit", {"enabled": True})
            self.assertEqual(cm.exception.status, 404)
        # The file is never written for a refused body.
        self.assertFalse(config_file_for(ctx.home).exists())

    def test_the_age_clamps_to_the_field_bounds(self):
        home = Path(tempfile.mkdtemp())
        # model and the ticker are patched like the neighbouring tests:
        # an enabled write starts the real sampler otherwise, and the
        # real sampler signals the host's real orphans.
        with patch.object(compute, "supported", return_value=True), \
                patch.object(compute, "model", return_value=self.model), \
                patch.object(compute, "start_ticker"):
            body = page.post(self._ctx(home), "settings",
                             {"enabled": True, "orphan_age_seconds": 5})
        self.assertEqual(body["autoclean"]["orphan_age_seconds"], 60)


class TestPage(_Model):
    def _ctx(self):
        class Ctx:
            home = HOME
            machine = {"name": "box"}
        return Ctx()

    def test_unsupported_os(self):
        with patch.object(compute, "supported", return_value=False), \
                patch("platform.system", return_value="Darwin"):
            body = page.get(self._ctx(), {})
        self.assertEqual((body["supported"], body["os"], body["machine"]), (False, "Darwin", "box"))
        self.assertIn("Mac", body["message"])

    def test_interval_is_checked_and_the_ticker_starts(self):
        with patch.object(compute, "supported", return_value=True), \
                patch.object(compute, "model", return_value=self.model), \
                patch.object(compute, "start_ticker") as ticker:
            body = page.get(self._ctx(), {"interval": "7"})
            self.assertEqual(body["trend"]["interval"], 10)
            self.assertEqual(page.get(self._ctx(), {"interval": "300"})["trend"]["interval"], 300)
            self.assertEqual(page.get(self._ctx(), {"interval": "x"})["trend"]["interval"], 10)
        self.assertTrue(ticker.called)
        self.assertEqual(body["machine"], "box")
        self.assertEqual(self.model.home, Path(HOME))


class TestRoute(_Model, HubBase):
    def test_needs_a_device_and_answers(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/compute")[0], 401)
        with patch.object(compute, "supported", return_value=True), \
                patch.object(compute, "model", return_value=self.model), \
                patch.object(compute, "start_ticker"):
            status, data = self.srv.request("GET", "/v1/app/compute?interval=3&refresh=1",
                                            headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        self.assertEqual(body["trend"]["interval"], 3)
        self.assertEqual(body["harnesses"][0]["harness"], "claude")

    def test_settings_post_writes_the_hub_config(self):
        self.assertEqual(self.srv.request(
            "POST", "/v1/app/compute/settings", {"enabled": True})[0], 401)
        with patch.object(compute, "supported", return_value=True), \
                patch.object(compute, "model", return_value=self.model), \
                patch.object(compute, "start_ticker"):
            status, data = self.srv.request(
                "POST", "/v1/app/compute/settings",
                {"enabled": True, "force": True, "orphan_age_seconds": 7200},
                headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertTrue(body["autoclean"]["enabled"])
        self.assertTrue(body["autoclean"]["force"])
        self.assertEqual(body["autoclean"]["orphan_age_seconds"], 7200)
        cfg = load_config(config_file_for(self.srv.state.home))
        self.assertEqual(cfg["compute"],
                         {"auto_clean": True, "auto_clean_force": True,
                          "orphan_age_seconds": 7200})

    def test_settings_post_rejects_a_bad_body(self):
        with patch.object(compute, "supported", return_value=True):
            status, data = self.srv.request(
                "POST", "/v1/app/compute/settings", {"enabled": "yes"},
                headers=self.auth())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)["error"]["code"], "bad_enabled")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestStoredChoices(unittest.TestCase):
    """pages/compute.js under node: the tab, interval and Temperature lines
    kept in localStorage fall back when the page doesn't know them (an
    unknown tab threw in the chart and stopped the poll)."""

    def choices(self, stored):
        script = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages/compute.js"
        prelude = ("const stored = " + json.dumps({f"quiver.compute.{k}": v for k, v in stored.items()}) + ";\n"
                   "globalThis.localStorage = { getItem: (k) => (k in stored ? stored[k] : null) };\n"
                   "function registerPage() {}\n")
        body = ("process.stdout.write(JSON.stringify({ tab: computePage.tab, interval: computePage.interval,"
                " lines: computePage.lines }));\n")
        done = subprocess.run(["node", "-e", prelude + script.read_text(encoding="utf-8") + "\n" + body],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_known_choices_are_kept(self):
        self.assertEqual(self.choices({"tab": "gpu", "interval": "30", "temperatureLines": '["ssd","cpu"]'}),
                         {"tab": "gpu", "interval": 30, "lines": ["ssd", "cpu"]})

    def test_unknown_choices_fall_back(self):
        self.assertEqual(self.choices({"tab": "disk", "interval": "7", "temperatureLines": '{"cpu":1}'}),
                         {"tab": "ram", "interval": 10, "lines": ["cpu", "gpu", "ssd", "battery"]})
        self.assertEqual(self.choices({"temperatureLines": '["cpu","fan",3]'})["lines"], ["cpu"])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSettingsSheetLeavesWithThePage(unittest.TestCase):
    """pages/compute.js's settings sheet under node with app.js's real
    bodySheet and closeRouteSheets: route() closes it on navigation, its
    close handler keeps a typed age and takes the sheet off the page. It
    used to be a plain dialog route() never saw, so Back left it modal
    over the next page."""

    def test_navigation_closes_and_removes_the_sheet(self):
        script = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages/compute.js"
        prelude = (
            "const stored = {};\n"
            "globalThis.localStorage = { getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; } };\n"
            "function registerPage() {}\n"
            "let routeGen = 1; const esc = (s) => String(s);\n"
            "const options = (list, sel) => list.map(([v, t]) => `<option${v === sel ? ' selected' : ''}>${t}</option>`).join('');\n"
            "const body = [];\n"
            "function makeEl(tag) {\n"
            "  const el = { tag, dataset: {}, attrs: {}, open: false, listeners: {}, kids: {}, innerHTML: '', value: '',\n"
            "    setAttribute(k, v) { this.attrs[k] = v; },\n"
            "    addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },\n"
            "    showModal() { this.open = true; },\n"
            "    close() { if (!this.open) return; this.open = false;\n"
            "      setTimeout(() => { for (const f of this.listeners.close || []) f(); }); },\n"
            "    remove() { const i = body.indexOf(this); if (i >= 0) body.splice(i, 1); },\n"
            "    querySelector(sel) { return (this.kids[sel] ||= makeEl('input')); },\n"
            "    querySelectorAll() { return []; } };\n"
            "  return el;\n"
            "}\n"
            "globalThis.document = { body: { appendChild: (el) => body.push(el) }, createElement: makeEl,\n"
            "  getElementById: (id) => body.find((e) => e.id === id) || null,\n"
            "  querySelectorAll: (sel) => (sel === 'dialog[data-route-sheet][open]'\n"
            "    ? body.filter((e) => 'routeSheet' in e.dataset && e.open) : []) };\n"
            + app_js_function("bodySheet") + app_js_function("closeRouteSheets"))
        body = (
            "openComputeLimits();\n"
            "const sheet = document.getElementById('compute-limits');\n"
            "const opened = !!sheet && sheet.open;\n"
            "sheet.querySelector('#compute-age').value = '3';\n"
            "sheet.querySelector('#compute-age-unit').value = 'hour';\n"
            "// route()'s order; the browser fires close later, as a task.\n"
            "closeRouteSheets(); routeGen++;\n"
            "const before = { open: sheet.open, left: body.length };\n"
            "setTimeout(() => process.stdout.write(JSON.stringify({ opened, before,\n"
            "  routeSheet: 'routeSheet' in sheet.dataset, left: body.length, age: computePage.limits.orphanAge })));\n")
        done = subprocess.run(["node", "-e", prelude + script.read_text(encoding="utf-8") + "\n" + body],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout),
                         {"opened": True, "before": {"open": False, "left": 1}, "routeSheet": True,
                          "left": 0, "age": 3 * 3600})


if __name__ == "__main__":
    unittest.main()
