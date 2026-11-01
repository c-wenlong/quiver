"""ntfy fan-out for ``notify`` and ``approval.requested`` events.

The hub watches its own event bus; when a session flips to needs_you /
finished / error / limit, or a tmux pane shows a permission menu, one
POST lands on the configured ntfy topic. Self-hosted ntfy + the phone's
ntfy subscription is the whole push path — no app server, no APNs code
here (iOS background delivery still rides ntfy's upstream relay; the
doc records that trade-off).

Approval notifications carry ntfy ``http`` action buttons that POST
``/v1/sessions/{key}/approval`` straight back to the hub over the
tailnet — approve/deny from the lock screen, no app code. Whatever
transits the topic is readable by the ntfy host (and ntfy.sh's relay
for iOS delivery), so two rules hold on everything placed in a body:

- No full-power credential. Each notification mints a scoped approval
  token (:class:`ApprovalTokens`) bound to the session and request it
  answers, minutes-lived and single-use; the approval route accepts it
  in place of a device credential, nothing else does.
- No raw prompt text. The message runs the transcript secret-redactor
  and a length cap, same as every other text that leaves the process.

Config lives at ``remote/notify.json`` (0600)::

    {"enabled": true, "url": "https://ntfy.example.com",
     "topic": "swe-alice", "ntfy_token": null, "service_status": false}

``service_status`` (off unless true) starts the hub's status-page check:
a model provider or agent platform starting or ending an incident posts
once per incident (``swe.status.alerts``).
"""

from __future__ import annotations

import json
import queue
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from swe.github import cli as ghcli
from swe.paths import atomic_write_text, remote_dir_for
from swe.remote import pairing, sshkeys, supervise
from swe.reports import transcripts as _tr

CONFIG_FILE = "notify.json"
_NOTIFY_KIND_TITLES = {
    "needs_you": "Needs you",
    "finished": "Finished",
    "error": "Errored",
    "limit": "Limit hit",
}
# github notify events carry ``reason`` — what GitHub thinks needs you —
# mapped to the notification's wording and tag. Reasons outside the map
# can still arrive (the hub gates on cli.PUSH_REASONS, this table is the
# display twin); they read generically rather than drop mid-flight.
_GITHUB_REASONS = {
    "review_requested": ("review requested", "bell"),
    "assignee": ("assigned to you", "bell"),
    "mention": ("mentioned you", "bell"),
    "ci_fail": ("CI failed", "rotating_light"),
}
_POST_TIMEOUT_S = 5.0
# The approval prompt is a convenience, not a record — enough to decide,
# short enough that a lock screen shows it whole.
_PROMPT_CAP = 280
# The semantic decisions an action token may spend when the prompt
# listed no menu rows; a bare digit answers the menu row with that
# number (option buttons send those). ``always`` is a standing grant and
# never rides a token: whoever reads the topic must not be able to turn
# off later approvals (#1464). It still answers from a paired device.
ACTION_DECISIONS = frozenset({"allow", "deny"})
# What ``ApprovalTokens.consume`` says about a presentation.
SPEND_OK = "ok"            # live, bound, and the decision was offered
SPEND_INVALID = "invalid"  # unknown, expired, spent or mis-bound
SPEND_REFUSED = "refused"  # a real token, but not a choice it carries
# ntfy apps render at most this many action buttons; a menu with more
# trades its tail for a link to the chat, where every option answers.
_ACTION_MAX = 3
# Button labels are a glance, not a document — the chat shows them full.
_ACTION_LABEL_CAP = 40


def _config_path(home: Path | None) -> Path:
    return remote_dir_for(home) / CONFIG_FILE


