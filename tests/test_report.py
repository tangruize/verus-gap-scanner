from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verus_gap.project import write_json
from verus_gap.report import (
    aggregate, atomic_text, language_family, main, markdown, public_api, refresh_report,
    review_markdown, save_report, source_link, write_report,
)
from verus_gap.source import Cfg, Source


class ReportTests(unittest.TestCase):
    def saved_result(self, temporary: str, *, state: str = "interrupted"):
        output = Path(temporary) / "results"
        output.mkdir()
        source = Source("src/lib.rs", b"pub fn example() {}\n", Cfg([]))
        original = output / "originals/src/lib.rs"
        original.parent.mkdir(parents=True)
        original.write_bytes(source.data)
        directory = "shards/" + hashlib.sha256(source.file.encode()).hexdigest()[:12]
        manifest = {
            "schema": "verus-gap/project-v2", "input": "/missing/original/input",
            "package": "fixture", "verus": {"version": "fixture"}, "baseline_passed": True,
            "cargo_args": [], "checks": {"proof_lifetimes": True}, "workspace_restored": True,
            "rust_cfg": [], "tool_sha256": {}, "scan_level": 2,
            "active_sources": {source.file: source.identity()}, "selected_files": [source.file],
            "shard_index": {source.file: directory},
            "scan_progress": {"state": state, "active_file": source.file, "active_shard": directory},
        }
        revision = hashlib.sha256(json.dumps({}, sort_keys=True).encode()).hexdigest()[:12]
        place = {"file": source.file, "byte": 0, "line": 1, "column": 1, "source": "pub fn example() {}"}
        record = {
            "category": "library_specification", "level": "error", "message": "model not recognized",
            "rendered": "unchanged raw diagnostic", "locations": [place], "related_locations": [],
            "confirmed": True, "macro_expansion": False, "raw_diagnostic": None,
            "scope": [source.file], "source_variant": "prepared", "round": 0,
            "evidence": "round-000/verus.txt",
        }
        shard = {
            "schema": "verus-gap/shard-v2", "scanner_revision": revision,
            "observations": [record, dict(record, confirmed=False, message="not yet confirmed")],
            "rounds": [{"round": 0}], "events": [], "focus": source.file, "scope": [source.file],
            "status": "partial", "stop_reason": "interrupted" if state == "interrupted" else "",
            "scan_level": 2, "workspace_restored": True,
        }
        checkpoint = output / directory / "report.json"
        checkpoint.parent.mkdir(parents=True)
        write_json(output / "project.json", manifest)
        write_json(checkpoint, shard)
        for relative in ("round-000/verus.txt", "round-000/confirmation/verus.txt"):
            evidence = checkpoint.parent / relative
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(record["rendered"])
        return output, manifest, shard, checkpoint

    def test_manual_refresh_uses_saved_evidence_without_input_workspace_or_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary)
            evidence = {
                path: path.read_bytes() for path in output.rglob("*") if path.is_file()
            }
            with mock.patch("verus_gap.gap.run", side_effect=AssertionError("No compiler allowed")):
                report = refresh_report(output)
                with redirect_stdout(io.StringIO()) as terminal:
                    self.assertEqual(main([str(output)]), 0)
            self.assertIn("Reports refreshed from saved evidence", terminal.getvalue())
            self.assertEqual(report["counts"]["library_specification"], 1)
            self.assertEqual(report["counts"]["unconfirmed_support_diagnostic"], 1)
            self.assertEqual(report["scan_level"], 2)
            self.assertEqual(report["scan_progress"]["state"], "interrupted")
            self.assertEqual(report["scan_progress"]["completed_files"], 0)
            self.assertEqual(report["scan_progress"]["last_completed_round"], 0)
            self.assertEqual(report["coverage"]["partial_files"], 1)
            self.assertIn("snapshot of completed, checkpointed rounds", (output / "report.md").read_text())
            self.assertIn("unchanged raw diagnostic", (output / "report.md").read_text())
            self.assertIn("interrupted", (output / "review.md").read_text())
            self.assertTrue((output / "agent-tasks.json").is_file())
            for path, before in evidence.items():
                self.assertEqual(path.read_bytes(), before, str(path))
            self.assertFalse((output / "workspace").exists())
            self.assertEqual(json.loads(checkpoint.read_text()), shard)
            self.assertEqual(json.loads((output / "project.json").read_text()), manifest)

    def test_manual_refresh_finds_an_unregistered_active_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary, state="running")
            manifest.pop("shard_index")
            manifest.pop("scan_progress")
            write_json(output / "project.json", manifest)
            report = refresh_report(output)
            self.assertEqual(report["counts"]["library_specification"], 1)
            self.assertEqual(report["scan_progress"]["state"], "incomplete")
            self.assertEqual(report["scan_progress"]["active_file"], shard["focus"])

    def test_manual_refresh_before_the_first_checkpoint_retains_pending_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary, state="running")
            checkpoint.unlink()
            report = refresh_report(output)
            self.assertEqual(report["counts"], {})
            self.assertEqual(report["coverage"]["pending_files"], 1)
            self.assertEqual(report["scan_progress"]["state"], "running")
            self.assertIsNone(report["scan_progress"]["last_completed_round"])

    def test_manual_refresh_reports_all_active_workers_without_requiring_their_workspaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary, state="running")
            second = Source("src/next.rs", b"pub fn next() {}\n", Cfg([]))
            (output / "originals" / second.file).write_bytes(second.data)
            manifest["active_sources"][second.file] = second.identity()
            manifest["selected_files"].append(second.file)
            manifest["jobs"] = 32
            manifest["scan_progress"] = {"state": "running", "active_files": [shard["focus"], second.file]}
            manifest["shard_index"][second.file] = "shards/not-started"
            write_json(output / "project.json", manifest)
            report = refresh_report(output)
            self.assertEqual(report["jobs"], 32)
            self.assertEqual(report["coverage"]["pending_files"], 1)
            self.assertEqual(report["scan_progress"]["active_files"], [
                {"file": shard["focus"], "last_completed_round": 0},
                {"file": second.file, "last_completed_round": None},
            ])
            self.assertIn("Current or interrupted files", (output / "report.md").read_text())
            self.assertFalse((output / "workers").exists())

    def test_invalid_worker_metadata_does_not_overwrite_a_previous_report(self):
        cases = [
            ("jobs", 0), ("jobs", True), ("workers_restored", "yes"),
            ("active_files", ["src/lib.rs", "src/lib.rs"]), ("active_files", ["outside.rs"]),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as temporary:
                output, manifest, shard, checkpoint = self.saved_result(temporary)
                if key == "active_files":
                    manifest["scan_progress"][key] = value
                else:
                    manifest[key] = value
                write_json(output / "project.json", manifest)
                report = output / "report.md"
                report.write_text("previous report")
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(output)]), 2)
                self.assertEqual(report.read_text(), "previous report")

    def test_manual_refresh_during_revision_specific_resume_does_not_mix_old_findings(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary, state="running")
            next_source = Source("src/next.rs", b"pub fn next() {}\n", Cfg([]))
            next_original = output / "originals" / next_source.file
            next_original.write_bytes(next_source.data)
            manifest["active_sources"][next_source.file] = next_source.identity()
            manifest["selected_files"].append(next_source.file)
            manifest["tool_sha256"] = {"changed": "new scanner identity"}
            manifest["scan_progress"]["active_file"] = next_source.file
            next_directory = "shards/" + hashlib.sha256(next_source.file.encode()).hexdigest()[:12]
            manifest["shard_index"][next_source.file] = next_directory
            manifest["scan_progress"]["active_shard"] = next_directory
            write_json(output / "project.json", manifest)
            before = checkpoint.read_bytes()
            report = refresh_report(output)
            self.assertEqual(report["counts"], {})
            self.assertEqual(report["coverage"]["pending_files"], 2)
            self.assertEqual(report["other_revision_checkpoints"][0]["file"], shard["focus"])
            self.assertIn("retained but not included", (output / "report.md").read_text())
            self.assertEqual(checkpoint.read_bytes(), before)

    def test_invalid_saved_evidence_preserves_the_previous_report(self):
        for problem in (
            "original", "scope", "revision", "level", "path", "missing", "json",
            "diagnostic", "confirmation", "unfinished-round", "source-location", "metadata",
        ):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as temporary:
                output, manifest, shard, checkpoint = self.saved_result(temporary)
                report = output / "report.md"
                report.write_text("previous report")
                if problem == "original":
                    (output / "originals/src/lib.rs").write_text("changed original")
                elif problem == "scope":
                    shard["focus"] = "outside.rs"
                    write_json(checkpoint, shard)
                elif problem == "revision":
                    shard["scanner_revision"] = "different"
                    write_json(checkpoint, shard)
                elif problem == "level":
                    shard["scan_level"] = 0
                    write_json(checkpoint, shard)
                elif problem == "path":
                    manifest["shard_index"]["src/lib.rs"] = "../outside"
                    write_json(output / "project.json", manifest)
                elif problem == "missing":
                    manifest["scan_progress"] = {"state": "finished"}
                    write_json(output / "project.json", manifest)
                    checkpoint.unlink()
                elif problem == "json":
                    checkpoint.write_text("{invalid")
                elif problem == "diagnostic":
                    (checkpoint.parent / "round-000/verus.txt").unlink()
                elif problem == "confirmation":
                    (checkpoint.parent / "round-000/confirmation/verus.txt").unlink()
                elif problem == "unfinished-round":
                    shard["observations"][0]["round"] = 1
                    write_json(checkpoint, shard)
                elif problem == "source-location":
                    shard["observations"][0]["locations"][0]["file"] = []
                    write_json(checkpoint, shard)
                elif problem == "metadata":
                    manifest["shard_index"] = []
                    write_json(output / "project.json", manifest)
                with redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main([str(output)]), 2)
                self.assertIn("Could not refresh reports", errors.getvalue())
                self.assertEqual(report.read_text(), "previous report")

    def test_atomic_report_text_preserves_the_previous_file_on_failed_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = output / "report.md"
            report.write_text("previous report")
            with mock.patch.object(Path, "replace", side_effect=OSError("cannot replace")):
                with self.assertRaisesRegex(OSError, "cannot replace"):
                    atomic_text(report, "new report")
            self.assertEqual(report.read_text(), "previous report")
            self.assertEqual(list(output.iterdir()), [report])

    def test_parallel_manual_refreshes_are_serialized_and_leave_complete_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary)
            before = checkpoint.read_bytes()
            with ThreadPoolExecutor(max_workers=4) as workers:
                results = list(workers.map(lambda _: refresh_report(output), range(8)))
            self.assertTrue(all(report["counts"]["library_specification"] == 1 for report in results))
            self.assertEqual(json.loads((output / "report.json").read_text())["groups"], results[-1]["groups"])
            self.assertIn("unchanged raw diagnostic", (output / "report.md").read_text())
            self.assertIn("interrupted", (output / "review.md").read_text())
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertEqual(list(output.glob(".*.tmp")), [])

    def test_worker_publication_excludes_partial_evidence_and_unpublished_checkpoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary, state="running")
            second = Source("src/next.rs", b"pub fn next() {}\n", Cfg([]))
            (output / "originals" / second.file).write_bytes(second.data)
            manifest["active_sources"][second.file] = second.identity()
            manifest["selected_files"].append(second.file)
            manifest["scan_progress"] = {"state": "running", "active_files": [shard["focus"], second.file]}
            directory = output / "shards/second"
            directory.mkdir()
            manifest["shard_index"][second.file] = "shards/second"
            write_json(output / "project.json", manifest)
            next_shard = copy.deepcopy(shard)
            next_shard.update(focus=second.file, scope=[second.file])
            for record in next_shard["observations"]:
                record["scope"] = [second.file]
                record["locations"][0].update(file=second.file, source="pub fn next() {}")
                record["message"] = "second completed model" if record["confirmed"] else "second unconfirmed"
            staged, publish = Event(), Event()

            def writing_worker():
                evidence = directory / "round-000/verus.txt"
                evidence.parent.mkdir()
                evidence.write_text("partially written initial diagnostic")
                (directory / "report.json.tmp").write_text('{"unfinished":')
                staged.set()
                if not publish.wait(10):
                    raise TimeoutError("checkpoint publication was not released")
                evidence.write_text("completed initial diagnostic")
                confirmation = evidence.parent / "confirmation/verus.txt"
                confirmation.parent.mkdir()
                confirmation.write_text("completed repeated diagnostic")
                write_json(directory / "report.json", next_shard)

            with ThreadPoolExecutor(max_workers=3) as workers:
                writer = workers.submit(writing_worker)
                try:
                    self.assertTrue(staged.wait(10))
                    snapshots = list(workers.map(lambda _: refresh_report(output), range(4)))
                    for report in snapshots:
                        self.assertEqual(report["counts"]["library_specification"], 1)
                        self.assertEqual(report["coverage"]["pending_files"], 1)
                        self.assertTrue(all(group["message"] != "second completed model" for group in report["groups"]))
                finally:
                    publish.set()
                writer.result(timeout=10)
            report = refresh_report(output)
            self.assertEqual(report["counts"]["library_specification"], 2)
            self.assertEqual(report["coverage"]["pending_files"], 0)
            self.assertEqual(report["counts"]["unconfirmed_support_diagnostic"], 2)
            self.assertFalse((directory / "report.json.tmp").exists())

    def test_automatic_and_manual_report_writers_share_the_same_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary, state="running")
            source = Source("src/lib.rs", (output / "originals/src/lib.rs").read_bytes(), Cfg([]))
            project = SimpleNamespace(output=output, manifest=manifest, sources={source.file: source})
            shard["directory"] = str(checkpoint.parent.relative_to(output))
            lock = Lock()
            concurrent = 0
            highest = 0

            def writing(path, report):
                nonlocal concurrent, highest
                with lock:
                    concurrent += 1
                    highest = max(highest, concurrent)
                try:
                    time.sleep(0.01)
                    write_report(path, report)
                finally:
                    with lock:
                        concurrent -= 1

            with mock.patch("verus_gap.report.write_report", side_effect=writing), ThreadPoolExecutor(max_workers=4) as workers:
                calls = [
                    workers.submit(save_report, project, [shard], manifest["selected_files"])
                    if number % 2 else workers.submit(refresh_report, output)
                    for number in range(8)
                ]
                reports = [future.result(timeout=10) for future in calls]
            self.assertEqual(highest, 1)
            self.assertTrue(all(report["counts"]["library_specification"] == 1 for report in reports))
            self.assertEqual(json.loads((output / "report.json").read_text())["groups"], reports[-1]["groups"])
            self.assertEqual(list(output.glob(".*.tmp")), [])

    def test_running_workers_wait_for_restoration_without_claiming_a_restoration_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, manifest, shard, checkpoint = self.saved_result(temporary, state="running")
            manifest["workers_restored"] = False
            write_json(output / "project.json", manifest)
            report = refresh_report(output)
            self.assertFalse(report["workspace_restored"])
            self.assertNotEqual(report["status"], "restoration_failed")
            self.assertIn("not yet verified while workers are active", (output / "report.md").read_text())
            manifest["scan_progress"]["state"] = "stopped"
            write_json(output / "project.json", manifest)
            self.assertEqual(refresh_report(output)["status"], "restoration_failed")

    def test_source_link_uses_the_original_file_and_preserves_visible_line_and_column(self):
        place = {"file": "src/engine/general_purpose/decode.rs", "line": 122, "column": 5}
        self.assertEqual(
            source_link(place),
            "[`src/engine/general_purpose/decode.rs:122:5`]"
            "(originals/src/engine/general_purpose/decode.rs#L122)",
        )

    def test_source_link_encodes_reserved_and_unicode_filename_characters(self):
        cases = (
            ("src/a b(1)[x]#%.rs", "src/a%20b%281%29%5Bx%5D%23%25.rs"),
            ("src/\u6570\u636e.rs", "src/%E6%95%B0%E6%8D%AE.rs"),
        )
        for file, encoded in cases:
            with self.subTest(file=file):
                self.assertEqual(
                    source_link({"file": file, "line": 12, "column": 3}),
                    f"[`{file}:12:3`](originals/{encoded}#L12)",
                )

    def test_source_link_keeps_backticks_in_a_filename_inside_a_code_span(self):
        self.assertEqual(
            source_link({"file": "src/ticks``.rs", "line": 2, "column": 1}),
            "[``` src/ticks``.rs:2:1 ```](originals/src/ticks%60%60.rs#L2)",
        )

    def test_review_links_original_locations_without_changing_diagnostics(self):
        report = {
            "coverage": {"checking_boundary_only_files": 1},
            "groups": [{
                "category": "checking_boundary", "message": "cannot use ignored local type",
                "locations": [{"file": "src/selected.rs", "line": 4, "column": 7}],
                "raw_example": "unchanged raw diagnostic",
                "evidence": ["shards/a/round-000/verus.txt"],
            }],
        }
        before = copy.deepcopy(report)
        text = review_markdown(report)
        self.assertIn("- [`src/selected.rs:4:7`](originals/src/selected.rs#L4)", text)
        self.assertIn("unchanged raw diagnostic", text)
        self.assertIn("[Raw evidence](shards/a/round-000/verus.txt)", text)
        self.assertIn("deliberately stopped", text)
        self.assertEqual(report, before)

    def test_fast_report_filters_primary_and_related_locations_without_editing_raw_evidence(self):
        source = SimpleNamespace(functions=[], declarations=[])
        project = SimpleNamespace(sources={"selected.rs": source, "dependency.rs": source}, manifest={
            "input": "fixture", "package": "fixture", "verus": {"version": "fixture"}, "baseline_passed": True,
            "cargo_args": [], "checks": {"proof_lifetimes": True}, "workspace_restored": True, "scan_mode": "fast",
            "scan_level": 1,
        })
        selected = {"file": "selected.rs", "byte": 0, "line": 1, "column": 1, "source": "target()"}
        dependency = dict(selected, file="dependency.rs")
        record = {
            "category": "library_specification", "level": "error", "message": "model not recognized",
            "rendered": "unchanged raw diagnostic", "locations": [selected, dependency],
            "related_locations": [dependency], "confirmed": True, "macro_expansion": False,
            "raw_diagnostic": None, "scope": ["selected.rs"], "source_variant": "prepared",
            "round": 0, "evidence": "round-000/verus.txt",
        }
        shard = {
            "observations": [record, dict(record, message="dependency-only", locations=[dependency])],
            "events": [], "focus": "selected.rs", "directory": "shards/a", "scope": ["selected.rs"],
            "status": "partial", "stop_reason": "focused_dependency_boundary",
            "dependency_boundary_limited": True, "scan_level": 1,
        }
        report = aggregate(project, [shard], ["selected.rs"])
        self.assertEqual(report["scan_mode"], "fast")
        self.assertEqual(report["scan_level"], 1)
        self.assertEqual(report["shards"][0]["scan_level"], 1)
        self.assertEqual(report["counts"], {"library_specification": 1})
        self.assertEqual(report["selected_scope_counts"], report["counts"])
        self.assertEqual(report["coverage"]["expanded_context_files"], 0)
        self.assertEqual(report["coverage"]["dependency_boundary_limited_files"], 1)
        self.assertEqual(report["coverage"]["outside_scope_diagnostics"], 1)
        self.assertEqual(report["groups"][0]["locations"], [selected])
        self.assertEqual(report["groups"][0]["related_locations"], [])
        self.assertEqual(report["groups"][0]["dependency_locations"], 0)
        self.assertEqual(report["groups"][0]["raw_example"], record["rendered"])
        self.assertEqual(record["locations"], [selected, dependency])
        self.assertEqual(report["outside_scope_evidence"][0]["evidence"], "shards/a/round-000/verus.txt")
        text = markdown(report)
        self.assertIn("- [`selected.rs:1:1`](originals/selected.rs#L1)", text)
        self.assertIn("Scan level: 1, selected source only", text)
        self.assertNotIn("#### dependency-only", text)
        self.assertNotIn("`dependency.rs:", text)
        project.manifest["scan_level"] = shard["scan_level"] = 2
        shard["stop_reason"] = "checking_boundary_only"
        early = aggregate(project, [shard], ["selected.rs"])
        self.assertEqual(early["scan_level"], 2)
        self.assertEqual(early["shards"][0]["scan_level"], 2)
        self.assertEqual(early["coverage"]["checking_boundary_only_files"], 1)
        self.assertEqual(early["coverage"]["partial_files"], 1)
        self.assertEqual(early["groups"], report["groups"])
        text = markdown(early)
        self.assertIn("Scan level: 2 (fast), selected source only", text)
        self.assertIn("Boundary-only early stopping", text)
        self.assertIn("Later gaps in the selected source may still be hidden", text)
        self.assertNotIn("`dependency.rs:", text)

    def test_public_api_ignores_bracketed_const_bounds(self):
        record = {"raw_diagnostic": {"children": [{
            "message": "pub assume_specification<T, impl [const] FnOnce(T) -> bool + [const] Destruct> "
                       "[std::option::Option::<T>::is_none_or] (_0: Option<T>, _1: F) -> bool;",
        }]}}
        self.assertEqual(public_api(record), "std::option::Option::<T>::is_none_or")

    def test_public_api_preserves_nested_array_brackets_and_missing_hints(self):
        path = "<&[u8; 16] as std::convert::AsRef<[u8]>>::as_ref"
        record = {"raw_diagnostic": {"children": [{
            "message": f"pub assume_specification [{path}] (_0: &[u8; 16]) -> &[u8];",
        }]}}
        self.assertEqual(public_api(record), path)
        self.assertIsNone(public_api({"raw_diagnostic": None}))
        record["raw_diagnostic"]["children"][0]["message"] = "pub assume_specification<T: [const] Destruct>"
        self.assertIsNone(public_api(record))

    def test_language_summary_keeps_raw_feature_details_separate(self):
        self.assertEqual(language_family(
            "The verifier does not yet support the following Rust feature: "
            "Path Def(Static { mutability: Mut }, DefId(kernel::STORAGE))"
        ), "Mutable static access")
        self.assertEqual(language_family(
            "The verifier does not yet support the following Rust feature: "
            "unsizing operation from `&S` to `&dyn T`"
        ), "Unsizing coercions")
        self.assertEqual(language_family("Verus does not support this cast: `usize` to `*mut u8`"),
                         "Pointer casts")

    def test_same_internal_method_name_does_not_merge_distinct_public_apis(self):
        def record(api, file):
            return {
                "category": "library_specification", "level": "error",
                "message": "`core::sync::atomic::impl&%1%default%load` is not supported",
                "rendered": "unchanged raw diagnostic",
                "locations": [{"file": file, "byte": 0, "line": 1, "column": 1, "source": "load()"}],
                "related_locations": [], "confirmed": True, "macro_expansion": False,
                "raw_diagnostic": {"children": [{"message": f"pub assume_specification [{api}] (_0: u32);"}]},
                "scope": ["selected.rs", "dependency.rs"], "source_variant": "prepared",
                "round": 0, "evidence": "round-000/verus.txt",
            }

        source = SimpleNamespace(functions=[], declarations=[])
        project = SimpleNamespace(sources={"selected.rs": source, "dependency.rs": source}, manifest={
            "input": "fixture", "package": "fixture", "verus": {}, "baseline_passed": True,
            "cargo_args": [], "checks": {}, "workspace_restored": True,
        })
        shard = {
            "observations": [record("AtomicBool::load", "selected.rs"), record("AtomicUsize::load", "dependency.rs")],
            "events": [], "focus": "selected.rs", "directory": "shards/a", "scope": ["selected.rs", "dependency.rs"],
            "status": "partial", "stop_reason": "unresolved_frontier",
        }
        report = aggregate(project, [shard], ["selected.rs"])
        self.assertEqual(report["scan_level"], 0)
        self.assertEqual(report["counts"]["library_specification"], 2)
        self.assertEqual(report["selected_scope_counts"]["library_specification"], 1)
        self.assertEqual(report["coverage"]["expanded_context_files"], 1)
        self.assertEqual(report["groups"][1]["dependency_locations"], 1)
        self.assertEqual(report["groups"][0]["raw_example"], "unchanged raw diagnostic")


if __name__ == "__main__":
    unittest.main()
