"""quiver.providers — manage AI provider API keys and metadata.

Keys live as plain-text files in ``~/.api_keys/`` (override via
``--api-keys-dir=DIR``). This submodule stores provider *metadata* only —
names, URLs, expected key filenames, and env-var names — never the
keys themselves. Keys are read dynamically on each command and never
persisted anywhere by quiver.
"""
