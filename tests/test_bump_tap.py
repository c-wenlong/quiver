"""Tests for scripts/bump_tap.py, which release.yml runs to point the
Homebrew tap (c-wenlong/homebrew-swe) at a new release."""

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

from tests._tempdir import temp_dir

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bump_tap.py"
bump_tap = None
if SCRIPT.exists():
    _SPEC = importlib.util.spec_from_file_location("bump_tap", SCRIPT)
    bump_tap = importlib.util.module_from_spec(_SPEC)
    sys.modules.setdefault("bump_tap", bump_tap)
    _SPEC.loader.exec_module(bump_tap)

SCRIPT_MISSING = "scripts/ is not part of this source tree (Nix package build)"

OLD_DMG = "bfe520dce920ff306717c6e14eaae359b764a8d7e4f9508ae92a1dac1d870e9f"
OLD_WHEEL = "63be610f2c433bafdb7f00c352cc6acbf5f5502bbc6932a63572c9cb0b40dc55"
NEW_DMG = "a" * 64
NEW_WHEEL = "b" * 64

# The frozen legacy cask: it points at the last real Quiver build and is
# never bumped again.
LEGACY_CASK = f'''cask "quiver" do
  version "1.1.1"
  sha256 "{OLD_DMG}"

  url "https://github.com/c-wenlong/swe/releases/download/v#{{version}}/Quiver-#{{version}}.dmg"
  name "Quiver"

  livecheck do
    skip "Renamed to swe"
  end

  auto_updates true
  depends_on macos: :tahoe

  app "Quiver.app"
end
'''

# The cask as the first SWE release materialized it.
CASK = f'''cask "swe" do
  version "1.2.0"
  sha256 "{NEW_DMG}"

  url "https://github.com/c-wenlong/swe/releases/download/v#{{version}}/SWE-#{{version}}.dmg"
  name "SWE"

  livecheck do
    url "https://github.com/c-wenlong/swe/releases/latest/download/appcast.xml"
    strategy :sparkle, &:short_version
  end

  auto_updates true
  depends_on macos: :tahoe

  app "SWE.app"
end
'''

FORMULA = f'''class Swe < Formula
  include Language::Python::Virtualenv

  url "https://github.com/c-wenlong/swe/releases/download/v1.1.1/quiver-1.1.1-py3-none-any.whl",
      using: :nounzip
  sha256 "{OLD_WHEEL}"
  license "MIT"

  depends_on "python@3.14"

  def install
    venv = virtualenv_create(libexec, "python3.14")
    venv.pip_install_and_link buildpath/"quiver-#{{version}}-py3-none-any.whl"
  end
end
'''


@unittest.skipIf(bump_tap is None, SCRIPT_MISSING)
class TestBumpCask(unittest.TestCase):
    def test_changes_only_version_and_sha(self):
        out = bump_tap.bump_cask(CASK, "1.3.0", NEW_DMG.replace("a", "c"))
        expected = CASK.replace('version "1.2.0"', 'version "1.3.0"').replace(
            NEW_DMG, NEW_DMG.replace("a", "c")
        )
        self.assertEqual(out, expected)
        # The url keeps its interpolation; Homebrew fills the version in.
        self.assertIn("v#{version}/SWE-#{version}.dmg", out)

    def test_same_version_is_a_no_op(self):
        self.assertEqual(bump_tap.bump_cask(CASK, "1.2.0", NEW_DMG), CASK)

    def test_missing_version_line_fails(self):
        with self.assertRaisesRegex(ValueError, "cask version line, found 0"):
            bump_tap.bump_cask(CASK.replace('  version "1.2.0"\n', ""), "1.3.0", NEW_DMG)

    def test_second_sha_line_fails(self):
        doubled = CASK.replace("  app ", f'  sha256 "{NEW_DMG}"\n  app ')
        with self.assertRaisesRegex(ValueError, "cask sha256 line, found 2"):
            bump_tap.bump_cask(doubled, "1.3.0", NEW_DMG)

    def test_new_cask_is_a_valid_bumpable_cask(self):
        text = bump_tap.new_cask("1.2.0", NEW_DMG)
        self.assertIn('cask "swe" do', text)
        self.assertIn('version "1.2.0"', text)
        self.assertIn(NEW_DMG, text)
        self.assertIn('app "SWE.app"', text)
        self.assertIn("c-wenlong/swe/releases/download/v#{version}/SWE-#{version}.dmg", text)
        self.assertEqual(bump_tap.cask_version(text), "1.2.0")


