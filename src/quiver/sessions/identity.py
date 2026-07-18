"""Tool identity vs launch/registry mapping.

Parsers emit a stable `tool_name` (product identity for filtering/display).
Launch and list-count keys may differ when a product is a mode of another CLI.
"""

from __future__ import annotations

# tool_name → registry/launch key used by `swe use` / `swe list`
LAUNCH_TOOL: dict[str, str] = {
    "antigravity": "gemini",
}

# tool_name → registry key for 100d session counts in `swe list`
COUNT_TO_REGISTRY: dict[str, str] = {
    "antigravity": "gemini",
}


def launch_tool(tool_name: str) -> str:
    """CLI binary / registry key to launch for a session tool_name."""
    return LAUNCH_TOOL.get(tool_name or "", tool_name or "")


def registry_tool(tool_name: str) -> str:
    """Registry key used when rolling session counts into `swe list`."""
    return COUNT_TO_REGISTRY.get(tool_name or "", tool_name or "")
