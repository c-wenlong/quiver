"""The fix for each way a usage read can fail.

``swe harness usage --json`` emits a ``remedy`` beside every non-ok
``status``, ``swe list --usage --json`` puts the same object on a row's
``issue``, and ``swe doctor`` renders the same rows for problems the app
recorded in its IssueStore, so the CLI and the app always agree on what
to do next.

Same philosophy as ``installers.py``'s ``INSTALLERS``/``WEBSITES``: a
module-level table keyed by registry name, an absent key meaning "no
specific entry", with ``_DEFAULT`` covering every other harness.
"""

from __future__ import annotations

import shlex

# The status vocabulary the JSON boundary and the app's IssueStore share.
# ``ok`` never produces an issue; the rest always carry a remedy.
OK = "ok"
SIGNED_OUT = "signed-out"
AUTH_REQUIRED = "auth-required"
PERMISSION_REQUIRED = "permission-required"
UNAVAILABLE = "unavailable"
TIMEOUT = "timeout"

# The macOS System Settings pane for Files & Folders access: the fix when
# a credential file exists but macOS refuses to let swe read it.
_PRIVACY_FILES_URL = (
    "x-apple.systempreferences:com.apple.preference.security"
    "?Privacy_FilesAndFolders"
)

_DEFAULT = "_default"

# How many polls in a row an outage (``unavailable``/``timeout``) must
# fail before Doctor lists it: one failed read is a blip the card already
# shows, three at the app's five-minute cadence is a pattern. The app's
# ``IssuesModel.persistentPolls`` uses the same number, since both read
# ``occurrences`` from the same issues file.
PERSISTENT_POLLS = 3


def _login_rows(name: str, command: str, how: str) -> dict[str, dict]:
    """The signed-out and auth-required rows for a harness whose fix is
    one login command: the same command renews a session it rejected."""
    return {
        SIGNED_OUT: {
            "kind": "relogin",
            "title": f"Sign in to {name}",
            "summary": f"{name} isn't signed in",
            "steps": [f"{how}, then refresh."],
            "command": command,
        },
        AUTH_REQUIRED: {
            "kind": "relogin",
            "title": f"Sign in to {name} again",
            "summary": f"{name}'s stored login was rejected",
            "steps": [f"{how} to renew the login SWE reads, then refresh."],
            "command": command,
        },
    }

# harness → status → remedy. Each remedy says what to show (``summary``,
# the problem as one line; ``title``, the fix's name), what to do
# (``steps``, numbered in the Fix sheet), and what to offer:
# ``command`` for a copy/run-in-terminal row, ``url`` for an Open button.
# ``kind`` tells the app which affordance fits: ``run-command``,
# ``relogin``, ``grant-permission`` or ``retry``.
REMEDIES: dict[str, dict[str, dict]] = {
    "cursor": {
        SIGNED_OUT: {
            "kind": "relogin",
            "title": "Sign in to Cursor",
            "summary": "Cursor isn't signed in on this account",
            "steps": [
                "Sign in in the Cursor app, or run `cursor-agent login` "
                "for the CLI's own session.",
            ],
            "command": "cursor-agent login",
        },
        AUTH_REQUIRED: {
            "kind": "run-command",
            "title": "Refresh Cursor's session",
            "summary": "Cursor's stored login is stale",
            "steps": [
                "Run `cursor-agent` once — it renews the login SWE reads.",
                "If that does not help, sign in again in the Cursor app.",
            ],
            "command": "cursor-agent status",
        },
        PERMISSION_REQUIRED: {
            "kind": "grant-permission",
            "title": "Let SWE read Cursor's data",
            "summary": "macOS is blocking Cursor's session files",
            "steps": [
                "Open System Settings → Privacy & Security → Files and "
                "Folders.",
                "Grant your terminal (or SWE) access to Cursor's data, "
                "then refresh.",
            ],
            "url": _PRIVACY_FILES_URL,
        },
        UNAVAILABLE: {
            "kind": "retry",
            "title": "Try fetching Cursor usage again",
            "summary": "Cursor's usage couldn't be fetched",
            "steps": [
                "Check the network connection, then refresh — nothing is "
                "wrong with the sign-in.",
            ],
        },
        TIMEOUT: {
            "kind": "retry",
            "title": "Try fetching Cursor usage again",
            "summary": "Cursor took too long to answer",
            "steps": [
                "Refresh to try again — nothing is wrong with the sign-in.",
            ],
        },
    },
    "devin": {
        SIGNED_OUT: {
            "kind": "relogin",
            "title": "Sign in to Devin",
            "summary": "Devin isn't signed in on this account",
            "steps": [
                "Run `devin auth login` to write a fresh API key to "
                "~/.local/share/devin/credentials.toml.",
            ],
            "command": "devin auth login",
        },
        AUTH_REQUIRED: {
            "kind": "relogin",
            "title": "Renew Devin's API key",
            "summary": "Devin's API key was rejected",
            "steps": [
                "Run `devin auth login` to write a fresh API key to "
                "~/.local/share/devin/credentials.toml.",
            ],
            "command": "devin auth login",
        },
        PERMISSION_REQUIRED: {
            "kind": "grant-permission",
            "title": "Let SWE read Devin's credentials",
            "summary": "macOS is blocking Devin's credentials",
            "steps": [
                "Open System Settings → Privacy & Security → Files and "
                "Folders.",
                "Grant your terminal (or SWE) access to "
                "~/.local/share/devin, then refresh.",
            ],
            "url": _PRIVACY_FILES_URL,
        },
        UNAVAILABLE: {
            "kind": "retry",
            "title": "Try fetching Devin usage again",
            "summary": "Devin's usage couldn't be fetched",
            "steps": [
                "Check the network connection, then refresh — nothing is "
                "wrong with the sign-in.",
            ],
        },
        TIMEOUT: {
            "kind": "retry",
            "title": "Try fetching Devin usage again",
            "summary": "Devin took too long to answer",
            "steps": [
                "Refresh to try again — nothing is wrong with the sign-in.",
            ],
        },
    },
    # The rest fix a stale or missing login with their own CLI's login
    # command (the web app's LOGIN_COMMANDS); every other status takes
    # the generic row.
    "claude": _login_rows(
        "Claude", "claude auth login", "Run `claude auth login`"),
    "codex": _login_rows(
        "Codex", "codex login", "Run `codex login`"),
    # swe reads Copilot's limits with the GitHub CLI's token.
    "copilot": _login_rows(
        "Copilot", "gh auth login",
        "Run `gh auth login` with the GitHub account that has Copilot"),
    # Droid signs in from inside its own TUI.
    "droid": _login_rows(
        "Droid", "droid", "Run `droid` and sign in when it asks"),
    "freebuff": _login_rows(
        "Freebuff", "freebuff login", "Run `freebuff login`"),
    _DEFAULT: {
        SIGNED_OUT: {
            "kind": "relogin",
            "title": "Sign in",
            "summary": "{name} isn't signed in",
            "steps": ["Sign in to {name} again."],
        },
        AUTH_REQUIRED: {
            "kind": "relogin",
            "title": "Sign in again",
            "summary": "{name}'s sign-in needs renewing",
            "steps": ["Sign in to {name} again, then refresh."],
        },
        PERMISSION_REQUIRED: {
            "kind": "grant-permission",
            "title": "Let SWE read {name}'s files",
            "summary": "macOS is blocking {name}'s files",
            "steps": [
                "Open System Settings → Privacy & Security → Files and "
                "Folders.",
                "Grant your terminal (or SWE) access, then refresh.",
            ],
            "url": _PRIVACY_FILES_URL,
        },
        UNAVAILABLE: {
            "kind": "retry",
            "title": "Try again",
            "summary": "{name}'s usage couldn't be fetched",
            "steps": ["Check the connection, then refresh."],
        },
        TIMEOUT: {
            "kind": "retry",
            "title": "Try again",
            "summary": "{name} took too long to answer",
            "steps": ["Refresh to try again."],
        },
    },
}


