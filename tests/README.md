# quiver tests

Run all tests (from the repo root, with the package installed — e.g. `pip install -e .`):

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

## Coverage by package

| Test file | Package | What it covers |
| --- | --- | --- |
| `test_harness_registry.py` | `quiver.harness` | Registry load/save, alias resolution, default seeding |
| `test_harness_discover.py` | `quiver.harness` | PATH scan for AI coding CLIs |
| `test_sessions_aggregator.py` | `quiver.sessions` | Parser registry, sort/filter by time, agent, cwd |
| `test_models_analytics.py` | `quiver.sessions` | Model provider classification |
| `test_skills_discovery.py` | `quiver.skills` | SKILL.md parsing, root dedup, discovery |
| `test_skills_catalogs.py` | `quiver.skills` | Catalog add/list/remove, Desktop/Documents discover |
| `test_skills_layout.py` | `quiver.skills` | Tree layout, link/unlink/move, skill_links.json |
| `test_skills_symlinks.py` | `quiver.skills` | Symlink dedup and visible-via resolution |
| `test_mcp_formats.py` | `quiver.mcp` | MCP format handler parse/emit/conversion |
| `test_mcp_discover.py` | `quiver.mcp` | MCP server discovery vs mcp.json |
| `test_mcp_sync_integration.py` | `quiver.mcp` | End-to-end `python -m quiver.mcp` sync/validate/doctor |

Integration tests run against a throwaway `$HOME` and never touch your real config.
