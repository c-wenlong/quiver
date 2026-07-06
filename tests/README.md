# quiver tests

Run all tests (from the repo root, with the package installed — e.g. `pip install -e .`):

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Current coverage:

- `test_mcp_formats.py`
  - format handler parse/emit roundtrips
  - standard/opencode conversion behavior
  - conversion issue detection used by `--strict`

- `test_mcp_sync_integration.py`
  - `swe mcp sync --dry-run` does not write files
  - real `sync` writes and converts formats
  - `sync --strict` blocks malformed/unsupported entries
  - `validate` reports bad entries and returns non-zero
  - `doctor --strict` returns non-zero for unhealthy servers

  These run the MCP subsystem as `python -m quiver.mcp` against a throwaway
  `$HOME`, so they never touch your real config.
