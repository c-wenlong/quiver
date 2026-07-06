# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security issue, please **do not** open a public GitHub issue.

Instead, email the maintainer or use GitHub's [private vulnerability reporting](https://github.com/c-wenlong/quiver/security/advisories/new) if enabled.

Include:

- A description of the vulnerability
- Steps to reproduce
- Potential impact

We aim to acknowledge reports within 48 hours.

## Scope notes

- `~/.config/swe/mcp.json` may contain API tokens — quiver never commits this file, but users should treat it as sensitive local state.
- MCP sync commands can write to tool config files; review `--dry-run` output before syncing in shared environments.
