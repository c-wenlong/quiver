"""Built-in default provider registry (seeded on first run).

Each entry stores *metadata only* — the filename to look for inside
the user's keys directory, the canonical slug, the dashboard URL,
and any env-var names that downstream tools use. No key strings live here.

The set of built-ins is curated against a real-world ``~/.api_keys``
export file (shell-style: ``export OPENAI_API_KEY=sk-...``). Providers
not in your personal file can still be added at runtime with
``swe providers add``.

Display ``name`` and ``alias`` fields are no longer persisted — they
are *derived* from ``env_vars[0]`` at load time (see ``display_name``
and ``derived_alias``) so the catalog has a single source of truth:
the env-var name itself. Manual aliases are not supported.
"""

# Known env-var suffixes, ordered longest-first so ``_HUB_TOKEN`` is
# stripped before ``_TOKEN`` can accidentally match it.
_STRIPPABLE_SUFFIXES = ("_API_KEY", "_HUB_TOKEN", "_TOKEN")


def _strip_suffix(env_var: str) -> str:
    """Strip a known env-var suffix (``_API_KEY``, ``_HUB_TOKEN``, ``_TOKEN``)."""
    for suffix in _STRIPPABLE_SUFFIXES:
        if env_var.endswith(suffix):
            return env_var[: -len(suffix)]
    return env_var


def display_name(info: dict) -> str:
    """Derive the human-readable name from ``env_vars[0]``.

    Strips a known suffix, replaces underscores with spaces, title-cases
    each word, and preserves the canonical ``AI`` capitalization
    (``TOGETHER_AI`` → ``Together AI``, not ``Together Ai``).
    """
    env_vars = info.get("env_vars") or []
    if not env_vars:
        return ""
    stripped = _strip_suffix(env_vars[0])
    titled = stripped.replace("_", " ").title()
    return titled.replace(" Ai", " AI")


def derived_alias(info: dict) -> str | None:
    """Derive the auto-alias: env-var prefix lowercased, ``_`` → ``-``.

    Returns ``None`` when no env-var is configured (the provider would
    then be matched only by its canonical slug).
    """
    env_vars = info.get("env_vars") or []
    if not env_vars:
        return None
    stripped = _strip_suffix(env_vars[0])
    if not stripped:
        return None
    return stripped.lower().replace("_", "-")


