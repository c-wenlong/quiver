"""Catalog of known AI coding CLI harnesses for discovery."""

from quiver.harness.defaults import DEFAULT_TOOLS

# Extended entries beyond DEFAULT_TOOLS (common installs not in the seed file).
_EXTENDED = {
    "cursor": {
        "command": "agent",
        "description": "Cursor CLI — AI-powered coding agent",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["cs"],
    },
    "cline": {
        "command": "cline",
        "description": "Cline — autonomous coding agent in the terminal",
        "tags": ["agentic", "coding", "open-source", "byok"],
        "aliases": ["cl"],
    },
    "continue": {
        "command": "cn",
        "description": "Continue CLI — open source, model-agnostic assistant",
        "tags": ["agentic", "coding", "open-source", "byok"],
        "aliases": ["cn"],
    },
    "amp": {
        "command": "amp",
        "description": "Amp by Anthropic — agentic coding assistant",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["ap"],
    },
    "crush": {
        "command": "crush",
        "description": "Crush by Charm — terminal AI coding assistant",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["cr"],
    },
    "kimi": {
        "command": "kimi",
        "description": "Kimi CLI — AI coding assistant by Moonshot AI",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["ki"],
    },
    "qwen-code": {
        "command": "qwen",
        "description": "Qwen Code — AI coding agent by Alibaba",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["qw"],
    },
    "mistral-vibe": {
        "command": "vibe",
        "description": "Mistral Vibe — AI coding assistant by Mistral",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["mv"],
    },
    "augment": {
        "command": "auggie",
        "description": "Augment Code — AI coding assistant",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["au"],
    },
    "blackbox": {
        "command": "blackbox",
        "description": "Blackbox — AI coding assistant",
        "tags": ["agentic", "coding", "subscription"],
        "aliases": ["bb"],
    },
    "freebuff": {
        "command": "freebuff",
        "description": "Freebuff — free coding agent",
        "tags": ["agentic", "coding", "free"],
        "aliases": ["fb"],
    },
    "kiro": {
        "command": "kiro-cli",
        "description": "Kiro — AI coding agent",
        "tags": ["agentic", "coding", "byok"],
        "aliases": ["kr"],
    },
}

HARNESS_CATALOG: dict[str, dict] = {**DEFAULT_TOOLS, **_EXTENDED}

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
