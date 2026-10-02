"""`mcp edit` temp file secrecy + providers --file containment.

The old edit flow wrote resolved secrets to a predictable
``~/.quiver/config/.mcp-edit-tmp.json``: symlink-following, umask-mode,
left behind on a crashed editor, inside the versioned git tree. And
``providers add --file=../../x`` stored a path that ``find_key_file``
joined verbatim — a masked-read oracle for any file on the box.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quiver.mcp import cli as mcp_cli
from quiver.providers.keys import find_key_file
from quiver.providers import commands as providers_commands


class McpEditTmpFileTest(unittest.TestCase):
    """cmd_edit's scratch file must be private, unpredictable, and always
    cleaned up — including when the editor crashes."""

    def _run_edit(self, editor, **patches):
        seen = {}

        def fake_run(argv, **kw):
            path = Path(argv[-1])
            seen["path"] = path
            seen["mode"] = stat.S_IMODE(path.stat().st_mode)
            seen["content"] = path.read_text()
            return subprocess.CompletedProcess(argv, 0)

        defaults = dict(
            load_registry=lambda: {},
            get_tool_config=lambda tool: {
                "path": Path("/nonexistent-target.json"),
                "key": "mcpServers",
                "format": "standard",
            },
            get_tool_loader=lambda tool: (lambda p: {"x": {"command": "npx"}}),
            get_tool_saver=lambda tool: (lambda servers, p: None),
            resolve_tool_arg=lambda reg, name: name,
        )
        defaults.update(patches)
        buf = io.StringIO()
        with patch.object(mcp_cli, "load_registry", defaults["load_registry"]), \
             patch.object(mcp_cli, "get_tool_config", defaults["get_tool_config"]), \
             patch.object(mcp_cli, "get_tool_loader", defaults["get_tool_loader"]), \
             patch.object(mcp_cli, "get_tool_saver", defaults["get_tool_saver"]), \
             patch.object(mcp_cli, "resolve_tool_arg", defaults["resolve_tool_arg"]), \
             patch.dict(os.environ, {"EDITOR": editor}), \
             patch("subprocess.run", fake_run), \
             redirect_stdout(buf):
            rc = mcp_cli.cmd_edit(["claude", "x"])
        return rc, seen, buf.getvalue()

    def test_tmp_file_is_private_unpredictable_and_cleaned_up(self):
        rc, seen, _ = self._run_edit("true")
        self.assertEqual(rc, 0)
        path = seen["path"]
        # Outside ~/.quiver entirely, unpredictable name, mode 0600.
        self.assertNotIn(".quiver", str(path))
        self.assertIn("quiver-mcp-edit-", path.name)
        self.assertEqual(seen["mode"], 0o600)
        # Resolved secret content reached the editor...
        self.assertIn("npx", seen["content"])
        # ...and is gone afterwards.
        self.assertFalse(path.exists())

    def test_editor_crash_still_cleans_up(self):
        def boom(argv, **kw):
            raise FileNotFoundError("editor binary missing")

        buf = io.StringIO()
        created = []

        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*a, **kw):
            fd, name = real_mkstemp(*a, **kw)
            created.append(name)
            return fd, name

        with patch.object(mcp_cli, "load_registry", lambda: {}), \
             patch.object(mcp_cli, "resolve_tool_arg", lambda reg, name: name), \
             patch.object(
                 mcp_cli, "get_tool_config",
                 lambda tool: {"path": Path("/t.json"), "key": "mcpServers",
                               "format": "standard"},
             ), \
             patch.object(
                 mcp_cli, "get_tool_loader",
                 lambda tool: (lambda p: {"x": {"command": "npx"}}),
             ), \
             patch.object(
                 mcp_cli, "get_tool_saver", lambda tool: (lambda s, p: None)
             ), \
             patch.dict(os.environ, {"EDITOR": "/nonexistent-editor"}), \
             patch("subprocess.run", boom), \
             patch("tempfile.mkstemp", spy_mkstemp), \
             redirect_stdout(buf):
            rc = mcp_cli.cmd_edit(["claude", "x"])
        self.assertEqual(rc, 1)
        self.assertIn("Editor failed", buf.getvalue())
        for name in created:
            self.assertFalse(Path(name).exists(), f"leaked tmp file {name}")


class ProviderFileContainmentTest(unittest.TestCase):
    def test_absolute_key_filename_rejected(self):
        self.assertIsNone(find_key_file({"key_filename": "/etc/passwd"}, Path("/k")))

    def test_traversal_key_filename_rejected(self):
        self.assertIsNone(
            find_key_file({"key_filename": "../../.ssh/id_ed25519"}, Path("/k"))
        )
        self.assertIsNone(
            find_key_file({"key_filename": "a/../../x"}, Path("/k"))
        )

    def test_plain_name_still_resolves(self):
        self.assertEqual(
            find_key_file({"key_filename": "openai"}, Path("/k")),
            Path("/k/openai"),
        )

    def test_symlink_escaping_keys_dir_rejected(self):
        """A clean name whose symlink points outside must not resolve."""
        with tempfile.TemporaryDirectory() as tmp:
            keys_dir = Path(tmp) / "keys"
            keys_dir.mkdir()
            outside = Path(tmp) / "outside_secret"
            outside.write_text("shh")
            (keys_dir / "openai").symlink_to(outside)
            self.assertIsNone(
                find_key_file({"key_filename": "openai"}, keys_dir)
            )

    def test_circular_symlink_reads_as_missing(self):
        """A symlink loop must never produce key content or a traceback.

        Python <= 3.12 raises RuntimeError inside resolve() (caught, ->
        None); 3.13 resolves the loop to itself, and read_key's ELOOP
        then reports missing. Pin the contract both ways: no content.
        """
        from quiver.providers.keys import read_key

        with tempfile.TemporaryDirectory() as tmp:
            keys_dir = Path(tmp) / "keys"
            keys_dir.mkdir()
            (keys_dir / "loopy").symlink_to(keys_dir / "loopy")
            key_path = find_key_file({"key_filename": "loopy"}, keys_dir)
            self.assertIsNone(read_key(key_path))

    def test_symlink_staying_inside_keys_dir_allowed(self):
        """A link whose target still lives under keys_dir is fine."""
        with tempfile.TemporaryDirectory() as tmp:
            keys_dir = Path(tmp) / "keys"
            keys_dir.mkdir()
            (keys_dir / "real").write_text("key")
            link = keys_dir / "alias"
            link.symlink_to(keys_dir / "real")
            self.assertEqual(
                find_key_file({"key_filename": "alias"}, keys_dir), link
            )

    def test_cmd_add_rejects_absolute_file(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = providers_commands.cmd_add(["p", "--file=/tmp/outside"])
        self.assertEqual(rc, 1)
        self.assertIn("inside the keys directory", buf.getvalue())

    def test_cmd_add_rejects_traversal_file(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = providers_commands.cmd_add(["p", "--file=../../secret"])
        self.assertEqual(rc, 1)
        self.assertIn("inside the keys directory", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
