# Compute

The Mac app's Compute page on the web: what the starred harnesses are
running on the hub machine, and what it costs it. The Mac page reads
macOS (libproc, IOKit, the SMC); the hub is usually Linux, so the web page
keeps the Mac page's layout and meaning and reads Linux's own sources.
Stdlib only.

Code: `src/quiver/webapp/compute.py` (sampler, attribution, meters,
history), `src/quiver/webapp/pages/compute.py` (the route),
`src/quiver/webapp/static/pages/compute.{js,css}` (the page).

## What it shows

In the Mac page's order:

1. The totals line: memory, process count, CPU % and threads of every
   process a starred harness runs plus the orphan trees.
2. A machine line (web only): the hub's CPU % over all cores, memory
   used of total and swap, load averages, and each NVIDIA GPU's
   utilisation and memory.
3. The trend card: RAM, CPU, GPU and Temperature tabs with the interval
   picker (every 3 s, 10 s, 30 s, minute, 5 minutes; 120 intervals per
   window). RAM, CPU and GPU draw one line per harness that has gone
   above the flag threshold, with its icon at the line's end, the dashed
   "Flag above" line on RAM, and a hover rule with a readout. Temperature draws
   one line per sensor component (CPU, GPU, Memory, SSD, Battery) with the
   legend chips that switch lines on and off and the "Fans up" shading.
4. A card per running starred harness: figures in the header, a row per
   process by memory (name, Over / Busy badges, PID, threads, age, memory,
   CPU %), the top five then "Show N more · N flagged". The header folds
   the card.
5. The Orphaned card: script runners whose parent is gone, with the Old
   and Over badges, folded after eight.
6. A "Not running" card per starred harness with nothing up.

