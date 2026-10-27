import math
from dataclasses import dataclass
from os import PathLike


@dataclass
class Session:
    timestamp: float
    agent: str
    path: str
    title: str = ""
    session_id: str = ""
    tool_name: str = ""
    # Where ``title`` came from: "" for the first prompt or an unknown
    # source, "rename" for a name the user set, "auto" for one the harness
    # generated. Lets the listing mark renamed sessions apart.
    title_source: str = ""
    # The Quiver account home the transcript sits under when it is not the
    # harness's default home (``~/.quiver/accounts/<vendor>/<id>/home``);
    # "" for a session in the default home. The consumer labels the row
    # with the account's name and resumes under this home, not the default.
    account_home: str = ""

    def __post_init__(self) -> None:
        # Coerce at the boundary so one malformed record cannot poison the
        # whole listing: json.loads accepts NaN/Infinity literals, sqlite
        # REAL columns can hold ±Inf, and a non-string cwd would explode at
        # ``path.replace``/``os.path.realpath``/``os.chdir`` call sites.
        # Engines and the cache reader all get the same guarantee.
        try:
            ts = float(self.timestamp)
        except (TypeError, ValueError):
            ts = 0.0
        self.timestamp = ts if math.isfinite(ts) else 0.0
        # A non-string cwd is treated as missing rather than str()'d: a
        # json number like 123 would survive as the relative path "123"
        # and a ``--here`` filter could then match it against the current
        # workspace, showing a session that belongs nowhere.
        if not isinstance(self.path, str):
            self.path = str(self.path) if isinstance(self.path, PathLike) else ""
        for field in (
            "agent",
            "title",
            "session_id",
            "tool_name",
            "title_source",
            "account_home",
        ):
            value = getattr(self, field)
            if not isinstance(value, str):
                setattr(self, field, "" if value is None else str(value))


@dataclass(frozen=True)
class ResumeCmd:
    """The ``resume`` block of one ``swe session --json`` row."""

    argv: list
    cwd: str | None
    # Env vars the harness needs to find this session, such as
    # CLAUDE_CONFIG_DIR for a session that lives in an account home.
    env: dict | None = None

    def to_json(self) -> dict:
        out = {"argv": self.argv, "cwd": self.cwd}
        if self.env:
            out["env"] = self.env
        return out


@dataclass(frozen=True)
class SessionRow:
    """One ``swe session --json`` row."""

    id: str | None
    harness: str
    agent: str
    cwd: str | None
    title: str
    title_source: str | None
    last_active_at: float
    status: str | None
    resume: dict | None
    # The Quiver account home holding the session's transcripts, or None
    # for the harness's default home.
    account_home: str | None = None

    @classmethod
    def of(cls, session, status: str, resume: dict | None) -> "SessionRow":
        # Session.timestamp is the transcript mtime in epoch ms; the contract
        # is seconds, named for what it is: when the session last moved.
        return cls(
            id=session.session_id or None,
            harness=session.tool_name,
            agent=session.agent,
            cwd=session.path or None,
            title=session.title,
            title_source=session.title_source or None,
            last_active_at=session.timestamp / 1000,
            status=status or None,
            resume=resume,
            account_home=session.account_home or None,
        )

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "harness": self.harness,
            "agent": self.agent,
            "cwd": self.cwd,
            "title": self.title,
            "title_source": self.title_source,
            "last_active_at": self.last_active_at,
            "status": self.status,
            "resume": self.resume,
            "account_home": self.account_home,
        }
