# Code Coverage Audit

Audit date: 2026-09-12

## Baseline

The full `unittest` suite was measured with branch and subprocess tracking
enabled against the `quiver` package, using an empty temporary `$HOME`. All
1,695 tests passed (2 skipped).

| Metric | Covered | Total | Coverage |
| --- | ---: | ---: | ---: |
| Lines | 11,636 | 14,587 | 79.8% |
| Branches | 4,024 | 5,706 | 70.5% |
| Combined coverage.py score | 15,660 | 20,293 | 77.2% |

Run the same audit locally with:

```bash
pip install -e ".[test]"
test_home="$(mktemp -d)"
HOME="$test_home" python3 -m coverage run -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage combine
python3 -m coverage report
```

`coverage report` prints only the combined figure — the 77.2% in the table.
The line and branch rows above come from `coverage json -o cov.json`, whose
`totals` object carries `covered_lines` / `num_statements` and
`covered_branches` / `num_branches` separately.

CI enforces a 69% combined regression floor (`fail_under` in `.coveragerc`) and
uploads a report from Python 3.13 to Codecov. Codecov also reports
project movement and patch coverage. The floor is a backstop; it is not the
target.

Subprocess tracking is significant here: integration tests launch `swe` in a
fresh interpreter, and omitting those child measurements made command modules
look artificially untested. The temporary `$HOME` is equally important because
otherwise local coding-session archives change the result between machines.

## Priority Gaps

| Priority | Area | Combined coverage | Why it matters |
| --- | --- | ---: | --- |
| P1 | `mcp/discover_commands.py` | 0% | An entire user-facing command adapter with no test reaching it; `swe mcp discover --apply` writes to `mcp.json`. |
| P1 | `harness/discover_commands.py` | 8% | Same shape — the discovery logic underneath is tested, the CLI validation and apply paths are not. |
| P1 | `skills/layout_commands.py` | 9% | Link, unlink and move perform filesystem surgery on harness skill roots; success, refusal and failure paths all need command-level tests. |
| P1 | `skills/catalog_commands.py` | 19% | The largest untested command surface in the tree (192 units). |
| P1 | `setup/commands.py` | 36% | Legacy setup/check routing, still well behind the 77% step-by-step wizard it wraps. |
| P2 | `config_commands.py` | 56% | `swe config set/unset` writes `config/config.json`; type coercion and rejection branches are thin. |
| P2 | `mcp/cli.py` | 66% | Large read/write surface; validation, edit and refusal branches remain uncovered. |
| P2 | `reports/commands.py` | 66% | Approval, cancellation, writer failure, warnings and follow-up lifecycle are covered; argument/error variants are not. |
| P2 | `prompt.py` | 53% | TTY handling is awkward to test, but `read_line()` sits under `swe edit` and `swe setup`. |
| P2 | `harness/commands.py` | 78% | Improved from 70%; editing and rarer diagnostic branches still mix subprocess and filesystem behaviour. |
| P3 | `help_text.py` | 10% | Mostly literal help strings. `swe doctor`'s drift check already enforces the part that can go wrong. |

`mcp_formats.py` and `history/__init__.py` both read 0% and should stay that
way — they are compatibility shims that re-export another module and hold no
logic. `mcp/server.py` (0%) is behind the optional `server` extra and is not
installed in the test environment.

## Strong Areas

Provider key and registry handling (97-98%), report triage (99%), report
models (95%), follow-up state (92%), the report pipeline (89%), table rendering
(94%), `init/commands.py` (95%), `completion.py` (94%) and `keys.py` (100%)
are all in good shape. Model analytics sits at 83%, the setup wizard at 77%.
The session engines improved to 73% (JSON) and 75% (JSONL), and
`skills/catalog_discover.py` rose out of the old 0-8% discovery band to 66%.
An earlier pass of this audit found and fixed an OpenCode SQLite handle leak
on failed queries.

## Recommended Order

1. Cover the three discovery command adapters — MCP, harness, skills — around
   their validation and `--apply` paths.
2. Add command-level success, refusal and failure tests for skills layout and
   catalog operations.
3. Exercise legacy setup/check orchestration around the now-covered wizard
   stages.
4. Expand `swe config` type coercion and rejection branches.
5. Expand report command argument/error variants and transcript source readers.
