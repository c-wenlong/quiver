"""MCP server format adapters.

This module defines tool/provider-specific MCP format handlers and conversion helpers.

Canonical server shape used by handlers:
  {
    "command": str,        # optional
    "args": list[str],     # optional
    "env": dict[str, str], # optional
    "url": str,            # optional
    "headers": dict[str, str],  # optional
  }
"""


class McpFormatHandler:
    """Adapter interface for tool-specific MCP server formats."""

    def parse(self, raw: dict) -> dict:
        raise NotImplementedError

    def emit(self, canonical: dict) -> dict:
        raise NotImplementedError


class TemplateMcpFormatHandler(McpFormatHandler):
    """Template for adding a new MCP format handler.

    How to use:
      1) Copy/rename this class for your tool/provider format.
      2) Implement parse() and emit() mappings.
      3) Register it in _FORMAT_MCP_HANDLERS.
      4) In mcp.py MCP_CONFIG_MAP, set tool entry: {"format": "your-format"}.

    Notes:
      - parse() should be tolerant and return {} for invalid input.
      - emit() should only output keys supported by that target format.
      - Preserve unknown/unsupported fields only if your format requires them.
    """

    def parse(self, raw: dict) -> dict:
        # raw tool format -> canonical shape
        if not isinstance(raw, dict):
            return {}

        out = {}

        # command/args examples:
        # command = raw.get("command")
        # if isinstance(command, str):
        #     out["command"] = command
        # if isinstance(raw.get("args"), list):
        #     out["args"] = list(raw["args"])

        # env examples:
        # if isinstance(raw.get("env"), dict):
        #     out["env"] = dict(raw["env"])
        # elif isinstance(raw.get("environment"), dict):
        #     out["env"] = dict(raw["environment"])

        # url/headers examples:
        # if raw.get("url"):
        #     out["url"] = raw["url"]
        # if isinstance(raw.get("headers"), dict):
        #     out["headers"] = dict(raw["headers"])

        return out

    def emit(self, canonical: dict) -> dict:
        # canonical shape -> raw tool format
        if not isinstance(canonical, dict):
            return {}

        out = {}

        # Example standard mapping:
        # if canonical.get("command"):
        #     out["command"] = canonical["command"]
        # if canonical.get("args"):
        #     out["args"] = list(canonical["args"])
        # if canonical.get("env"):
        #     out["env"] = dict(canonical["env"])
        # if canonical.get("url"):
        #     out["url"] = canonical["url"]
        # if canonical.get("headers"):
        #     out["headers"] = dict(canonical["headers"])

        return out


class StandardMcpFormatHandler(McpFormatHandler):
    """Format used by most tools (mcpServers)."""

    def parse(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}

        out = {}
        command = raw.get("command")
        if isinstance(command, list):
            out["command"] = command[0] if command else ""
            out["args"] = command[1:]
        elif isinstance(command, str):
            out["command"] = command

        if "args" in raw and isinstance(raw.get("args"), list):
            out["args"] = list(raw["args"])

        if "env" in raw and isinstance(raw.get("env"), dict):
            out["env"] = dict(raw["env"])
        elif "environment" in raw and isinstance(raw.get("environment"), dict):
            out["env"] = dict(raw["environment"])

        if raw.get("url"):
            out["url"] = raw["url"]
        if "headers" in raw and isinstance(raw.get("headers"), dict):
            out["headers"] = dict(raw["headers"])

        return out

    def emit(self, canonical: dict) -> dict:
        if not isinstance(canonical, dict):
            return {}
        out = {}
        if canonical.get("command"):
            out["command"] = canonical["command"]
        if canonical.get("args"):
            out["args"] = list(canonical["args"])
        if canonical.get("env"):
            out["env"] = dict(canonical["env"])
        if canonical.get("url"):
            out["url"] = canonical["url"]
        if canonical.get("headers"):
            out["headers"] = dict(canonical["headers"])
        return out


class CopilotMcpFormatHandler(StandardMcpFormatHandler):
    """GitHub Copilot CLI MCP format.

    Copilot validates stdio servers as either `{command, args}` or `{url}`.
    Unlike most standard-format tools, it rejects command-only servers when
    `args` is omitted, so emit an empty list for every command server.
    """

    def emit(self, canonical: dict) -> dict:
        out = super().emit(canonical)
        if out.get("command") and "args" not in out:
            out["args"] = []
        return out


