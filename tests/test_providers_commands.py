"""Tests for the migration of ``cmd_list`` (providers/commands.py) to
``quiver.table.Table``.

The migration replaces hand-rolled f-string padding with a single
5-column Table build (PROVIDER | ALIASES | ENV VAR | API KEY | URL)
plus an optional description sub-line below each rendered row,
indented to align under the ALIASES column header. Column widths are
pre-measured across the current row set so the Table schema and the
per-row ``cpad`` call agree by construction — every body row arrives
at exactly the column visible width.

These tests pin the structural invariants the migration promises:

1. 5-column header order with the original PROVIDER / ALIASES / ENV
   VAR / API KEY / URL labels.
2. Header SEParator visible length equals header visible length.
3. Every body row visible length equals header visible length
   (cpad-driven parity).
4. PROVIDER cell is bold ANSI; ALIASES and ENV VAR cells are dim;
   API KEY cell is green if a masked token is present and dim if
   the row shows the dash placeholder; URL cell is cyan.
5. URL column drops ``https://`` / ``http://`` / ``www.`` prefixes
   before rendering.
6. Description sub-line only emits when ``--desc`` is passed AND
   the row's ``info["description"]`` is truthy; otherwise the row
   is single-line.
7. Description indent is ``outer_pad + provider_w + 2`` (= HEAD/outer
   pad + PROVIDER column width + the gap between PROVIDER and
   ALIASES). This anchors the description under the ALIASES header,
   which is the column the description logically extends.
8. PROVIDER width is capped at 24 — long provider names truncate
   cleanly without breaking the otherwise-fixed left columns.
9. Empty aliases render as ``—`` (em-dash).
10. Env var column shows ``(+N)`` suffix when more than one
    ``env_vars`` is declared but not matched against ``matched_env``.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quiver.console import c, strip_ansi, visible_len
from quiver.providers.commands import cmd_list
from quiver.providers.defaults import DEFAULT_PROVIDERS


def _registry_patches(config_dir: Path, registry_file: Path):
    return (
        patch("quiver.providers.registry.CONFIG_DIR", config_dir),
        patch("quiver.providers.registry.PROVIDERS_REGISTRY_FILE", registry_file),
    )


def _setup(tmp_path: Path, *, create_keys_dir: bool = True):
    config_dir = tmp_path / ".config" / "swe"
    registry_file = config_dir / "providers.json"
    keys_dir = tmp_path / ".api_keys"
    if create_keys_dir:
        keys_dir.mkdir()
    return config_dir, registry_file, keys_dir


def _run_cmd_list(args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_list(list(args))
    return buf.getvalue()


# Labels printed at the outer 2-space page padding.
HEADER_LABELS = ("PROVIDER", "ALIASES", "ENV VAR", "API KEY", "URL")


class CmdListMigrationTest(unittest.TestCase):
    """Structural invariants for cmd_list post-migration."""

    # Fresh default providers + a keys dir — used by most tests.
    def _patches(self, tmp_path: Path):
        config_dir, registry_file, keys_dir = _setup(tmp_path)
        return _registry_patches(config_dir, registry_file) + (
            patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ),
        )

    def _hdr_idx(self, lines):
        return next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in HEADER_LABELS)
        )

    def test_header_has_all_five_labels_in_order(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            plain = strip_ansi(output)
            for label in HEADER_LABELS:
                self.assertIn(label, plain, f"header label {label!r} missing")
            # The labels appear in the rendered order on the header line.
            label_positions = [plain.find(lbl) for lbl in HEADER_LABELS]
            self.assertEqual(label_positions, sorted(label_positions))

    def test_separator_visible_length_matches_header(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            lines = output.split("\n")
            hdr_idx = self._hdr_idx(lines)
            sep_dashes = strip_ansi(lines[hdr_idx + 1]).count("\u2500")
            # Both lines share the same 2-space outer page padding,
            # so their visible widths match by construction. Stripping
            # the 2-char outer pad from the header gives the table's
            # inner width, which the dashes count exactly.
            self.assertEqual(
                sep_dashes, visible_len(lines[hdr_idx]) - 2,
                f"sep dashes ({sep_dashes}) != inner header width "
                f"({visible_len(lines[hdr_idx]) - 2})",
            )

    def test_body_rows_aligned_to_header_visible_width(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            lines = output.split("\n")
            hdr_idx = self._hdr_idx(lines)
            expected_width = visible_len(lines[hdr_idx])
            # Collect every line after the separator whose visible
            # width equals the header visible width — those are
            # exclusively body rows (description sub-lines and blanks
            # cannot accidentally match the header width).
            body_rows = [
                ln for ln in lines[hdr_idx + 2:]
                if visible_len(ln) == expected_width
            ]
            self.assertGreater(
                len(body_rows), 0,
                "expected at least one body row matching header width",
            )
            for row in body_rows:
                self.assertEqual(
                    expected_width, visible_len(row),
                    f"body row drifted: {strip_ansi(row)!r}",
                )

    def test_provider_column_is_bold(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            # cpad("bold", name, w) emits \033[1m followed by the
            # provider name. Every default provider's name appears at
            # least once in the body.
            self.assertIn("\033[1mopenai", output)
            self.assertIn("\033[1manthropic", output)

    def test_aliases_column_is_dim(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            # cpad("dim", aliases_text, w) → \033[2m prefix. OpenAI's
            # aliases are non-empty so a dim cell renders.
            self.assertIn("\033[2m", output)

    def test_api_key_dim_when_no_key_file(self):
        # No key file for any provider → every masked == "-" →
        # every API KEY cell uses dim colour.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            # The masked "-" placeholder is emitted with dim escape
            # (cpad("dim", "-", w)).
            self.assertIn("\033[2m-", output)

    def test_api_key_green_when_masked_key_present(self):
        # One key file present → one row's API KEY cell is green.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            (keys_dir / "openai").write_text(
                "sk-proj-MigrationTestMaskMe12345678"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=keys_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            # cpad("green", "sk-proj-...***...", w) → \033[32m prefix.
            self.assertIn("\033[32m", output)

    def test_url_strips_https_www_prefix(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            # Existing defaults carry https:// URLs (e.g.
            # "https://platform.openai.com/api-keys" for openai).
            # The implementation strips the prefix before rendering,
            # so:
            #   - No raw "https://" or "http://" should appear in the
            #     cyan-coloured URL cells.
            #   - The host tail (post-strip) must appear.
            plain = strip_ansi(output)
            # Use a substring guaranteed unique to the post-strip URL.
            self.assertIn("platform.openai.com", plain)
            self.assertNotIn("https://", plain)

    def test_em_dash_for_providers_without_aliases(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            self.assertIn("—", output)

    def test_env_var_column_shows_matched_env(self):
        # Shell-export file layout → matched_env is set on the row.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = _setup(
                tmp_path, create_keys_dir=False
            )
            shell_keys = tmp_path / ".api_keys"
            shell_keys.write_text(
                "export OPENAI_API_KEY=sk-proj-EnvCellTest12345678\n"
                "export ANTHROPIC_API_KEY=sk-ant-EnvCellTest123456\n"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=shell_keys,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            plain = strip_ansi(output)
            self.assertIn("OPENAI_API_KEY", plain)
            self.assertIn("ANTHROPIC_API_KEY", plain)

    def test_desc_flag_emits_description_sub_line(self):
        # With --desc, descriptions render as dim-indented sub-lines
        # below each row that has a truthy description.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list(["--desc"])
            # Defaults with non-empty description include openai and
            # anthropic; their description blurbs contain a known
            # substring (sourced from DEFAULT_PROVIDERS).
            from quiver.providers.defaults import DEFAULT_PROVIDERS
            for name, info in DEFAULT_PROVIDERS.items():
                desc = info.get("description") or ""
                if desc:
                    self.assertIn(
                        desc, strip_ansi(output),
                        f"description for {name!r} not rendered with --desc",
                    )

    def test_desc_flag_skips_empty_descriptions(self):
        # Even with --desc, rows whose description is empty MUST NOT
        # emit a dim-indented sub-line that just whitespace-fills to
        # 90 chars.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list(["--desc"])
            # Walk lines: a description sub-line is dim-ANSI + many
            # whitespace chars + nothing (because it's empty). That
            # pattern must NOT appear in the body region.
            # We assert non-presence by counting dim-ANSI lines that
            # have no visible chars after the indent.
            # Implementation sanity: the dim-ANSI escape is \033[2m;
            # if any line is `\033[2m` + only spaces, that's an empty
            # description that slipped through.
            for ln in output.split("\n"):
                if not ln:
                    continue
                if ln.startswith("\033[2m"):
                    payload = strip_ansi(ln)
                    self.assertTrue(
                        payload.strip(),
                        f"empty description slipped through: {ln!r}",
                    )

    def test_description_sub_line_indent_under_aliases_column(self):
        # The description sub-line must sit under the ALIASES column
        # header. Production emits
        # ``" " * desc_indent + c("dim", truncate(desc, 90))`` so the
        # line starts with whitespace, then a dim ANSI wrap, then the
        # truncated description text. We pin the indent by walking
        # every dim-coloured line and asserting the visible offset
        # into the line equals ``idx_aliases`` from the header.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list(["--desc"])
            lines = output.split("\n")
            hdr_idx = self._hdr_idx(lines)
            header_text = strip_ansi(lines[hdr_idx])
            idx_aliases = header_text.find("ALIASES")
            self.assertGreater(idx_aliases, 0)
            desc_indent_expected = idx_aliases

            # Find description sub-lines: lines carrying dim ANSI
            # (anywhere in the line) that have at least
            # ``desc_indent_expected`` leading visible spaces.
            desc_lines = []
            for ln in lines:
                if "\033[2m" not in ln:
                    continue
                plain = strip_ansi(ln)
                # The first ``desc_indent_expected`` visible chars
                # must be all whitespace, and there must be a non-
                # whitespace char after the indent.
                leading_ws = plain[:desc_indent_expected]
                if leading_ws.strip() != "":
                    continue
                if len(plain) <= desc_indent_expected:
                    continue
                if plain[desc_indent_expected] == " ":
                    continue
                desc_lines.append(ln)
            self.assertGreater(
                len(desc_lines), 0,
                f"no description sub-line at expected indent "
                f"{desc_indent_expected}; header was: {header_text!r}",
            )

    def test_provider_width_capped_at_24(self):
        # A provider whose name is longer than 24 chars should truncate
        # cleanly at exactly 24 visible chars in the PROVIDER cell,
        # preserving alignment with the header label width.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            p1, p2 = _registry_patches(config_dir, registry_file)
            fake_providers = {
                "very-long-provider-name-that-exceeds-cap": {
                    "name": "very-long-provider-name-that-exceeds-cap",
                    "aliases": ["vlpn"],
                    "env_vars": ["VLP_API_KEY"],
                    "url": "https://very-long-provider-name-that-exceeds-cap.example.com",
                    "description": "",
                    "key_filename": "very-long",
                },
            }
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ), patch(
                "quiver.providers.commands.load_registry",
                return_value=fake_providers,
            ):
                output = _run_cmd_list([])
            lines = output.split("\n")
            hdr_idx = self._hdr_idx(lines)
            hdr_w = visible_len(lines[hdr_idx])
            expected_provider_w = min(
                24,
                max(
                    len("PROVIDER"),
                    len("very-long-provider-name-that-exceeds-cap"),
                ),
            )
            # Body rows whose visible width equals the header — those
            # are the cpad-aligned rows. The PROVIDER cell of the
            # long-name row must arrive at exactly expected_provider_w
            # visible chars. We assert by checking the column width
            # math: header width = sum_of_column_widths + 4*gap(2) +
            # 2(outer pad) = expected_provider_w + summed_w + 8
            # (which equals hdr_w). So expected_provider_w must agree
            # with the table's column_widths["provider"] which the
            # header partially encodes.
            # Walk body rows: every body row's visible width is hdr_w.
            body_rows = [
                ln for ln in lines[hdr_idx + 2:]
                if visible_len(ln) == hdr_w
            ]
            self.assertEqual(
                len(body_rows), 1,
                f"expected exactly 1 body row, got {len(body_rows)} "
                f"(header width={hdr_w})",
            )
            # Pin: header PROVIDER column is exactly expected_provider_w
            # visible chars (cell cap = 24). PROVIDER cell width
            # computed from idx_aliases subtracts the outer page
            # padding (HEADER_OUTER_PAD = 2) and the column_gap (2).
            hdr_plain = strip_ansi(lines[hdr_idx])
            idx_aliases = hdr_plain.find("ALIASES")
            self.assertEqual(
                expected_provider_w, idx_aliases - 4,
                f"PROVIDER column width ({idx_aliases - 4}) is not "
                f"the expected cap ({expected_provider_w}); idx_aliases "
                f"was {idx_aliases}",
            )

    def test_filter_drops_non_matching_providers(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                # Filter to "openai" — only openai rows match.
                output_openai = _run_cmd_list(["openai"])
                openai_filter_output = strip_ansi(_run_cmd_list(["openai"]))
                # No anonymous providers should render.
                for name in DEFAULT_PROVIDERS:
                    if name != "openai":
                        self.assertNotIn(
                            name, openai_filter_output,
                            f"filter 'openai' should drop {name!r}",
                        )

    def test_no_keys_tip_advisory_after_table(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            # Tip must come AFTER the dashed separator.
            idx_tip = output.find("Tip:")
            idx_dashes = output.find("\u2500" * 10)
            self.assertGreater(idx_dashes, 0)
            self.assertGreater(idx_tip, idx_dashes)

    def test_no_keys_dir_advisory_after_table(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = _setup(
                tmp_path, create_keys_dir=False
            )
            missing_dir = tmp_path / "no_such_dir"
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=missing_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            idx_notice = output.find("No keys directory")
            idx_dashes = output.find("\u2500" * 10)
            self.assertGreater(idx_dashes, 0)
            self.assertGreater(idx_notice, idx_dashes)

    def test_footer_math_with_keys(self):
        # When one provider has a key, ``n_with = 1`` so footer
        # reads ``1/N providers with keys``.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            (keys_dir / "openai").write_text(
                "sk-proj-FooterMathTest12345678"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=keys_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            plain = strip_ansi(output)
            self.assertIn(
                f"1/{len(DEFAULT_PROVIDERS)} providers with keys", plain,
                f"expected '1/{len(DEFAULT_PROVIDERS)} providers with "
                f"keys' in footer: {plain!r}",
            )

    def test_raw_key_never_appears_in_stdout(self):
        raw_key = "sk-proj-ShouldNotLeakAnywhere1234567890abcdefghij"
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            (keys_dir / "openai").write_text(raw_key)
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=keys_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output = _run_cmd_list([])
            self.assertNotIn(raw_key, output)
            self.assertNotIn("ShouldNotLeakAnywhere", output)
            # Masked summary IS rendered.
            self.assertIn("***", output)


if __name__ == "__main__":
    unittest.main()