Empty states and placeholders use the Mac wording ("Nothing is starred…"
with Open Harnesses, "Nothing a starred harness runs is up right now.",
"Collecting, the first points arrive within…", "No harness has gone above
2 GB yet.").

The gear opens the thresholds: "Orphans count as old after" (a minute to a
year) and "Flag a process above" (1, 2, 4 or 8 GB). The tab, interval,
temperature lines and thresholds are kept per browser in `localStorage`,
as the Mac app keeps them in UserDefaults; flags are worked out in the
page, so a changed threshold reflags at once.

The page polls every 3 seconds while it is open and visible, as the Mac
page samples every 3 seconds.

## Payload

`GET /v1/app/compute?interval=10[&refresh=1]`. `interval` is one of 3,
10, 30, 60, 300 (anything else is 10) and only chooses how the history is
thinned. `refresh=1` samples now; otherwise every viewer shares one
sample taken at most every two seconds.

```json
{
  "supported": true, "machine": "home-server", "sampled_at": 1792780571.9,
  "matchers_loaded": true, "starred": ["claude"], "error": null,
  "totals": {"memory_bytes": 0, "cpu_percent": 0, "threads": 0, "process_count": 0},
  "harnesses": [{"harness": "claude", "process_count": 2, "threads": 30, "memory_bytes": 0,
                 "cpu_percent": 12.5, "running_since": 1792700000.0,
                 "processes": [{"pid": 1, "name": "node", "command_line": "…", "threads": 7,
                                "memory_bytes": 0, "cpu_percent": 3.1, "started_at": 0, "busy": false}]}],
  "not_running": [],
  "orphans": [{"pid": 2, "command": "node server.js --port", "command_line": "…", "cwd": "/home/u/app",
               "started_at": 0, "memory_bytes": 0, "descendants": 1}],
  "system": {"cpu_percent": 4.4, "cores": 12, "load": [1.0, 0.8, 0.6],
             "memory": {"total_bytes": 0, "used_bytes": 0, "swap_used_bytes": 0, "swap_total_bytes": 0}},
  "gpus": [{"index": 0, "name": "NVIDIA GeForce GTX 1070", "utilization_percent": 0.0,
            "memory_used_bytes": 0, "memory_total_bytes": 0, "temperature_c": 45.0, "power_watts": 10.7}],
  "temperatures": {"cpu": 55.9, "gpu": 45.0}, "fans": null,
  "trend": {"interval": 10, "window": 120, "last_tick": 1792780571.9,
            "harnesses": {"claude": [[1792780571.9, 1343664128, 12.5, 3.0]]},
            "peaks": {"claude": 1343664128}, "gpu_available": true,
            "temperatures": [{"at": 1792780571.9, "values": {"cpu": 55.9}, "fans": null}],
            "temperature_available": true}
}
```

A harness point is `[at, memory bytes, CPU %, GPU %]`. GPU % is `null`
when `nvidia-smi pmon` gave no reading at that tick, and the chart skips
the point rather than draw a zero; `gpu_available` turns true once it
has answered.

On a hub that is not Linux the answer is `{"supported": false, "os":
"Darwin", "message": "…"}` and the page shows the message.

## Where the numbers come from

| Figure | Linux source | Mac source |
|---|---|---|
| The user's processes | `/proc/<pid>` owned by the hub's user, pid 1 and kernel threads left out | libproc |
| Memory per process | `RssAnon` + `VmSwap` from `/proc/<pid>/status`: private memory, so shared libraries are not counted once per process | physical footprint |
| CPU % per process | `utime` + `stime` from `/proc/<pid>/stat` between two samples, over all cores (can pass 100) | rusage CPU time |
| Busy | at or above 80% for two minutes, as the Mac | same |
| Machine CPU, memory, load | `/proc/stat`, `/proc/meminfo`, `/proc/loadavg` | not shown |
| Temperatures | `/sys/class/hwmon` (k10temp, coretemp, zenpower → CPU; amdgpu, nouveau → GPU; jc42, spd5118 → Memory; nvme, drivetemp → SSD; BAT → Battery), then `/sys/class/thermal` zones for what hwmon left out, then `nvidia-smi` for the GPU. Each component is the mean of its plausible (10 to 150 °C) sensors | SMC keys, HID sensors |
| Fans | `fan*_input` and `fan*_min` in hwmon; "up" is 15% over the minimum | SMC |
| GPU per device (machine line) | `nvidia-smi --query-gpu=…` with a 3 s timeout, when installed | not shown |
| GPU % per harness | `nvidia-smi pmon -c 1 -s u` with a 3 s timeout: each pid's SM utilisation, summed over its GPUs, then over the harness's processes | IOKit per-process GPU time |

Which processes belong to a harness follows `ProcessAttribution`: the
starred, unarchived harnesses in registry order (`swe list`'s), each with
its command, aliases and `~/.<name>` folder; a process belongs to the
harness of its nearest self-or-ancestor root. Two Linux additions: the
home folder also matches with symlinks resolved (`/proc/<pid>/exe` always
names the real file), and argv[0] matches a command or alias, because a
Node CLI that sets `process.title` (Claude Code does) rewrites its
`/proc/<pid>/cmdline` to the bare name.

An orphan tree's root is a script runner (node, npm, npx, pnpm, yarn,
bun, deno, python, uv, uvx, tsx, vite) working somewhere other than `/`
whose parent is pid 1 or the user's `systemd --user`, which is where
Linux reparents orphans inside a user session. A systemd service (its
cgroup ends in `.service`) is Linux's launchd agent and is left out, and
so are the hub and its ancestors.

## History

The Mac trend sampler runs from app launch, so its card shows history
from before the page was opened. To match, the first request starts one
daemon thread in the hub that samples every 10 seconds for the life of
the process; until a browser opens the page once, nothing is sampled.
Page requests sample too (at most every two seconds). Samples go into two
rings in memory: every 3 s for six minutes (the 3 s view) and every 10 s
for ten hours (the 5 minute view's window). A hub restart starts the
history again, as an app relaunch does.

## Differences from the Mac app

- **Read only.** Quit, Force quit, Quit all and the Clean up sheet are
  left out: they signal processes, and the web app has no writes for this
  page yet. The thresholds sheet keeps only what drives the badges and
  the trend card.
- **Fan control is Mac only.** The fan button and its popover write the
  SMC through Quiver's privileged helper on a Mac; there is no Linux
  counterpart, so the page has no fan button and no "Fans up above"
  threshold line. Fan readings still shade the Temperature chart when
  hwmon reports them.
- **GPU** per harness is the driver's per-process SM utilisation from
  `nvidia-smi pmon`, a share of each GPU's compute over its last sample,
  where the Mac reads cumulative GPU time. A process doing no GPU work
  reads 0. Without `nvidia-smi`, or on a GPU whose driver has no process
  monitoring, the GPU tab says GPU readings aren't available.
- **Memory** is private memory (`RssAnon` + `VmSwap`), the nearest Linux
  figure to the Mac's physical footprint. It reads lower than `top`'s RES,
  which counts shared pages.
- **The machine line** is web only.
- **Airflow and Display** temperatures have no Linux source and never
  appear; Wi-Fi and ACPI board sensors are left out.
- No app bundles on Linux, so the app-bundle rules (helpers left over from
  an earlier launch) do not apply.
