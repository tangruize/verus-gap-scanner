from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verus_gap.cli import VerboseProgress, main
from verus_gap.gap import GapError
from verus_gap.project import scan_level_from


class ScanLevelTests(unittest.TestCase):
    def test_default_numeric_levels_and_fast_alias_reach_the_same_engine_option(self):
        cases = [
            ([], 0), (["--scan-level", "0"], 0), (["--scan-level", "1"], 1),
            (["--scan-level=2"], 2), (["--fast"], 2), (["--fast", "--scan-level", "2"], 2),
        ]
        for options, expected in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temporary:
                project = SimpleNamespace(
                    manifest={"tool_sha256": {}}, sources={"lib.rs": SimpleNamespace()},
                    deadline=time.monotonic() + 30, save=mock.Mock(), verify_originals=mock.Mock(),
                )

                def probing(project, focus, directory, *, scan_level, progress):
                    directory.mkdir(parents=True)
                    return {"status": "frontend_clear", "stop_reason": "no_remaining_errors"}

                report = {
                    "counts": {}, "status": "no_confirmed_limitations",
                    "coverage": {"pending_files": 0, "partial_files": 0},
                }
                with mock.patch("verus_gap.cli.prepare", return_value=project) as preparing, mock.patch(
                    "verus_gap.cli.probe", side_effect=probing,
                ) as probing_mock, mock.patch("verus_gap.cli.save_report", return_value=report), redirect_stdout(io.StringIO()):
                    status = main(["fixture.rs", "--out", str(Path(temporary) / "results"), *options])
                self.assertEqual(status, 0)
                self.assertEqual(preparing.call_args.args[0].scan_level, expected)
                self.assertEqual(probing_mock.call_args.kwargs["scan_level"], expected)
                self.assertEqual(project.manifest["scan_level"], expected)

    def test_invalid_or_conflicting_levels_fail_before_setup(self):
        cases = [
            ["--scan-level", "-1"], ["--scan-level", "3"], ["--scan-level", "quick"],
            ["--fast", "--scan-level", "0"], ["--scan-level", "1", "--fast"],
        ]
        for options in cases:
            with self.subTest(options=options), mock.patch("verus_gap.cli.prepare") as preparing:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                    main(options)
                self.assertEqual(failure.exception.code, 2)
                preparing.assert_not_called()

    def test_scanner_levels_cannot_be_combined_with_legacy_frontier(self):
        for options in (["--scan-level", "0"], ["--scan-level=2"], ["--fast"]):
            with self.subTest(options=options), redirect_stderr(io.StringIO()) as output:
                status = main(["--frontier", *options])
            self.assertEqual(status, 2)
            self.assertIn("do not combine", output.getvalue())

    def test_cargo_level_after_separator_is_not_the_scanner_option(self):
        with mock.patch("verus_gap.gap.main", return_value=0) as frontier:
            status = main(["--frontier", "--", "--scan-level", "2"])
        self.assertEqual(status, 0)
        frontier.assert_called_once_with(["--", "--scan-level", "2"])

    def test_saved_levels_are_validated_without_coercion(self):
        self.assertEqual(scan_level_from({}), 0)
        self.assertEqual(scan_level_from({"scan_mode": "fast"}), 1)
        for level in (0, 1, 2):
            self.assertEqual(scan_level_from({"scan_level": level}), level)
        for level in (-1, 3, "2", None, True, False, []):
            with self.subTest(level=level), self.assertRaises(GapError):
                scan_level_from({"scan_level": level})
        for mode in ("unknown", [], {}):
            with self.subTest(mode=mode), self.assertRaises(GapError):
                scan_level_from({"scan_mode": mode})

    def test_resume_rejects_a_different_level_even_when_both_modes_are_focused(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            project_path, report_path = output / "project.json", output / "report.md"
            project_path.write_text(json.dumps({"scan_level": 1, "scan_mode": "fast"}))
            report_path.write_text("original evidence")
            before = project_path.read_bytes()
            for options in (["--fast"], ["--scan-level", "2"], ["--scan-level", "0"]):
                with self.subTest(options=options), mock.patch("verus_gap.cli.resume") as resuming:
                    with redirect_stderr(io.StringIO()) as errors:
                        status = main(["--resume", str(output), *options])
                    self.assertEqual(status, 2)
                    self.assertIn("keeps its original scan level", errors.getvalue())
                    resuming.assert_not_called()
                    self.assertEqual(project_path.read_bytes(), before)
                    self.assertEqual(report_path.read_text(), "original evidence")

    def test_invalid_saved_level_does_not_overwrite_existing_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            project_path, report_path = output / "project.json", output / "report.md"
            project_path.write_text(json.dumps({"scan_level": 3}))
            report_path.write_text("original evidence")
            before = project_path.read_bytes()
            with mock.patch("verus_gap.cli.resume") as resuming, redirect_stderr(io.StringIO()) as errors:
                status = main(["--resume", str(output)])
            self.assertEqual(status, 2)
            self.assertIn("Invalid scan level", errors.getvalue())
            resuming.assert_not_called()
            self.assertEqual(project_path.read_bytes(), before)
            self.assertEqual(report_path.read_text(), "original evidence")


class VerboseProgressTests(unittest.TestCase):
    def record(self, file="selected.rs", message="Verus does not support example", **changes):
        return {
            "category": "language_rejection", "level": "error", "message": message,
            "locations": [{"file": file, "byte": 0, "line": 1, "column": 1, "source": "example()"}],
            "related_locations": [], "confirmed": True, "raw_diagnostic": None, **changes,
        }

    def round(self, progress, records):
        progress("round", {
            "focus": "selected.rs", "round": 0, "observations": records,
            "seconds": 1.0, "confirmation_seconds": 1.1,
            "confirmation_performed": True, "blockers": [],
        })

    def test_same_group_is_announced_once_without_rewriting_the_original_message(self):
        progress = VerboseProgress(["selected.rs"], fast=True)
        message = "Path Def(Static, DefId(0:123 ~ fixture[abcd]::VALUE))"
        first = self.record(message=message)
        second = self.record(message=message.replace("0:123", "0:456").replace("abcd", "ffff"))
        output = io.StringIO()
        with redirect_stdout(output):
            self.round(progress, [first])
            self.round(progress, [first, second])
        text = output.getvalue()
        self.assertEqual(text.count("[limitation]"), 1)
        self.assertIn("selected.rs:1:1: " + message, text)
        self.assertIn("confirmed groups: 1 language / 0 models", text)

    def test_distinct_public_apis_remain_separate_groups(self):
        progress = VerboseProgress(["selected.rs"], fast=True)
        message = "`core::sync::atomic::impl&%1%default%load` is not supported"
        records = [
            self.record(
                message=message, category="library_specification",
                raw_diagnostic={"children": [{"message": f"pub assume_specification [{api}] (_0: u32);"}]},
            )
            for api in ("AtomicBool::load", "AtomicUsize::load")
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            self.round(progress, records)
        text = output.getvalue()
        self.assertEqual(text.count("[limitation]"), 2)
        self.assertIn("API: AtomicBool::load", text)
        self.assertIn("API: AtomicUsize::load", text)
        self.assertIn("confirmed groups: 0 language / 2 models", text)

    def test_fast_output_filters_scope_and_non_support_records_without_mutation(self):
        mixed = self.record()
        mixed["locations"].append(dict(mixed["locations"][0], file="dependency.rs"))
        records = [
            mixed, self.record(file="dependency.rs"),
            self.record(locations=[]), self.record(confirmed=False),
            self.record(category="checking_boundary"),
            self.record(category="compiler_failure"),
            self.record(category="instrumentation_failure"),
        ]
        original = copy.deepcopy(records)
        output = io.StringIO()
        with redirect_stdout(output):
            self.round(VerboseProgress(["selected.rs"], fast=True), records)
        text = output.getvalue()
        self.assertEqual(text.count("[limitation]"), 1)
        self.assertIn("[language][selected]", text)
        self.assertNotIn("dependency.rs", text)
        self.assertNotIn("[dependency]", text)
        self.assertEqual(records, original)

    def test_default_output_labels_dependency_and_later_selected_locations(self):
        progress = VerboseProgress(["selected.rs"], fast=False)
        output = io.StringIO()
        with redirect_stdout(output):
            self.round(progress, [self.record(file="dependency.rs")])
            self.round(progress, [self.record()])
            self.round(progress, [self.record()])
        text = output.getvalue()
        self.assertEqual(text.count("[limitation]"), 1)
        self.assertEqual(text.count("[selected-location]"), 1)
        self.assertIn("[language][dependency] dependency.rs:1:1", text)
        self.assertIn("[language][selected] selected.rs:1:1", text)
        self.assertIn("selected: 1 language / 0 models", text)

    def test_restored_observations_seed_counts_without_announcing_old_findings(self):
        progress = VerboseProgress(["selected.rs"], fast=True)
        output = io.StringIO()
        with redirect_stdout(output):
            progress("restore", {"observations": [self.record()]})
            self.round(progress, [self.record()])
        self.assertNotIn("[limitation]", output.getvalue())
        self.assertIn("confirmed groups: 1 language / 0 models", output.getvalue())

    def test_progress_events_are_flushed_and_actions_are_distinct_from_limitations(self):
        progress = VerboseProgress(["selected.rs"], fast=False)
        with mock.patch("builtins.print") as printing:
            progress("probe", {"focus": "selected.rs", "round": 0, "scope": ["selected.rs"]})
            progress("confirmation", {
                "focus": "selected.rs", "round": 0, "candidates": 1, "compiler_failure": False,
            })
            progress("action", {
                "focus": "selected.rs", "round": 1,
                "events": [
                    {"kind": "scope_expansion", "file": "dependency.rs"},
                    {"kind": "external_body"},
                ],
            })
        text = "\n".join(call.args[0] for call in printing.call_args_list)
        self.assertIn("[probe]", text)
        self.assertIn("[confirm]", text)
        self.assertIn("not yet confirmed", text)
        self.assertIn("[scope]", text)
        self.assertIn("[isolate]", text)
        self.assertNotIn("[limitation]", text)
        self.assertTrue(all(call.kwargs["flush"] for call in printing.call_args_list))

    def test_verbose_cannot_be_combined_with_legacy_frontier(self):
        output = io.StringIO()
        with redirect_stderr(output):
            status = main(["--frontier", "--verbose"])
        self.assertEqual(status, 2)
        self.assertIn("do not combine", output.getvalue())

    def test_cargo_verbose_after_separator_is_not_the_scanner_option(self):
        with mock.patch("verus_gap.gap.main", return_value=0) as frontier:
            status = main(["--frontier", "--", "--verbose"])
        self.assertEqual(status, 0)
        frontier.assert_called_once_with(["--", "--verbose"])


if __name__ == "__main__":
    unittest.main()
