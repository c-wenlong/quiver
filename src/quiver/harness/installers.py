"""How each known harness is installed, per package manager.

`swe install` used to run `npm install -g <name>` for anything missing
from a small npm map. On npm a bare name often belongs to someone else
(`gemini`, `crush` and `kiro-cli` are unrelated packages), and several
harnesses are not on npm at all. So every entry here was checked against
the registry it names: npm maintainers and repository, Homebrew's
formula/cask homepage, nixpkgs `meta.homepage` and `meta.mainProgram`,
and the vendor's own install script. A harness with no entry has no
known installer, and `swe install` refuses rather than guessing.

Bare Homebrew names are just as ambiguous (`goose`, `forge`, `blackbox`
and `copilot` are unrelated formulae), so the brew/cask values are the
exact tokens, tap-qualified where the package lives in a third-party tap.
"""

from __future__ import annotations

from dataclasses import dataclass

# Display and `--via` order.
METHODS = ("brew", "cask", "npm", "uv", "nix", "script")

# The executable each method needs on PATH.
METHOD_TOOL = {
    "brew": "brew",
    "cask": "brew",
    "npm": "npm",
    "uv": "uv",
    "nix": "nix",
    "script": "curl",
}

INSTALLERS: dict[str, dict[str, str]] = {
    "claude": {
        "cask": "claude-code",
        "npm": "@anthropic-ai/claude-code",
        "nix": "claude-code",
        "script": "https://claude.ai/install.sh",
    },
    "codex": {
        "cask": "codex",
        "npm": "@openai/codex",
        "nix": "codex",
    },
    "gemini": {
        "brew": "gemini-cli",
        "npm": "@google/gemini-cli",
        "nix": "gemini-cli",
    },
    "copilot": {
        "cask": "copilot-cli",
        "npm": "@github/copilot",
        "nix": "github-copilot-cli",
        "script": "https://gh.io/copilot-install",
    },
    "cursor": {
        "cask": "cursor-cli",
        "nix": "cursor-cli",
        "script": "https://cursor.com/install",
    },
    "qwen-code": {
        "brew": "qwen-code",
        "npm": "@qwen-code/qwen-code",
        "nix": "qwen-code",
    },
    "mistral-vibe": {
        "brew": "mistral-vibe",
        "uv": "mistral-vibe",
        "nix": "mistral-vibe",
        "script": "https://mistral.ai/vibe/install.sh",
    },
    "opencode": {
        "brew": "opencode",
        "npm": "opencode-ai",
        "nix": "opencode",
        "script": "https://opencode.ai/install",
    },
    "crush": {
        "brew": "charmbracelet/tap/crush",
        "npm": "@charmland/crush",
        "nix": "crush",
    },
    # No brew: the formula is deprecated upstream.
    "cline": {
        "npm": "cline",
        "nix": "cline",
    },
    "kilo": {
        "brew": "kilo-org/tap/kilo",
        "npm": "@kilocode/cli",
        "nix": "kilocode-cli",
    },
    "goose": {
        "brew": "block-goose-cli",
        "nix": "goose-cli",
        "script": "https://github.com/block/goose/releases/download/stable/download_cli.sh",
    },
    "aider": {
        "brew": "aider",
        "uv": "aider-chat",
        "nix": "aider-chat",
        "script": "https://aider.chat/install.sh",
    },
    "continue": {
        "npm": "@continuedev/cli",
    },
    "pi": {
        "brew": "pi-coding-agent",
        "npm": "@earendil-works/pi-coding-agent",
        "nix": "pi-coding-agent",
    },
    "forge": {
        "npm": "forgecode",
    },
    "devin": {
        "cask": "devin-cli",
        "npm": "devin",
        "script": "https://cli.devin.ai/install.sh",
    },
    "droid": {
        "cask": "droid",
        "npm": "droid",
        "script": "https://app.factory.ai/cli",
    },
    "augment": {
        "npm": "@augmentcode/auggie",
    },
    "kiro": {
        "cask": "kiro-cli",
        "nix": "kiro-cli",
        "script": "https://cli.kiro.dev/install",
    },
    "blackbox": {
        "npm": "@blackbox_ai/blackbox-cli",
    },
    "freebuff": {
        "npm": "freebuff",
    },
    # Not in HARNESS_CATALOG (discovery does not recognise them), kept so
    # `swe install jules` / `swe install mastracode` still work.
    "jules": {
        "npm": "@google/jules",
    },
    "mastracode": {
        "npm": "mastracode",
    },
}

# Vendor scripts that are POSIX sh; every other script is piped to bash,
# matching each vendor's documented one-liner.
_SH_SCRIPTS = frozenset({
    "https://app.factory.ai/cli",
    "https://aider.chat/install.sh",
})


@dataclass(frozen=True)
class Installer:
    via: str
    package: str  # formula, cask token, npm/PyPI package, nixpkgs attr, or script URL

    def display(self) -> str:
        """The command as a person would type it."""
        return " ".join(self.argv()) if self.via != "script" else self._script_line()

    def argv(self, npm: str = "npm") -> list[str]:
        """What `swe install --via` runs. ``npm`` is the resolved npm binary."""
        if self.via == "brew":
            return ["brew", "install", self.package]
        if self.via == "cask":
            return ["brew", "install", "--cask", self.package]
        if self.via == "npm":
            return [npm, "install", "-g", self.package]
        if self.via == "uv":
            return ["uv", "tool", "install", self.package]
        if self.via == "nix":
            return ["nix", "profile", "install", f"nixpkgs#{self.package}"]
        if self.via == "script":
            return ["/bin/bash", "-o", "pipefail", "-c", self._script_line()]
        raise ValueError(f"unknown install method: {self.via}")

    def _script_line(self) -> str:
        shell = "sh" if self.package in _SH_SCRIPTS else "bash"
        return f"curl -fsSL {self.package} | {shell}"


def installers_for(name: str) -> list[Installer]:
    """Known installers for ``name`` in METHODS order; empty when unknown."""
    spec = INSTALLERS.get(name) or {}
    return [Installer(via, spec[via]) for via in METHODS if via in spec]
