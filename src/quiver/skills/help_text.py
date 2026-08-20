"""Centralized help text for swe skills subcommands."""

from quiver.console import c
from quiver.paths import SKILL_CATALOGS_FILE, SKILL_LINKS_FILE


def print_skills_overview():
    print(
        f"""
  {c('bold', 'swe skills')} — Discover, list, and manage agent skills

{c('bold', 'List & search')}
  {c('cyan', 'swe skills')}                   One line per skill: name, root, path
  {c('cyan', 'swe skills <filter>')}          Filter by name, scope, or harness
  {c('cyan', 'swe skills -d')}                Include descriptions

{c('bold', 'Where skills live')}
  {c('cyan', 'swe find skills -r')}           Every root on disk, and what it links to
  {c('cyan', 'swe find skills -r --scope=all')} Include vendored and project-local roots
  {c('dim', 'swe skills tree and swe skills scope list now forward here.')}

{c('bold', 'Layout & symlinks')}
  {c('cyan', 'swe skills link <harness> [target]')}   Link codex/cursor/claude → shared
  {c('cyan', 'swe skills unlink <harness> [--mkdir]')} Break link; optional empty dir
  {c('cyan', 'swe skills move <name> --from A --to B')} Move a skill folder

{c('bold', 'Catalogs (extra skill directories)')}
  {c('cyan', 'swe skills discover [--apply]')} Scan ~/Desktop and ~/Documents
  {c('cyan', 'swe skills catalog .')}         Add current directory as catalog
  {c('cyan', 'swe skills catalog add [path]')} Register a skills directory
  {c('cyan', 'swe skills catalog list')}      List configured catalogs
  {c('cyan', 'swe skills catalog remove <key>')} Remove a catalog entry

{c('bold', 'Config')}
  {SKILL_CATALOGS_FILE}   extra catalog paths
  {SKILL_LINKS_FILE}      recorded harness symlinks

{c('bold', 'Help')}  {c('cyan', 'swe skills help <topic>')}  — topics: catalog, discover, link, unlink, move
"""
    )


def print_skills_catalog_help():
    print(
        f"""
  {c('bold', 'swe skills catalog')} — Register extra skill directories

  {c('cyan', 'swe skills catalog list')}                 List configured catalogs
  {c('cyan', 'swe skills catalog add [path] [label]')}   Register (default path: .)
  {c('cyan', 'swe skills catalog .')}                    Add current directory
  {c('cyan', 'swe skills catalog remove <label|path>')}  Remove from registry

{c('bold', 'Examples')}
  cd ~/Projects/my-app/skills && swe skills catalog .
  swe skills catalog add ~/Desktop/Projects/ai-engineering/skills
  swe skills catalog add ./skills my-project

{c('bold', 'Config')}  {SKILL_CATALOGS_FILE}
"""
    )


def print_skills_discover_help():
    print(
        f"""
  {c('bold', 'swe skills discover')} — Find skill catalogs on Desktop/Documents

  Scans {c('dim', '~/Desktop')} and {c('dim', '~/Documents')} for folders named {c('dim', 'skills')}
  that contain SKILL.md files (nested catalogs are collapsed to the outermost match).

  {c('cyan', 'swe skills discover')}              Dry-run list
  {c('cyan', 'swe skills discover --apply')}      Register new catalogs
  {c('cyan', 'swe skills discover --json')}       Machine-readable output
  {c('cyan', 'swe skills discover --all')}        Include already-registered catalogs

{c('bold', 'See also')}  {c('cyan', 'swe skills catalog add .')} for manual registration
"""
    )


def print_skills_link_help():
    print(
        f"""
  {c('bold', 'swe skills link')} — Point a harness skills folder at another root

  {c('cyan', 'swe skills link codex')}                 Link codex → shared (default)
  {c('cyan', 'swe skills link codex shared')}           Explicit target
  {c('cyan', 'swe skills link codex claude')}           Link to claude's resolved root
  {c('cyan', 'swe skills link codex --force')}          Replace non-empty directory

{c('bold', 'Harness labels')}  shared, cursor, codex, claude  (or a path)
"""
    )


def print_skills_unlink_help():
    print(
        f"""
  {c('bold', 'swe skills unlink')} — Break a harness symlink for private skills

  {c('cyan', 'swe skills unlink codex')}              Remove codex → shared symlink
  {c('cyan', 'swe skills unlink codex --mkdir')}      Replace with empty directory

  Then move harness-specific skills:
  {c('cyan', 'swe skills move my-skill --from shared --to codex')}
"""
    )


def print_skills_move_help():
    print(
        f"""
  {c('bold', 'swe skills move')} — Move a skill folder between scope roots

  {c('cyan', 'swe skills move <name> --from <scope> --to <scope>')}

{c('bold', 'Examples')}
  swe skills move query --from ai-engineering --to shared
  swe skills move my-skill --from shared --to codex

  Scopes are harness labels (shared, codex, claude, cursor), catalog labels,
  or paths registered via {c('cyan', 'swe skills catalog add')}.

  If both scopes resolve to the same tree (symlinked), unlink the destination
  harness first or pass {c('cyan', '--force')}.
"""
    )


SKILLS_HELP_TOPICS = {
    "": print_skills_overview,
    "catalog": print_skills_catalog_help,
    "catalogs": print_skills_catalog_help,
    "discover": print_skills_discover_help,
    "link": print_skills_link_help,
    "unlink": print_skills_unlink_help,
    "move": print_skills_move_help,
}


def cmd_skills_help(args: list[str]) -> int:
    topic = args[0].lower() if args else ""
    if topic in ("-h", "--help"):
        topic = ""
    handler = SKILLS_HELP_TOPICS.get(topic)
    if handler is None:
        print(c("red", f"  Unknown skills help topic: {topic!r}"))
        if topic in ("tree", "scope", "scopes"):
            print(c("dim", "  That view is now swe find skills -r"))
        else:
            print(c("dim", "  Topics: catalog, discover, link, unlink, move"))
        return 1
    handler()
    return 0
