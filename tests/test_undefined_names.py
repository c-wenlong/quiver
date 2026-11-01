"""No module under src/swe reads a global name it never defines.

`swe doctor` shipped calling `is_dir_on_path` from a module that never
imported it, so it raised NameError on every machine with an nvm node.
The suite missed it because the branch only runs when that directory
exists. A missing import is a static fact, though: the compiler's symbol
table already says which names each function reads as globals, and every
one of those has to be bound at module level or be a builtin.
"""

import builtins
import symtable
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "swe"

_IMPLICIT = set(dir(builtins))


def _undefined(path: Path, root: Path = SRC.parent) -> list[str]:
    top = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
    symbols = top.get_symbols()
    if any(s.get_name() == "*" for s in symbols):
        return []          # a star import binds names the table cannot see
    bound = {s.get_name() for s in symbols
             if s.is_assigned() or s.is_imported() or s.is_namespace()}
    out = []

    def walk(table):
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced() or sym.is_declared_global():
                continue
            if not (sym.is_global() or table.get_type() == "module"):
                continue
            # Dunders (__file__, and the compiler's own bookkeeping such as
            # 3.14's __conditional_annotations__) are always bound.
            if name.startswith("__") and name.endswith("__"):
                continue
            if name not in bound and name not in _IMPLICIT:
                out.append(f"{path.relative_to(root)}: {table.get_name()} reads {name}")
        for child in table.get_children():
            walk(child)

    walk(top)
    return out


class UndefinedNamesTest(unittest.TestCase):
    def test_every_global_read_is_bound(self):
        problems = [p for path in sorted(SRC.rglob("*.py")) for p in _undefined(path)]
        self.assertEqual(problems, [])

    def test_the_check_catches_a_missing_import(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text("import os\n\ndef f(d):\n    return [x for x in d if helper(x)]\n")
            self.assertEqual(len(_undefined(probe, Path(tmp))), 1)


if __name__ == "__main__":
    unittest.main()
