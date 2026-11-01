"""How ``swe session`` reopens a session in its harness.

Pure logic: which flags a harness takes to resume, which harnesses can
only launch in the session's directory, and the ``--json`` ``resume``
payload. Presentation (the "limited resume" note) stays in commands.py.
"""

from __future__ import annotations

from swe.sessions.identity import launch_tool
from swe.sessions.models import ResumeCmd


def codex_resume_args(session_id: str) -> list[str]:
    """``codex resume <uuid>``, or nothing when there is no uuid to pass.

    Codex resumes through a subcommand rather than a flag, and its argument
    is the thread uuid or a thread name. SWE's session id is the rollout
    file's stem, which is neither, so the uuid has to be lifted out of it: a
    ``--resume rollout-2026-09-09T14-52-42-<uuid>`` never resumed anything.
    """
    from swe.sessions.parsers import codex_thread_id

    thread_id = codex_thread_id(session_id)
    return ["resume", thread_id] if thread_id else []


# Resume flag strategies keyed by tool_name (not launch key)
RESUME_FLAGS = {
    "opencode": lambda sid: ["--session", sid] if sid else [],
    "kilo": lambda sid: ["--session", sid] if sid else [],
    "claude": lambda sid: ["--resume", sid] if sid else [],
    "codex": codex_resume_args,
    "pi": lambda sid: ["--session", sid] if sid else [],
    "droid": lambda sid: ["--resume", sid] if sid else [],
    "copilot": lambda sid: ["--resume", sid] if sid else [],
    "devin": lambda sid: ["--resume", sid] if sid else [],
    "freebuff": lambda sid: ["--continue", sid] if sid else [],
    "cursor": lambda sid: ["--resume", sid] if sid else [],
    "cline": lambda sid: ["--id", sid] if sid else [],
}

LIMITED_RESUME = frozenset(
    {
        "gemini",
        "continue",
        "crush",
        "grok",
        "forge",
    }
)

# The env var each account-aware harness reads for its config home, so a
# session in an SWE account home resumes under that account, not the
# default home.
ACCOUNT_HOME_ENV = {
    "claude": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}


def account_env(session) -> dict | None:
    """``{VAR: account_home}`` for a session in an SWE account home, else None."""
    variable = ACCOUNT_HOME_ENV.get(session.tool_name)
    if variable and session.account_home:
        return {variable: session.account_home}
    return None


def launch_tool_name(tool_name: str) -> str:
    return launch_tool(tool_name)


def resume_flags(session) -> list[str]:
    """The flags that reopen ``session`` in its harness, or [] when none do."""
    builder = RESUME_FLAGS.get(session.tool_name)
    return builder(session.session_id) if builder else []


def resume_json(session) -> dict | None:
    """The ``resume`` payload for one ``swe session --json`` row, or None.

    A harness in ``LIMITED_RESUME`` only launches in the session's
    directory, and a session whose id yields no flags (a codex rollout with
    no uuid in its name) has nothing to reopen. Neither is a resume.
    """
    flags = resume_flags(session)
    if not flags:
        return None
    return ResumeCmd(
        argv=[launch_tool_name(session.tool_name), *flags],
        cwd=session.path or None,
        env=account_env(session),
    ).to_json()
