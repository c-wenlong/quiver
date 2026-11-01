#!/usr/bin/env python3
"""Trim LiteLLM's model price list to what the four harnesses can run.

Usage:
    curl -sSfo /tmp/litellm.json \
      https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
    python3 -I app/mac/script/trim-litellm-pricing.py /tmp/litellm.json \
      > app/mac/Sources/SWECore/Resources/pricing.json

Keeps chat and responses models of the families Claude Code, Codex, OpenCode
and Devin use, under their bare name or a provider prefix those tools log,
and only the price fields `PricingCatalog` reads. LiteLLM is MIT licensed;
see app/mac/THIRD_PARTY_NOTICES.md.
"""
import json
import re
import sys

FAMILIES = re.compile(
    r"^(claude|gpt|o1|o3|o4|codex|chatgpt|gemini|gemma|grok|deepseek|kimi|moonshot|glm|"
    r"qwen|minimax|mimo|devstral|codestral|mistral|magistral|kat|nemotron)",
    re.IGNORECASE,
)
PREFIXES = {
    "", "anthropic", "openai", "gemini", "xai", "deepseek", "moonshot", "zai",
    "minimax", "mistral", "openrouter", "github_copilot", "dashscope",
}
FIELD = re.compile(
    r"^(input_cost_per_token|output_cost_per_token|cache_read_input_token_cost|"
    r"cache_creation_input_token_cost|output_cost_per_reasoning_token)"
    r"(_above_\d+k_tokens)?(_above_1hr)?(_priority|_flex|_ultrafast|_fast)?$"
)


def keep(key, entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("mode") not in (None, "chat", "responses"):
        return False
    if "input_cost_per_token" not in entry or "output_cost_per_token" not in entry:
        return False
    prefix, _, name = key.rpartition("/")
    if prefix not in PREFIXES and not prefix.startswith("openrouter/"):
        return False
    return bool(FAMILIES.match(name))


def main(path):
    with open(path) as fh:
        source = json.load(fh)
    trimmed = {}
    for key in sorted(source):
        entry = source[key]
        if not keep(key, entry):
            continue
        fields = {k: v for k, v in entry.items() if FIELD.match(k) and isinstance(v, (int, float))}
        fields["litellm_provider"] = entry.get("litellm_provider", "")
        trimmed[key] = fields
    json.dump(trimmed, sys.stdout, indent=0, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
