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
