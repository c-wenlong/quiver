"""quiver.providers.keyfile: the Mac app's APIKeysFile and APIKeysStore."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.providers import keyfile
from quiver.providers.keyfile import KeyFileError, KeysFile


class TestParse(unittest.TestCase):
    def test_entries_take_the_heading_above_them(self):
        text = ("export LOOSE=1\n# Anthropic\nexport ANTHROPIC_API_KEY=\"a\\\"b\"\n"
                "# https://example.com/keys\nexport B='c d' # note\n"
                "# Kimi (Moonshot AI)\nexport KIMI_API_KEY=k\n")
        got = [(e.provider, e.name, e.value) for e in KeysFile(text).entries]
        self.assertEqual(got, [("", "LOOSE", "1"), ("Anthropic", "ANTHROPIC_API_KEY", 'a"b'),
                               ("Anthropic", "B", "c d"), ("Kimi (Moonshot AI)", "KIMI_API_KEY", "k")])

    def test_lines_it_cannot_round_trip_are_not_keys(self):
        for line in ['export A="open', "export A=x; export B=y", "export A=$HOME", 'export A="x"y',
                     'export A="$OTHER"', 'export A="pre-${B}"', 'export A="$(cat f)"',
                     'export A="`cat f`"', "EXPORT A=1", "A=1", "# export A=1"]:
            self.assertEqual(KeysFile(line + "\n").entries, [], line)

    def test_a_backslash_before_another_character_is_kept(self):
        self.assertEqual(keyfile.unquote('"a\\nb"'), "a\\nb")

    def test_an_escaped_dollar_is_a_literal_value(self):
        self.assertEqual(keyfile.unquote('"a\\$b\\`c"'), "a$b`c")
        self.assertEqual(keyfile.unquote("'$OTHER'"), "$OTHER")

    def test_an_unsafe_line_still_counts_as_an_assignment(self):
        later = 'export A="old"\nexport A="$OTHER"\n'
        keys = KeysFile(later)
        self.assertEqual(keys.keys, [])
        for call in (lambda: keys.set("A", "new", ""), lambda: keys.remove("A")):
            with self.assertRaises(KeyFileError) as caught:
                call()
            self.assertEqual(caught.exception.code, "duplicate")
            self.assertIn("can't edit safely", str(caught.exception.message))
        self.assertEqual(keys.text, later)
        only = KeysFile('export A="$OTHER"\n')
        with self.assertRaises(KeyFileError):
            only.set("A", "new", "")
        earlier = KeysFile('export A="$OTHER"\nexport A="old"\n')
        self.assertEqual([e.value for e in earlier.keys], ["old"])
        earlier.set("A", "new", "")
        self.assertEqual(earlier.text, 'export A="$OTHER"\nexport A="new"\n')

    def test_keys_holds_the_later_value_in_first_place(self):
        keys = KeysFile("export A=1\nexport B=2\nexport A=3\n").keys
        self.assertEqual([(e.name, e.value) for e in keys], [("A", "3"), ("B", "2")])

    def test_labels(self):
        for good in ["Anthropic", "ZAI (01.ai)", "Moonshot / Kimi", "x.ai"]:
            self.assertTrue(keyfile.is_label(good), good)
        for bad in ["", "123", "sk-ant-api03-abcdefgh", "a" * 61, "export FOO", "key: value", "=="]:
            self.assertFalse(keyfile.is_label(bad), bad)

    def test_render_escapes_what_double_quotes_need(self):
        line = keyfile.render("A", 'x"\\$`y')
        self.assertEqual(line, 'export A="x\\"\\\\\\$\\`y"')
        self.assertEqual(keyfile.export(line), ("A", 'x"\\$`y'))


class TestEdit(unittest.TestCase):
    def test_set_on_an_empty_file_ends_with_a_newline(self):
        keys = KeysFile("")
        keys.set("A_KEY", "v", "Acme")
        self.assertEqual(keys.text, '# Acme\nexport A_KEY="v"\n')

    def test_set_appends_a_new_group_after_a_blank_line(self):
        keys = KeysFile("# One\nexport A=1")
        keys.set("B", "2", "Two")
        self.assertEqual(keys.text, '# One\nexport A=1\n\n# Two\nexport B="2"')

    def test_set_refusals(self):
        keys = KeysFile("export A=1\nexport A=2\n")
        cases = [(("1A", "v", "P"), "bad_name"), (("B", "a\nb", "P"), "bad_value"),
                 (("B", "v", "P\nQ"), "bad_provider"), (("B", "v", "sk-ant-api03-abcdefghijk"), "bad_provider"),
                 (("A", "v", "P"), "duplicate")]
        for args, code in cases:
            with self.assertRaises(KeyFileError) as caught:
                keys.set(*args)
            self.assertEqual(caught.exception.code, code, args)

    def test_remove_keeps_one_blank_line_between_groups(self):
        keys = KeysFile("# One\nexport A=1\n\n# Two\nexport B=2\n\n# Three\nexport C=3\n")
        keys.remove("B")
        self.assertEqual(keys.text, "# One\nexport A=1\n\n# Three\nexport C=3\n")

    def test_remove_keeps_a_heading_with_keys_left(self):
        keys = KeysFile("# One\nexport A=1\nexport B=2\n")
        keys.remove("A")
        self.assertEqual(keys.text, "# One\nexport B=2\n")
        with self.assertRaises(KeyFileError) as caught:
            keys.remove("A")
        self.assertEqual(caught.exception.code, "not_found")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.path = self.home / ".quiver" / "secrets" / "providers" / ".api_keys"

    def test_a_new_file_is_private_and_its_folder_sealed(self):
        keys, stamp = keyfile.load(self.path)
        self.assertIsNone(stamp)
        keys.set("A", "v", "Acme")
        keyfile.save(self.path, keys, stamp, home=self.home)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.home / ".quiver" / "secrets" / ".gitignore").read_text().endswith("*\n"))
        self.assertFalse((self.home / ".quiver" / "backups" / "api-keys" / "api_keys.bak").exists())

    def test_a_save_through_a_symlink_keeps_the_link(self):
        real = self.home / "real_keys"
        real.write_text("export A=1\n")
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(real)
        keys, stamp = keyfile.load(self.path)
        keys.set("A", "2", "")
        keyfile.save(self.path, keys, stamp, home=self.home)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(real.read_text(), 'export A="2"\n')

    def test_a_symlink_loop_is_a_refusal_not_a_crash(self):
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(self.path.parent / "other")
        (self.path.parent / "other").symlink_to(self.path)
        for call in (lambda: keyfile.load(self.path),
                     lambda: keyfile.save(self.path, KeysFile(""), None, home=self.home)):
            with self.assertRaises(KeyFileError) as caught:
                call()
            self.assertEqual(caught.exception.code, "unreadable")

    def test_an_edit_while_saving_wins(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("export A=1\n")
        keys, stamp = keyfile.load(self.path)
        keys.set("A", "2", "")
        real = keyfile._private_temp

        def edit_meanwhile(data, destination):
            temp = real(data, destination)
            if destination.name == ".api_keys":
                self.path.write_text("export A=edited\n")
            return temp

        with patch.object(keyfile, "_private_temp", edit_meanwhile):
            with self.assertRaises(KeyFileError) as caught:
                keyfile.save(self.path, keys, stamp, home=self.home)
        self.assertEqual(caught.exception.code, "changed_on_disk")
        self.assertEqual(self.path.read_text(), "export A=edited\n")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [".api_keys"])

    def test_the_backup_folder_is_tightened(self):
        backups = self.home / ".quiver" / "backups" / "api-keys"
        backups.mkdir(parents=True)
        os.chmod(backups, 0o755)
        self.path.parent.mkdir(parents=True)
        self.path.write_text("export A=1\n")
        keys, stamp = keyfile.load(self.path)
        keys.remove("A")
        keyfile.save(self.path, keys, stamp, home=self.home)
        self.assertEqual(backups.stat().st_mode & 0o777, 0o700)
        self.assertEqual((backups / "api_keys.bak").read_text(), "export A=1\n")
        self.assertEqual((backups / "api_keys.bak").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
