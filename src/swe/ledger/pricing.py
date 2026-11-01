"""Model prices (ports ``Pricing/PricingCatalog.swift`` and ``ModelRates.swift``).

Prices come from LiteLLM's list, refreshed once a day into
``~/.swe/cache/web-ledger/litellm.json`` like the Mac app does, with
the trimmed snapshot shipped beside this module (a copy of the app's
``Resources/pricing.json``) when the download is unavailable. Quiver's
supplement adds aliases and tier multipliers; the user's
``~/.swe/config/pricing.json`` (USD per million tokens) wins over both.
The Mac app also keeps every past price; this port prices everything at
today's rates.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path

from swe.paths import atomic_write_text, cache_dir_for, swe_dir_for, read_json_dict

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
REFRESH_S = 24 * 3600
_HERE = Path(__file__).resolve().parent
BUNDLED = _HERE / "pricing.json"
SUPPLEMENT = _HERE / "pricing-supplement.json"


@dataclass
class LongContext:
    threshold: int
    input: float
    output: float
    cache_read: float
    cache_write: float
    cache_write_1h: float
    reasoning: float


@dataclass
class ModelRates:
    """USD per token for one model, each token kind at its own rate."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    cache_write_1h: float
    reasoning: float
    long_context: LongContext | None = None
    tiers: dict = field(default_factory=dict)
    tier_multipliers: dict = field(default_factory=dict)

    def cost(self, input: int, output: int, cache_read: int, cache_write: int,
             cache_write_1h: int, reasoning: int, tier: str | None = None,
             long: bool = False) -> float:
        rates, multiplier = self, 1.0
        if tier:
            if tier in self.tiers:
                rates = self.tiers[tier]
            elif tier in self.tier_multipliers:
                multiplier = self.tier_multipliers[tier]
        r = rates.long_context if long and rates.long_context else rates
        five_minute = max(0, cache_write - cache_write_1h)
        return (input * r.input + output * r.output + cache_read * r.cache_read
                + five_minute * r.cache_write + cache_write_1h * r.cache_write_1h
                + reasoning * r.reasoning) * multiplier


def _num(entry: dict, key: str) -> float | None:
    v = entry.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def rates_from_entry(entry: dict) -> ModelRates | None:
    """One LiteLLM entry, or ``None`` for a non-chat or unpriced one."""
    mode = entry.get("mode")
    if isinstance(mode, str) and mode not in {"chat", "responses"}:
        return None
    base_in, base_out = _num(entry, "input_cost_per_token"), _num(entry, "output_cost_per_token")
    if base_in is None or base_out is None or base_in < 0 or base_out < 0:
        return None
    provider = entry.get("litellm_provider")
    anthropic = isinstance(provider, str) and (
        provider == "anthropic" or "bedrock" in provider or "vertex_ai-anthropic" in provider
    )

    def build(suffix: str) -> ModelRates | None:
        inp = _num(entry, "input_cost_per_token" + suffix)
        if inp is None:
            return None
        out = _num(entry, "output_cost_per_token" + suffix)
        out = base_out if out is None else out
        cr = _first(_num(entry, "cache_read_input_token_cost" + suffix),
                    _num(entry, "cache_read_input_token_cost"), inp)
        cw = _first(_num(entry, "cache_creation_input_token_cost" + suffix), inp)
        cw1h = _first(_num(entry, "cache_creation_input_token_cost_above_1hr" + suffix),
                      inp * 2 if anthropic else cw)
        reas = _first(_num(entry, "output_cost_per_reasoning_token" + suffix), out)
        pattern = re.compile(r"^input_cost_per_token_above_(\d+)k_tokens" + re.escape(suffix) + "$")
        thresholds = [int(m.group(1)) * 1000 for k in entry if (m := pattern.match(k))]
        long = None
        if thresholds:
            threshold = min(thresholds)
            tag = f"_above_{threshold // 1000}k_tokens{suffix}"
            l_in = _first(_num(entry, "input_cost_per_token" + tag), inp)
            l_out = _first(_num(entry, "output_cost_per_token" + tag), out)
            l_cw = _first(_num(entry, "cache_creation_input_token_cost" + tag), cw)
            long = LongContext(
                threshold=threshold, input=l_in, output=l_out,
                cache_read=_first(_num(entry, "cache_read_input_token_cost" + tag), cr),
                cache_write=l_cw, cache_write_1h=l_in * 2 if anthropic else l_cw,
                reasoning=_first(_num(entry, "output_cost_per_reasoning_token" + tag), l_out),
            )
        return ModelRates(inp, out, cr, cw, cw1h, reas, long)

    base = build("")
    if base is None:
        return None
    for tier in ("priority", "flex", "ultrafast", "fast"):
        rates = build("_" + tier)
        if rates is not None:
            base.tiers[tier] = rates
    return base


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------- names