DEFAULT_PROVIDERS = {
    "openai": {
        "description": "OpenAI API (ChatGPT, GPT-4/5, DALL-E)",
        "url": "https://platform.openai.com/api-keys",
        "key_filename": "openai",
        "env_vars": ["OPENAI_API_KEY"],
    },
    "anthropic": {
        "description": "Anthropic Claude API",
        "url": "https://console.anthropic.com/settings/keys",
        "key_filename": "anthropic",
        "env_vars": ["ANTHROPIC_API_KEY"],
    },
    "gemini": {
        "description": "Google Gemini / AI Studio",
        "url": "https://aistudio.google.com/app/apikey",
        "key_filename": "gemini",
        "env_vars": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    },
    "deepseek": {
        "description": "DeepSeek API",
        "url": "https://platform.deepseek.com/api_keys",
        "key_filename": "deepseek",
        "env_vars": ["DEEPSEEK_API_KEY"],
    },
    "zai": {
        "description": "ZAI / 01.ai inference",
        "url": "https://z.ai/",
        "key_filename": "zai",
        "env_vars": ["ZAI_API_KEY"],
    },
    "minimax": {
        "description": "MiniMax inference",
        "url": "https://minimax.io/",
        "key_filename": "minimax",
        "env_vars": ["MINIMAX_API_KEY"],
    },
    "kimi": {
        "description": "Moonshot AI Kimi API",
        "url": "https://platform.moonshot.ai/",
        "key_filename": "kimi",
        # KIMI_API_KEY is the newer Kimi-branded env var; MOONSHOT_API_KEY
        # is the canonical Moonshot SDK env var. Some users have only one.
        "env_vars": ["KIMI_API_KEY", "MOONSHOT_API_KEY"],
    },
    "qwen": {
        "description": "Alibaba Qwen / Bailian / DashScope",
        "url": "https://bailian.console.aliyun.com/",
        "key_filename": "qwen",
        # DASHSCOPE_API_KEY is the Alibaba DashScope SDK env var;
        # QWEN_API_KEY is recognized in some custom wrappers.
        "env_vars": ["QWEN_API_KEY", "DASHSCOPE_API_KEY"],
    },
    "mimo": {
        "description": "Xiaomi MiMo model API",
        "url": "https://platform.xiaomi.com/",
        "key_filename": "mimo",
        "env_vars": ["MIMO_API_KEY"],
    },
    "xai": {
        "description": "xAI (Grok)",
        "url": "https://console.x.ai/",
        "key_filename": "xai",
        "env_vars": ["XAI_API_KEY"],
    },
    "stepfun": {
        "description": "StepFun inference API",
        "url": "https://platform.stepfun.ai/",
        "key_filename": "stepfun",
        "env_vars": ["STEPFUN_API_KEY"],
    },
    "groq": {
        "description": "Groq fast-inference API",
        "url": "https://console.groq.com/keys",
        "key_filename": "groq",
        "env_vars": ["GROQ_API_KEY"],
    },
    "upstage": {
        "description": "Upstage Solar API",
        "url": "https://console.upstage.ai/",
        "key_filename": "upstage",
        "env_vars": ["UPSTAGE_API_KEY"],
    },
    "cerebras": {
        "description": "Cerebras inference cloud",
        "url": "https://cloud.cerebras.ai/",
        "key_filename": "cerebras",
        "env_vars": ["CEREBRAS_API_KEY"],
    },
    "mistral": {
        "description": "Mistral inference API",
        "url": "https://console.mistral.ai/api-keys/",
        "key_filename": "mistral",
        "env_vars": ["MISTRAL_API_KEY"],
    },
    "routing_run": {
        "description": "Routing.Run",
        "url": "https://routing.run/",
        "key_filename": "routing_run",
        "env_vars": ["ROUTING_RUN_API_KEY"],
    },
    "opencode_zen": {
        "description": "Opencode Zen inference",
        "url": "https://opencode.ai/zen",
        "key_filename": "opencode_zen",
        "env_vars": ["OPENCODE_ZEN_API_KEY"],
    },
    "openrouter": {
        "description": "Unified router across many LLM providers",
        "url": "https://openrouter.ai/keys",
        "key_filename": "openrouter",
        "env_vars": ["OPENROUTER_API_KEY"],
    },
    "together_ai": {
        "description": "Together AI inference API",
        "url": "https://api.together.xyz/",
        "key_filename": "together",
        # Both spellings appear in userland: TOGETHER_AI_API_KEY (newer)
        # and TOGETHER_API_KEY (older SDKs).
        "env_vars": ["TOGETHER_AI_API_KEY", "TOGETHER_API_KEY"],
    },
    "fireworks_ai": {
        "description": "Fireworks AI inference cloud",
        "url": "https://fireworks.ai/",
        "key_filename": "fireworks",
        "env_vars": ["FIREWORKS_AI_API_KEY"],
    },
    "vercel_gateway": {
        "description": "Vercel AI Gateway",
        "url": "https://vercel.com/docs/ai-gateway",
        "key_filename": "vercel_gateway",
        "env_vars": ["VERCEL_GATEWAY_API_KEY"],
    },
    "nebius": {
        "description": "Nebius inference studio",
        "url": "https://studio.nebius.com/",
        "key_filename": "nebius",
        "env_vars": ["NEBIUS_API_KEY"],
    },
    "featherless": {
        "description": "Featherless AI inference",
        "url": "https://featherless.ai/",
        "key_filename": "featherless",
        "env_vars": ["FEATHERLESS_API_KEY"],
    },
    "cohere": {
        "description": "Cohere API",
        "url": "https://dashboard.cohere.com/api-keys",
        "key_filename": "cohere",
        "env_vars": ["COHERE_API_KEY"],
    },
    "perplexity": {
        "description": "Perplexity search API",
        "url": "https://www.perplexity.ai/settings/api",
        "key_filename": "perplexity",
        "env_vars": ["PERPLEXITY_API_KEY"],
    },
    "github": {
        "description": "GitHub personal access tokens",
        "url": "https://github.com/settings/tokens",
        "key_filename": "github",
        "env_vars": ["GITHUB_TOKEN", "GH_TOKEN"],
    },
    "huggingface": {
        "description": "Hugging Face Hub tokens",
        "url": "https://huggingface.co/settings/tokens",
        "key_filename": "huggingface",
        # HUGGINGFACE_HUB_TOKEN first so the derived display name reads
        # "Huggingface" (not the awkward "Hf" form of HF_TOKEN).
        "env_vars": ["HUGGINGFACE_HUB_TOKEN", "HF_TOKEN"],
    },
}
