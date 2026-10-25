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

import os
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
    # No brew: the formula is deprecated upstream — Google replaced Gemini
    # CLI with Antigravity CLI for most users and Homebrew disables the
    # formula outright on 2026-12-18 (its named replacement is the
    # antigravity-cli cask).
    "gemini": {
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
        # nixpkgs renamed kilocode-cli to kilo; the old attr only lives on
        # as a warnAlias in pkgs/top-level/aliases.nix.
        "nix": "kilo",
    },
    "goose": {
        "brew": "block-goose-cli",
        "nix": "goose-cli",
        # The repo moved orgs: block/goose now redirects to aaif-goose/goose,
        # which is the path the project's own install docs and Homebrew's
        # block-goose-cli formula use.
        "script": "https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh",
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
    # The app's extra providers. kimi-code on PyPI is a legacy alias of the
    # archived kimi-cli, so no uv entry; nixpkgs grok-cli is superagent-ai's
    # unrelated CLI, so no nix entry for grok.
    "kimi": {
        "brew": "kimi-code",
        "npm": "@moonshot-ai/kimi-code",
        "script": "https://code.kimi.com/kimi-code/install.sh",
    },
    "grok": {
        "npm": "@xai-official/grok",
        "script": "https://x.ai/cli/install.sh",
    },
    "antigravity": {
        "cask": "antigravity",
        "nix": "antigravity-cli",
        "script": "https://antigravity.google/cli/install.sh",
    },
    "commandcode": {
        "npm": "command-code",
    },
    "qoder": {
        "cask": "qoderai/qoder/qodercli",
        "npm": "@qoder-ai/qodercli",
        "script": "https://qoder.com/install",
    },
    "qoder-cn": {
        "script": "https://qoder.com.cn/install",
    },
    # Not in HARNESS_CATALOG (discovery does not recognise them), kept so
    # `swe install jules` / `swe install mastracode` still work. zcode too:
    # a GUI app with no PATH binary to discover.
    "jules": {
        "npm": "@google/jules",
    },
    "mastracode": {
        "npm": "mastracode",
    },
    "zcode": {
        "cask": "zcode",
    },
}

# Vendor download or product pages, emitted as ``website`` in
# `swe install --json` for every provider the sheet can show. The sheet
# links to it when swe has no install command to offer — and to find the
# GUI app when only the CLI is in the table (qoder's cask is qodercli,
# not the IDE) — so a provider without one dead-ends. Where the vendor
# publishes the product only as a repo (qwen-code, crush) the repo is the
# canonical page; everything else is the vendor's own product or download
# URL, checked against cask/formula homepages and npm package metadata.
WEBSITES: dict[str, str] = {
    "claude": "https://claude.com/product/claude-code",
    "codex": "https://openai.com/codex/",
    "gemini": "https://geminicli.com",
    "copilot": "https://github.com/features/copilot/cli",
    "cursor": "https://cursor.com/cli",
    "qwen-code": "https://github.com/QwenLM/qwen-code",
    "mistral-vibe": "https://mistral.ai/products/vibe",
    "opencode": "https://opencode.ai",
    "crush": "https://github.com/charmbracelet/crush",
    "cline": "https://cline.bot",
    "kilo": "https://kilo.ai",
    "goose": "https://goose-docs.ai",
    "aider": "https://aider.chat",
    "continue": "https://continue.dev",
    "pi": "https://pi.dev",
    "forge": "https://forgecode.dev",
    "devin": "https://devin.ai",
    "droid": "https://factory.com",
    "augment": "https://augmentcode.com",
    "kiro": "https://kiro.dev",
    "blackbox": "https://blackbox.ai",
    "freebuff": "https://freebuff.com",
    "kimi": "https://code.kimi.com",
    "grok": "https://x.ai",
    "antigravity": "https://antigravity.google",
    "commandcode": "https://commandcode.ai",
    "qoder": "https://qoder.com",
    "qoder-cn": "https://qoder.com.cn",
    "jules": "https://jules.google",
    "mastracode": "https://mastra.ai",
    "zcode": "https://zcode.z.ai",
}