_KNOWN_PREFIXES = ["", "anthropic", "openai", "gemini", "vertex_ai", "xai", "deepseek",
                   "moonshot", "zai", "minimax", "mistral", "dashscope", "github_copilot"]
_CLEANUP = [re.compile(p) for p in (r"^([a-z]{2,4}\.)?anthropic\.", r"-v\d+(:\d+)?$", r"\[[^\]]*\]$")]
_DATES = [re.compile(p) for p in (r"@\d{8}$", r"-\d{4}-\d{2}-\d{2}$", r"-\d{8}$")]
_EFFORT = re.compile(r"-(minimal|low|medium|high|xhigh|max)$")


def _prefix_rank(key: str) -> int:
    prefix = "/".join(key.split("/")[:-1])
    try:
        return _KNOWN_PREFIXES.index(prefix)
    except ValueError:
        return len(_KNOWN_PREFIXES)


def bare_name(key: str) -> str:
    name = key.lower()
    return name[name.rfind("/") + 1:] if "/" in name else name


def forms(key: str, strip_dates: bool, strip_effort: bool = False) -> list[str]:
    """The names ``key`` can be found under, most specific first."""
    name = bare_name(key)
    for pattern in _CLEANUP:
        name = pattern.sub("", name, count=1)
    out = [name]
    if strip_dates:
        undated = name
        for pattern in _DATES:
            undated = pattern.sub("", undated, count=1)
        if undated != name:
            out.append(undated)
    for form in list(out):
        if "." in form or "@" in form:
            out.append(form.replace(".", "-").replace("@", "-"))
    if strip_effort:
        for form in list(out):
            bare, n = _EFFORT.subn("", form, count=1)
            if n:
                out.append(bare)
    return out


def base_name(model: str) -> str:
    """``PricingCatalog.baseName``: the name a price is saved under by
    default, with its provider, date and effort stripped."""
    found = forms(model, True, True)
    return found[-1] if found else model


def alias_target(model: str, aliases: dict[str, str]) -> str | None:
    """The real name the supplement's ``aliases`` table gives ``model``
    (``MODEL_PRIVATE_11`` is ``claude-haiku-4-5``), chains followed and
    forms tried as ``PricingCatalog._key`` tries them; ``None`` when no
    alias applies. Pages name and brand a model by it."""
    target = None
    name = model.strip().lower()
    for _ in range(5):
        hit = next((aliases[f] for f in [name] + forms(name, True, True)
                    if f in aliases and aliases[f].lower() != f), None)
        if hit is None:
            break
        target, name = hit, hit.lower()
    return target


def is_free(model: str) -> bool:
    bare = bare_name(model)
    return bare == "free" or bare.endswith(":free") or bare.endswith("-free")


