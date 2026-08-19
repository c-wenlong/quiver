"""General user configuration for Quiver.

The configuration file is deliberately small and credential-free.  Harness
authentication remains owned by the harness itself (or the environment).
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from quiver.paths import CONFIG_DIR, CONFIG_FILE


# CONFIG_FILE now lives in paths.py so the layout has one owner.
SUPPORTED_REPORT_HARNESSES = frozenset({"claude", "codex"})

DEFAULT_CONFIG: dict[str, Any] = {
    "report": {
        "session": {"harness": None, "model": None, "args": []},
        "writer": {"harness": None, "model": None, "args": []},
        "max_workers": 3,
        "max_summary_calls": 20,
        "max_estimated_input_tokens": 200_000,
        "batch": {"max_sessions": 25, "max_chars": 60_000},
        "runner_timeout_seconds": 300,
        "writer_timeout_seconds": 600,
        "transcript": {
            "max_chars": 240_000,
            "max_message_chars": 40_000,
        },
    }
}


class ConfigurationError(ValueError):
    """Base class for invalid or unsafe configuration operations."""


class CorruptConfigurationError(ConfigurationError):
    """Raised when an operation would overwrite malformed configuration."""


@dataclass(frozen=True)
class ConfigIssue:
    """One actionable validation result."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


_MISSING = object()
_SECRET_KEY_PARTS = {
    "apikey",
    "api_key",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
_SECRET_ARG_MARKERS = (
    "--api-key",
    "--apikey",
    "--password",
    "--secret",
    "--token",
    "api_key=",
    "apikey=",
    "password=",
    "secret=",
    "token=",
)


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(value))


def _config_path(path: Path | None) -> Path:
    return CONFIG_FILE if path is None else path


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load raw configuration, returning an empty mapping when unreadable.

    The file is never changed here.  Callers that need to distinguish a
    missing file from malformed JSON can use :func:`check_config`.
    """

    path = _config_path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return _copy_mapping(data) if isinstance(data, dict) else {}


def _assert_existing_file_is_safe(path: Path) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptConfigurationError(
            f"Refusing to overwrite malformed configuration at {path}"
        ) from exc
    if not isinstance(data, dict):
        raise CorruptConfigurationError(
            f"Refusing to overwrite non-object configuration at {path}"
        )


def save_config(config: Mapping[str, Any], path: Path | None = None) -> None:
    """Validate and atomically save configuration without exposing secrets."""

    path = _config_path(path)
    raw = _copy_mapping(config)
    issues = validate_config(raw)
    if issues:
        raise ConfigurationError("; ".join(str(issue) for issue in issues))
    _assert_existing_file_is_safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _parts(key: str) -> list[str]:
    parts = key.split(".") if key else []
    if not parts or any(not part for part in parts):
        raise ConfigurationError("Configuration keys must be non-empty dotted paths")
    return parts


def get_value(
    config: Mapping[str, Any], key: str, default: Any = None
) -> Any:
    """Return a value addressed by a dotted key."""

    current: Any = config
    for part in _parts(key):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return copy.deepcopy(current)


def set_value(config: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """Return a copy with a dotted key set."""

    result = _copy_mapping(config)
    current = result
    parts = _parts(key)
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            current[part] = {}
        elif not isinstance(existing, dict):
            raise ConfigurationError(f"Cannot set {key}: {part} is not an object")
        current = current[part]
    current[parts[-1]] = copy.deepcopy(value)
    return result


def unset_value(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return a copy without a dotted key, pruning empty parent objects."""

    result = _copy_mapping(config)
    current: dict[str, Any] = result
    parents: list[tuple[dict[str, Any], str]] = []
    parts = _parts(key)
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return result
        parents.append((current, part))
        current = child
    current.pop(parts[-1], None)
    for parent, part in reversed(parents):
        child = parent.get(part)
        if isinstance(child, dict) and not child:
            parent.pop(part)
        else:
            break
    return result


# Friendly aliases for command integration.
dotted_get = get_value
dotted_set = set_value
dotted_unset = unset_value