class OpencodeMcpFormatHandler(McpFormatHandler):
    """opencode format (mcp key, command list, environment, type)."""

    def parse(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}

        out = {}
        command = raw.get("command")
        if isinstance(command, list):
            out["command"] = command[0] if command else ""
            out["args"] = command[1:]
        elif isinstance(command, str):
            out["command"] = command

        if "args" in raw and isinstance(raw.get("args"), list):
            out["args"] = list(raw["args"])

        if "environment" in raw and isinstance(raw.get("environment"), dict):
            out["env"] = dict(raw["environment"])
        elif "env" in raw and isinstance(raw.get("env"), dict):
            out["env"] = dict(raw["env"])

        if raw.get("url"):
            out["url"] = raw["url"]
        if "headers" in raw and isinstance(raw.get("headers"), dict):
            out["headers"] = dict(raw["headers"])

        return out

    def emit(self, canonical: dict) -> dict:
        if not isinstance(canonical, dict):
            return {}

        out = {}

        # Remote-only server
        if canonical.get("url") and not canonical.get("command"):
            out["url"] = canonical["url"]
            if canonical.get("headers"):
                out["headers"] = dict(canonical["headers"])
            out["type"] = "remote"
            return out

        # Local/stdio server
        if canonical.get("command"):
            out["command"] = [canonical["command"]] + list(canonical.get("args", []))
            if canonical.get("env"):
                out["environment"] = dict(canonical["env"])
            if canonical.get("url"):
                out["url"] = canonical["url"]
            if canonical.get("headers"):
                out["headers"] = dict(canonical["headers"])
            out["enabled"] = True
            out["type"] = "local"
            return out

        return {}


_FORMAT_MCP_HANDLERS = {
    "standard": StandardMcpFormatHandler(),
    "copilot": CopilotMcpFormatHandler(),
    "opencode": OpencodeMcpFormatHandler(),
}


def register_format_handler(format_name: str, handler: McpFormatHandler):
    """Register/override an MCP format handler by name."""
    _FORMAT_MCP_HANDLERS[format_name] = handler


def get_format_handler(format_name: str) -> McpFormatHandler:
    return _FORMAT_MCP_HANDLERS.get(format_name, _FORMAT_MCP_HANDLERS["standard"])


def normalize_server(cfg: dict) -> dict:
    """Normalize mixed tool formats to canonical standard MCP shape."""
    if isinstance(cfg, dict) and (
        isinstance(cfg.get("command"), list)
        or "environment" in cfg
        or cfg.get("type") in ("local", "remote", "http")
    ):
        return get_format_handler("opencode").parse(cfg)
    return get_format_handler("standard").parse(cfg)


def convert_server_between_formats(cfg: dict, source_format: str, target_format: str) -> dict:
    """Convert server config between source/target MCP formats."""
    source_handler = get_format_handler(source_format)
    target_handler = get_format_handler(target_format)
    canonical = source_handler.parse(cfg)
    return target_handler.emit(canonical)


def get_conversion_issues(cfg: dict, source_format: str, target_format: str) -> list[str]:
    """Return lossy-conversion issues from source format -> target format.

    Strategy:
      source raw -> source canonical -> target raw -> target canonical
      Any canonical field loss/change in this roundtrip is reported.
    """
    if not isinstance(cfg, dict):
        return ["source config is not a JSON object"]

    source_handler = get_format_handler(source_format)
    target_handler = get_format_handler(target_format)

    source_canonical = source_handler.parse(cfg)
    if cfg and not source_canonical:
        return ["source config could not be parsed into canonical format"]

    emitted = target_handler.emit(source_canonical)
    roundtrip_canonical = target_handler.parse(emitted)

    issues = []
    all_keys = sorted(set(source_canonical.keys()) | set(roundtrip_canonical.keys()))
    for k in all_keys:
        if k not in roundtrip_canonical:
            issues.append(f"lost field '{k}'")
        elif k not in source_canonical:
            issues.append(f"added field '{k}'")
        elif source_canonical[k] != roundtrip_canonical[k]:
            issues.append(f"changed field '{k}'")

    return issues
