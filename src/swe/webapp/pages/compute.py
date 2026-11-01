"""``GET /v1/app/compute``: the Compute page. What the starred harnesses
run on the hub machine and what it costs it, from Linux's /proc and /sys
(see ``swe.webapp.compute``). The one write is
``POST /v1/app/compute/settings``, the auto clean switches; quitting
processes and fan control by hand stay in the Mac app.

Query: ``interval`` is the trend card's picker in seconds (3, 10, 30, 60
or 300, default 10); it only chooses how the history is thinned, so each
browser keeps its own. ``refresh=1`` samples now even inside the shared
two-second minimum.

Post: ``{"enabled": bool, "force": bool, "orphan_age_seconds": int}``.
``enabled`` must be a bool; the other two are optional — a key the body
leaves out keeps its saved value, ``force`` present is read as "is it
the JSON true" so it cannot turn on by accident, and the age clamps to
the field's minute-to-year range. The reply is ``{"autoclean": …}``,
the settings as saved.
"""

from __future__ import annotations

import platform

from swe.configuration import ConfigurationError
from swe.webapp import compute
from swe.webapp.pages import PageError, home_path


def get(ctx, args: dict) -> dict:
    machine = (ctx.machine or {}).get("name") or ""
    if not compute.supported():
        system = platform.system() or "this OS"
        return {
            "supported": False,
            "machine": machine,
            "os": system,
            "message": f"Compute reads Linux's /proc and /sys, which {system} doesn't have. "
                       "On a Mac, the SWE app's own Compute page shows this.",
        }
    try:
        interval = int(args.get("interval") or compute.DEFAULT_INTERVAL)
    except ValueError:
        interval = compute.DEFAULT_INTERVAL
    if interval not in compute.INTERVALS:
        interval = compute.DEFAULT_INTERVAL
    model = compute.model()
    model.home = home_path(ctx)
    model.sample(force=args.get("refresh") == "1")
    compute.start_ticker()
    body = model.payload(interval)
    body["machine"] = machine
    return body


def post(ctx, action: str, body: dict) -> dict:
    """The settings sheet's one write. Enabling also sets the model's
    home and starts the ticker, so a hub that has never shown the page
    still cleans while the switch is on."""
    if action != "settings":
        raise PageError(404, "not_found", "unknown compute action")
    if not compute.supported():
        raise PageError(404, "unsupported", "compute is only on Linux hubs")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise PageError(400, "bad_enabled", "enabled must be true or false")
    # Keys the body leaves out keep their saved values, so flipping the
    # switch never silently resets the age the sheet already wrote.
    # ``force`` present is read as "is it the JSON true" so a string
    # cannot turn it on by accident; the age clamps on the way in.
    force = body["force"] is True if "force" in body else None
    home = home_path(ctx)
    try:
        compute.save_clean_settings(home, enabled=enabled, force=force,
                                    orphan_age_seconds=body.get("orphan_age_seconds"))
    except (ConfigurationError, OSError) as exc:
        raise PageError(409, "config_unwritable", str(exc)) from exc
    if enabled:
        model = compute.model()
        model.home = home
        compute.start_ticker()
    return {"autoclean": compute.clean_settings(home)}
