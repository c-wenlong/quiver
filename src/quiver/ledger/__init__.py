"""Usage ledger: token counts read from harness logs, priced at query time.

A Python port of the Mac app's ``QuiverCore/Ledger`` for the web app, so
the hub can show the same Overview on any machine. The rules (which lines
count, dedup keys, the sidechain upsert, Codex's cumulative totals, the
price lookup) follow the Swift code; each module names the file it ports.
Its store is ``~/.quiver/data/web-ledger/``, never the Mac app's
``data/ledger/``, so the two can run on one machine without sharing a file.

Not ported yet: Cursor's usage feed and the Pi, Droid and Cline session
files, so on a machine that uses those the Mac's totals run higher.
"""
