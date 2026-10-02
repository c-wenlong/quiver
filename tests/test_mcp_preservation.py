"""MCP sync/summary warnings from the second audit pass.

1. Overwriting a server in a target config dropped the target's own
   format-extension keys: opencode's ``enabled: false`` silently became
   ``true``, droid's ``type: sse`` became ``http``, claude's
   ``disabled``/``autoApprove`` vanished. ``_merge_format_keys`` keeps
   non-canonical keys from the existing entry.

2. ``resolve()`` substitutes ``${NAME}`` anywhere in a string, but
   ``redact()`` only rewrote whole-field values — a ``?key=TOKEN`` URL
   got its literal credential persisted into the hub. Long secrets now
   redact wherever they appear.

3. ``server_summary`` printed resolved URLs to stdout/``--json``,
   leaking the credential. Summaries redact first.

4. Unverified registry names flowed into ``~/.<name>/mcp.json`` — a
   name with ``/`` or ``..`` became a write path outside the home dir.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quiver.mcp import cli as mcp_cli
from quiver.mcp.cli import _merge_format_keys
from quiver.mcp.secrets import redact


class MergeFormatKeysTest(unittest.TestCase):
    def test_extension_keys_survive_overwrite(self):
        existing = {
            "command": ["old", "x"],
            "enabled": False,
            "type": "local",
            "autoApprove": ["*"],
        }
        converted = {"command": ["new", "y"], "enabled": True, "type": "local"}
        merged = _merge_format_keys(existing, converted)
        self.assertEqual(merged["command"], ["new", "y"])
        self.assertIs(merged["enabled"], False)
        self.assertEqual(merged["type"], "local")
        self.assertEqual(merged["autoApprove"], ["*"])

    def test_sse_type_not_forced_to_http(self):
        existing = {"url": "https://a/mcp", "type": "sse"}
        converted = {"url": "https://a/mcp", "type": "http"}
        self.assertEqual(
            _merge_format_keys(existing, converted)["type"], "sse"
        )

    def test_canonical_fields_update(self):
        existing = {"command": "a", "env": {"OLD": "1"}, "timeout": 30}
        converted = {"command": "b", "env": {"NEW": "2"}}
        merged = _merge_format_keys(existing, converted)
        self.assertEqual(merged["command"], "b")
        self.assertEqual(merged["env"], {"NEW": "2"})
        self.assertEqual(merged["timeout"], 30)

    def test_non_dict_existing_returns_converted(self):
        self.assertEqual(
            _merge_format_keys("junk", {"command": "x"}), {"command": "x"}
        )


class SyncPreserveEndToEndTest(unittest.TestCase):
    """cmd_sync overwrite keeps the target entry's extension keys."""

    def _run_sync(self, source_servers, target_servers, target="opencode"):
        saved = {}

        def _cfg(t):
            if t == "opencode":
                return {"path": Path("/tmp/oc.json"), "key": "mcp",
                        "format": "opencode", "unverified": False}
            return {"path": Path(f"/tmp/{t}.json"), "key": "mcpServers",
                    "format": "standard", "unverified": False}

        with patch.object(mcp_cli, "load_registry", lambda: {}), \
             patch.object(mcp_cli, "resolve_tool_arg", lambda reg, n: n), \
             patch.object(mcp_cli, "get_tool_config", _cfg), \
             patch.object(
                 mcp_cli, "get_tool_servers",
                 lambda t: dict(source_servers) if t == "claude"
                 else dict(target_servers),
             ), \
             patch.object(
                 mcp_cli, "get_tool_saver",
                 lambda t: (lambda servers, p: saved.update(servers)),
             ), \
             redirect_stdout(io.StringIO()):
            rc = mcp_cli.cmd_sync(
                ["claude", target, "--no-interactive", "--force"]
            )
        return rc, saved

    def test_overwrite_preserves_disabled_state(self):
        rc, saved = self._run_sync(
            {"srv": {"command": "newcmd", "args": ["--v2"]}},
            {"srv": {"command": ["oldcmd"], "enabled": False,
                     "type": "local"}},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(saved["srv"]["command"], ["newcmd", "--v2"])
        self.assertIs(saved["srv"]["enabled"], False)
        self.assertEqual(saved["srv"]["type"], "local")

    def test_added_server_gets_emit_defaults(self):
        rc, saved = self._run_sync(
            {"srv": {"command": "c"}},
            {},
        )
        self.assertEqual(rc, 0)
        # New entries keep the format's own defaults (enabled: true).
        self.assertIs(saved["srv"]["enabled"], True)


class RedactParityTest(unittest.TestCase):
    def test_long_secret_embedded_in_url_is_redacted(self):
        secrets = {"API_KEY": "sk-abcdef0123456789xyz"}
        out = redact(
            {"url": "https://x/mcp?key=sk-abcdef0123456789xyz"}, secrets
        )
        self.assertEqual(out["url"], "https://x/mcp?key=${API_KEY}")

    def test_short_secret_stays_whole_field_only(self):
        secrets = {"TG_ID": "12345678"}
        # A short value embedded in text must not be substring-matched —
        # 8 digits could be a date or a counter, not a credential.
        out = redact({"url": "https://x/?id=12345678"}, secrets)
        self.assertEqual(out["url"], "https://x/?id=12345678")
        self.assertEqual(redact({"k": "12345678"}, secrets)["k"], "${TG_ID}")

    def test_scheme_prefixed_secret_still_redacts(self):
        secrets = {"TOK": "tok-abcdefghijklmnop"}
        self.assertEqual(
            redact({"h": "Bearer tok-abcdefghijklmnop"}, secrets)["h"],
            "Bearer ${TOK}",
        )

    def test_multiple_embedded_secrets_redact(self):
        secrets = {"A": "aaaaaaaaaaaaaaaaaaaa", "B": "bbbbbbbbbbbbbbbbbbbb"}
        out = redact(
            {"u": "https://x/?a=aaaaaaaaaaaaaaaaaaaa&b=bbbbbbbbbbbbbbbbbbbb"},
            secrets,
        )
        self.assertEqual(out["u"], "https://x/?a=${A}&b=${B}")


class SummaryMaskingTest(unittest.TestCase):
    def test_http_summary_masks_url_secret(self):
        with patch(
            "quiver.mcp.secrets.load_secrets",
            return_value={"K": "sk-abcdef0123456789xyz"},
        ):
            s = mcp_cli.server_summary(
                {"url": "https://x/mcp?key=sk-abcdef0123456789xyz"}
            )
        self.assertNotIn("sk-abcdef", s)
        self.assertIn("${K}", s)

    def test_wrapped_summary_masks_url_secret(self):
        with patch(
            "quiver.mcp.secrets.load_secrets",
            return_value={"K": "sk-abcdef0123456789xyz"},
        ):
            s = mcp_cli.server_summary(
                {"command": "npx",
                 "args": ["mcp-remote",
                          "https://x/mcp?key=sk-abcdef0123456789xyz"]}
            )
        self.assertNotIn("sk-abcdef", s)
        self.assertIn("${K}", s)


class UnverifiedNameSafetyTest(unittest.TestCase):
    def test_traversal_name_gets_no_config(self):
        for bad in ("../x", "a/b", "a\\b", "..", "a..b/c", "-x"):
            self.assertIsNone(
                mcp_cli.get_tool_config(bad), msg=f"{bad!r} got a config"
            )

    def test_normal_unverified_name_gets_config(self):
        cfg = mcp_cli.get_tool_config("myagent")
        self.assertIsNotNone(cfg)
        self.assertTrue(cfg["unverified"])
        self.assertIn(".myagent", str(cfg["path"]))

    def test_unsafe_name_skipped_in_mcp_tools(self):
        tools = mcp_cli.get_mcp_tools({"../evil": {}, "oktool": {}})
        self.assertIn("oktool", tools)
        self.assertNotIn("../evil", tools)

    def test_sync_to_unsafe_target_exits_cleanly(self):
        """A registry name that resolves but can't form a safe path must
        be rejected, not crash on target_cfg=None."""
        buf = io.StringIO()
        with patch.object(
            mcp_cli, "load_registry", lambda: {"../evil": {}, "claude": {}}
        ), patch.object(
            mcp_cli, "resolve_tool_arg",
            lambda reg, n: n if n in reg else None,
        ), redirect_stdout(buf):
            rc = mcp_cli.cmd_sync(["claude", "../evil", "--no-interactive"])
        self.assertEqual(rc, 1)
        self.assertIn("Unsafe harness name", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
