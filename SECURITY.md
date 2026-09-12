# Security Policy

## Supported Versions

Only the current minor gets fixes. Quiver is a single-binary CLI with no LTS
branch — upgrading is `pipx install --force git+https://github.com/c-wenlong/quiver.git` (the PyPI name `quiver` belongs to an unrelated project).

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :x:                |

## Reporting a Vulnerability

If you discover a security issue, please **do not** open a public GitHub issue
describing it.

GitHub's [private vulnerability reporting](https://github.com/c-wenlong/quiver/security/advisories/new)
is the preferred channel, but it is **not enabled on this repository yet** — the
link only works once a maintainer turns it on under Settings → Code security.
Until it is, open a public issue titled `Security: request for private contact`
that contains **no details of the vulnerability** — not the affected command,
not the file, not a reproduction. A maintainer will reply on that issue with a
private channel to send the details to. Once the form above is enabled this
section will point at it and nothing else.

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
- `~/.quiver/secrets/.api_keys` — the values behind those `${NAME}` references,
  mode 600. **This path is not in the `.gitignore` that `swe init` writes into
  `~/.quiver`**, so if you version that directory, add `secrets/` yourself
  before your first commit.
- `~/.api_keys/` — plain-text provider API keys, one file per provider. `swe
  providers` records metadata only and never reads the raw key into the
  registry.
- Harness MCP configs written by `swe mcp sync` contain *resolved* tokens and
  are mode 600 for that reason. Review `--dry-run` output before syncing in a
  shared environment.

`~/.config/swe` was the root before 0.2.7. A machine upgraded from it may still
have a copy of the old files; `swe init --migrate` moves them rather than
deleting them, so treat the leftovers as sensitive too.

Quiver never commits any of this. This repository is the control plane (code);
`~/.quiver` is the data plane (state) with its own git history, kept separate
precisely so machine credentials cannot land here by accident.
