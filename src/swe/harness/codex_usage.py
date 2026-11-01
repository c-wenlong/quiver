"""Per-day token usage for one Codex account via ``codex app-server``.

Codex 0.162+ exposes ``account/usage/read`` over its JSON-RPC app-server:
spawned per call with ``CODEX_HOME`` pointing at the account's home, it
answers a ``summary`` plus ``dailyUsageBuckets``. The exchange is a fixed
handshake — ``initialize``, the ``initialized`` notification, then the
usage request — under one hard timeout, and the child is always killed.
"""

import json
import os
import select
import subprocess
import sys
import time

# The whole handshake gets one budget: a hung app-server must never
# stall `swe harness usage-daily`.
DEFAULT_TIMEOUT = 30.0


def _classify_error(error: dict | None) -> str:
    """A JSON-RPC error object to the report's status vocabulary."""
    message = str((error or {}).get("message", "")).lower()
    code = (error or {}).get("code")
    if "authentication required" in message:
        return "auth-required"
    if code == -32601 or "method not found" in message or "unknown method" in message:
        return "unsupported"
    return "unavailable"


def _read_message(proc, buffer: bytearray, deadline: float):
    """The next newline-delimited JSON object on the child's stdout.

    Notification lines and chatter are skipped by the caller via the id
    match; unparseable lines are dropped here. Returns None on timeout
    or EOF.
    """
    while True:
        newline = buffer.find(b"\n")
        if newline >= 0:
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            try:
                return json.loads(line)
            except (ValueError, RecursionError):
                continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        # Always the deadline-aware select + os.read, even after the child
        # exits: a grandchild holding stdout open would make a blocking
        # read() wait forever. EOF still ends the loop (os.read -> b"").
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            return None
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            return None
        buffer += chunk


def _wait_for(proc, buffer: bytearray, want_id: int, deadline: float):
    """The response carrying ``want_id``; other messages are ignored."""
    while True:
        message = _read_message(proc, buffer, deadline)
        if message is None:
            return None
        if message.get("id") == want_id:
            return message


def fetch_codex_usage_daily(account_dir: str | None = None, *,
                            timeout: float | None = None,
                            codex_bin: str = "codex") -> dict:
    """One account's daily token usage.

    Returns ``{"status", "summary", "days"}``: ``ok`` with a
    ``{lifetime_tokens, peak_daily_tokens, current_streak_days,
    longest_streak_days}`` summary and ``[{"date", "tokens"}]`` days;
    ``auth-required`` when the account is not signed in with ChatGPT;
    ``unsupported`` when the codex build lacks ``account/usage/read``;
    ``unavailable`` for a missing binary, timeout or any other failure.
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    home = account_dir or os.path.join(os.path.expanduser("~"), ".codex")
    env = dict(os.environ)
    env["CODEX_HOME"] = home
    try:
        proc = subprocess.Popen(
            [codex_bin, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env)
    except OSError:
        return {"status": "unavailable", "summary": None, "days": []}

    deadline = time.monotonic() + timeout
    buffer = bytearray()
    try:
        from swe import __version__

        def send(payload: dict) -> None:
            proc.stdin.write(json.dumps(payload).encode() + b"\n")
            proc.stdin.flush()

        send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"clientInfo": {"name": "quiver",
                                        "version": __version__}}})
        reply = _wait_for(proc, buffer, 0, deadline)
        if reply is None:
            return {"status": "unavailable", "summary": None, "days": []}
        if "error" in reply:
            return {"status": _classify_error(reply.get("error")),
                    "summary": None, "days": []}

        send({"jsonrpc": "2.0", "method": "initialized"})
        send({"jsonrpc": "2.0", "id": 1, "method": "account/usage/read",
              "params": None})
        reply = _wait_for(proc, buffer, 1, deadline)
        if reply is None:
            return {"status": "unavailable", "summary": None, "days": []}
        if "error" in reply:
            return {"status": _classify_error(reply.get("error")),
                    "summary": None, "days": []}

        result = reply.get("result") or {}
        raw_summary = result.get("summary") or {}
        summary = {
            "lifetime_tokens": int(raw_summary.get("lifetimeTokens")
                                   or raw_summary.get("lifetime_tokens") or 0),
            "peak_daily_tokens": int(raw_summary.get("peakDailyTokens")
                                     or raw_summary.get("peak_daily_tokens") or 0),
            "current_streak_days": int(
                raw_summary.get("currentStreakDays")
                or raw_summary.get("current_streak_days") or 0),
            "longest_streak_days": int(
                raw_summary.get("longestStreakDays")
                or raw_summary.get("longest_streak_days") or 0),
        }
        days = []
        for bucket in result.get("dailyUsageBuckets") or []:
            date = bucket.get("startDate") or bucket.get("date")
            tokens = bucket.get("tokens")
            if date is None or tokens is None:
                continue
            days.append({"date": str(date), "tokens": int(tokens)})
        return {"status": "ok", "summary": summary, "days": days}
    except (BrokenPipeError, OSError):
        return {"status": "unavailable", "summary": None, "days": []}
    except Exception:  # noqa: BLE001 — any surprise is "unavailable", not a crash
        return {"status": "unavailable", "summary": None, "days": []}
    finally:
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except OSError:
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — a wedged child is abandoned, not awaited
            pass


def print_codex_usage_daily(report: dict, account_dir: str | None,
                            err=None) -> int:
    """Human output: the summary, then the last 14 days.

    Exit code follows `swe harness usage`: ``auth-required`` is a state,
    not a failure (0); ``unsupported`` and ``unavailable`` are errors (1).
    """
    from swe.console import c

    if err is None:
        err = sys.stderr
    status = report["status"]
    if status == "auth-required":
        print("  codex: not signed in with ChatGPT — sign in to read usage")
        return 0
    if status == "unsupported":
        print(c("red", "  codex: this build cannot answer account usage — "
                "update Codex"), file=err)
        return 1
    if status != "ok":
        print(c("red", "  codex: usage could not be read"), file=err)
        return 1

    summary = report["summary"] or {}
    which = account_dir or os.path.join(os.path.expanduser("~"), ".codex")
    print(f"  {c('bold', 'codex')}  {c('dim', which)}")
    print(f"    lifetime  {summary.get('lifetime_tokens', 0):>15,} tokens")
    print(f"    peak day  {summary.get('peak_daily_tokens', 0):>15,} tokens")
    print(f"    streak    {summary.get('current_streak_days', 0)} days "
          f"(longest {summary.get('longest_streak_days', 0)})")
    days = report["days"][-14:]
    if days:
        print()
        for day in days:
            print(f"    {day['date']}  {day['tokens']:>15,}")
    return 0
