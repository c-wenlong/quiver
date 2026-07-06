from dataclasses import dataclass

@dataclass
class Session:
    timestamp: float
    agent: str
    path: str
    title: str = ""
    session_id: str = ""
    tool_name: str = ""