class PricingCatalog:
    def __init__(self, entries: dict[str, ModelRates], supplement: dict | None = None,
                 overrides: set[str] | None = None, quiver: set[str] | None = None):
        supplement = supplement or {}
        # The keys Quiver's supplement priced, for ``source``.
        self.quiver = set(quiver or ())
        self.aliases = {k.lower(): v for k, v in (supplement.get("aliases") or {}).items()
                        if isinstance(v, str)}
        tier_mult = {k.lower(): {t: float(x) for t, x in v.items() if isinstance(x, (int, float))}
                     for k, v in (supplement.get("tier_multipliers") or {}).items() if isinstance(v, dict)}
        default_mult = {t: float(x) for t, x in (supplement.get("default_tier_multipliers") or {}).items()
                        if isinstance(x, (int, float))}
        self.entries = {}
        for key, rates in entries.items():
            specific = next((tier_mult[f] for f in forms(key, True) if f in tier_mult), {})
            self.entries[key] = replace(rates, tier_multipliers={**default_mult, **specific})
        self.overrides = overrides or set()
        ordered = sorted(self.entries, key=lambda k: (_prefix_rank(k), k))
        self.index: dict[str, str] = {}
        self.override_index: dict[str, str] = {}
        for strip in (False, True):
            for key in ordered:
                for form in forms(key, strip):
                    self.index.setdefault(form, key)
                    if key in self.overrides:
                        self.override_index.setdefault(form, key)

    def _key(self, model: str, depth: int = 0) -> str | None:
        trimmed = model.strip()
        lowered = trimmed.lower()
        if trimmed in self.overrides:
            return trimmed
        for form in [lowered] + forms(lowered, True, True):
            if form in self.override_index:
                return self.override_index[form]
        if trimmed in self.entries:
            return trimmed
        if depth > 4:
            return None
        alias = self.aliases.get(lowered)
        if alias and alias.lower() != lowered:
            return self._key(alias, depth + 1)
        for form in forms(lowered, True, True):
            alias = self.aliases.get(form)
            if alias and alias != form:
                return self._key(alias, depth + 1)
            if form in self.index:
                return self.index[form]
        return None

    def resolve(self, model: str, tier: str | None) -> tuple[str, ModelRates | None, str | None]:
        """``("priced", rates, tier)``, ``("free", None, None)`` or
        ``("unpriced", None, None)``. A ``-fast`` name with no tier is
        tried again without the suffix, at the fast tier."""
        status, key, tier = self.resolve_entry(model, tier)
        return status, (self.entries[key] if key is not None else None), tier

    def resolve_entry(self, model: str, tier: str | None) -> tuple[str, str | None, str | None]:
        """``resolve`` with the entry key that answered in place of its
        rates (``lookup`` plus ``resolve`` in the Swift catalog), so the
        Pricing page shows the same resolution the ledger prices with."""
        key = self._key(model)
        if key is not None:
            return "priced", key, tier
        if not tier and model.lower().endswith("-fast"):
            key = self._key(model[:-5])
            if key is not None:
                return "priced", key, "fast"
        if is_free(model):
            return "free", None, None
        return "unpriced", None, None

    def user_keys(self, model: str) -> list[str]:
        """``PricingCatalog.userKeys(matching:)``: every saved key that
        prices ``model``. Several can (``gpt-4.1`` and ``gpt-4-1`` both
        cover ``gpt-4.1``), so removing a price takes them all."""
        lowered = model.strip().lower()
        lookup = {lowered, *forms(lowered, True, True)}
        if lowered.endswith("-fast"):
            lookup |= {lowered[:-5], *forms(lowered[:-5], True, True)}
        # A saved key keeps its effort suffix, as in ``override_index``.
        found = {k for k in self.overrides
                 if k.lower() in lookup or any(f in lookup for f in forms(k, True))}
        # The key the name resolves to, aliases followed (``MODEL_PRIVATE_11``
        # priced by a saved ``claude-haiku-4-5``): the price the row shows.
        for name in ([model, model.strip()[:-5]] if lowered.endswith("-fast") else [model]):
            key = self._key(name)
            if key in self.overrides:
                found.add(key)
        return sorted(found)

    def source(self, key: str) -> str:
        """Where an entry's price came from: ``"yours"`` (the user's
        ``config/pricing.json``), ``"quiver"`` (the bundled supplement)
        or ``"litellm"``."""
        if key in self.overrides:
            return "yours"
        return "quiver" if key in self.quiver else "litellm"


# ---------------------------------------------------------------- loading

_LOCK = threading.Lock()


def _litellm_cache(home: Path | None) -> Path:
    return cache_dir_for(home) / "web-ledger" / "litellm.json"


def _refresh_download(home: Path | None, now: float) -> None:
    path = _litellm_cache(home)
    try:
        if now - path.stat().st_mtime < REFRESH_S:
            return
    except OSError:
        pass
    try:
        # The python.org build swe runs from ships no CA store, so every
        # plain urlopen of an https URL failed verification and the list
        # never refreshed. agent._urlopen retries a certificate failure
        # against the OS bundle, still verified, as notify and linear do.
        from swe.remote.agent import _urlopen

        req = urllib.request.Request(LITELLM_URL, headers={"User-Agent": "quiver"})
        with _urlopen(req, 15) as resp:
            body = resp.read()
        data = json.loads(body)
        if not isinstance(data, dict) or len(data) < 100:
            return
    except (OSError, ValueError):
        # Offline or refused: keep the last download, or the snapshot,
        # and mark the attempt so the next one waits a day too. An empty
        # file reads as no download, which falls back to the snapshot.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, body.decode("utf-8"))