# GUI apps leave no binary on PATH; the .app bundle is the install
# evidence. A CLI-bearing provider (kiro, antigravity, qoder) keeps its
# bundle here too, so the sheet can say the desktop app is already on the
# Mac. Same locations the app's own provider detection checks.
GUI_APPS: dict[str, tuple[str, ...]] = {
    "antigravity": ("/Applications/Antigravity.app", "~/Applications/Antigravity.app"),
    "kiro": ("/Applications/Kiro CLI.app", "~/Applications/Kiro CLI.app"),
    "zcode": ("/Applications/ZCode.app", "~/Applications/ZCode.app"),
    "qoder": ("/Applications/Qoder.app", "~/Applications/Qoder.app"),
    "qoder-cn": ("/Applications/Qoder.app", "~/Applications/Qoder.app"),
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


def installed_app(name: str) -> str | None:
    """The .app bundle a GUI harness lives in, or None."""
    for path in GUI_APPS.get(name, ()):
        full = os.path.expanduser(path)
        if os.path.isdir(full):
            return full
    return None


def installed_cask(name: str) -> str | None:
    """The Caskroom record ``brew install --cask`` leaves for the harness's
    cask token, or None. That record is what proves brew owns the install,
    for a GUI app that never put a binary on PATH."""
    token = (INSTALLERS.get(name) or {}).get("cask")
    if not token:
        return None
    # Tap-qualified tokens record under the bare cask name.
    token = token.split("/")[-1]
    prefixes = (os.environ.get("HOMEBREW_PREFIX") or "", "/opt/homebrew", "/usr/local")
    for prefix in prefixes:
        if not prefix:
            continue
        room = os.path.join(prefix, "Caskroom", token)
        if os.path.isdir(room):
            return room
    return None


@dataclass(frozen=True)
class Uninstaller:
    via: str
    package: str  # cask token, formula, npm package, uv tool, or nix store path

    def display(self) -> str:
        """The uninstall command as a person would type it."""
        if self.via == "brew":
            return f"brew uninstall {self.package}"
        if self.via == "cask":
            return f"brew uninstall --cask {self.package}"
        if self.via == "npm":
            return f"npm uninstall -g {self.package}"
        if self.via == "uv":
            return f"uv tool uninstall {self.package}"
        if self.via == "nix":
            return f"nix profile remove {self.package}"
        raise ValueError(f"unknown uninstall method: {self.via}")


def _segment_after(parts: list[str], marker: str) -> str | None:
    """The path segment right after ``marker``, or None when absent."""
    if marker in parts:
        index = parts.index(marker)
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def uninstaller_for(name: str, real_path: str | None) -> Uninstaller | None:
    """How to remove an installed harness, read off where its binary lives.

    ``real_path`` is the binary with symlinks resolved. The package comes
    from the path itself, not the install table, because the table only
    says what swe would install: a cask installed as ``claude-code@latest``
    lives in ``Caskroom/claude-code@latest``. A vendor script leaves no
    such trace, so it returns None rather than guessing a file to delete.
    """
    if not real_path:
        return None
    parts = real_path.split("/")
    # A Homebrew keg owns everything under it, a Node CLI bundled in its
    # libexec/node_modules included. Only node's own keg holds packages npm
    # installed, when the npm prefix was left inside it.
    cask = _segment_after(parts, "Caskroom")
    if cask:
        return Uninstaller("cask", cask)
    formula = _segment_after(parts, "Cellar")
    if formula and formula != "node" and not formula.startswith("node@"):
        return Uninstaller("brew", formula)
    if "node_modules" in parts:
        index = len(parts) - 1 - parts[::-1].index("node_modules")
        rest = parts[index + 1:]
        if rest and rest[0].startswith("@") and len(rest) > 1:
            return Uninstaller("npm", f"{rest[0]}/{rest[1]}")
        if rest:
            return Uninstaller("npm", rest[0])
    if "uv" in parts:
        uv = parts.index("uv")
        if uv + 2 < len(parts) and parts[uv + 1] == "tools":
            return Uninstaller("uv", parts[uv + 2])
    if real_path.startswith("/nix/store/"):
        # Remove by store path, not the table's attr: the profile element
        # may carry a renamed name (nixpkgs renamed kilocode-cli to kilo,
        # so an install from before the rename is element "kilocode-cli"),
        # and `nix profile remove` matches an element on its store path.
        return Uninstaller("nix", "/".join(parts[:4]))
    return None
