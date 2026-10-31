# Shared instruction chapters

`swe instructions` builds one complete shared instruction file from small
Markdown chapters. The chapters and their order live under
`~/.quiver/AGENTS_MD/`. Harness links keep reading `~/.quiver/AGENTS.md`.

## Source layout

Create `~/.quiver/AGENTS_MD/index.txt` with one relative `.md` path per line:

```text
# Order matters: later chapters follow earlier ones.
01-general.md
02-shipping.md

harnesses/claude.md
```

Write the corresponding chapter files under `~/.quiver/AGENTS_MD/`.
Blank lines and lines starting with `#` are ignored. The index explicitly
chooses the chapters and their order; files outside it do not enter the output.
Paths must stay inside the chapter directory. Missing chapters, invalid
paths and malformed sources fail the build and preserve the existing output.

The compiler produces the entire `~/.quiver/AGENTS.md`, including all
chapters in index order. Before adopting chapters, put all rules you want
to retain into the source chapters. Edit those chapters afterward: the next
build replaces edits made directly to the generated file.

Quiver's Mac AGENTS.md editor still edits the shared output directly; the
web app reads it. Neither client has a chapter editor. Use a text editor
for the source chapters when you adopt this mode. A running watcher also
replaces direct output edits with the compiled source.

## Commands

```bash
swe instructions build
swe instructions check
swe instructions watch
```

`build` validates the source first, then atomically replaces the generated
file. It compares SHA256 hashes and reports `Unchanged` without rewriting
matching output. Existing harness symlinks keep pointing at the same shared
path; running [swe init](../README.md) remains the way to create those links.

`check` validates the source and compares the expected output without
writing. It exits zero when the output matches and one when the output is
missing, stale or the source is invalid. Use it in a hub's validation checks.

`watch` builds immediately and rebuilds after source edits. It reports
invalid source without replacing the previous valid output, and continues
watching so a corrected source can build. Press Ctrl-C to stop.
Failed builds also retry every five seconds without a file edit, so a
temporary write failure can recover. Repeated identical errors print once
until a build succeeds.

The watcher uses the Python standard library, with no extra package needed.
It polls the index, listed chapter files and generated output; unlisted
backups, images and repository files are not read. Instruction chapters
require macOS or Linux for file locking and safe no-follow file opens.
Keep it running in a terminal while editing chapters. It does not install
a background service or change harness configuration.

## When harnesses read changes

The generated file stays at the existing shared path, so Quiver's clients
and every linked harness see the updated file on disk. A harness may load
instructions only when a chat starts. Start a new chat if the current chat
keeps using the old instructions.
