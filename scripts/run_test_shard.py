"""Run one shard of the unittest suite, for CI's parallel test jobs.

    python3 scripts/run_test_shard.py <index> <count>

Discovers the suite exactly as `python -m unittest discover -s tests -p
'test_*.py'` does, flattens it to single test cases, and runs every
<count>-th case starting at <index> (0-based).  Shards are taken by case,
not by file, so one slow file can not leave a shard running long; the
order is kept, so a class's cases stay contiguous and setUpClass still
runs once per class per shard.  Every case lands in exactly one shard.

A module that fails to import shows up as a single failing case, so it
fails whichever shard draws it rather than vanishing.
"""

import sys
import unittest
from pathlib import Path


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: run_test_shard.py <index> <count>")
    index, count = int(argv[0]), int(argv[1])
    if count < 1 or not 0 <= index < count:
        sys.exit(f"run_test_shard.py: shard {index} of {count} is out of range")

    # `python -m unittest` puts the working directory first on sys.path, and
    # some test modules import `tests.<helper>` through it.  Running this file
    # directly puts scripts/ there instead, so add the repo root back.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    cases = list(flatten(suite))
    picked = cases[index::count]
    print(f"Shard {index + 1} of {count}: {len(picked)} of {len(cases)} tests",
          flush=True)

    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(picked))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
