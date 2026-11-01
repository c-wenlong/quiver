"""How a fresh launch points a harness at a picked provider.

A launch with ``provider=<slug>`` starts the harness with that
provider's key (and, for an Anthropic-compatible endpoint, its base
URL) in its environment, read from this machine's own keys file
(``~/.swe/secrets/providers/.api_keys``). How each harness takes a
provider is declared in ``harness.json`` as ``capabilities.provider``:

  supported   false: the harness takes no provider at launch, and the
              launch routes refuse a ``provider`` for it
  providers   ``{slug: entry}``, merged over the fallback slug by slug;
              an entry of ``null`` or ``false`` drops that slug

Each entry says which variables the harness reads:

  keys    ``{TARGET: [SOURCE, ...]}``: the harness reads ``TARGET``,
          filled from the first ``SOURCE`` variable in the keys file
          that has a value (``ZHIPU_API_KEY`` from ``ZAI_API_KEY``)
  set     ``{TARGET: "value"}``: fixed values that are not secrets
          (``ANTHROPIC_BASE_URL``; ``ANTHROPIC_API_KEY: ""`` so an
          inherited Anthropic key never reaches a third-party endpoint)
  label   the name the launch sheets show (optional)

``PROVIDER_FALLBACK`` is the read-only floor for a harness whose
registry entry declares nothing, the same role ``MODEL_FALLBACK`` and
``YOLO_FALLBACK`` play. Every entry was tried on the owner's Mac on
2026-11-01 with fake keys and a throwaway HOME: ``opencode auth list``
(v2.2.5) and ``pi --list-models`` (1.4.0) listed the provider from the
target variable alone, and ``claude -p`` (2.1.316) sent
``ANTHROPIC_AUTH_TOKEN`` as a bearer to ``ANTHROPIC_BASE_URL`` and no
``x-api-key`` with ``ANTHROPIC_API_KEY`` empty; each base URL answered
a fake bearer on ``/v1/messages`` with 401.

Nothing here logs, prints or returns a key value except
``provider_env``, whose result goes straight into the launched
process's environment. Error notes name variables, never values.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# A provider slug on the wire and in harness.json.
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Variables a provider entry may never set: they would change how the
# harness starts or what it loads, not which provider it talks to.
_RESERVED = frozenset({"PATH", "HOME", "SHELL", "USER", "LOGNAME", "PWD",
                       "TMPDIR", "TERM", "TMUX", "TMUX_PANE", "NODE_OPTIONS"})
_RESERVED_PREFIXES = ("LD_", "DYLD_", "PYTHON")


def _same(*names: str) -> dict:
    """An entry whose harness reads the keys file's own variable."""
    return {"keys": {names[0]: list(names)}}


def _renamed(target: str, *sources: str) -> dict:
    return {"keys": {target: list(sources)}}


def _anthropic_compatible(base_url: str, *sources: str) -> dict:
    return {"keys": {"ANTHROPIC_AUTH_TOKEN": list(sources)},
            "set": {"ANTHROPIC_BASE_URL": base_url,
                    "ANTHROPIC_API_KEY": ""}}


