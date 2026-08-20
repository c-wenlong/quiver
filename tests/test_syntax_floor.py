"""Every source file must compile on the oldest Python this project supports.

Nested f-strings with implicit concatenation inside a replacement field are
PEP 701, valid only on 3.12+. They pass on a 3.12 interpreter and then crash
the CLI on import for anyone on 3.10, which is exactly what shipped in v0.2.8.

``ast.parse(feature_version=...)`` does not help: it gates a short list of AST
features and accepts this syntax even at ``(3, 8)``. The only reliable check is
to hand the source to an older interpreter, so that is what this does. The
CI matrix is still the real gate; this makes the failure local and immediate.
"""

import pathlib
import re
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _floor() -> tuple[int, int]:
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'requires-python\s*=\s*"[^0-9]*(\d+)\.(\d+)"', text)
    assert m, "requires-python not found in pyproject.toml"
    return int(m.group(1)), int(m.group(2))


def _older_interpreter(floor: tuple[int, int]) -> str | None:
    """An interpreter at or below the declared floor, if the machine has one."""
    candidates = [f"python{floor[0]}.{floor[1]}"]
    candidates += [f"python{floor[0]}.{m}" for m in range(floor[1] - 1, 5, -1)]
    candidates += ["/usr/bin/python3"]
    for cand in candidates:
        exe = shutil.which(cand) if not cand.startswith("/") else (
            cand if pathlib.Path(cand).exists() else None)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "-c", "import sys;print(sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            major, minor = eval(out.stdout.strip())  # noqa: S307 - our own output
            if (major, minor) <= floor:
                return exe
    return None


class SyntaxFloorTest(unittest.TestCase):
    def test_source_compiles_on_the_oldest_supported_python(self):
        floor = _floor()
        exe = _older_interpreter(floor)
        if exe is None:
            self.skipTest(
                f"no interpreter <= {floor[0]}.{floor[1]} on this machine; "
                "the CI matrix covers this"
            )
        # The builtin compile(), not py_compile: the latter insists on writing
        # a .pyc and raises FileExistsError against /dev/null before it ever
        # reaches the parser, which silently hid the very bug this guards.
        script = (
            "import sys\n"
            "bad = []\n"
            "for p in sys.argv[1:]:\n"
            "    src = open(p, encoding='utf-8').read()\n"
            "    try:\n"
            "        compile(src, p, 'exec')\n"
            "    except SyntaxError as exc:\n"
            "        bad.append(f'{p}:{exc.lineno} {exc.msg}')\n"
            "print('\\n'.join(bad))\n"
        )
        files = [str(p) for p in sorted((ROOT / "src").rglob("*.py"))]
        out = subprocess.run([exe, "-c", script, *files],
                             capture_output=True, text=True, timeout=120)
        offenders = out.stdout.strip()
        self.assertEqual(
            offenders, "",
            f"syntax newer than Python {floor[0]}.{floor[1]} "
            f"(checked with {exe}):\n{offenders}",
        )
