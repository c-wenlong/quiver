"""Catalog of known AI coding CLI harnesses for discovery.

This is a recognition table, not a seed. Nothing here is written to
~/.quiver/config/harness.json on first run; `swe discover` matches what is
actually on PATH against these names and commands, and only what it finds
gets registered. A machine's registry therefore describes that machine.

Two consequences worth keeping in mind when editing:

  * No `version` field. A version is a fact about an install, so it comes
    from `swe check` probing the binary. A constant here would be printed
    as though it were live, which is what the old defaults file did.
  * A wrong `command` is worse than a missing entry. `_resolve_executable`
    trusts it, so a name that shadows an unrelated binary (cursor's CLI is
    `cursor-agent`, and a bare `agent` is something else entirely) makes
    discovery confidently wrong rather than silently quiet.
"""

HARNESS_CATALOG: dict[str, dict] = {
    # Vendor CLIs from the major model labs.
    "claude": {
        "command": "claude",
        "description": "Anthropic's flagship, defined the skills, MCP and hooks others copy",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["cc"],
    },
    "codex": {
        "command": "codex",
        "description": "OpenAI's, OS-level sandbox by default, network off unless approved",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["cx"],
    },
    "gemini": {
        "command": "gemini",
        "description": "Google's first, 1M token context, 1,000 free requests a day",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["gg"],
    },
    "copilot": {
        "command": "copilot",
        "description": "First cross platform harness in CLI, VSCode and Cloud",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["cp"],
    },
    "cursor": {
        # `cursor-agent`, not `agent`: the short name collides with unrelated
        # binaries that ship into ~/.local/bin.
        "command": "cursor-agent",
        "description": "The editor's CLI half, shares its rules and index, print mode for CI",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["cs"],
    },
    "amp": {
        "command": "amp",
        "description": "Sourcegraph's, multiplayer threads, pay per token with no rationing",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["ap"],
    },
    "kimi": {
        "command": "kimi",
        "description": "Moonshot's, Ctrl-X drops to a real shell without leaving the session",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["ki"],
    },
    "qwen-code": {
        "command": "qwen",
        "description": "Alibaba's Gemini CLI fork, drive it from Telegram or WeChat",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["qw"],
    },
    "mistral-vibe": {
        "command": "vibe",
        "description": "Mistral's, own 24B Apache weights, runs offline on one GPU",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["mv"],
    },
    "mimo": {
        "command": "mimo",
        "description": "Xiaomi's, distils finished sessions into reusable skills",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["mm"],
    },

    # Open source and model-agnostic.
    "opencode": {
        "command": "opencode",
        "description": "OS, 75+ providers, local model support, fully customisable",
        "tags": ["agentic", "coding", "open-source", "byok"],
        "aliases": ["oc"],
    },
    "crush": {
        "command": "crush",
        "description": "Charm's, prettiest TUI, swaps models mid-session, context intact",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["cr"],
    },
    "cline": {
        "command": "cline",
        "description": "VS Code extension first, inference at cost or BYOK, no subscription",
        "tags": ["agentic", "coding", "open-source", "byok"],
        "aliases": ["cl"],
    },
    "goose": {
        "command": "goose",
        "description": "Block's, extensions are MCP servers, local or hosted models",
        "tags": ["agentic", "coding", "open-source", "byok"],
        "aliases": ["gs"],
    },
    "aider": {
        "command": "aider",
        "description": "Git-native pair programmer, commits every edit as it goes",
        "tags": ["agentic", "coding", "open-source", "byok"],
        "aliases": ["ad"],
    },
    "continue": {
        # The CLI installs as `cn`; `continue` is the shell keyword.
        "command": "cn",
        "description": "Headless CI runs and PR review, team acquired by Cursor Jun 2026",
        "tags": ["agentic", "coding", "open-source", "byok"],
        "aliases": ["cn"],
    },
    "pi": {
        "command": "pi",
        "description": "Mario's minimal agent harness for full control",
        "tags": ["agentic", "coding", "customisable", "byok"],
        "aliases": ["pi"],
    },
    "forge": {
        "command": "forge",
        "description": "Lives in your zsh prompt, fire it inline with a : prefix",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["fc"],
    },

    # Autonomous, enterprise and long tail.
    "droid": {
        "command": "droid",
        "description": "Factory's, spawns a worker per feature and syncs them through git",
        "tags": ["agentic", "coding", "autonomous", "byok"],
        "aliases": ["df"],
    },
    "augment": {
        "command": "auggie",
        "description": "Indexes up to 500k files across repos, retrieval is the whole pitch",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["au"],
    },
    "kiro": {
        "command": "kiro-cli",
        "description": "AWS's, writes requirements and design docs before any code",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["kr"],
    },
    "blackbox": {
        "command": "blackbox",
        "description": "Fans one task to four models, a judge picks the winner",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["bb"],
    },
    "freebuff": {
        "command": "freebuff",
        "description": "Free harness for light tasks, single thread, Chinese models only",
        "tags": ["agentic", "coding", "free"],
        "aliases": ["fb"],
    },

    # Not a harness: a local inference engine. Listed so discovery names it
    # instead of reporting an unknown binary, never seeded as a harness row.
    "ollama": {
        "command": "ollama",
        "description": "Most popular local inference engine",
        "tags": ["local", "llm", "infrastructure"],
        "aliases": ["ol"],
    },
}

# Basenames to skip when scanning PATH (common false positives).
EXCLUDE_BASENAMES = frozenset(
    {
        "agentd",
        "python",
        "python3",
        "pip",
        "pip3",
        "node",
        "npm",
        "npx",
        "git",
        "docker",
        "kubectl",
        "brew",
        "make",
        "cargo",
        "go",
        "ruby",
        "perl",
        "bash",
        "zsh",
        "sh",
        "curl",
        "wget",
        "ssh",
        "scp",
        "rsync",
        "sed",
        "awk",
        "grep",
        "rg",
        "find",
        "ls",
        "cat",
        "echo",
        "swe",
    }
)

# Extra directories to scan beyond $PATH.
EXTRA_BIN_DIRS = [
    "~/.local/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.npm-global/bin",
    "~/go/bin",
]
