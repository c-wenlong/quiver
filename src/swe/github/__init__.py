"""GitHub read surface: PRs, issues, actions, notifications and repo tabs."""


def main(argv=None):
    from swe.github.cli import main as _cli_main

    return _cli_main(argv)


__all__ = ["main"]
