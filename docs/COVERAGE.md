# Code Coverage Audit

Audit date: 2026-10-31 (main at `8431e96f`)

## Baseline

The full `unittest` suite was measured with branch and subprocess tracking
enabled against the `quiver` package, using an empty temporary `$HOME`. All
6,767 tests passed (5 skipped).

| Metric | Covered | Total | Coverage |
| --- | ---: | ---: | ---: |
| Lines | 44,974 | 48,539 | 92.7% |
| Branches | 16,337 | 18,586 | 87.9% |
| Combined coverage.py score | 61,311 | 67,125 | 91.3% |

The previous audit (2026-09-12) measured 77.2% combined over 14,587 lines;
the package has more than tripled since, and coverage rose with it.

Run the same audit locally with:

```bash
pip install -e ".[test]"
test_home="$(mktemp -d)"
HOME="$test_home" python3 -m coverage run -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage combine
python3 -m coverage report
```

`coverage report` prints only the combined figure, the 91.3% in the table.
The line and branch rows above come from `coverage json -o cov.json`, whose
`totals` object carries `covered_lines` / `num_statements` and
`covered_branches` / `num_branches` separately.

CI enforces an 84% combined regression floor (`fail_under` in `.coveragerc`)
and uploads a report from Python 3.13 to Codecov. Codecov also reports
project movement and patch coverage. The floor is a backstop; it is not the
target.

Subprocess tracking is significant here: integration tests launch `swe` in a
fresh interpreter, and omitting those child measurements made command modules
look artificially untested. The temporary `$HOME` is equally important because
otherwise local coding-session archives change the result between machines.

## Untested modules

No module with logic is left without a test. The only files at 0% are
`mcp_formats.py` and `history/__init__.py`, compatibility shims that
re-export another module and hold no logic; nothing in the tree imports
either any more. `mcp/server.py` (89%) is tested against a stand-in
`FastMCP`, since the optional `server` extra is not installed in the test
environment.

## Lowest coverage

| Module | Combined coverage | What is not reached |
| --- | ---: | --- |
| `providers/cli.py` | 65% | The `swe providers` dispatcher's help, unknown-subcommand and `ConfigurationError` branches. |
| `skills/catalog_discover.py` | 73% | `OSError` while resolving known roots, and the Desktop/Documents source labels for a catalog path. |
| `skills/catalogs.py` | 76% | A `catalogs` value that is not a list, replacing an existing catalog by label or path, and pruning nested catalogs. |
| `reports/batching.py` | 77% | The `_trim_text` / `_trim_both` compaction paths, where a transcript exceeds the batch budget. |
| `mcp/discover_commands.py` | 77% | `--help`, unknown-argument refusal and the versioned-path warning in `swe mcp discover`. |
| `harness/archive.py` | 77% | `save_archive`, which replaces the archived set wholesale. |
| `setup/wizard.py` | 79% | The report-runner stage's save and its refused-configuration path, among other prompt branches. |

The earlier P1 list (the discovery command adapters, skills layout and
catalog commands, legacy setup) is closed: `skills/layout_commands.py` is
at 100%, `setup/commands.py` at 95%, `skills/catalog_commands.py` at 88%
and `harness/discover_commands.py` at 84%.

## Recommended order

1. Cover `mcp/discover_commands.py`'s argument refusal, the one
   user-facing command adapter still under 80%.
2. Test `harness/archive.save_archive` and catalog replacement and pruning
   in `skills/catalogs.py`, both of which rewrite config files.
3. Exercise the compaction paths in `reports/batching.py`.
