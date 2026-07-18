"""Unit tests for quiver.providers.registry — load/save/resolution."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quiver.providers.defaults import DEFAULT_PROVIDERS
from quiver.providers.registry import alias_map, load_registry, resolve, save_registry


def _registry_patches(config_dir: Path, registry_file: Path):
    return (
        patch("quiver.providers.registry.CONFIG_DIR", config_dir),
        patch("quiver.providers.registry.PROVIDERS_REGISTRY_FILE", registry_file),
    )


class RegistryAutoMergeTest(unittest.TestCase):
    """When DEFAULT_PROVIDERS gains a new built-in, an existing
    providers.json should pick it up on next load without losing the
    user's existing entries or edits."""

    def test_existing_registry_gains_new_defaults(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)
            registry_file.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI (user-tweaked)",
                            "url": "https://api.openai.com",
                            "key_filename": "openai",
                            "env_vars": ["OPENAI_API_KEY"],
                            "aliases": ["oai"],
                        },
                    },
                    indent=2,
                )
            )

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                providers = load_registry()
                # User-editable fields preserved verbatim.
                self.assertEqual(
                    providers["openai"]["url"], "https://api.openai.com"
                )
                self.assertEqual(providers["openai"]["key_filename"], "openai")
                self.assertEqual(
                    providers["openai"]["env_vars"], ["OPENAI_API_KEY"]
                )
                # `name` and `aliases` are derived from env_vars[0] at
                # hydration time (API_KEY is the source of truth).
                self.assertEqual(providers["openai"]["name"], "Openai")
                self.assertEqual(providers["openai"]["aliases"], ["openai"])
                # Defaults built-ins that weren't in the snapshot are merged in.
                for name in DEFAULT_PROVIDERS:
                    self.assertIn(name, providers)

    def test_explicit_remove_is_persisted_across_merges(self):
        """If the user removed a built-in, re-loading doesn't bring it back."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)
            registry_file.write_text(
                json.dumps(
                    {
                        "_removed": ["openai"],
                        "anthropic": {
                            "name": "Anthropic",
                            "url": "https://api.anthropic.com",
                            "key_filename": "anthropic",
                            "env_vars": ["ANTHROPIC_API_KEY"],
                            "aliases": [],
                        },
                    },
                    indent=2,
                )
            )

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                providers = load_registry()
                self.assertNotIn("openai", providers)
                self.assertIn("anthropic", providers)
                # Bookkeeping key never leaks into the user-visible view.
                self.assertNotIn("_removed", providers)


class RegistryLoadSeedingTest(unittest.TestCase):
    def test_load_seeds_with_defaults_on_first_run(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                providers = load_registry()
                self.assertEqual(set(providers), set(DEFAULT_PROVIDERS))
                self.assertIn("openai", providers)
                self.assertIn("anthropic", providers)
                self.assertTrue(registry_file.exists())
                self.assertEqual(providers["openai"]["key_filename"], "openai")

    def test_persisted_registry_loads_without_seeding(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)

            payload = {
                "openai": {
                    "name": "OpenAI",
                    "url": "https://x",
                    "key_filename": "openai",
                    "env_vars": ["OPENAI_API_KEY"],
                    "aliases": ["oai"],
                }
            }
            registry_file.write_text(json.dumps(payload, indent=2))

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                loaded = load_registry()
                # Hydration rewrites `name` and `aliases` from env_vars[0]
                # (the API_KEY is the source of truth); preserved fields are
                # the user-editable ones.
                self.assertIn("openai", loaded)
                self.assertEqual(loaded["openai"]["url"], "https://x")
                self.assertEqual(loaded["openai"]["key_filename"], "openai")
                self.assertEqual(loaded["openai"]["env_vars"], ["OPENAI_API_KEY"])
                self.assertEqual(loaded["openai"]["name"], "Openai")
                self.assertEqual(loaded["openai"]["aliases"], ["openai"])

    def test_corrupt_json_returns_empty(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            registry_file.parent.mkdir(parents=True, exist_ok=True)
            registry_file.write_text("{not-json}")

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                self.assertEqual(load_registry(), {})


class RegistrySaveRoundtripTest(unittest.TestCase):
    def test_save_then_load_roundtrips(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                payload = {
                    "openai": {
                        "name": "OpenAI",
                        "url": "https://x",
                        "key_filename": "openai",
                        "env_vars": ["OPENAI_API_KEY"],
                        "aliases": ["oai"],
                    },
                    "myprov": {
                        "name": "MyProv",
                        "url": "https://y",
                        "key_filename": "myprov",
                        "env_vars": ["MY_KEY"],
                        "aliases": [],
                    },
                }
                save_registry(payload)
                loaded = load_registry()
                self.assertEqual(loaded, payload)


class ResolveTest(unittest.TestCase):
    def test_resolve_via_canonical_name(self):
        providers = {
            "openai": {"name": "OpenAI", "aliases": ["oai"]},
        }
        self.assertEqual(resolve(providers, "openai"), "openai")

    def test_resolve_via_alias(self):
        providers = {
            "openai": {"name": "OpenAI", "aliases": ["oai", "gpt"]},
            "anthropic": {"name": "Anthropic", "aliases": ["claude-key"]},
        }
        self.assertEqual(resolve(providers, "oai"), "openai")
        self.assertEqual(resolve(providers, "gpt"), "openai")
        self.assertEqual(resolve(providers, "claude-key"), "anthropic")

    def test_unknown_returns_none(self):
        self.assertIsNone(resolve({}, "missing"))
        providers = {"openai": {"name": "OpenAI"}}
        self.assertIsNone(resolve(providers, "missing"))

    def test_alias_map_contains_all(self):
        providers = {
            "openai": {"name": "OpenAI", "aliases": ["oai", "gpt"]},
        }
        m = alias_map(providers)
        self.assertEqual(set(m), {"openai", "oai", "gpt"})
        for v in m.values():
            self.assertEqual(v, "openai")


if __name__ == "__main__":
    unittest.main()