def load_config(home: Path | str | None = None) -> dict:
    """notify.json contents; {} when unset or unreadable."""
    home_path = Path(home) if home is not None else None
    try:
        data = json.loads(_config_path(home_path).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(home: Path | str | None, cfg: dict) -> None:
    home_path = Path(home) if home is not None else None
    path = _config_path(home_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(cfg, indent=1), private=True)


def configure(
    home: Path | str | None,
    *,
    url: str | None = None,
    topic: str | None = None,
    ntfy_token: str | None = None,
    enabled: bool | None = None,
    service_status: bool | None = None,
) -> dict:
    """Merge a partial update into notify.json; returns the result."""
    cfg = load_config(home)
    if url is not None:
        cfg["url"] = url.rstrip("/")
    if topic is not None:
        cfg["topic"] = topic
    if ntfy_token is not None:
        cfg["ntfy_token"] = ntfy_token or None
    if enabled is not None:
        cfg["enabled"] = enabled
    if service_status is not None:
        cfg["service_status"] = service_status
    save_config(home, cfg)
    return cfg


def ready(cfg: dict) -> bool:
    """A config the notifier can actually post with. ``enabled`` without
    url+topic is off in practice — the hub treats it as disabled rather
    than parking a subscriber on the bus that nobody drains."""
    return bool(
        cfg.get("enabled") and cfg.get("url") and cfg.get("topic")
    )


def _warn(msg: str) -> None:
    """A non-fatal problem the hub operator should see: stderr lands
    in the launchd/systemd unit log."""
    print(f"swe remote notify: {msg}", file=sys.stderr)


def retire_action_credential(home: Path | None, cfg: dict) -> None:
    """Revoke the legacy ``ntfy-actions`` device row, best-effort.

    Scoped approval tokens replaced it; every copy already sent to a
    topic transited the ntfy host, so the credential is treated as
    leaked — revoked like a lost phone, then scrubbed from notify.json.
    The config fields stay until the revocation is on disk: scrubbing
    them on a failed revoke would leave the exposed credential active
    with nothing left to name it for the next attempt.
    """
    device_id = str(cfg.get("action_device_id") or "")
    if not device_id and "action_credential" not in cfg:
        return
    if device_id:
        try:
            pairing.revoke_device(home, device_id)
        except sshkeys.KeysNotSynced as exc:
            # revoke_device saves the revocation before the
            # authorized_keys rewrite, so the credential is dead
            # either way — warn about the leftover key line and scrub.
            _warn(
                f"revoked legacy action device {device_id}, but its "
                f"SSH key line remains in authorized_keys: {exc}"
            )
        except Exception as exc:
            # Nothing was revoked — keep the fields so the next start
            # retries instead of stranding a live credential.
            _warn(
                f"could not revoke legacy action device {device_id} "
                f"({exc}); will retry on next start"
            )
            return
    cfg.pop("action_device_id", None)
    cfg.pop("action_credential", None)
    try:
        save_config(home, cfg)
    except OSError as exc:
        _warn(f"could not scrub the legacy action fields from "
              f"notify.json: {exc}")


class ApprovalTokens:
    """Scoped bearer tokens for ntfy approval action buttons.

    A notification's buttons share one token bound to ``(session key,
    request_id)`` and to the decisions those buttons send: the approval
    route — and no other route — accepts it in place of a device
    credential, once, inside ``TTL_S``, for one of those decisions.
    Standing grants are never among them (:func:`spendable`). Tokens coexist per session because approvals overlap —
    a Codex app-server holds them independently — so minting or
    resolving one request never touches another's buttons.

    In-memory like the pending approvals it answers: a hub restart loses
    the tokens with the prompts, and the re-emitted ``approval.requested``
    mints fresh ones into the replacement notification.
    """

    TTL_S = 600.0
    _MAX = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, dict] = {}

    def mint(
        self,
        key: str,
        request_id: str,
        choices=(),
        now: float | None = None,
    ) -> str:
        """A token for ``(key, request_id)`` that can spend only
        ``choices``: the ``(decision, label)`` pairs its notification
        shows as buttons. A choice that would outlive this prompt (see
        :func:`spendable`) is dropped here, so a token never carries
        one, whatever the caller passes."""
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(24)
        allowed = frozenset(
            d for d, label in choices if spendable(d, label)
        )
        with self._lock:
            self._prune(now)
            # A re-mint of the same request replaces its token — the
            # newest notification's buttons answer. A *different*
            # pending request keeps its token: overlapping approvals
            # must not kill each other's buttons.
            self._drop(key, request_id)
            if len(self._tokens) >= self._MAX:
                oldest = min(
                    self._tokens,
                    key=lambda t: self._tokens[t]["expires_at"],
                )
                del self._tokens[oldest]
            self._tokens[token] = {
                "key": key,
                "request_id": request_id,
                "choices": allowed,
                "expires_at": now + self.TTL_S,
            }
        return token

    def consume(
        self,
        token: str,
        key: str,
        request_id: str,
        decision,
        now: float | None = None,
    ) -> str:
        """Spend ``token`` on ``decision``. One presentation, good or
        bad, burns it.

        ``SPEND_OK`` iff it was live, bound to ``(key, request_id)`` and
        minted with ``decision`` among its choices; ``SPEND_REFUSED``
        when the binding held but the decision is not one it carries;
        ``SPEND_INVALID`` otherwise. An entry without ``choices`` (the
        shape tokens had before they carried them) spends nothing."""
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            entry = self._tokens.pop(token, None)
        if (
            entry is None
            or entry["key"] != key
            or entry["request_id"] != request_id
        ):
            return SPEND_INVALID
        allowed = entry.get("choices") or frozenset()
        if isinstance(decision, str) and decision in allowed:
            return SPEND_OK
        return SPEND_REFUSED

    def drop(self, key: str, request_id: str) -> None:
        """Retire the tokens answering ``request_id`` on ``key`` — that
        approval resolved, so its buttons are dead either way. Other
        pending requests on the session keep working."""
        with self._lock:
            self._drop(key, request_id)

    def _drop(self, key: str, request_id: str) -> None:
        self._tokens = {
            t: e for t, e in self._tokens.items()
            if not (e["key"] == key and e["request_id"] == request_id)
        }

    def _prune(self, now: float) -> None:
        expired = [
            t for t, e in self._tokens.items() if e["expires_at"] <= now
        ]
        for t in expired:
            del self._tokens[t]


