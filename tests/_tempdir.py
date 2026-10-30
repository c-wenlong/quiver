"""A temp folder a test removes when it ends.

``tempfile.mkdtemp()`` in a test leaves the folder behind on every run;
``temp_dir(self)`` is the short way to the cleaned-up kind.
"""

import tempfile
import unittest
from pathlib import Path


def temp_dir(case: unittest.TestCase) -> Path:
    """A new empty folder, removed when ``case``'s test ends.

    Cleanup errors are ignored: a background thread a test left behind
    may still be writing there, which is not what the test checks.
    """
    return Path(case.enterContext(
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True)))