@unittest.skipIf(bump_tap is None, SCRIPT_MISSING)
class TestBumpFormula(unittest.TestCase):
    def test_rewrites_url_and_sha(self):
        out = bump_tap.bump_formula(FORMULA, "1.2.0", NEW_WHEEL)
        expected = FORMULA.replace(
            "download/v1.1.1/quiver-1.1.1-py3-none-any.whl",
            "download/v1.2.0/swe-1.2.0-py3-none-any.whl",
        ).replace(OLD_WHEEL, NEW_WHEEL).replace(
            'buildpath/"quiver-#{version}-py3-none-any.whl"',
            'buildpath/"swe-#{version}-py3-none-any.whl"',
        )
        self.assertEqual(out, expected)
        # The install block's wheel filename follows the package rename too.
        self.assertIn('buildpath/"swe-#{version}-py3-none-any.whl"', out)

    def test_rebumps_a_formula_already_on_swe(self):
        out = bump_tap.bump_formula(FORMULA.replace("quiver", "swe"), "1.3.0", NEW_WHEEL)
        self.assertIn("download/v1.3.0/swe-1.3.0-py3-none-any.whl", out)
        self.assertIn('buildpath/"swe-#{version}-py3-none-any.whl"', out)

    def test_missing_url_fails(self):
        broken = FORMULA.replace("-py3-none-any.whl\",", ".tar.gz\",")
        with self.assertRaisesRegex(ValueError, "formula wheel url, found 0"):
            bump_tap.bump_formula(broken, "1.2.0", NEW_WHEEL)


@unittest.skipIf(bump_tap is None, SCRIPT_MISSING)
class TestMain(unittest.TestCase):
    def _tap(self) -> Path:
        tap = temp_dir(self)
        (tap / "Casks").mkdir()
        (tap / "Formula").mkdir()
        (tap / "Casks/quiver.rb").write_text(LEGACY_CASK, encoding="utf-8")
        (tap / "Formula/swe.rb").write_text(FORMULA, encoding="utf-8")
        return tap

    def _run(self, *argv: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return bump_tap.main(list(argv))

    def test_first_swe_release_materializes_the_cask(self):
        tap = self._tap()
        self.assertEqual(self._run(str(tap), "v1.2.0", NEW_DMG, NEW_WHEEL), 0)
        cask = (tap / "Casks/swe.rb").read_text(encoding="utf-8")
        self.assertIn('version "1.2.0"', cask)
        self.assertIn(NEW_DMG, cask)
        formula = (tap / "Formula/swe.rb").read_text(encoding="utf-8")
        self.assertIn("download/v1.2.0/swe-1.2.0-py3-none-any.whl", formula)
        self.assertIn(NEW_WHEEL, formula)

    def test_legacy_cask_is_never_touched(self):
        tap = self._tap()
        self.assertEqual(self._run(str(tap), "v1.2.0", NEW_DMG, NEW_WHEEL), 0)
        self.assertEqual((tap / "Casks/quiver.rb").read_text(encoding="utf-8"), LEGACY_CASK)

    def test_existing_swe_cask_bumps_in_place(self):
        tap = self._tap()
        (tap / "Casks/swe.rb").write_text(CASK, encoding="utf-8")
        newer = NEW_DMG.replace("a", "d")
        self.assertEqual(self._run(str(tap), "v1.3.0", newer, NEW_WHEEL), 0)
        cask = (tap / "Casks/swe.rb").read_text(encoding="utf-8")
        self.assertIn('version "1.3.0"', cask)
        self.assertIn(newer, cask)
        self.assertIn('app "SWE.app"', cask)

    def test_rejects_bad_arguments_without_writing(self):
        tap = self._tap()
        self.assertEqual(self._run(str(tap), "1.2.0", "NOT-A-SHA", NEW_WHEEL), 2)
        self.assertEqual(self._run(str(tap), "1.2.0-rc1", NEW_DMG, NEW_WHEEL), 2)
        self.assertEqual(self._run(str(tap), "1.2.0"), 2)
        self.assertFalse((tap / "Casks/swe.rb").exists())

    def test_older_version_leaves_the_tap_alone(self):
        tap = self._tap()
        self.assertEqual(self._run(str(tap), "v1.0.0", NEW_DMG, NEW_WHEEL), 0)
        self.assertEqual(self._run(str(tap), "1.1.0", NEW_DMG, NEW_WHEEL), 0)
        self.assertFalse((tap / "Casks/swe.rb").exists())
        self.assertEqual((tap / "Formula/swe.rb").read_text(encoding="utf-8"), FORMULA)

    def test_rollback_guard_reads_an_existing_swe_cask(self):
        tap = self._tap()
        (tap / "Casks/swe.rb").write_text(CASK, encoding="utf-8")
        self.assertEqual(self._run(str(tap), "v1.1.9", NEW_DMG, NEW_WHEEL), 0)
        self.assertEqual((tap / "Casks/swe.rb").read_text(encoding="utf-8"), CASK)

    def test_numeric_not_text_comparison(self):
        tap = self._tap()
        self.assertEqual(self._run(str(tap), "1.10.0", NEW_DMG, NEW_WHEEL), 0)
        self.assertIn('version "1.10.0"', (tap / "Casks/swe.rb").read_text(encoding="utf-8"))

    def test_missing_tap_file_fails(self):
        tap = self._tap()
        (tap / "Formula/swe.rb").unlink()
        self.assertEqual(self._run(str(tap), "1.2.0", NEW_DMG, NEW_WHEEL), 1)


if __name__ == "__main__":
    unittest.main()
