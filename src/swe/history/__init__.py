"""Backward-compatible re-exports (prefer ``swe.sessions``)."""

from swe.sessions import Session, get_all_sessions

__all__ = ["Session", "get_all_sessions"]
