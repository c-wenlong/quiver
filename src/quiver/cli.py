"""Compatibility entry: ``python -m quiver.cli`` runs ``swe``'s CLI."""

from swe.cli import main

if __name__ == "__main__":
    main()
