"""``GET /v1/app/compute``: the Compute page. What the starred harnesses
run on the hub machine and what it costs it, from Linux's /proc and /sys
(see ``quiver.webapp.compute``). Read only: quitting processes and fan
control stay in the Mac app.

Query: ``interval`` is the trend card's picker in seconds (3, 10, 30, 60
or 300, default 10); it only chooses how the history is thinned, so each
browser keeps its own. ``refresh=1`` samples now even inside the shared
two-second minimum.
"""

from __future__ import annotations

import platform
from pathlib import Path

from quiver.webapp import compute


def get(ctx, args: dict) -> dict:
    machine = (ctx.machine or {}).get("name") or ""
    if not compute.supported():
        system = platform.system() or "this OS"
        return {
            "supported": False,
            "machine": machine,
            "os": system,
            "message": f"Compute reads Linux's /proc and /sys, which {system} doesn't have. "
                       "On a Mac, the Quiver app's own Compute page shows this.",
        }
    try:
        interval = int(args.get("interval") or compute.DEFAULT_INTERVAL)
    except ValueError:
        interval = compute.DEFAULT_INTERVAL
    if interval not in compute.INTERVALS:
        interval = compute.DEFAULT_INTERVAL
    model = compute.model()
    model.home = Path(ctx.home) if ctx.home is not None else Path.home()
    model.sample(force=args.get("refresh") == "1")
    compute.start_ticker()
    body = model.payload(interval)
    body["machine"] = machine
    return body
