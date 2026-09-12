# Getting help with quiver

Thanks for using `swe`. Here is where to go, in order.

## 1. Read the docs

- [README.md](README.md) — install, the command tour, and the flags for each one.
- `swe help` and `swe help <command>` — the same reference, offline.
- [ARCHITECTURE.md](ARCHITECTURE.md) — the `~/.quiver` layout, registry states, and how sessions are parsed. Read this before filing anything about a harness being miscounted or missing.
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to set up a development checkout and open a pull request.

## 2. Run the self-check

Most problems are a stale install or a drifted config, and `swe` can tell you which:

```bash
swe doctor        # registry, help/dispatch drift, broken table joins
swe check         # which harnesses are installed and on PATH
swe init --check  # which shared files are linked into which harness
swe mcp doctor    # MCP hub vs per-tool config files
```

Include that output in any bug report.

## 3. Open an issue

- Bug: [bug report](https://github.com/c-wenlong/quiver/issues/new?template=bug_report.yml)
- Idea or missing harness: [feature request](https://github.com/c-wenlong/quiver/issues/new?template=feature_request.yml)
- Anything else: [browse open issues](https://github.com/c-wenlong/quiver/issues) first, then open a blank one.

Always say which OS, which Python (`python3 -V`), and which version of quiver (`pip show quiver`) you are on.

## 4. Security problems do not go in an issue

Do not put vulnerability details in an issue. [SECURITY.md](SECURITY.md) has the
reporting route, including what to do while private reporting is not yet enabled.

## Response times

This is a single-maintainer project worked on in spare time. Issues are read, but a reply can take a week or two. A pull request with a failing test attached is the fastest route to a fix.