def remedy_for(name: str, status: str, display_name: str | None = None) -> dict | None:
    """The remedy for a harness's failure status, or None for ``ok``.

    Falls back to the generic ``_DEFAULT`` row with the harness's display
    name filled in, so a harness never surfaces a status with no fix.
    """
    if status in (None, "", OK, "no-sub"):
        return None
    row = REMEDIES.get(name, {}).get(status) or REMEDIES[_DEFAULT].get(status)
    if row is None:
        return None
    label = display_name or name
    return {
        key: ([step.format(name=label) for step in value]
              if key == "steps" else
              value.format(name=label) if isinstance(value, str) else value)
        for key, value in row.items()
    }


def remedy_for_account(name: str, status: str, account_dir: str) -> dict | None:
    """The remedy for an extra account's failure.

    The generic remedy's commands fix the default login, which the extra
    account never reads: Devin's CLI writes ``XDG_DATA_HOME``'s
    credentials.toml and Cursor's extra accounts sign in through a
    desktop app pointed at their own ``--user-data-dir``. Retarget those
    at the account's data dir so the offered fix reaches the account
    that failed. Only a login fix is retargeted: an outage or a
    permission grant has the same fix for every account.

    The commands are offered to copy or run in a terminal, so the account
    dir is shell-quoted: a ``"``, ``$`` or backtick in it must stay part
    of the path rather than end the string or run something.
    """
    remedy = remedy_for(name, status)
    if remedy is None:
        return None
    remedy = dict(remedy)
    if status not in (SIGNED_OUT, AUTH_REQUIRED):
        return remedy
    if name == "devin":
        remedy.update({
            "summary": ("This account isn't signed in to Devin"
                        if status == SIGNED_OUT
                        else "This account's Devin API key was rejected"),
            "command": f"XDG_DATA_HOME={shlex.quote(account_dir)} devin auth login",
            "steps": [
                "Run `devin auth login` with XDG_DATA_HOME pointed at "
                "this account's data dir so its credentials file gets "
                "the key."
            ],
        })
    elif name == "cursor":
        remedy.update({
            "kind": "open-app",
            "summary": ("This account isn't signed in to Cursor"
                        if status == SIGNED_OUT
                        else "This account's Cursor login is stale"),
            "command": ('open -na "/Applications/Cursor.app" --args '
                        f"--user-data-dir={shlex.quote(account_dir)}"),
            "steps": [
                "Open Cursor with this account's own data dir and sign "
                "in again — the shared `cursor-agent` login never "
                "reaches it."
            ],
        })
    return remedy
