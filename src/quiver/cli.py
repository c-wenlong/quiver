"""Compatibility entry: ``python -m quiver.cli`` runs ``swe``'s CLI."""

import sys

from swe.cli import main

if __name__ == "__main__":
    sys.exit(main())
