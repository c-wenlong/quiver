"""Compile the hub's authored instruction chapters into its shared file."""

from .compiler import InstructionError, build

__all__ = ["InstructionError", "build"]
