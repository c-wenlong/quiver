# Code Coverage Audit

Audit date: 2026-08-01

## Baseline

The full `unittest` suite was measured with branch and subprocess tracking
enabled against the `quiver` package, using an empty temporary `$HOME`. All 710
tests passed during the audit, including a warning-as-error run for leaked
resources.

| Metric | Covered | Total | Coverage |
| --- | ---: | ---: | ---: |
| Lines | 7,269 | 9,875 | 73.6% |
| Branches | 2,362 | 3,824 | 61.8% |
| Combined coverage.py score | 9,631 | 13,699 | 70.3% |

Run the same audit locally with:

```bash
pip install -e ".[test]"
test_home="$(mktemp -d)"
HOME="$test_home" python -m coverage run -m unittest discover -s tests -p 'test_*.py'
python -m coverage combine
python -m coverage report
```

CI enforces a 69% combined regression floor and uploads reports from Python
3.10 and 3.13 to Codecov. Codecov also reports project movement and patch
coverage. The floor is a backstop; it is not the target.

Subprocess tracking is significant here: integration tests launch `swe` in a
fresh interpreter, and omitting those child measurements made command modules
look artificially untested. The temporary `$HOME` is equally important because
otherwise local coding-session archives change the result between machines.

## Priority Gaps

| Priority | Area | Combined coverage | Why it matters |
| --- | --- | ---: | --- |
| P1 | `mcp/cli.py` | 65% | Large user-facing read/write command surface still has validation, edit, and refusal branches uncovered. |
| P1 | `setup/commands.py` | 24% | Legacy setup/check routing remains much less exercised than the 77% step-by-step wizard. |
| P1 | `harness/commands.py` | 70% | Editing and less common diagnostic branches still combine subprocess and filesystem behavior. |
| P1 | `reports/commands.py` | 66% | Core approval, cancellation, writer failure, warnings, and follow-up lifecycle paths are covered; argument/error variants remain. |
| P2 | MCP, harness, and skills discover command adapters | 0-8% | Core discovery functions have tests, but their CLI validation and apply paths do not. |
| P2 | Skills layout and catalog commands | 6-19% | Filesystem operations need command-level success, refusal, and failure tests. |
| P2 | Session JSON and JSONL engines | 71% and 64% | Malformed siblings, fallback paths, and callback failures are covered; less common discovery modes remain. |

## Strong Areas

Provider key and registry handling, report triage, table rendering, report
models, follow-up state, and the report pipeline remain strong. Model analytics
rose to 83%, the setup wizard to 77%, and report commands to 66%. The audit also
found and fixed an OpenCode SQLite handle leak on failed queries.

## Recommended Order

1. Add MCP CLI integration tests around validate, sync, edit, and refusal paths.
2. Exercise legacy setup/check orchestration around the now-covered wizard stages.
3. Add command-level discovery and skills filesystem failure tests.
4. Expand report command argument/error variants and transcript source readers.
5. Cover remaining parser discovery modes with malformed neighboring records.
