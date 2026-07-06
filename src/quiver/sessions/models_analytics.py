"""Model usage analytics mined read-only from tool session logs."""

import glob
import os
import re
import sqlite3


def classify_provider(model: str) -> str:
    """Classify provider from model name using regex patterns."""
    model_lower = model.lower()
    patterns = [
        (r"^accounts?/fireworks/", "fireworks"),
        (r"^fireworks/", "fireworks"),
        (r"^openai/", "openai"),
        (r"^(gpt|o[1-9]|chatgpt)", "openai"),
        (r"^anthropic/", "anthropic"),
        (r"^(claude|claude-opus|claude-sonnet|claude-sonnet|haiku|<synthetic>)", "anthropic"),
        (r"^google/", "google"),
        (r"^(gemini|googlegemini)", "google"),
        (r"^deepseek/", "deepseek"),
        (r"^deepseek", "deepseek"),
        (r"^meta/", "meta"),
        (r"^(llama|llamameta)", "meta"),
        (r"^mistral/", "mistral"),
        (r"^mistral", "mistral"),
        (r"^aws?/bedrock/", "aws"),
        (r"^(aws|amazon)/", "aws"),
        (r"^azure/", "azure"),
        (r"^ollama/", "ollama"),
        (r"^local/", "local"),
        (r"^(qwen|qwen3|qwen2)", "alibaba"),
        (r"^(kimi|moonshot)", "moonshot"),
        (r"^minimax/", "minimax"),
        (r"^minimax", "minimax"),
        (r"^groq/", "groq"),
        (r"^(glm|zhipu)", "zhipu"),
        (r"^zhipu", "zhipu"),
        (r"^gemma", "google"),
        (r"^(big-pickle|bigpickle)", "bigscience"),
        (r"^synthesis|synthetic", "synthetic"),
        (r"^mimo", "xiaomi"),
    ]
    for pattern, provider in patterns:
        if re.match(pattern, model_lower):
            return provider
    return "other"


def _scan_jsonl_models(path: str, max_lines: int) -> dict[tuple[str, str], int]:
    seen: dict[tuple[str, str], int] = {}
    with open(path) as handle:
        for _ in range(max_lines):
            line = handle.readline()
            if not line:
                break
            match = re.search(r'"model":"([^"]+)"', line)
            if match:
                key = ("", match.group(1))
                seen[key] = seen.get(key, 0) + 1
                break
    return seen


def collect_model_usage() -> dict[str, dict[tuple[str, str], int]]:
    """Return raw model counts keyed by tool name."""
    raw: dict[str, dict[tuple[str, str], int]] = {}

    db_path = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT json_extract(data, '$.model.providerID'),
                       json_extract(data, '$.model.modelID'),
                       COUNT(*)
                FROM message
                WHERE json_extract(data, '$.model.modelID') IS NOT NULL
                GROUP BY 1, 2
                """
            )
            for provider, model, cnt in cur.fetchall():
                if model:
                    raw.setdefault("opencode", {})[(provider or "", model)] = cnt
            conn.close()
        except Exception:
            pass

    claude_dir = os.path.expanduser("~/.claude/projects/")
    if os.path.exists(claude_dir):
        try:
            seen: dict[tuple[str, str], int] = {}
            for directory in os.listdir(claude_dir):
                dir_path = os.path.join(claude_dir, directory)
                if not os.path.isdir(dir_path) or not directory.startswith("-"):
                    continue
                for jsonl in glob.glob(os.path.join(dir_path, "*.jsonl")):
                    for key, cnt in _scan_jsonl_models(jsonl, 30).items():
                        seen[key] = seen.get(key, 0) + cnt
            if seen:
                raw["claude"] = seen
        except Exception:
            pass

    codex_dir = os.path.expanduser("~/.codex/sessions/")
    if os.path.exists(codex_dir):
        try:
            seen: dict[tuple[str, str], int] = {}
            for jsonl in glob.glob(os.path.join(codex_dir, "*", "*", "*", "*.jsonl")):
                for key, cnt in _scan_jsonl_models(jsonl, 20).items():
                    seen[key] = seen.get(key, 0) + cnt
            if seen:
                raw["codex"] = seen
        except Exception:
            pass

    freebuff_dir = os.path.expanduser("~/.config/manicode/projects/")
    if os.path.exists(freebuff_dir):
        try:
            seen: dict[tuple[str, str], int] = {}
            for project in os.listdir(freebuff_dir):
                project_path = os.path.join(freebuff_dir, project)
                if not os.path.isdir(project_path):
                    continue
                chats_dir = os.path.join(project_path, "chats")
                if not os.path.exists(chats_dir):
                    continue
                for session_dir in os.listdir(chats_dir):
                    session_path = os.path.join(chats_dir, session_dir)
                    if not os.path.isdir(session_path):
                        continue
                    log_path = os.path.join(session_path, "log.jsonl")
                    if os.path.exists(log_path):
                        for key, cnt in _scan_jsonl_models(log_path, 50).items():
                            seen[key] = seen.get(key, 0) + cnt
            if seen:
                raw["freebuff"] = seen
        except Exception:
            pass

    return raw
