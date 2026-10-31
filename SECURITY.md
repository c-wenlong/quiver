# Security Policy

## Supported Versions

Only the current minor gets fixes. Quiver is a single-binary CLI with no LTS
branch — upgrading is `pipx install --force git+https://github.com/c-wenlong/quiver.git` (the PyPI name `quiver` belongs to an unrelated project).

| Version | Supported          |
| ------- | ------------------ |
| 1.1.x   | :white_check_mark: |
| 1.0.x   | :x:                |
| 0.2.x   | :x:                |
| 0.1.x   | :x:                |

## Reporting a Vulnerability

If you discover a security issue, please **do not** open a public GitHub issue
describing it.

Use GitHub's [private vulnerability reporting](https://github.com/c-wenlong/quiver/security/advisories/new):
it opens a private draft advisory only maintainers can see.

Include, once you have somewhere private to send it:

- A description of the vulnerability
- Steps to reproduce
- Potential impact

We aim to acknowledge reports within 48 hours.

## Scope notes

Quiver's whole job is reading and rewriting other tools' config files, several
of which hold credentials. The sensitive paths:

- `~/.quiver/mcp.json` — the MCP hub. It stores credentials as `${NAME}`
  references rather than values, so the file itself is comparatively safe, but
  it still maps out every server you run.
- `~/.quiver/secrets/mcp/.api_keys` — the values behind those `${NAME}`
  references, mode 600.
- `~/.quiver/secrets/providers/.api_keys` — plain-text provider API keys,
  mode 600. `~/.api_keys` is a symlink to it once `swe init` has moved it. `swe
  providers` records metadata only and never reads the raw key into the
  registry.
- `secrets/` carries its own `.gitignore` of `*`, written by `swe init` and by
  the app before its first save, so git skips it whatever `~/.quiver/.gitignore`
  says. The root `.gitignore` that `swe init` writes lists `secrets/` too.
- Harness MCP configs written by `swe mcp sync` contain *resolved* tokens and
  are mode 600 for that reason. Review `--dry-run` output before syncing in a
  shared environment.

`~/.config/swe` was the root before 0.2.7. A machine upgraded from it may still
have a copy of the old files; `swe init --migrate` moves them rather than
deleting them, so treat the leftovers as sensitive too.

Quiver never commits any of this. This repository is the control plane (code);
`~/.quiver` is the data plane (state) with its own git history, kept separate
precisely so machine credentials cannot land here by accident.
