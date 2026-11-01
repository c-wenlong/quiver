"""The generated shell scripts hand `swe __complete` the right words.

Each test sources the real script into its shell with `swe` replaced by a
function that records its arguments, so what is checked is exactly what the
shell would pass. Skipped where that shell is not installed.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from swe.completion_scripts import BASH_SCRIPT, FISH_SCRIPT, ZSH_SCRIPT


def _run(shell_argv, script):
    """Source ``script`` and return the lines the stub ``swe`` recorded.

    The stub writes to a file named by $PROBE_OUT because every script
    sends ``swe``'s stderr to /dev/null and keeps its stdout for itself.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "probe")
        out = os.path.join(tmp, "args")
        with open(path, "w") as fh:
            fh.write(script)
        open(out, "w").close()
        subprocess.run([*shell_argv, path], capture_output=True, text=True,
                       timeout=30, env={**os.environ, "PROBE_OUT": out})
        with open(out) as fh:
            return fh.read().splitlines()


@unittest.skipUnless(shutil.which("zsh"), "zsh not installed")
class ZshScriptTest(unittest.TestCase):
    def args_for(self, words, current):
        quoted = " ".join(f"'{w}'" for w in words)
        probe = (
            "compdef() { :; }\n_describe() { :; }\n"
            "swe() { for a in \"$@\"; do print -r -- \"[$a]\" >> $PROBE_OUT; done; }\n"
            + ZSH_SCRIPT
            + f"\nwords=({quoted})\nCURRENT={current}\n_swe\n"
        )
        return _run(["zsh", "-f"], probe)

    def test_empty_current_word_is_passed(self):
        # TAB after a space: the empty word is what tells __complete to
        # offer mcp's subcommands rather than complete "mcp" itself.
        self.assertEqual(self.args_for(["swe", "mcp", ""], 3), ["[__complete]", "[mcp]", "[]"])

    def test_words_after_the_cursor_are_not_passed(self):
        self.assertEqual(self.args_for(["swe", "mcp", "sy", "later"], 3),
                         ["[__complete]", "[mcp]", "[sy]"])


@unittest.skipUnless(shutil.which("bash"), "bash not installed")
class BashScriptTest(unittest.TestCase):
    def test_empty_current_word_is_passed(self):
        probe = (
            "complete() { :; }\n"
            "swe() { for a in \"$@\"; do echo \"[$a]\" >> \"$PROBE_OUT\"; done; }\n"
            + BASH_SCRIPT
            + "\nCOMP_WORDS=(swe mcp '')\nCOMP_CWORD=2\n_swe_completion\n"
        )
        self.assertEqual(_run(["bash", "--norc", "--noprofile"], probe),
                         ["[__complete]", "[mcp]", "[]"])


@unittest.skipUnless(shutil.which("fish"), "fish not installed")
class FishScriptTest(unittest.TestCase):
    def args_for(self, before, current):
        tokens = " ".join(f"'{w}'" for w in before)
        probe = (
            "function complete; end\n"
            f"function commandline\n"
            f"    if test \"$argv[1]\" = -opc\n        printf '%s\\n' {tokens}\n"
            f"    else if test -n '{current}'\n        echo '{current}'\n    end\nend\n"
            "function swe; for a in $argv; echo \"[$a]\" >> $PROBE_OUT; end; end\n"
            + FISH_SCRIPT
            + "\n__swe_complete\n"
        )
        return _run(["fish", "--no-config"], probe)

    def test_empty_current_word_is_passed(self):
        self.assertEqual(self.args_for(["swe", "mcp"], ""), ["[__complete]", "[mcp]", "[]"])

    def test_partial_current_word_is_passed(self):
        self.assertEqual(self.args_for(["swe", "mcp"], "sy"), ["[__complete]", "[mcp]", "[sy]"])


if __name__ == "__main__":
    unittest.main()
