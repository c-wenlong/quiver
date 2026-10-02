import math
from dataclasses import dataclass


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
        for field in (
            "agent",
            "path",
            "title",
            "session_id",
            "tool_name",
            "title_source",
        ):
            value = getattr(self, field)
            if not isinstance(value, str):
                setattr(self, field, "" if value is None else str(value))
