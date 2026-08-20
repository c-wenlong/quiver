"""Shared test fixtures.

Rendering became terminal-aware, so a table's resolved column widths now
depend on the window the suite happens to run in. Without pinning, a test
asserting on rendered output passes on a developer's wide terminal and
fails in a narrow CI shell, which is the worst kind of failure: real, but
about the environment rather than the code.
"""

import pytest


@pytest.fixture(autouse=True)
def _pin_terminal_width(monkeypatch):
    """Render every test against a fixed, generous width.

    Tests that care about narrow terminals should patch
    ``quiver.console.terminal_width`` themselves rather than relying on
    whatever shell invoked pytest.
    """
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "50")
    monkeypatch.setattr("quiver.console.terminal_width", lambda default=146: 200)
    yield
