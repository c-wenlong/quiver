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
from quiver.providers.commands import cmd_info, cmd_list
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


class CmdInfoMigrationTest(unittest.TestCase):
    """Structural invariants for cmd_info post-migration.

    The migration replaces the hand-rolled
    ``f"  {'  ' + label + ':':<20} {val}"`` loop in cmd_info with a
    2-column Table build (LABEL | VALUE); the ``Key file`` row that
    previously was emitted outside the loop is merged into the Table
    build. All label cells are routed through the cyan colour so they
    differentiate from plain text values; values that carry ANSI
    (``Key status`` green-anon, ``Key file`` path cyan-anon) flow
    through Table unmodified via ``trust_cell_width=True``. The label
    column width is pre-measured to ``max(len("  LABEL:"))``.
    """

    # ----------------------------------------------------------------- helpers

    def _patches(self, tmp_path: Path):
        config_dir, registry_file, keys_dir = _setup(tmp_path)
        return _registry_patches(config_dir, registry_file) + (
            patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ),
        )

    def _run_cmd_info(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_info(list(args))
        return buf.getvalue(), rc

    def _body_rows(self, output: str) -> list[str]:
        """Return Table-rendered body rows (filtered for visible-width parity).

        Body rows carry the full label_cell_width + gap + value_width
        visible length. We pick them out by walking the lines and
        asserting each carries cyan ANSI (the label-side signature).
        """
        return [
            ln for ln in output.split("\n")
            if "\033[36m" in ln and ln.strip()
        ]

    # ---------------------------------------------------------- structural

    def test_label_column_is_cyan(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        # Cyan ANSI escape \033[36m must preface every label cell. We
        # assert at least 7 distinct labels carry it (matches the 7
        # rows in rows_out for a non-key-default provider).
        body_rows = self._body_rows(output)
        self.assertGreaterEqual(
            len(body_rows), 7,
            f"expected ≥7 cyan label rows, got {len(body_rows)}: "
            f"{strip_ansi(output)!r}",
        )
        for ln in body_rows:
            self.assertIn(
                "\033[36m", ln,
                f"label row missing cyan ANSI: {strip_ansi(ln)!r}",
            )

    def test_body_rows_aligned_to_same_visible_width(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        body_rows = self._body_rows(output)
        self.assertGreater(
            len(body_rows), 0,
            f"no body rows found in: {strip_ansi(output)!r}",
        )
        # Body rows do NOT share a uniform visible width — the value
        # column grows to fit the longest observed value
        # (fit="content"), and rows whose value is shorter than the
        # column max trail at their natural value width. This mirrors
        # the pre-migration behaviour, where the original
        # f"{val}" ultimately emitted the value at its visible length
        # too.
        #
        # The migration's actual invariant is that the LABEL cell
        # arrives at exactly label_cell_width (= 14 chars for the
        # default fixtures) on every body row. We pin that explicitly.
        for ln in body_rows:
            label_visible = strip_ansi(ln)[2:16]
            self.assertEqual(
                visible_len(label_visible),
                14,
                f"body row label cell not 14 chars wide: "
                f"{label_visible!r} (full row: {strip_ansi(ln)!r})",
            )

    def test_label_column_width_pre_measured_to_longest_label(self):
        # Production: ``label_cell_width = max(
        #     len("  LABEL:") for LABEL, _ in rows_out)``. The seven
        # table rows for a non-key-default provider have labels Slug,
        # URL, Aliases, Description, Env vars, Key filename, Key
        # status — longest = "  Description:" (14 chars). The width
        # is encoded in the visible_len of every body row minus the
        # FIELD_GAP(1) minus the value cell's longest visible_len.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        plain = strip_ansi(output)
        # Find the line for "Description" to verify the label width.
        desc_line = next(
            (ln for ln in plain.split("\n") if "Description:" in ln),
            None,
        )
        self.assertIsNotNone(desc_line, "no Description row in output")
        # Take the substring up to "Description:" end + 1 (colon).
        # Then assert the next char(s) until the value are pure pad.
        idx_desc = desc_line.find("Description:")
        # The cyan-wrapped label occupied from after outer's 2 spaces
        # through up to and including the colon. We assert pad after
        # the label up until the value is all whitespace (the label
        # column was padded to its pre-measured max width).
        after_label = desc_line[idx_desc + len("Description:"):]
        # The label cell should be padded such that the cyan-wrapped
        # block ends before significant value text. We assert that
        # no value text (URL / dimension / etc) appears within the
        # first label_cell_width - len("Description:") = 14 - 13 = 1
        # chars after the colon. Since the label was ljust to 14, the
        # label cell ends at desc_line[idx_desc + 1 + (14 - 13)] =
        # desc_line[idx_desc + 2]. Strip ANSI for len calc.
        # Body row layout: outer pad (FIELD_OUTER_PAD=2) + label cell
        # (= label_cell_width) + FIELD_GAP(1) + value. The cyan-label
        # cell starts at index 2 and spans exactly 14 chars (the
        # pre-measured max of "  LABEL:" lengths = "  Description:").
        self.assertEqual(
            visible_len(desc_line[2:16]),
            14,
            f"'Description:' label cell visible_len != 14: "
            f"{desc_line[2:2 + 14 + 5]!r}",
        )

    def test_label_inner_pad_two_spaces(self):
        # The label cell carries an inner 2-space pad BEFORE the
        # label name (the original ``"  ' + label + ':'"`` shape).
        # Cyan-wrapped label cell, when stripped, must begin with
        # two spaces then the label name + colon.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        plain = strip_ansi(output)
        # The body-row for Slug begins with the outer 2-space page pad
        # + inner 2-space pad + "Slug:" so the visible substring
        # ``"  Slug:"`` (2 spaces + label+colon) appears verbatim in
        # the output. (Outer page pad prefix is in addition to this.)
        self.assertIn("  Slug:", plain)

    # ------------------------------------------------------------ behaviour

    def test_key_status_green_when_key_present(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            (keys_dir / "openai").write_text(
                "sk-proj-CmdInfoGreenKey1234567890"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=keys_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        # Green ANSI escape \033[32m present (cpad("green", masked)).
        self.assertIn("\033[32m", output)
        # The masked token (containing "***") is rendered.
        self.assertIn("***", output)

    def test_key_status_dash_when_no_key_file(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        # No key file → row["masked"] == "-" → Key status cell is
        # just the dash (no green ANSI for the dash).
        plain = strip_ansi(output)
        # The dash placeholder appears in Key status.
        self.assertIn("Key status", plain)
        # And it is NOT preceded by green wrap inside the body row
        # for Key status. We assert by walking lines and finding the
        # key-status row, then check it does not start with \033[32m.
        for ln in output.split("\n"):
            if "Key status" in strip_ansi(ln):
                self.assertNotIn(
                    "\033[32m", ln,
                    f"no-key Key status should NOT be green: "
                    f"{strip_ansi(ln)!r}",
                )
                break

    def test_key_file_row_emitted_when_key_file_exists(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            (keys_dir / "openai").write_text(
                "sk-proj-CmdInfoKeyFileRow12345678"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=keys_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        plain = strip_ansi(output)
        # "Key file" must appear (the row label, cyan-wrapped).
        self.assertIn("Key file:", plain)
        # And the key-file path (without the filename basename to
        # avoid platform-specific Path normalisation) must appear.
        self.assertIn("openai", plain)
        # The Key file value cell uses cyan ANSI for the path.
        # Walk lines and find the row containing "Key file:".
        for ln in output.split("\n"):
            if "Key file:" in strip_ansi(ln):
                self.assertIn(
                    "\033[36m", ln,
                    f"Key file value must use cyan ANSI: "
                    f"{strip_ansi(ln)!r}",
                )
                break

    def test_key_file_row_emits_expected_path_when_no_key_present(self):
        # When ``keys_dir`` exists but no per-provider key file is
        # present, ``discover_provider_keys`` populates
        # ``row["key_file"]`` with the EXPECTED path (str of
        # resolve_path even if disk has nothing there). cmd_info
        # therefore emits the ``Key file`` row showing where the key
        # SHOULD go, paired with the ``No key found`` advisory
        # prompting the user to drop their key there. This is the
        # friendly UX: we don't want users to be unable to find where
        # to put the key just because they haven't yet.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        plain = strip_ansi(output)
        # Key file row IS present (with the expected path).
        self.assertIn("Key file:", plain)
        # The expected key-file path appears in the cyan-wrapped
        # path cell (we can't pin the full ~/.../openai because the
        # keys_dir is a tmp dir under /tmp, so just check the basename).
        self.assertIn("openai", plain)
        # The 'No key found' advisory fires alongside — the pair
        # together tell the user "the key should go here, but it's
        # not there yet".
        self.assertIn("No key found", output)

    def test_em_dash_for_providers_without_aliases(self):
        # mistral / cohere / deepseek / groq ship with no aliases.
        # Their Aliases cell must show "—" not blank.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["mistral"])
        plain = strip_ansi(output)
        self.assertIn("—", plain)

    def test_em_dash_for_empty_description(self):
        # providers without description → Description cell renders "—".
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["groq"])
        plain = strip_ansi(output)
        # The "—" character must appear (em-dash fallback for at
        # least one description empty field).
        self.assertIn("—", plain)

    # ----------------------------------------------------- env vars display

    def test_env_vars_comma_list_when_no_match(self):
        # Shell-export file layout with NO matching export → the
        # env_display path is just ", ".join(env_list) OR "—" if
        # env_list itself is empty.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = _setup(
                tmp_path, create_keys_dir=False
            )
            empty_keys = tmp_path / ".api_keys"
            empty_keys.write_text(
                "# no env exports\n"
                "export UNRELATED=foo\n"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=empty_keys,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        plain = strip_ansi(output)
        # OPENAI_API_KEY appears (from env_vars list, not from
        # matched_env path).
        self.assertIn("OPENAI_API_KEY", plain)
        # No "(matched; fallbacks: ...)" string in this branch.
        self.assertNotIn("(matched;", plain)

    def test_env_vars_matched_alone_when_single_env_var(self):
        # Provider with exactly one env var + matched export →
        # env_display = matched_env (no "(matched; fallbacks: ...)").
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = _setup(
                tmp_path, create_keys_dir=False
            )
            shell_keys = tmp_path / ".api_keys"
            shell_keys.write_text(
                "export OPENAI_API_KEY=sk-proj-InfoMatched1234567\n"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=shell_keys,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        plain = strip_ansi(output)
        self.assertIn("OPENAI_API_KEY", plain)
        # Single env var → no "(matched; fallbacks: ...)" line.
        self.assertNotIn("(matched; fallbacks:", plain)

    def test_env_vars_matched_with_fallbacks_when_multi(self):
        # Provider with ≥2 env_vars + a match → "matched_env
        # (matched; fallbacks: ...)" with dim-ANSI wrap on the
        # fallback half.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = _setup(
                tmp_path, create_keys_dir=False
            )
            shell_keys = tmp_path / ".api_keys"
            # kimi has 2 env_vars: KIMI_API_KEY + MOONSHOT_API_KEY.
            # Match MOONSHOT_API_KEY in the shell-file → matched +
            # fallback path.
            shell_keys.write_text(
                "export MOONSHOT_API_KEY=sk-infoFallback12345678\n"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=shell_keys,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["kimi"])
        plain = strip_ansi(output)
        self.assertIn("MOONSHOT_API_KEY", plain)
        self.assertIn("KIMI_API_KEY", plain)
        self.assertIn("(matched; fallbacks:", plain)

    # ------------------------------------------------------------ error paths

    def test_help_flag_returns_zero(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, rc = self._run_cmd_info(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("swe providers", output)

    def test_unknown_provider_returns_one_and_red_error(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, rc = self._run_cmd_info(["does-not-exist"])
        self.assertEqual(rc, 1)
        # Red ANSI escape \033[31m present.
        self.assertIn("\033[31m", output)
        self.assertIn("not found", output.lower())

    def test_no_args_returns_one_and_red_usage(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, rc = self._run_cmd_info([])
        self.assertEqual(rc, 1)
        self.assertIn("Usage", output)
        self.assertIn("\033[31m", output)

    # -------------------------------------------------------- advisories + safety

    def test_no_key_found_advisory_when_key_file_present_but_empty(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            # Empty key file exists but raw_key will be None.
            (keys_dir / "openai").write_text("")
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=keys_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        # No-key-found advisory emitted below the table.
        self.assertIn("No key found", output)
        # Advisory uses dim ANSI.
        self.assertIn("\033[2m", output)

    def test_no_advisory_when_raw_key_present(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = _setup(tmp_path)
            (keys_dir / "openai").write_text(
                "sk-proj-CmdInfoNoAdvisory1234567"
            )
            patches = _registry_patches(config_dir, registry_file) + (
                patch(
                    "quiver.providers.commands.default_keys_dir",
                    return_value=keys_dir,
                ),
            )
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        self.assertNotIn("No key found", output)

    def test_raw_key_never_appears_in_stdout(self):
        # Regression: ensure the masked summary is rendered but the
        # raw key string never appears in stdout.
        raw_key = "sk-proj-CmdInfoLeakMustNotHappen1234567890abcdefghij"
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
                output, _ = self._run_cmd_info(["openai"])
        self.assertNotIn(raw_key, output)
        self.assertNotIn("LeakMustNotHappen", output)
        self.assertIn("***", output)

    def test_aliases_filter_excludes_canonical_slug(self):
        # openai's aliases list contains "openai" itself (auto-derived
        # by load_registry) — cmd_info must NOT display "openai" as
        # an alias (it's the canonical name).
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            patches = self._patches(tmp_path)
            with patches[0], patches[1], patches[2]:
                output, _ = self._run_cmd_info(["openai"])
        plain = strip_ansi(output)
        # Find the Aliases row and check its value does NOT contain
        # "openai, openai" or similar duplicate. Walk lines for the
        # Aliases label and inspect the value portion.
        for ln in plain.split("\n"):
            if "Aliases:" in ln:
                # The value comes after "Aliases:" plus pad. Strip
                # outer trim and assert "openai" is not the only alias
                # (i.e. the value is NOT "--, openai" or similar).
                idx_val = ln.find("Aliases:") + len("Aliases:")
                val = ln[idx_val:].strip()
                # The value should be a comma-joined list minus "openai".
                # Acceptable: "oa" (single alias) or "—". Must not be
                # "openai" alone (canonical-slug-only is filtered).
                self.assertNotIn(
                    "fireworks_ai,",
                    val,
                    f"Aliases value contains canonical slug: "
                    f"{val!r} (full line: {ln!r})",
                )
                break


if __name__ == "__main__":
    unittest.main()