def _overrides(home: Path | None) -> dict[str, ModelRates]:
    """``config/pricing.json``: ``{"models": {name: {input, output,
    cache_read, cache_write}}}`` in USD per million tokens, read as the
    app's ``PricingOverrides`` reads it: input and output required, a
    missing cache price is $0, and each becomes a LiteLLM-shaped entry."""
    raw = read_json_dict(_overrides_path(home)).get("models") or {}
    if not isinstance(raw, dict):
        return {}  # an unsupported shape, ignored as the app ignores it
    out = {}

    def usd(value) -> float | None:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return float(value) if ok else None

    for name, p in raw.items():
        if not isinstance(p, dict):
            continue
        inp, outp = usd(p.get("input")), usd(p.get("output"))
        if inp is None or outp is None or inp < 0 or outp < 0:
            continue
        rates = rates_from_entry({
            "input_cost_per_token": inp / 1e6,
            "output_cost_per_token": outp / 1e6,
            "cache_read_input_token_cost": (usd(p.get("cache_read")) or 0) / 1e6,
            "cache_creation_input_token_cost": (usd(p.get("cache_write")) or 0) / 1e6,
        })
        if rates is not None:
            out[name] = rates
    return out


def _overrides_path(home: Path | None) -> Path:
    return swe_dir_for(home) / "config" / "pricing.json"


def _write_overrides(home: Path | None, edit) -> None:
    """Read ``config/pricing.json``'s ``models``, let ``edit`` change them,
    and write the file back atomically in the app's shape (``{"schema": 1,
    "models": ...}``, sorted keys). Entries the reader skips are kept as
    they are. Raises ValueError when the file is there but isn't JSON, so
    a hand-edited file is never overwritten."""
    path = _overrides_path(home)
    with _LOCK:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        doc = {}
        if text.strip():
            try:
                doc = json.loads(text)
            except ValueError as exc:
                raise ValueError(f"{path} isn't valid JSON; fix or remove it first") from exc
            if not isinstance(doc, dict):
                raise ValueError(f"{path} isn't a JSON object; fix or remove it first")
        models = doc.get("models")
        models = dict(models) if isinstance(models, dict) else {}
        edit(models)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({**doc, "schema": 1, "models": models},
                                           indent=2, sort_keys=True) + "\n")


def save_override(home: Path | None, key: str, prices: dict, replacing: str | None = None) -> None:
    """``PricingOverrides.set``: ``key``'s price in USD per million tokens
    (``input``, ``output``, ``cache_read``, ``cache_write``). ``replacing``
    is the entry being renamed; it leaves in the same write."""
    def edit(models: dict) -> None:
        if replacing and replacing != key:
            models.pop(replacing, None)
        models[key] = {k: float(prices.get(k) or 0) for k in ("input", "output", "cache_read", "cache_write")}
    _write_overrides(home, edit)


def remove_overrides(home: Path | None, keys: list[str]) -> None:
    """``PricingOverrides.remove``: drop every key in one write."""
    def edit(models: dict) -> None:
        for key in keys:
            models.pop(key, None)
    _write_overrides(home, edit)


def load_catalog(home: Path | None = None, download: bool = True) -> PricingCatalog:
    with _LOCK:
        if download:
            _refresh_download(home, time.time())
        listed = read_json_dict(_litellm_cache(home)) or read_json_dict(BUNDLED)
    supplement = read_json_dict(SUPPLEMENT)
    raw = dict(listed)
    quiver = set()
    for key, entry in (supplement.get("prices") or {}).items():
        if isinstance(entry, dict):
            raw[key] = entry
            quiver.add(key)
    entries = {}
    for key, entry in raw.items():
        if isinstance(entry, dict):
            rates = rates_from_entry(entry)
            if rates is not None:
                entries[key] = rates
    overrides = _overrides(home)
    entries.update(overrides)
    return PricingCatalog(entries, supplement, set(overrides), quiver & set(entries))
