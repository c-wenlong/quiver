## Summary

<!-- What does this PR do? One or two sentences. -->

## Type of change

- [ ] Bug fix
- [ ] New feature (parser, MCP format, command, etc.)
- [ ] Documentation
- [ ] Refactor / chore

## Checklist

- [ ] Tests pass against a throwaway `$HOME` — `HOME="$(mktemp -d)" python3 -m unittest discover -s tests -p 'test_*.py'`
- [ ] Reinstalled (`pip install -e .`) and verified the real `swe` binary, not just `PYTHONPATH=src`
- [ ] Core CLI remains stdlib-only (optional deps go in `[project.optional-dependencies]`)
- [ ] No user state committed (`harness.json`, `mcp.json`, machine paths)
- [ ] Help text updated if a command changed — `swe doctor` fails on help/dispatch drift
- [ ] One concern per PR

## Test plan

<!-- How did you verify this works? -->

```bash
# commands you ran
```