def parse_config_value(text: str) -> Any:
    """Parse JSON scalars and arrays; keep non-JSON input as plain text."""

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    # Objects make shell updates too easy to misapply.  Nested configuration is
    # addressed with dotted keys; scalars and arrays are the supported values.
    return text if isinstance(value, dict) else value


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = _copy_mapping(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_config(
    config: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge defaults, saved values, then per-command overrides."""

    resolved = _deep_merge(DEFAULT_CONFIG, config or {})
    return _deep_merge(resolved, overrides or {})


def load_resolved_config(
    path: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load saved configuration and apply defaults and command overrides."""

    return resolve_config(load_config(path), overrides)


merge_overrides = resolve_config


def _looks_like_secret_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return normalized in _SECRET_KEY_PARTS or normalized.endswith(
        ("_api_key", "_apikey", "_credential", "_password", "_secret", "_token")
    )


def _looks_like_secret_arg(argument: str) -> bool:
    lowered = argument.lower()
    for marker in _SECRET_ARG_MARKERS:
        if marker.endswith("="):
            if marker in lowered:
                return True
        elif lowered == marker or lowered.startswith(f"{marker}="):
            return True
    return False


def _secret_issues(value: Any, path: str = "") -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _looks_like_secret_key(key):
                issues.append(ConfigIssue(child_path, "credentials must not be stored"))
            issues.extend(_secret_issues(child, child_path))
    return issues


def _positive_int(config: Mapping[str, Any], key: str, issues: list[ConfigIssue]) -> None:
    value = get_value(config, key, _MISSING)
    if value is _MISSING:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        issues.append(ConfigIssue(key, "must be a positive integer"))


def validate_config(config: Mapping[str, Any]) -> list[ConfigIssue]:
    """Return all configuration problems without raising."""

    if not isinstance(config, Mapping):
        return [ConfigIssue("", "configuration must be a JSON object")]
    issues = _secret_issues(config)

    for role in ("session", "writer"):
        prefix = f"report.{role}"
        harness = get_value(config, f"{prefix}.harness", _MISSING)
        if harness is not _MISSING and harness is not None:
            if harness not in SUPPORTED_REPORT_HARNESSES:
                issues.append(ConfigIssue(
                    f"{prefix}.harness", "must be claude or codex"
                ))
        model = get_value(config, f"{prefix}.model", _MISSING)
        if model is not _MISSING and model is not None:
            if not isinstance(model, str) or not model.strip():
                issues.append(ConfigIssue(f"{prefix}.model", "must be a non-empty string"))
        args = get_value(config, f"{prefix}.args", _MISSING)
        if args is not _MISSING:
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                issues.append(ConfigIssue(f"{prefix}.args", "must be a list of strings"))
            elif any(_looks_like_secret_arg(arg) for arg in args):
                issues.append(ConfigIssue(
                    f"{prefix}.args", "credential-bearing arguments are not allowed"
                ))

    for key in (
        "report.max_workers",
        "report.max_summary_calls",
        "report.max_estimated_input_tokens",
        "report.batch.max_sessions",
        "report.batch.max_chars",
        "report.runner_timeout_seconds",
        "report.writer_timeout_seconds",
        "report.transcript.max_chars",
        "report.transcript.max_message_chars",
    ):
        _positive_int(config, key, issues)
    return issues


def check_config(path: Path | None = None) -> list[ConfigIssue]:
    """Load and validate a file, distinguishing malformed JSON from absence."""

    path = _config_path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [ConfigIssue("", f"cannot read valid JSON from {path}: {exc}")]
    if not isinstance(data, dict):
        return [ConfigIssue("", "configuration must be a JSON object")]
    return validate_config(data)


def select_editor(env: Mapping[str, str] | None = None) -> list[str]:
    """Select an editor command, preferring VISUAL over EDITOR."""

    variables = os.environ if env is None else env
    command = variables.get("VISUAL") or variables.get("EDITOR")
    if not command:
        raise ConfigurationError("Set VISUAL or EDITOR to edit configuration")
    try:
        parsed = shlex.split(command)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid editor command: {exc}") from exc
    if not parsed:
        raise ConfigurationError("Editor command cannot be empty")
    return parsed


def edit_config(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> int:
    """Open the config file with VISUAL or EDITOR and return its exit code."""

    path = _config_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        save_config({}, path)
    completed = run([*select_editor(env), str(path)], check=False)
    return int(completed.returncode)


def build_report_setup(
    *,
    session_harness: str,
    session_model: str,
    writer_harness: str,
    writer_model: str,
    session_args: Sequence[str] = (),
    writer_args: Sequence[str] = (),
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate report configuration from interactive answers."""

    updated = _deep_merge(base or {}, {
        "report": {
            "session": {
                "harness": session_harness.strip().lower(),
                "model": session_model.strip(),
                "args": list(session_args),
            },
            "writer": {
                "harness": writer_harness.strip().lower(),
                "model": writer_model.strip(),
                "args": list(writer_args),
            },
        }
    })
    issues = validate_config(updated)
    if issues:
        raise ConfigurationError("; ".join(str(issue) for issue in issues))
    return updated


def report_setup_complete(config: Mapping[str, Any]) -> bool:
    """Return whether both report runners have valid harness and model choices."""

    if validate_config(config):
        return False
    for role in ("session", "writer"):
        harness = get_value(config, f"report.{role}.harness")
        model = get_value(config, f"report.{role}.model")
        if harness not in SUPPORTED_REPORT_HARNESSES:
            return False
        if not isinstance(model, str) or not model.strip():
            return False
    return True


def interactive_report_setup(
    base: Mapping[str, Any] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
) -> dict[str, Any]:
    """Prompt only for report harness/model choices, preserving current values."""

    current = resolve_config(base)

    def ask(label: str, key: str) -> str:
        existing = get_value(current, key)
        suffix = f" [{existing}]" if existing else ""
        value = input_fn(f"  {label}{suffix}: ").strip()
        return value or str(existing or "")

    session_harness = ask(
        "Session summarizer harness (claude/codex)", "report.session.harness"
    )
    session_model = ask("Session summarizer model", "report.session.model")
    writer_harness = ask(
        "Report writer harness (claude/codex)", "report.writer.harness"
    )
    writer_model = ask("Report writer model", "report.writer.model")
    return build_report_setup(
        base=base,
        session_harness=session_harness,
        session_model=session_model,
        writer_harness=writer_harness,
        writer_model=writer_model,
    )
