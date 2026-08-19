import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quiver.configuration import (
    CONFIG_FILE,
    DEFAULT_CONFIG,
    ConfigurationError,
    CorruptConfigurationError,
    build_report_setup,
    check_config,
    edit_config,
    get_value,
    interactive_report_setup,
    load_resolved_config,
    load_config,
    parse_config_value,
    resolve_config,
    report_setup_complete,
    save_config,
    select_editor,
    set_value,
    unset_value,
    validate_config,
)


class ConfigurationValueTests(unittest.TestCase):
    def test_dotted_operations_are_immutable_and_prune_empty_parents(self):
        original = {"report": {"max_workers": 3}}
        changed = set_value(original, "report.session.harness", "codex")
        self.assertEqual(get_value(changed, "report.session.harness"), "codex")
        self.assertIsNone(get_value(original, "report.session.harness"))

        removed = unset_value(changed, "report.session.harness")
        self.assertIsNone(get_value(removed, "report.session"))
        self.assertEqual(get_value(removed, "report.max_workers"), 3)

    def test_setting_through_scalar_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            set_value({"report": 1}, "report.session.model", "small")

    def test_parse_config_value_supports_json_scalars_and_arrays(self):
        self.assertEqual(parse_config_value("3"), 3)
        self.assertEqual(parse_config_value("true"), True)
        self.assertIsNone(parse_config_value("null"))
        self.assertEqual(parse_config_value('["--flag", "value"]'), ["--flag", "value"])
        self.assertEqual(parse_config_value("claude-sonnet"), "claude-sonnet")
        self.assertEqual(parse_config_value('{"not": "supported"}'), '{"not": "supported"}')

    def test_resolve_precedence_defaults_then_saved_then_overrides(self):
        resolved = resolve_config(
            {"report": {"max_workers": 7, "session": {"harness": "claude"}}},
            {"report": {"max_workers": 2, "session": {"model": "fast"}}},
        )
        self.assertEqual(resolved["report"]["max_workers"], 2)
        self.assertEqual(resolved["report"]["session"]["harness"], "claude")
        self.assertEqual(resolved["report"]["session"]["model"], "fast")
        self.assertEqual(resolved["report"]["max_summary_calls"], 20)
        self.assertEqual(DEFAULT_CONFIG["report"]["max_workers"], 3)

    def test_load_resolved_config_uses_runtime_default_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"report": {"max_workers": 8}}')
            with patch("quiver.configuration.CONFIG_FILE", path):
                resolved = load_resolved_config(
                    overrides={"report": {"max_summary_calls": 9}}
                )
            self.assertEqual(resolved["report"]["max_workers"], 8)
            self.assertEqual(resolved["report"]["max_summary_calls"], 9)


class ConfigurationIOTests(unittest.TestCase):
    def test_round_trip_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = {"report": {"max_workers": 4}}
            with patch("quiver.configuration.os.replace", wraps=__import__("os").replace) as replace:
                save_config(config, path)
            self.assertEqual(load_config(path), config)
            replace.assert_called_once()
            source, destination = replace.call_args.args
            self.assertEqual(Path(source).parent, path.parent)
            self.assertEqual(Path(destination), path)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_malformed_json_is_preserved_on_load_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            malformed = b'{"report": '
            path.write_bytes(malformed)
            self.assertEqual(load_config(path), {})
            self.assertTrue(check_config(path))
            with self.assertRaises(CorruptConfigurationError):
                save_config({}, path)
            self.assertEqual(path.read_bytes(), malformed)

    def test_default_path_uses_config_dir(self):
        # ~/.quiver/config/config.json since the roots merged in 0.2.7.
        self.assertEqual(CONFIG_FILE.name, "config.json")
        self.assertEqual(CONFIG_FILE.parent.name, "config")
        self.assertEqual(CONFIG_FILE.parent.parent.name, ".quiver")


class ConfigurationValidationTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        self.assertEqual(validate_config(DEFAULT_CONFIG), [])

    def test_report_setup_builds_valid_credential_free_config(self):
        config = build_report_setup(
            session_harness="CODEX",
            session_model="cheap-model",
            writer_harness="claude",
            writer_model="strong-model",
            session_args=["--base-url", "http://localhost:8080"],
        )
        self.assertEqual(get_value(config, "report.session.harness"), "codex")
        self.assertEqual(validate_config(config), [])
        self.assertTrue(report_setup_complete(config))

    def test_default_config_is_valid_but_setup_is_incomplete(self):
        self.assertFalse(report_setup_complete(DEFAULT_CONFIG))

    def test_invalid_harness_args_and_limits_are_reported(self):
        issues = validate_config({"report": {
            "session": {"harness": "droid", "args": "--fast"},
            "writer": {"harness": "codex", "args": []},
            "max_workers": 0,
        }})
        rendered = "\n".join(map(str, issues))
        self.assertIn("report.session.harness", rendered)
        self.assertIn("report.session.args", rendered)
        self.assertIn("report.max_workers", rendered)

    def test_secret_keys_and_credential_arguments_are_rejected(self):
        config = {"report": {
            "session": {"harness": "codex", "args": ["--api-key=abc"]},
            "writer": {"harness": "claude", "args": []},
            "api_key": "abc",
        }}
        rendered = "\n".join(map(str, validate_config(config)))
        self.assertIn("credentials must not be stored", rendered)
        self.assertIn("credential-bearing arguments", rendered)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigurationError):
                save_config(config, Path(tmp) / "config.json")

    def test_token_budget_argument_is_not_mistaken_for_a_secret(self):
        config = {"report": {
            "session": {"harness": "codex", "args": ["--token-budget=1000"]},
            "writer": {"harness": "claude", "args": []},
        }}
        self.assertEqual(validate_config(config), [])

    def test_interactive_setup_only_asks_for_harnesses_and_models(self):
        answers = iter(["codex", "small", "claude", "large"])
        prompts = []

        def answer(prompt):
            prompts.append(prompt)
            return next(answers)

        config = interactive_report_setup(input_fn=answer)
        self.assertEqual(get_value(config, "report.writer.model"), "large")
        self.assertEqual(len(prompts), 4)
        self.assertFalse(any("key" in prompt.lower() or "token" in prompt.lower() for prompt in prompts))

    def test_interactive_setup_keeps_returning_user_values_on_enter(self):
        base = build_report_setup(
            session_harness="codex",
            session_model="gpt-small",
            writer_harness="claude",
            writer_model="opus",
        )
        prompts = []

        configured = interactive_report_setup(
            base,
            input_fn=lambda prompt: prompts.append(prompt) or "",
        )

        self.assertEqual(get_value(configured, "report.session.model"), "gpt-small")
        self.assertEqual(get_value(configured, "report.writer.model"), "opus")
        self.assertTrue(any("[gpt-small]" in prompt for prompt in prompts))
        self.assertTrue(any("[opus]" in prompt for prompt in prompts))


class EditorTests(unittest.TestCase):
    def test_visual_precedes_editor_and_is_shell_split(self):
        self.assertEqual(
            select_editor({"VISUAL": "code --wait", "EDITOR": "vim"}),
            ["code", "--wait"],
        )
        self.assertEqual(select_editor({"EDITOR": "nano"}), ["nano"])

    def test_missing_editor_is_actionable(self):
        with self.assertRaises(ConfigurationError):
            select_editor({})

    def test_edit_opens_selected_editor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            run = Mock(return_value=Mock(returncode=0))
            result = edit_config(path, env={"VISUAL": "code --wait"}, run=run)
            self.assertEqual(result, 0)
            run.assert_called_once_with(["code", "--wait", str(path)], check=False)
            self.assertEqual(json.loads(path.read_text()), {})


if __name__ == "__main__":
    unittest.main()