# Quiver's provider slugs (providers/defaults.py) to the variables each
# harness reads. Pi reads KIMI_API_KEY and ZAI_API_KEY as coding-plan
# keys, so the pay-as-you-go kimi goes in as MOONSHOT_API_KEY and zai
# is left out.
PROVIDER_FALLBACK: dict[str, dict[str, dict]] = {
    "opencode": {
        "openai": _same("OPENAI_API_KEY"),
        "anthropic": _same("ANTHROPIC_API_KEY"),
        "gemini": _same("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "deepseek": _same("DEEPSEEK_API_KEY"),
        "zai": _renamed("ZHIPU_API_KEY", "ZAI_API_KEY"),
        "minimax": _same("MINIMAX_API_KEY"),
        "kimi": _renamed("MOONSHOT_API_KEY", "KIMI_API_KEY",
                         "MOONSHOT_API_KEY"),
        "qwen": _renamed("DASHSCOPE_API_KEY", "QWEN_API_KEY",
                         "DASHSCOPE_API_KEY"),
        "mimo": _renamed("XIAOMI_API_KEY", "MIMO_API_KEY"),
        "xai": _same("XAI_API_KEY"),
        "stepfun": _same("STEPFUN_API_KEY"),
        "groq": _same("GROQ_API_KEY"),
        "upstage": _same("UPSTAGE_API_KEY"),
        "cerebras": _same("CEREBRAS_API_KEY"),
        "mistral": _same("MISTRAL_API_KEY"),
        "opencode_zen": _renamed("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"),
        "openrouter": _same("OPENROUTER_API_KEY"),
        "together_ai": _renamed("TOGETHER_API_KEY", "TOGETHER_AI_API_KEY",
                                "TOGETHER_API_KEY"),
        "fireworks_ai": _renamed("FIREWORKS_API_KEY", "FIREWORKS_AI_API_KEY"),
        "vercel_gateway": _renamed("AI_GATEWAY_API_KEY",
                                   "VERCEL_GATEWAY_API_KEY"),
        "nebius": _same("NEBIUS_API_KEY"),
        "cohere": _same("COHERE_API_KEY"),
        "perplexity": _same("PERPLEXITY_API_KEY"),
        "huggingface": _renamed("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN",
                                "HF_TOKEN"),
    },
    "pi": {
        "anthropic": _same("ANTHROPIC_API_KEY"),
        "openai": _same("OPENAI_API_KEY"),
        "gemini": _same("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "deepseek": _same("DEEPSEEK_API_KEY"),
        "groq": _same("GROQ_API_KEY"),
        "cerebras": _same("CEREBRAS_API_KEY"),
        "xai": _same("XAI_API_KEY"),
        "fireworks_ai": _renamed("FIREWORKS_API_KEY", "FIREWORKS_AI_API_KEY"),
        "together_ai": _renamed("TOGETHER_API_KEY", "TOGETHER_AI_API_KEY",
                                "TOGETHER_API_KEY"),
        "openrouter": _same("OPENROUTER_API_KEY"),
        "vercel_gateway": _renamed("AI_GATEWAY_API_KEY",
                                   "VERCEL_GATEWAY_API_KEY"),
        "mistral": _same("MISTRAL_API_KEY"),
        "minimax": _same("MINIMAX_API_KEY"),
        "kimi": _renamed("MOONSHOT_API_KEY", "KIMI_API_KEY",
                         "MOONSHOT_API_KEY"),
        "opencode_zen": _renamed("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"),
        "mimo": _renamed("XIAOMI_API_KEY", "MIMO_API_KEY"),
    },
    "claude": {
        "anthropic": _same("ANTHROPIC_API_KEY"),
        "deepseek": _anthropic_compatible(
            "https://api.deepseek.com/anthropic", "DEEPSEEK_API_KEY"),
        "kimi": _anthropic_compatible(
            "https://api.moonshot.ai/anthropic", "KIMI_API_KEY",
            "MOONSHOT_API_KEY"),
        "minimax": _anthropic_compatible(
            "https://api.minimax.io/anthropic", "MINIMAX_API_KEY"),
        "openrouter": _anthropic_compatible(
            "https://openrouter.ai/api", "OPENROUTER_API_KEY"),
    },
}

LABELS: dict[str, str] = {
    "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini",
    "deepseek": "DeepSeek", "zai": "Z.ai", "minimax": "MiniMax",
    "kimi": "Kimi (Moonshot)", "qwen": "Qwen (DashScope)",
    "mimo": "Xiaomi MiMo", "xai": "xAI", "stepfun": "StepFun",
    "groq": "Groq", "upstage": "Upstage", "cerebras": "Cerebras",
    "mistral": "Mistral", "opencode_zen": "OpenCode Zen",
    "openrouter": "OpenRouter", "together_ai": "Together AI",
    "fireworks_ai": "Fireworks AI", "vercel_gateway": "Vercel AI Gateway",
    "nebius": "Nebius", "cohere": "Cohere", "perplexity": "Perplexity",
    "huggingface": "Hugging Face",
}


def _target_ok(name: object) -> bool:
    return (isinstance(name, str) and bool(_NAME_RE.fullmatch(name))
            and name not in _RESERVED
            and not name.startswith(_RESERVED_PREFIXES))


def _clean(entry: object) -> dict | None:
    """A provider entry in canonical form, or None when it is malformed
    (a hand-edited registry can hold anything) or names a reserved
    variable."""
    if not isinstance(entry, dict):
        return None
    keys_in = entry.get("keys")
    if not isinstance(keys_in, dict) or not keys_in:
        return None
    keys: dict[str, list[str]] = {}
    for target, sources in keys_in.items():
        if isinstance(sources, str):
            sources = [sources]
        if (not _target_ok(target) or not isinstance(sources, list)
                or not sources
                or not all(isinstance(s, str) and _NAME_RE.fullmatch(s)
                           for s in sources)):
            return None
        keys[target] = list(sources)
    fixed: dict[str, str] = {}
    set_in = entry.get("set") or {}
    if not isinstance(set_in, dict):
        return None
    for target, value in set_in.items():
        if (not _target_ok(target) or target in keys
                or not isinstance(value, str)
                or any(c in value for c in "\x00\n\r")):
            return None
        fixed[target] = value
    out: dict = {"keys": keys, "set": fixed}
    label = entry.get("label")
    if isinstance(label, str) and label.strip():
        out["label"] = label.strip()[:60]
    return out


def capability(name: str, reg: dict | None = None) -> dict[str, dict] | None:
    """``name``'s launch providers, ``{slug: entry}``: the registry's
    ``capabilities.provider.providers`` over ``PROVIDER_FALLBACK``, or
    None when the harness takes no provider at launch. ``reg`` is the
    loaded registry; None reads it (read-only)."""
    if reg is None:
        from swe.harness import registry

        try:
            reg = registry.load_registry_readonly()
        except (OSError, ValueError):
            reg = {}
    entry = reg.get(name) if isinstance(reg, dict) else None
    caps = (entry if isinstance(entry, dict) else {}).get("capabilities")
    caps = caps.get("provider") if isinstance(caps, dict) else None
    merged: dict[str, object] = dict(PROVIDER_FALLBACK.get(name) or {})
    if isinstance(caps, dict):
        if "supported" in caps and not caps["supported"]:
            return None
        declared = caps.get("providers")
        if isinstance(declared, dict):
            for slug, value in declared.items():
                if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
                    continue
                if value is None or value is False:
                    merged.pop(slug, None)
                else:
                    merged[slug] = value
    out = {}
    for slug, value in merged.items():
        clean = _clean(value)
        if clean is not None:
            out[slug] = clean
    return out or None


def label(slug: str, entry: dict | None = None) -> str:
    """The name a launch sheet shows for ``slug``."""
    if entry and entry.get("label"):
        return str(entry["label"])
    return LABELS.get(slug) or slug.replace("_", " ").title()


def read_keys(path: Path) -> dict[str, str]:
    """``NAME -> value`` for every readable key in the keys file at
    ``path``; {} when there is none. Raises ValueError (worded without
    any value) when the file can't be read."""
    from swe.providers import keyfile

    if not path.exists() and not path.is_symlink():
        return {}
    if path.is_dir():
        raise ValueError(f"{path} is a folder, not a keys file")
    try:
        keys, _data = keyfile.load(path)
    except keyfile.KeyFileError as exc:
        raise ValueError(exc.message) from None
    # Line by line, as a shell reads it: the last assignment of a name
    # wins, and ``swe providers`` also counts a bare ``NAME=value``. A
    # last assignment we can't read (``A="$OTHER"``) leaves no value.
    out: dict[str, str] = {}
    for line in keys.lines:
        text = line.strip(" \t")
        bare = _BARE_RE.match(text)
        if not text.startswith("export") and not bare:
            continue
        pair = keyfile.export(text if not bare else f"export {text}")
        if pair:
            out.pop(pair[0], None)
            out[pair[0]] = pair[1]
        else:
            match = _ASSIGN_RE.match(text)
            if match:
                out.pop(match.group(1), None)
    return out


_BARE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_ASSIGN_RE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")


def _resolve(entry: dict, keys: dict[str, str]) -> tuple[dict[str, str],
                                                        list[str]]:
    """The env an entry builds from ``keys``, and the targets with no
    source holding a value."""
    env: dict[str, str] = {}
    missing: list[str] = []
    for target, sources in entry["keys"].items():
        value = next((keys[s] for s in sources if keys.get(s)), None)
        if value is None:
            missing.append(target)
        else:
            env[target] = value
    env.update(entry["set"])
    return env, missing


def provider_error(name: str, provider: str | None,
                   reg: dict | None = None,
                   keys: dict[str, str] | None = None) -> str:
    """The refusal note when ``name``'s launch can't take ``provider``,
    else "" (always "" for no pick). With ``keys`` (this machine's keys
    file, ``read_keys``) a provider whose key is missing is refused too.
    The note names variables, never values."""
    if not provider:
        return ""
    caps = capability(name, reg)
    if caps is None:
        return (f"{name} launches can't take a provider: harness.json "
                f"declares no capabilities.provider for it")
    entry = caps.get(provider)
    if entry is None:
        return f"{name} launches can't take the provider {provider!r}"
    if keys is not None:
        _env, missing = _resolve(entry, keys)
        if missing:
            wanted = " or ".join(
                s for target in missing for s in entry["keys"][target])
            return (f"no key for {label(provider, entry)} on this machine: "
                    f"set {wanted} in Settings › Providers")
    return ""


def provider_env(name: str, provider: str, keys: dict[str, str],
                 reg: dict | None = None) -> dict[str, str]:
    """The variables to add to ``name``'s environment for ``provider``:
    exactly that entry's ``keys`` targets and ``set`` values, nothing
    else from ``keys``. Raises ValueError (see ``provider_error``)."""
    note = provider_error(name, provider, reg, keys)
    if note:
        raise ValueError(note)
    env, _missing = _resolve((capability(name, reg) or {})[provider], keys)
    return env


def configured(name: str, keys: dict[str, str],
               reg: dict | None = None) -> list[str]:
    """The slugs ``name`` takes at launch whose keys are all set in
    ``keys``, in declaration order. Slugs only, for the machine row."""
    caps = capability(name, reg) or {}
    return [slug for slug, entry in caps.items()
            if not _resolve(entry, keys)[1]]


def keys_path(home: Path | None = None) -> Path:
    """This machine's provider keys file. The hub never sends keys: a
    satellite reads its own."""
    from swe.paths import provider_keys_path_for

    return provider_keys_path_for(home)


def wrap_argv(name: str, provider: str, argv: list[str], *,
              keys_file: Path, reg: dict | None = None) -> list[str]:
    """``argv`` behind ``provider_exec.py``, which reads ``keys_file``
    and execs the harness with ``provider``'s variables. The wrapper's
    argv carries variable names and fixed values only, never a key.
    Raises ValueError when ``name`` doesn't take ``provider``."""
    note = provider_error(name, provider, reg)
    if note:
        raise ValueError(note)
    entry = (capability(name, reg) or {})[provider]
    spec = json.dumps({"keys": entry["keys"], "set": entry["set"]},
                      separators=(",", ":"), sort_keys=True)
    script = Path(__file__).with_name("provider_exec.py")
    return [sys.executable, "-I", str(script), f"--keys={keys_file}",
            f"--spec={spec}", "--", *argv]
