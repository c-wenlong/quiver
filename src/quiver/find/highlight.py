"""Colour a single line of source for the browser's preview pane.

Deliberately regex-based and small. The CLI is stdlib-only, and a preview
pane is not a code editor: it needs a reader to recognise a heading, a
string or a comment at a glance, not a correct parse. Pygments is used
when it happens to be installed, and nothing depends on it being there.

Highlighting runs after truncation, never before. The pane cuts lines to
its own width, and cutting a line that already carries escape sequences
would slice one in half and bleed colour across the rest of the row.
"""

from __future__ import annotations

import re
from pathlib import Path

RESET = "\033[0m"

# 256-colour codes rather than the eight basic ones. The basic set is
# remapped by most terminal themes, so a "green" string can arrive as
# whatever the user set green to, while these stay recognisable.
STYLE = {
    "comment": "\033[38;5;245m",
    "string": "\033[38;5;114m",
    "number": "\033[38;5;180m",
    "keyword": "\033[38;5;176m",
    "key": "\033[38;5;81m",
    "heading": "\033[38;5;75m\033[1m",
    "fence": "\033[38;5;245m",
    "bullet": "\033[38;5;180m",
    "emphasis": "\033[38;5;222m",
    "plain": "\033[38;5;250m",
}

# Suffix to grammar. Anything unlisted is shown plain, which is the right
# default: a wrong guess about a language is more distracting than none.
LANGUAGES = {
    ".md": "markdown", ".markdown": "markdown",
    ".json": "json",
    ".toml": "toml", ".ini": "toml", ".cfg": "toml",
    ".yaml": "yaml", ".yml": "yaml",
    ".py": "python",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ts": "clike", ".js": "clike",
}

PY_KEYWORDS = (
    "def|class|return|import|from|if|elif|else|for|while|try|except|finally|"
    "with|as|and|or|not|in|is|None|True|False|lambda|yield|raise|assert|"
    "async|await|pass|break|continue|global|nonlocal|del"
)
SH_KEYWORDS = (
    "if|then|else|elif|fi|for|while|do|done|case|esac|function|return|"
    "export|local|source|echo|set|cd|exit"
)
JS_KEYWORDS = (
    "const|let|var|function|return|if|else|for|while|import|export|from|"
    "class|extends|new|await|async|try|catch|finally|throw|typeof|interface|"
    "type|true|false|null|undefined"
)


def language_for(path: Path) -> str:
    """Grammar name for ``path``, or "" when there is nothing sensible."""
    try:
        return LANGUAGES.get(path.suffix.lower(), "")
    except Exception:
        return ""


def _paint(text: str, kind: str) -> str:
    return STYLE[kind] + text + RESET


def _scan(line: str, rules: list[tuple[str, str]]) -> str:
    """Colour a line in one pass, longest rule first.

    Applying one regex after another does not work here: the second pass
    sees the escape sequences the first inserted and happily paints the
    digits inside them, so "38" out of "\033[38;5;81m" comes back
    coloured and the sequence is destroyed. Scanning once means every
    character is consumed exactly once and no rule can see another's
    output.
    """
    combined = "|".join(f"(?P<{name}>{pattern})" for name, pattern in rules)
    out, last = [], 0
    for match in re.finditer(combined, line):
        kind = match.lastgroup
        if kind is None:
            continue
        out.append(line[last:match.start()])
        out.append(_paint(match.group(0), kind))
        last = match.end()
    out.append(line[last:])
    return "".join(out)


STR_RE = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''
NUM_RE = r"\b\d+(?:\.\d+)?\b"

JSON_RULES = [
    ("key", r'"(?:[^"\\]|\\.)*"(?=\s*:)'),
    ("string", r'"(?:[^"\\]|\\.)*"'),
    ("keyword", r"\b(?:true|false|null)\b"),
    ("number", NUM_RE),
]

TOML_RULES = [
    ("comment", r"#.*$"),
    ("heading", r"^\s*\[[^\]]*\]"),
    ("string", STR_RE),
    ("key", r"^\s*[A-Za-z_][\w.-]*(?=\s*=)"),
    ("number", NUM_RE),
]

YAML_RULES = [
    ("comment", r"#.*$"),
    ("string", STR_RE),
    ("key", r"^\s*-?\s*[A-Za-z_][\w.-]*(?=\s*:)"),
    ("number", NUM_RE),
]


def _code_rules(keywords: str, comment: str) -> list[tuple[str, str]]:
    # Strings first, so a marker inside one is not read as a comment.
    return [
        ("string", STR_RE),
        ("comment", re.escape(comment) + r".*$"),
        ("keyword", rf"\b(?:{keywords})\b"),
        ("number", NUM_RE),
    ]


MARKDOWN_RULES = [
    ("string", r"`[^`]*`"),
    ("emphasis", r"\*\*[^*]+\*\*"),
]


def _markdown(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("```"):
        return _paint(line, "fence")
    if stripped.startswith("#"):
        return _paint(line, "heading")
    bullet = re.match(r"^(\s*)([-*+]|\d+[.)])(\s)", line)
    if bullet:
        # Only the marker. Painting the whole item turns a bulleted page
        # into one solid block of colour.
        rest = _scan(line[bullet.end():], MARKDOWN_RULES)
        return bullet.group(1) + _paint(bullet.group(2), "bullet") + bullet.group(3) + rest
    return _scan(line, MARKDOWN_RULES)


def highlight(line: str, language: str) -> str:
    """Colour one already-truncated line. Returns it unchanged on any doubt.

    Never raises: a preview that fails to colour should still show the
    text, and a pane is not worth a traceback.
    """
    if not language or not line.strip():
        return line
    try:
        if language == "markdown":
            return _markdown(line)
        if language == "json":
            return _scan(line, JSON_RULES)
        if language == "toml":
            return _scan(line, TOML_RULES)
        if language == "yaml":
            return _scan(line, YAML_RULES)
        if language == "python":
            return _scan(line, _code_rules(PY_KEYWORDS, "#"))
        if language == "shell":
            return _scan(line, _code_rules(SH_KEYWORDS, "#"))
        if language == "clike":
            return _scan(line, _code_rules(JS_KEYWORDS, "//"))
    except Exception:
        return line
    return line
