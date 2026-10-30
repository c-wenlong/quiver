# quiver tests

Run the whole suite from the repo root, with the package installed
(`pip install -e ".[test]"`), against a throwaway `HOME`:

```bash
HOME="$(mktemp -d)" python3 -m unittest discover -s tests -p 'test_*.py'
```

`swe` resolves `~/.quiver` at import time, so a run without the fresh
`HOME` reads and writes your real registry and caches. In a git worktree
add `PYTHONPATH=src`, or the editable install imports the main checkout's
code instead.

Files are named after the package they test: `test_harness_*.py` covers
`quiver.harness`, `test_remote_*.py` the hub and satellite in
`quiver.remote`, `test_webapp_*.py` the web app's pages, and so on.
[docs/testing.md](../docs/testing.md) has the conventions (mocking
harness homes, node-run web app scripts, width-asserting tests) and the
coverage commands; [docs/COVERAGE.md](../docs/COVERAGE.md) has the latest
coverage audit.