def spendable(decision, label: str = "") -> bool:
    """True when an ntfy action token may answer with ``decision``.

    Only a choice confined to the prompt on screen qualifies: ``allow``
    (the one-shot grant), ``deny``, or a menu row's number whose label
    is not a standing grant. Always-allow, don't-ask-again, session-wide
    and bypass/yolo rows need a paired device (#1464).
    """
    if not isinstance(decision, str):
        return False
    if decision in ACTION_DECISIONS:
        return True
    if decision.isdigit() and len(decision) <= 2:
        return not supervise.standing_grant(label)
    return False


class Notifier:
    """Bus subscriber → ntfy POSTs. Best-effort; failures drop silently.

    ``base_url`` is the hub's own reachable base (its Tailscale bind) —
    action buttons point back at it so a tap on the phone hits the same
    host the app talks to.
    """

    def __init__(self, state, base_url: str, *, subscribe_key: str) -> None:
        self._state = state
        self._base = base_url.rstrip("/")
        # One stderr line per outage, not one per push.
        self._post_warned = False
        self._subscribe_key = subscribe_key
        self._stop = state._stop
        self._q: queue.Queue | None = None

    def subscribe(self) -> None:
        """Claim the bus queue. HubState.start() calls this *before*
        spawning the producer threads so an approval already on screen
        at boot lands in the queue instead of racing the subscribe."""
        if self._q is None:
            self._q = self._state.bus.subscribe(self._subscribe_key)

    def _unsubscribe(self) -> None:
        if self._q is not None:
            self._state.bus.unsubscribe(self._q)
            self._q = None

    def run(self) -> None:
        cfg = load_config(self._state.home)
        if not ready(cfg):
            # start() subscribed before the config check could re-run:
            # leave nothing on the bus that would fill and drop.
            self._unsubscribe()
            return
        # start() already tried this; a failed revoke kept the fields
        # so the retry here can still finish it.
        retire_action_credential(self._state.home, cfg)
        self.subscribe()
        q = self._q
        try:
            while not self._stop.is_set():
                try:
                    ev = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self._handle(ev, cfg)
                except Exception:
                    continue
        finally:
            self._unsubscribe()

    def _handle(self, ev: dict, cfg: dict) -> None:
        etype = ev.get("type")
        data = ev.get("data") or {}
        key = str(ev.get("session") or "")
        title = self._session_title(key)
        if etype == "approval.requested":
            rid = str(data.get("request_id") or "")
            prompt = _tr._redact_secrets(
                str(data.get("prompt") or "Approval requested")
            )[:_PROMPT_CAP]
            chat_url = (
                f"{self._base}/#/sessions?chat="
                f"{urllib.parse.quote(key, safe='')}"
            )
            options = [
                (str(o.get("n") or ""), str(o.get("label") or ""))
                for o in (data.get("options") or [])
                if isinstance(o, dict)
            ]
            options = [(n, l) for n, l in options if n.isdigit() and l]
            if options:
                # One button per row a token may answer, while they fit.
                # Standing grants (always allow, don't ask again, bypass)
                # never get a button: a topic reader must not be able to
                # turn off later approvals (#1464). Any row left out
                # trades the last slot for a link to the chat, where
                # every option answers. The push is a shortcut, never
                # the only way in.
                safe = [(n, l) for n, l in options if spendable(n, l)]
                if len(safe) == len(options) and len(safe) <= _ACTION_MAX:
                    shown = safe
                else:
                    # Trimmed: yes/no rows before "Edit command" and
                    # the like, so the push keeps both answers.
                    picked = sorted(
                        safe, key=lambda o: not supervise.decides(o[1])
                    )[:_ACTION_MAX - 1]
                    shown = [o for o in safe if o in picked]
                if len({l[:_ACTION_LABEL_CAP] for _n, l in shown}) \
                        != len(shown):
                    # Two rows truncate to the same button text — a tap
                    # could answer either, so neither may be a button.
                    shown = []
            else:
                shown = [("allow", "Allow"), ("deny", "Deny")]
            # The token spends exactly the buttons this push shows, and
            # mint drops any standing grant whatever it is handed.
            token = self._state.approval_tokens.mint(key, rid, shown)
            actions = [
                self._action(key, rid, n, l[:_ACTION_LABEL_CAP], token)
                for n, l in shown
            ]
            if len(shown) < len(options):
                actions.append({
                    "action": "view",
                    "label": f"All {len(options)} options…",
                    "url": chat_url,
                })
            self._post(cfg, {
                "topic": cfg["topic"],
                "title": f"{title}: approval needed",
                "message": prompt,
                "priority": 5,
                "tags": ["lock"],
                "click": chat_url,
                "actions": actions,
            })
            return
        if etype != "notify":
            return
        kind = str(data.get("kind") or "")
        if kind == "github":
            self._github(cfg, data)
            return
        if kind == "service":
            self._service(cfg, data)
            return
        label = _NOTIFY_KIND_TITLES.get(kind)
        if label is None:
            return
        priority = 4 if kind in ("needs_you", "error", "limit") else 3
        # A limit carries the harness's own line ("You've hit your
        # session limit · resets 9:30am"), which says when work resumes.
        message = ""
        if kind == "limit":
            message = _tr._redact_secrets(
                str(data.get("message") or ""))[:_PROMPT_CAP]
        tag = {"error": "rotating_light", "limit": "hourglass"}.get(
            kind, "bell")
        self._post(cfg, {
            "topic": cfg["topic"],
            "title": f"{title}: {label.lower()}",
            "message": message,
            "priority": priority,
            "tags": [tag],
        })

    def _github(self, cfg: dict, data: dict) -> None:
        """A GitHub thread just turned unread: ``repo: why`` in the
        title, the subject in the body, the URL as the tap target. The
        title is notification text, not a prompt — it runs the same
        secret-redactor as everything else that leaves the process.
        """
        # The hub only emits the push reasons; a stray event for
        # anything else (a comment on a subscribed thread) drops here
        # too rather than ringing the phone.
        if str(data.get("reason") or "") not in ghcli.PUSH_REASONS:
            return
        repo = str(data.get("repo") or "")
        label, tag = _GITHUB_REASONS.get(
            str(data.get("reason") or ""), ("needs you", "bell"))
        url = str(data.get("url") or "")
        payload = {
            "topic": cfg["topic"],
            "title": f"{repo}: {label}" if repo else f"GitHub: {label}",
            "message": _tr._redact_secrets(
                str(data.get("title") or ""))[:_PROMPT_CAP],
            "priority": 4,
            "tags": [tag],
        }
        if url.startswith("https://") or url.startswith("http://"):
            payload["click"] = url
            payload["actions"] = [
                {"action": "view", "label": "Open", "url": url}]
        self._post(cfg, payload)

    def _service(self, cfg: dict, data: dict) -> None:
        """A model provider or agent platform started or ended an
        incident (hub-local, from ``swe.status.alerts``): the hub
        already wrote the title and body; the status page is the tap
        target."""
        started = data.get("change") == "started"
        payload = {
            "topic": cfg["topic"],
            "title": str(data.get("title") or "Service status")[:_PROMPT_CAP],
            "message": str(data.get("message") or "")[:_PROMPT_CAP],
            "priority": 4 if started else 3,
            "tags": ["warning" if started else "white_check_mark"],
        }
        url = str(data.get("url") or "")
        if url.startswith("https://") or url.startswith("http://"):
            payload["click"] = url
        self._post(cfg, payload)

    def _session_title(self, key: str) -> str:
        """The notification title's prefix. A session title is often
        the first prompt, so it runs the secret-redactor, before the
        cut so a half-truncated token can't slip past the patterns."""
        row = self._state.catalog.row_for(key) if key else None
        if row:
            title = str(row.get("title") or "").strip()
            if title:
                return _tr._redact_secrets(title)[:60]
            cwd = str(row.get("cwd") or "")
            if cwd:
                return cwd.rstrip("/").rsplit("/", 1)[-1] or cwd
        return "SWE"

    def _action(
        self, key: str, rid: str, decision: str, label: str, token: str
    ) -> dict:
        return {
            "action": "http",
            "label": label,
            "method": "POST",
            "url": (f"{self._base}/v1/sessions/"
                    f"{urllib.parse.quote(key, safe='')}/approval"),
            "headers": {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            "body": json.dumps(
                {"request_id": rid, "decision": decision}
            ),
        }

    def _post(self, cfg: dict, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            str(cfg["url"]), data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        token = cfg.get("ntfy_token")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        # The bundled python.org build has no CA store: agent._urlopen
        # retries a certificate failure against the OS bundle.
        from swe.remote.agent import _urlopen

        try:
            with _urlopen(req, _POST_TIMEOUT_S):
                pass
        except Exception as exc:
            if not self._post_warned:
                self._post_warned = True
                _warn(f"push failed ({exc}); later failures stay quiet "
                      "until one succeeds")
            raise
        self._post_warned = False
