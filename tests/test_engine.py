from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verus_gap.engine import DependencyIndex, State, canonical, classify, compiler_panic, dependencies, execute, parse_output, probe
from verus_gap.gap import run
from verus_gap.source import Cfg, Source


class PerformanceTests(unittest.TestCase):
    def support(self, byte: int = 0):
        return {
            "category": "language_rejection", "level": "error",
            "message": "Verus does not support example",
            "locations": [{"file": "lib.rs", "byte": byte}], "related_locations": [],
        }

    def run_rounds(self, outputs: list[list[dict]], rounds: int, progress=None,
                   scan_level: int = 1, result: dict | None = None):
        if result is None:
            result = {"exit_code": 1, "seconds": 0.1, "timed_out": False, "stderr": "", "stdout": ""}
        project = SimpleNamespace(
            manifest={"tool_sha256": {}}, sources={}, deadline=time.monotonic() + 30,
            verify_originals=mock.Mock(),
        )

        def advance(project, state, records, number):
            state.bodies.setdefault("lib.rs", set()).add(str(number))
            return True

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "verus_gap.engine.execute", side_effect=[(result, records, "") for records in outputs],
        ) as execute, mock.patch("verus_gap.engine.refine", side_effect=advance):
            report = probe(
                project, "lib.rs", Path(directory), max_rounds=rounds,
                scan_level=scan_level, progress=progress,
            )
        return report, execute.call_count

    def boundary(self):
        return dict(
            self.support(), category="checking_boundary",
            message="cannot use function `fixture::helper::value` which is ignored because it is external.",
        )

    def test_level_2_stops_at_boundary_only_errors_without_further_isolation(self):
        boundary = self.boundary()
        summary = dict(boundary, category="compiler_summary", message="aborting due to 1 previous error")
        report, calls = self.run_rounds([[boundary, summary]], 2, scan_level=2)
        self.assertEqual(calls, 1)
        self.assertEqual(report["scan_level"], 2)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["stop_reason"], "checking_boundary_only")
        self.assertTrue(report["dependency_boundary_limited"])
        self.assertTrue(report["workspace_restored"])
        self.assertFalse(report["complete_inventory"])
        self.assertEqual(report["events"], [])
        self.assertEqual(len(report["rounds"]), 1)
        self.assertEqual(report["observations"][0]["message"], boundary["message"])
        self.assertFalse(report["rounds"][0]["confirmation_performed"])

    def test_probe_defaults_to_level_2_without_dependency_expansion_or_boundary_isolation(self):
        project = SimpleNamespace(
            manifest={"tool_sha256": {}}, sources={}, deadline=time.monotonic() + 30,
            verify_originals=mock.Mock(),
        )
        result = {"exit_code": 1, "seconds": 0.1, "timed_out": False, "stderr": "", "stdout": ""}
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "verus_gap.engine.execute", return_value=(result, [self.boundary()], ""),
        ) as executing, mock.patch("verus_gap.engine.refine") as refining, mock.patch(
            "verus_gap.engine.dependencies",
        ) as expanding:
            report = probe(project, "lib.rs", Path(directory), max_rounds=2)
        executing.assert_called_once()
        refining.assert_not_called()
        expanding.assert_not_called()
        self.assertEqual(report["scan_level"], 2)
        self.assertEqual(report["stop_reason"], "checking_boundary_only")
        self.assertEqual(report["scope"], ["lib.rs"])

    def test_level_2_confirms_support_before_stopping_on_a_later_boundary_only_round(self):
        support, boundary = self.support(), self.boundary()
        progress = mock.Mock()
        report, calls = self.run_rounds(
            [[support, boundary], [support, boundary], [boundary]], 3, progress, scan_level=2,
        )
        self.assertEqual(calls, 3)
        self.assertEqual(report["stop_reason"], "checking_boundary_only")
        self.assertTrue(report["observations"][0]["confirmed"])
        self.assertEqual(report["observations"][0]["evidence"], "round-000/verus.txt")
        completed = [call.args[1] for call in progress.call_args_list if call.args[0] == "round"]
        self.assertTrue(completed[0]["confirmation_performed"])
        self.assertEqual(completed[1]["observations"], [])
        self.assertEqual(len(report["rounds"]), 2)

    def test_level_1_continues_past_a_boundary_that_can_hide_a_later_support_gap(self):
        boundary, support = self.boundary(), self.support()
        outputs = [[boundary], [support], [support]]
        focused, focused_calls = self.run_rounds(outputs, 2, scan_level=1)
        fast, fast_calls = self.run_rounds(outputs, 2, scan_level=2)
        self.assertEqual(focused_calls, 3)
        self.assertTrue(focused["observations"][1]["confirmed"])
        self.assertEqual(fast_calls, 1)
        self.assertEqual(fast["stop_reason"], "checking_boundary_only")
        self.assertTrue(all(record["category"] == "checking_boundary" for record in fast["observations"]))

    def test_level_2_does_not_early_stop_with_non_boundary_errors_or_instrumentation_warnings(self):
        boundary = self.boundary()
        for category in (
            "language_rejection", "library_specification", "migration_requirement",
            "rust_or_wrapper_blocker", "checking_scope_artifact", "dependency_codegen_blocker",
            "compiler_failure", "instrumentation_failure",
        ):
            other = dict(
                self.support(), category=category,
                level="warning" if category == "instrumentation_failure" else "error",
            )
            with self.subTest(category=category):
                report, _ = self.run_rounds(
                    [[boundary, other], [boundary, other]], 1, scan_level=2,
                )
                self.assertEqual(report["stop_reason"], "round_limit")

    def test_level_2_boundary_warning_does_not_create_an_error_frontier(self):
        boundary = dict(self.boundary(), level="warning")
        result = {"exit_code": 0, "seconds": 0.1, "timed_out": False, "stderr": "", "stdout": ""}
        report, calls = self.run_rounds([[boundary]], 1, scan_level=2, result=result)
        self.assertEqual(calls, 1)
        self.assertEqual(report["status"], "frontend_clear")
        self.assertEqual(report["stop_reason"], "no_remaining_errors")
        self.assertFalse(report["dependency_boundary_limited"])

    def test_level_2_keeps_timeout_and_compiler_failure_stop_policies(self):
        boundary = self.boundary()
        timeout = {"exit_code": 1, "seconds": 0.1, "timed_out": True, "stderr": "", "stdout": ""}
        report, calls = self.run_rounds([[boundary]], 1, scan_level=2, result=timeout)
        self.assertEqual(calls, 1)
        self.assertEqual(report["stop_reason"], "per_run_timeout")
        panic = dict(timeout, timed_out=False, stderr="thread 'rustc' (42) panicked at vir/src/traits.rs:1:1:\n")
        report, calls = self.run_rounds([[boundary], [boundary]], 1, scan_level=2, result=panic)
        self.assertEqual(calls, 2)
        self.assertEqual(report["stop_reason"], "round_limit")
        self.assertTrue(report["rounds"][0]["confirmation_performed"])
        self.assertTrue(any(record["category"] == "compiler_failure" for record in report["observations"]))

    def test_progress_does_not_change_observations_states_or_compiler_calls(self):
        support = self.support()
        quiet, quiet_calls = self.run_rounds([[support], [support], [support]], 2)
        progress = mock.Mock()
        verbose, verbose_calls = self.run_rounds([[support], [support], [support]], 2, progress)
        self.assertEqual(quiet, verbose)
        self.assertEqual(quiet_calls, verbose_calls)
        phases = [call.args[0] for call in progress.call_args_list]
        self.assertEqual(phases, [
            "restore", "probe", "confirmation", "round", "action", "probe", "round", "action",
        ])
        completed = [call.args[1] for call in progress.call_args_list if call.args[0] == "round"]
        self.assertTrue(completed[0]["observations"][0]["confirmed"])
        self.assertEqual(completed[1]["observations"], [])
        self.assertEqual(progress.call_args_list[0].args[1]["observations"], [])

    def test_ctrl_c_keeps_only_complete_rounds_and_marks_the_checkpoint_interrupted(self):
        support = self.support()
        result = {"exit_code": 1, "seconds": 0.1, "timed_out": False, "stderr": "", "stdout": ""}
        for interrupted_call, expected_rounds, expected_observations in (
            (0, 0, 0), (1, 0, 0), (2, 1, 1),
        ):
            with self.subTest(interrupted_call=interrupted_call), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                project = SimpleNamespace(
                    manifest={"tool_sha256": {}}, sources={}, deadline=time.monotonic() + 30,
                    verify_originals=mock.Mock(),
                )
                calls = [(result, [support], "")] * interrupted_call + [KeyboardInterrupt()]

                def advance(project, state, records, number):
                    state.bodies.setdefault("lib.rs", set()).add(str(number))
                    return True

                with mock.patch("verus_gap.engine.execute", side_effect=calls), mock.patch(
                    "verus_gap.engine.refine", side_effect=advance,
                ), self.assertRaises(KeyboardInterrupt):
                    probe(project, "lib.rs", directory, max_rounds=2, scan_level=1)
                saved = json.loads((directory / "report.json").read_text())
                self.assertEqual(saved["stop_reason"], "interrupted")
                self.assertEqual(saved["status"], "partial")
                self.assertTrue(saved["workspace_restored"])
                self.assertEqual(len(saved["rounds"]), expected_rounds)
                self.assertEqual(len(saved["observations"]), expected_observations)
                if expected_observations:
                    self.assertTrue(saved["observations"][0]["confirmed"])
                    self.assertEqual(saved["observations"][0]["evidence"], "round-000/verus.txt")
                project.verify_originals.assert_called_once()

    def test_interruption_during_source_write_restores_the_changed_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Source("lib.rs", b"pub fn example() {}\n", Cfg([]))
            working = root / source.file
            working.write_bytes(source.data)
            project = SimpleNamespace(
                manifest={"root_source": source.file}, sources={source.file: source},
                source_paths={source.file: working}, workspace=root, cwd=root, env={},
                command=["unused-verus", "--out-dir", str(root / "build")],
                deadline=time.monotonic() + 30,
            )
            original_write = Path.write_bytes

            def interrupt_after_write(path, data):
                written = original_write(path, data)
                if path == working and data != source.data:
                    raise KeyboardInterrupt
                return written

            with mock.patch.object(Path, "write_bytes", interrupt_after_write), mock.patch(
                "verus_gap.engine.run",
            ) as running:
                with self.assertRaises(KeyboardInterrupt):
                    execute(project, State([source.file]), root / "probe", 30)
            running.assert_not_called()
            self.assertEqual(working.read_bytes(), source.data)

    def test_ctrl_c_remains_an_interrupt_if_the_compiler_has_already_exited(self):
        for delayed in (False, True):
            with self.subTest(delayed=delayed):
                child = mock.Mock(pid=1234)
                child.communicate.side_effect = [
                    KeyboardInterrupt(),
                    *([subprocess.TimeoutExpired("fixture", 3)] if delayed else []),
                    (b"", b""),
                ]
                with mock.patch("verus_gap.gap.subprocess.Popen", return_value=child), mock.patch(
                    "verus_gap.gap.os.killpg", side_effect=ProcessLookupError,
                ) as killing, self.assertRaises(KeyboardInterrupt):
                    run(["fixture"], Path("."), {}, 10)
                self.assertEqual(child.communicate.call_count, 3 if delayed else 2)
                self.assertEqual(killing.call_count, 2 if delayed else 1)
                self.assertEqual(killing.call_args_list[0].args, (1234, signal.SIGTERM))

    def test_progress_never_announces_unconfirmed_stored_observations(self):
        support = self.support()
        progress = mock.Mock()
        report, calls = self.run_rounds([[support], [], [support], [support]], 2, progress)
        self.assertEqual(calls, 4)
        self.assertFalse(report["observations"][0]["confirmed"])
        self.assertTrue(all(
            not call.args[1]["observations"]
            for call in progress.call_args_list if call.args[0] == "round"
        ))

    def test_already_accepted_diagnostic_does_not_reconfirm_another_overlay(self):
        support = self.support()
        report, calls = self.run_rounds([[support], [support], [support]], 2)
        self.assertEqual(calls, 3)
        self.assertTrue(report["observations"][0]["confirmed"])
        self.assertEqual(report["observations"][0]["round"], 0)
        self.assertEqual(report["observations"][0]["evidence"], "round-000/verus.txt")
        self.assertTrue(report["rounds"][0]["confirmation_performed"])
        self.assertFalse(report["rounds"][1]["confirmation_performed"])
        self.assertEqual(report["rounds"][1]["previously_confirmed_support"], 1)
        self.assertEqual(len(report["observations"]), 1)

    def test_same_diagnostic_at_a_new_location_still_requires_confirmation(self):
        first, second = self.support(), self.support(1)
        for level in (1, 2):
            with self.subTest(level=level):
                report, calls = self.run_rounds(
                    [[first], [first], [first, second], [first, second]], 2, scan_level=level,
                )
                self.assertEqual(calls, 4)
                self.assertTrue(all(record["confirmed"] for record in report["observations"]))
                self.assertTrue(report["rounds"][1]["confirmation_performed"])
                self.assertEqual(report["observations"][1]["round"], 1)

    def test_unconfirmed_observation_never_suppresses_a_later_confirmation(self):
        support = self.support()
        report, calls = self.run_rounds([[support], [], [support], [support]], 2)
        self.assertEqual(calls, 4)
        self.assertTrue(report["rounds"][1]["confirmation_performed"])
        self.assertEqual(report["rounds"][1]["previously_confirmed_support"], 0)
        self.assertFalse(report["observations"][0]["confirmed"])

    def test_dependency_index_preserves_priority_scope_and_ambiguity(self):
        sources = {
            "src/lib.rs": "mod a; mod b;\n",
            "src/a.rs": "pub fn value() {} pub struct Value;\n",
            "src/b.rs": "pub fn value() {} pub struct Value;\n",
            "outside.rs": "pub fn other() {}\n",
        }
        project = SimpleNamespace(
            sources={name: Source(name, text.encode(), Cfg([])) for name, text in sources.items()},
            manifest={"crate_name": "fixture", "root_source": "src/lib.rs"},
        )
        index = DependencyIndex(project)

        def resolve(path, scope=(), kind="function"):
            record = {"category": "checking_boundary", "message": f"cannot use {kind} `fixture::{path}`"}
            return dependencies(project, State(list(scope)), [record], index)

        self.assertEqual(resolve("a::value"), ["src/a.rs"])
        self.assertEqual(resolve("value"), [])
        self.assertEqual(resolve("value", ["src/a.rs"]), ["src/b.rs"])
        self.assertEqual(resolve("a::value", ["src/a.rs"]), ["src/b.rs"])
        self.assertEqual(resolve("a::GENERATED"), ["src/a.rs"])
        self.assertEqual(resolve("a::Value", kind="type"), ["src/a.rs"])
        self.assertEqual(resolve("unknown::other"), ["outside.rs"])
        self.assertEqual(resolve("value", ["src/a.rs", "src/b.rs"]), [])

    def test_dependency_index_does_not_resolve_inactive_declarations(self):
        project = SimpleNamespace(
            sources={"src/a.rs": Source("src/a.rs", b"#[cfg(windows)] pub fn gone() {}\n", Cfg([]))},
            manifest={"crate_name": "fixture", "root_source": "src/lib.rs"},
        )
        record = {"category": "checking_boundary", "message": "cannot use function `fixture::unknown::gone`"}
        self.assertEqual(dependencies(project, State([]), [record], DependencyIndex(project)), [])


class ClassificationTests(unittest.TestCase):
    def test_instrumentation_warning_prevents_support_confirmation(self):
        support = {
            "category": "language_rejection", "level": "error", "message": "Verus does not support example",
            "locations": [{"file": "lib.rs", "byte": 0}], "related_locations": [],
        }
        warning = {
            "category": "instrumentation_failure", "level": "warning",
            "message": "#[verus_spec] is likely used inside a verus! block.",
            "locations": [], "related_locations": [],
        }
        result = {"exit_code": 1, "seconds": 0.1, "timed_out": False, "stderr": "", "stdout": ""}
        project = SimpleNamespace(
            manifest={"tool_sha256": {}}, sources={}, deadline=time.monotonic() + 30,
            verify_originals=mock.Mock(),
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "verus_gap.engine.execute", return_value=(result, [support, warning], ""),
        ) as execute:
            report = probe(project, "lib.rs", Path(directory), scan_level=1)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["observations"][0]["confirmed"])

    def test_instrumentation_warning_in_confirmation_prevents_acceptance(self):
        support = {
            "category": "language_rejection", "level": "error", "message": "Verus does not support example",
            "locations": [{"file": "lib.rs", "byte": 0}], "related_locations": [],
        }
        warning = {
            "category": "instrumentation_failure", "level": "warning",
            "message": "#[verus_spec] is likely used inside a verus! block.",
            "locations": [], "related_locations": [],
        }
        result = {"exit_code": 1, "seconds": 0.1, "timed_out": False, "stderr": "", "stdout": ""}
        project = SimpleNamespace(
            manifest={"tool_sha256": {}}, sources={}, deadline=time.monotonic() + 30,
            verify_originals=mock.Mock(),
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "verus_gap.engine.execute", side_effect=[
                (result, [support], ""), (result, [support, warning], ""),
            ],
        ) as execute:
            report = probe(project, "lib.rs", Path(directory), scan_level=1)
        self.assertEqual(execute.call_count, 2)
        self.assertFalse(report["observations"][0]["confirmed"])

    def test_warning_only_instrumentation_round_does_not_clear_frontier(self):
        warning = {
            "category": "instrumentation_failure", "level": "warning",
            "message": "#[verus_spec] is likely used inside a verus! block.",
            "locations": [], "related_locations": [],
        }
        result = {"exit_code": 0, "seconds": 0.1, "timed_out": False, "stderr": "", "stdout": ""}
        project = SimpleNamespace(
            manifest={"tool_sha256": {}}, sources={}, deadline=time.monotonic() + 30,
            verify_originals=mock.Mock(),
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "verus_gap.engine.execute", return_value=(result, [warning], ""),
        ) as execute:
            report = probe(project, "lib.rs", Path(directory), scan_level=1)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["stop_reason"], "instrumentation_failure")

    def test_double_verus_rewriting_warning_is_retained_as_instrumentation(self):
        record = {
            "level": "warning", "message": "#[verus_spec] is likely used inside a verus! block.",
            "spans": [], "rendered": "the unchanged compiler warning\n",
        }
        self.assertEqual(classify(record, "fixture"), "instrumentation_failure")
        parsed, text = parse_output(
            json.dumps(record), SimpleNamespace(cwd=Path("/workspace"), manifest={"crate_name": "fixture"}), {},
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["category"], "instrumentation_failure")
        self.assertEqual(text, record["rendered"])

    def test_panic_excerpt_starts_with_the_trace_not_preceding_json_warnings(self):
        result = {
            "stderr": '{"level":"warning","message":"unrelated warning"}\n'
                      "thread 'rustc' (42) panicked at vir/src/traits.rs:1:1:\n"
                      "the original panic message\n",
            "stdout": "",
        }
        record = compiler_panic(result, SimpleNamespace(sources={}), State([]))
        self.assertIn("the original panic message", record["rendered"])
        self.assertNotIn("unrelated warning", record["rendered"])

    def test_unstable_definition_numbers_are_normalized_without_losing_the_path(self):
        first = "Path Def(Static, DefId(0:2111 ~ kernel[7250]::hal::GDT_STORAGE))"
        second = "Path Def(Static, DefId(0:2115 ~ kernel[ffff]::hal::GDT_STORAGE))"
        self.assertEqual(canonical(first), canonical(second))
        self.assertIn("kernel::hal::GDT_STORAGE", canonical(first))
        self.assertNotEqual(canonical(first), canonical(second.replace("GDT_STORAGE", "HEAP_STORAGE")))

    def record(self, message: str, **kwargs):
        return dict(level="error", message=message, **kwargs)

    def test_library_hint_does_not_promote_local_boundary(self):
        record = self.record(
            "cannot use function `fixture::S::f` which is ignored because it is not marked verus_verify",
            children=[{"message": "use assume_specification"}],
        )
        self.assertEqual(classify(record, "fixture"), "checking_boundary")
        record["message"] = "`fixture::S` is not supported"
        self.assertEqual(classify(record, "fixture"), "checking_boundary")

    def test_rust_diagnostic_never_becomes_support_gap(self):
        record = self.record("unsupported type", code={"code": "E0432"})
        self.assertEqual(classify(record, "fixture"), "rust_or_wrapper_blocker")

    def test_cfg_parser_failure_is_not_a_language_gap(self):
        record = self.record("removing an expression is not supported in this position")
        self.assertEqual(classify(record, "fixture"), "rust_or_wrapper_blocker")

    def test_parser_expected_tokens_are_not_specification_hints(self):
        record = self.record("expected one of: `broadcast`, `assume_specification`, `fn`")
        self.assertEqual(classify(record, "fixture"), "rust_or_wrapper_blocker")

    def test_api_name_containing_expected_is_not_a_parser_error(self):
        record = self.record(
            "`library::get_expected` is not supported",
            children=[{"message": "use assume_specification"}],
        )
        self.assertEqual(classify(record, "fixture"), "library_specification")

    def test_missing_dependency_mir_is_not_a_source_migration_requirement(self):
        record = self.record("missing optimized MIR for `dependency::f` in the crate `dependency`")
        self.assertEqual(classify(record, "fixture"), "dependency_codegen_blocker")


@unittest.skipUnless((ROOT / ".tools/installation.json").is_file(), "run ./setup")
class RealInventoryTests(unittest.TestCase):
    def scan(self, files: dict[str, str], *options: str, target_relative: str | None = None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        project = root / "project"
        for name, text in files.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        output = root / "results"
        target = project / target_relative if target_relative else (
            project if "Cargo.toml" in files else project / "lib.rs"
        )
        process = subprocess.run(
            [str(ROOT / "verus-gap"), str(target), "--out", str(output), *options],
            capture_output=True, text=True, timeout=120,
        )
        for name, text in files.items():
            self.assertEqual((project / name).read_text(), text)
        self.assertFalse((project / "target").exists())
        return process, output

    def test_default_verbose_matches_explicit_level_2_and_streams_confirmed_findings(self):
        files = {
            "Cargo.toml": '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n',
            "src/lib.rs": "mod selected;\nmod helper;\n",
            "src/selected.rs": (
                (ROOT / "tests/fixtures/frontier/lib.rs").read_text()
                + "\npub fn blocked() -> usize { crate::helper::value() }\n"
            ),
            "src/helper.rs": "pub fn value() -> usize { let _ = std::time::SystemTime::now(); 1 }\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, output = root / "project", root / "results"
            for name, text in files.items():
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            with subprocess.Popen(
                [str(ROOT / "verus-gap"), str(project / "src/selected.rs"), "--out", str(output),
                 "--verbose", "--timeout", "30"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            ) as process:
                try:
                    self.assertIsNotNone(process.stdout)
                    prefix = []
                    for line in process.stdout:
                        prefix.append(line)
                        if "[limitation]" in line:
                            self.assertIsNone(process.poll())
                            initial = json.loads((output / "report.json").read_text())
                            self.assertEqual(initial["scan_progress"]["state"], "running")
                            self.assertEqual(initial["coverage"]["pending_files"], 1)
                            self.assertEqual(initial["counts"], {})
                            checkpoints = list((output / "shards").glob("*/report.json"))
                            self.assertEqual(len(checkpoints), 1)
                            checkpoint = json.loads(checkpoints[0].read_text())
                            accepted = [
                                record for record in checkpoint["observations"]
                                if record["confirmed"] and record["category"] == "language_rejection"
                            ]
                            self.assertTrue(accepted)
                            evidence = checkpoints[0].parent / Path(accepted[0]["evidence"]).parent
                            self.assertTrue((evidence / "verus.txt").is_file())
                            self.assertTrue((evidence / "confirmation/verus.txt").is_file())
                            break
                    else:
                        self.fail("No live confirmed limitation was emitted")
                    remaining, _ = process.communicate(timeout=30)
                    stdout = "".join(prefix) + remaining
                    self.assertEqual(process.returncode, 1, stdout)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            report = json.loads((output / "report.json").read_text())
            shard = json.loads((output / report["shards"][0]["directory"] / "report.json").read_text())
            self.assertEqual(stdout.count("[limitation]"), 3)
            self.assertNotIn("SystemTime", stdout)
            self.assertIn("[probe]", stdout)
            self.assertIn("[confirm]", stdout)
            self.assertIn("[isolate]", stdout)
            self.assertIn("confirmed groups: 1 language / 2 models", stdout)
            self.assertIn("[scan] level=2", stdout)
            self.assertEqual(report["scan_level"], 2)
            self.assertEqual(shard["status"], "partial")
            self.assertTrue(shard["dependency_boundary_limited"])
            self.assertEqual(report["coverage"]["expanded_context_files"], 0)
            for name, text in files.items():
                self.assertEqual((project / name).read_text(), text)
            quiet_process, quiet_output = self.scan(
                files, "--scan-level", "2", target_relative="src/selected.rs",
            )
            self.assertEqual(quiet_process.returncode, 1, quiet_process.stdout + quiet_process.stderr)
            quiet = json.loads((quiet_output / "report.json").read_text())
            quiet_shard = json.loads((quiet_output / quiet["shards"][0]["directory"] / "report.json").read_text())
            self.assertNotIn("[probe]", quiet_process.stdout)
            self.assertNotIn("[limitation]", quiet_process.stdout)
            self.assertEqual(report["counts"], quiet["counts"])
            self.assertEqual(report["coverage"], quiet["coverage"])
            self.assertEqual(shard["events"], quiet_shard["events"])
            self.assertEqual(
                [(item["state"], item["confirmation_performed"]) for item in shard["rounds"]],
                [(item["state"], item["confirmation_performed"]) for item in quiet_shard["rounds"]],
            )

    def test_ctrl_c_writes_partial_reports_and_offline_refresh_needs_no_tools(self):
        files = {
            "Cargo.toml": '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n',
            "src/lib.rs": "mod selected;\nmod helper;\n",
            "src/selected.rs": (
                (ROOT / "tests/fixtures/frontier/lib.rs").read_text()
                + "\npub fn blocked() -> usize { crate::helper::value() }\n"
            ),
            "src/helper.rs": "pub fn value() -> usize { 1 }\n",
        }
        for phase in ("confirmation", "second_probe"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                project, output = root / "project", root / "results"
                for name, text in files.items():
                    path = project / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                with subprocess.Popen(
                    [str(ROOT / "verus-gap"), str(project / "src/selected.rs"), "--out", str(output),
                     "--scan-level", "1", "--verbose", "--timeout", "30"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                ) as process:
                    try:
                        self.assertIsNotNone(process.stdout)
                        prefix = []
                        for line in process.stdout:
                            prefix.append(line)
                            hit = "[confirm]" in line if phase == "confirmation" else (
                                "[probe]" in line and "round 001" in line
                            )
                            if hit:
                                process.send_signal(signal.SIGINT)
                                break
                        else:
                            self.fail("The requested interruption phase was not reached")
                        remaining, _ = process.communicate(timeout=30)
                        stdout = "".join(prefix) + remaining
                        self.assertEqual(process.returncode, 2, stdout)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                report = json.loads((output / "report.json").read_text())
                manifest = json.loads((output / "project.json").read_text())
                self.assertEqual(report["scan_progress"]["state"], "interrupted", stdout)
                self.assertEqual(report["scan_progress"]["completed_files"], 0)
                self.assertEqual(report["coverage"]["partial_files"], 1)
                self.assertEqual(report["coverage"]["pending_files"], 0)
                self.assertTrue(report["workspace_restored"])
                if phase == "confirmation":
                    self.assertEqual(report["counts"], {})
                    self.assertIsNone(report["scan_progress"]["last_completed_round"])
                else:
                    self.assertEqual(report["counts"]["language_rejection"], 1)
                    self.assertEqual(report["counts"].get("library_specification", 0), 0)
                    self.assertEqual(report["scan_progress"]["last_completed_round"], 0)
                self.assertIn("Interrupted report:", stdout)
                self.assertIn("interrupted", (output / "report.md").read_text())
                self.assertIn("interrupted", (output / "review.md").read_text())
                for name, text in files.items():
                    self.assertEqual((project / name).read_text(), text)
                    self.assertEqual((output / "workspace" / name).read_text(), text)
                index = manifest["shard_index"]["src/selected.rs"]
                checkpoint = output / index / "report.json"
                self.assertEqual(json.loads(checkpoint.read_text())["stop_reason"], "interrupted")
                evidence = {
                    path: path.read_bytes() for path in (output / "shards").rglob("*") if path.is_file()
                }
                original_manifest = (output / "project.json").read_bytes()
                (output / "workspace").rename(output / "unused-workspace")
                refreshed = subprocess.run(
                    [sys.executable, str(ROOT / "verus-gap"), "report", str(output)],
                    env={**os.environ, "PATH": str(root / "no-tools")},
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
                current = json.loads((output / "report.json").read_text())
                self.assertEqual(current["counts"], report["counts"])
                self.assertEqual(current["groups"], report["groups"])
                self.assertEqual(current["scan_progress"]["state"], "interrupted")
                self.assertEqual((output / "project.json").read_bytes(), original_manifest)
                for path, before in evidence.items():
                    self.assertEqual(path.read_bytes(), before, str(path))
                if phase == "second_probe":
                    (output / "unused-workspace").rename(output / "workspace")
                    first_round = checkpoint.parent / "round-000"
                    stable = {
                        path: (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in first_round.rglob("*") if path.is_file()
                    }
                    resumed = subprocess.run(
                        [str(ROOT / "verus-gap"), "--resume", str(output), "--verbose"],
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(resumed.returncode, 1, resumed.stdout + resumed.stderr)
                    final = json.loads((output / "report.json").read_text())
                    self.assertEqual(final["scan_progress"]["state"], "finished")
                    self.assertEqual(final["counts"]["language_rejection"], 1)
                    self.assertEqual(final["counts"]["library_specification"], 2)
                    for path, (before, timestamp) in stable.items():
                        self.assertEqual(path.read_bytes(), before, str(path))
                        self.assertEqual(path.stat().st_mtime_ns, timestamp, str(path))

    def test_level_1_file_retains_cargo_context_without_exploring_dependency_bodies(self):
        files = {
            "Cargo.toml": (
                '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n'
                '[features]\ndefault=["enabled"]\nenabled=[]\n'
            ),
            "src/lib.rs": "mod selected;\nmod helper;\nmod unrelated;\n",
            "src/selected.rs": (
                "#[cfg(feature=\"enabled\")] pub fn local() { let _ = std::time::Instant::now(); }\n"
                "pub fn blocked() -> usize { crate::helper::value() }\n"
            ),
            "src/helper.rs": "pub fn value() -> usize { let _ = std::time::SystemTime::now(); 1 }\n",
            "src/unrelated.rs": "pub struct Unrelated; unsafe impl Send for Unrelated {}\n",
        }
        process, output = self.scan(files, "--scan-level", "1", target_relative="src/selected.rs")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        project = json.loads((output / "project.json").read_text())
        self.assertEqual(report["scan_mode"], "fast")
        self.assertEqual(project["scan_mode"], "fast")
        self.assertEqual(report["scan_level"], 1)
        self.assertEqual(project["scan_level"], 1)
        self.assertEqual(report["shards"][0]["scan_level"], 1)
        self.assertTrue(project["baseline_passed"])
        self.assertIn('feature="enabled"', project["rust_cfg"])
        self.assertEqual(report["coverage"]["selected_files"], 1)
        self.assertEqual(report["coverage"]["expanded_context_files"], 0)
        self.assertEqual(report["coverage"]["dependency_boundary_limited_files"], 1)
        self.assertEqual(report["coverage"]["partial_files"], 1)
        self.assertEqual(report["selected_scope_counts"], report["counts"])
        self.assertEqual(report["counts"]["library_specification"], 2)
        self.assertNotIn("language_rejection", report["counts"])
        self.assertEqual(report["shards"][0]["scope"], ["src/selected.rs"])
        self.assertEqual(report["shards"][0]["stop_reason"], "focused_dependency_boundary")
        support = {"language_rejection", "library_specification"}
        documents = {
            True: (output / "report.md").read_text(),
            False: (output / "review.md").read_text(),
        }
        for group in report["groups"]:
            self.assertTrue(all(place["file"] == "src/selected.rs" for place in group["locations"]))
            if group["category"] == "library_specification":
                self.assertTrue(group["confirmed"])
            for place in group["locations"]:
                self.assertIn(
                    f"- [`{place['file']}:{place['line']}:{place['column']}`]"
                    f"(originals/{place['file']}#L{place['line']})",
                    documents[group["category"] in support],
                )
                source = output / "originals" / place["file"]
                self.assertEqual(source.read_text().splitlines()[place["line"] - 1], place["source"])
        self.assertNotIn("SystemTime", (output / "report.md").read_text())
        self.assertIn("selected source only", (output / "report.md").read_text())
        self.assertFalse(any(event["kind"] == "scope_expansion" for event in report["shards"][0]["events"]))
        baseline_timestamp = (output / "baseline.stdout").stat().st_mtime_ns
        resumed = subprocess.run(
            [str(ROOT / "verus-gap"), "--resume", str(output), "--verbose"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(resumed.returncode, 1, resumed.stdout + resumed.stderr)
        resumed_report = json.loads((output / "report.json").read_text())
        self.assertEqual(resumed_report["scan_level"], 1)
        self.assertEqual(resumed_report["counts"], report["counts"])
        self.assertIn("[scan] level=1", resumed.stdout)
        self.assertEqual((output / "baseline.stdout").stat().st_mtime_ns, baseline_timestamp)

    def test_default_level_2_resume_retains_existing_evidence(self):
        process, output = self.scan(
            {"lib.rs": "pub fn gap() { let _ = std::time::Instant::now(); }\n"},
        )
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["counts"]["library_specification"], 2)
        evidence = output / report["shards"][0]["directory"] / "round-000/verus.stderr"
        timestamp = evidence.stat().st_mtime_ns
        resumed = subprocess.run(
            [str(ROOT / "verus-gap"), "--resume", str(output), "--verbose"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(resumed.returncode, 1, resumed.stdout + resumed.stderr)
        self.assertEqual(json.loads((output / "report.json").read_text())["scan_mode"], "fast")
        self.assertEqual(json.loads((output / "report.json").read_text())["scan_level"], 2)
        self.assertEqual(evidence.stat().st_mtime_ns, timestamp)
        self.assertIn("[reuse]", resumed.stdout)
        self.assertNotIn("[probe]", resumed.stdout)
        self.assertNotIn("[limitation]", resumed.stdout)
        quiet_resume = subprocess.run(
            [str(ROOT / "verus-gap"), "--resume", str(output)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(quiet_resume.returncode, 1, quiet_resume.stdout + quiet_resume.stderr)
        self.assertNotIn("[reuse]", quiet_resume.stdout)
        self.assertEqual(evidence.stat().st_mtime_ns, timestamp)

    def test_default_directory_checks_all_selected_files_but_no_other_files(self):
        files = {
            "Cargo.toml": '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n',
            "src/lib.rs": "mod selected;\nmod unrelated;\n",
            "src/selected/mod.rs": "mod first;\nmod second;\n",
            "src/selected/first.rs": "pub fn first() { let _ = std::time::Instant::now(); }\n",
            "src/selected/second.rs": "pub fn second(value: &mut u32) { let _ = std::mem::replace(value, 2); }\n",
            "src/unrelated.rs": "pub struct Unrelated; unsafe impl Send for Unrelated {}\n",
        }
        process, output = self.scan(files, target_relative="src/selected")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["coverage"]["selected_files"], 3)
        self.assertEqual(report["scan_level"], 2)
        self.assertEqual(report["coverage"]["expanded_context_files"], 0)
        self.assertEqual(report["counts"]["library_specification"], 3)
        self.assertNotIn("language_rejection", report["counts"])
        self.assertTrue(all(shard["scope"] == [shard["focus"]] for shard in report["shards"]))

    def test_level_0_resume_rejects_a_different_explicit_level(self):
        process, output = self.scan(
            {"lib.rs": "pub fn good() -> u32 { 1 }\n"}, "--scan-level", "0",
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        before = {name: (output / name).read_bytes() for name in ("project.json", "report.md", "report.json")}
        self.assertEqual(json.loads(before["project.json"])["scan_level"], 0)
        for options in (["--scan-level", "1"], ["--scan-level", "2"]):
            with self.subTest(options=options):
                resumed = subprocess.run(
                    [str(ROOT / "verus-gap"), "--resume", str(output), *options],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(resumed.returncode, 2)
                self.assertIn("keeps its original scan level", resumed.stderr)
                for name, data in before.items():
                    self.assertEqual((output / name).read_bytes(), data)

    def test_default_boundary_only_scan_does_not_claim_a_clear_frontier(self):
        files = {
            "Cargo.toml": '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n',
            "src/lib.rs": "mod selected;\nmod helper;\n",
            "src/selected.rs": "pub fn run() -> usize { crate::helper::value() }\n",
            "src/helper.rs": "pub fn value() -> usize { 1 }\n",
        }
        process, output = self.scan(files, target_relative="src/selected.rs")
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertNotIn("library_specification", report["counts"])
        self.assertEqual(report["coverage"]["partial_files"], 1)
        self.assertEqual(report["coverage"]["frontier_cleared_after_exclusions"], 0)
        self.assertEqual(report["coverage"]["expanded_context_files"], 0)
        self.assertEqual(report["coverage"]["checking_boundary_only_files"], 1)
        self.assertEqual(report["coverage"]["excluded_bodies"], 0)
        self.assertEqual(report["coverage"]["excluded_declarations"], 0)
        self.assertEqual(report["shards"][0]["stop_reason"], "checking_boundary_only")
        self.assertEqual(report["scan_level"], 2)
        directory = output / report["shards"][0]["directory"]
        initial = json.loads((directory / "report.json").read_text())
        self.assertEqual(len(initial["rounds"]), 1)
        evidence = directory / initial["rounds"][0]["evidence"]
        timestamp = evidence.stat().st_mtime_ns
        baseline_timestamp = (output / "baseline.stdout").stat().st_mtime_ns
        resumed = subprocess.run(
            [str(ROOT / "verus-gap"), "--resume", str(output), "--verbose"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(resumed.returncode, 2, resumed.stdout + resumed.stderr)
        self.assertIn("[reuse]", resumed.stdout)
        self.assertNotIn("[probe]", resumed.stdout)
        self.assertEqual(evidence.stat().st_mtime_ns, timestamp)
        self.assertEqual((output / "baseline.stdout").stat().st_mtime_ns, baseline_timestamp)
        self.assertEqual(json.loads((directory / "report.json").read_text())["rounds"], initial["rounds"])
        self.assertEqual(json.loads((output / "report.json").read_text())["scan_level"], 2)
        self.assertIn("Boundary-only early stopping", (output / "report.md").read_text())
        self.assertIn("deliberately stopped", (output / "review.md").read_text())
        task = json.loads((output / "agent-tasks.json").read_text())["tasks"][0]
        self.assertEqual(task["scan_level"], 2)

    def test_recognized_standard_models_do_not_become_missing_library_gaps(self):
        source = (
            "pub fn recognized() -> bool {\n"
            "let mut values: Vec<u32> = Vec::new();\n"
            "values.push(3);\n"
            "values.len() == 1 && Some(2u32).is_some()\n}\n"
        )
        process, output = self.scan({"lib.rs": source})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["counts"], {})
        self.assertEqual(report["coverage"]["excluded_bodies"], 0)
        self.assertEqual(report["coverage"]["excluded_declarations"], 0)

    def test_scoped_file_follows_more_than_seven_dependency_hops(self):
        depth = 9
        files = {
            "Cargo.toml": '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n',
            "src/lib.rs": "mod selected;\nmod unrelated;\n" + "".join(
                f"mod chain{index};\n" for index in range(depth)
            ),
            "src/selected.rs": "pub fn run() -> usize { crate::chain0::value() }\n",
            "src/unrelated.rs": "pub struct Unrelated; unsafe impl Send for Unrelated {}\n",
        }
        for index in range(depth):
            body = f"crate::chain{index + 1}::value()" if index + 1 < depth else (
                "let _ = std::time::Instant::now(); 1"
            )
            files[f"src/chain{index}.rs"] = f"pub fn value() -> usize {{ {body} }}\n"
        process, output = self.scan(
            files, "--scan-level", "0", "--verbose", target_relative="src/selected.rs",
        )
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["coverage"]["selected_files"], 1)
        self.assertEqual(report["coverage"]["expanded_context_files"], depth)
        self.assertEqual(report["coverage"]["partial_files"], 0)
        self.assertNotIn("language_rejection", report["counts"])
        self.assertGreater(report["counts"].get("library_specification", 0), 0)
        self.assertNotIn("library_specification", report["selected_scope_counts"])
        for group in report["groups"]:
            if group["category"] == "library_specification":
                self.assertTrue(group["confirmed"])
                self.assertTrue(all(place["file"] == f"src/chain{depth - 1}.rs" for place in group["locations"]))
        self.assertNotIn("src/unrelated.rs", report["shards"][0]["scope"])
        self.assertIn("[scope]", process.stdout)
        self.assertIn("[model][dependency]", process.stdout)

    def test_scoped_directory_keeps_cargo_features_and_transitive_context(self):
        files = {
            "Cargo.toml": (
                '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n'
                '[features]\ndefault=["enabled"]\nenabled=[]\n'
            ),
            "src/lib.rs": "mod selected;\nmod helper;\nmod leaf;\nmod unrelated;\n",
            "src/selected/mod.rs": (
                "#[cfg(feature=\"enabled\")] pub fn run() -> usize { crate::helper::value() }\n"
            ),
            "src/helper.rs": "pub fn value() -> usize { crate::leaf::value() }\n",
            "src/leaf.rs": "pub fn value() -> usize { let _ = std::time::Instant::now(); 1 }\n",
            "src/unrelated.rs": "pub struct Unrelated; unsafe impl Send for Unrelated {}\n",
        }
        process, output = self.scan(files, "--scan-level", "0", target_relative="src/selected")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["coverage"]["selected_files"], 1)
        self.assertEqual(report["coverage"]["expanded_context_files"], 2)
        self.assertNotIn("language_rejection", report["counts"])
        self.assertNotIn("src/unrelated.rs", report["shards"][0]["scope"])
        project = json.loads((output / "project.json").read_text())
        self.assertIn('feature="enabled"', project["rust_cfg"])

    def test_layering_exposes_hidden_library_specs(self):
        source = (ROOT / "tests/fixtures/frontier/lib.rs").read_text()
        process, output = self.scan({"lib.rs": source})
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["counts"]["language_rejection"], 1)
        self.assertEqual(report["counts"]["library_specification"], 2)
        self.assertEqual(report["selected_scope_counts"], report["counts"])
        self.assertIn("inline-asm expressions", report["language_families"])
        self.assertTrue(report["workspace_restored"])
        self.assertTrue(all(group["confirmed"] for group in report["groups"]))
        directory = output / report["shards"][0]["directory"]
        shard = json.loads((directory / "report.json").read_text())
        self.assertGreater(len(shard["rounds"]), 1)
        self.assertTrue((directory / "round-000/confirmation/verus.txt").is_file())
        self.assertNotIn("std::time::Instant", (directory / "round-000/verus.txt").read_text())
        self.assertIn("std::time::Instant", (output / "report.md").read_text())

    def test_local_signature_boundary_is_not_a_library_gap(self):
        manifest = '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n'
        files = {
            "Cargo.toml": manifest, "src/lib.rs": "mod child;\npub fn f(_value: child::S) {}\n",
            "src/child.rs": "pub struct S;\n",
        }
        process, output = self.scan(files, "--file", "lib.rs", "--scan-level", "0")
        self.assertIn(process.returncode, {0, 2}, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertNotIn("library_specification", report["counts"])
        self.assertEqual(report["coverage"]["selected_files"], 1)
        events = report["shards"][0]["events"]
        self.assertTrue(any(event["kind"] == "scope_expansion" for event in events), report)

    def test_baseline_failure_persists_actual_error(self):
        process, output = self.scan({"lib.rs": "pub fn f() { missing(); }\n"})
        self.assertEqual(process.returncode, 2)
        project = json.loads((output / "project.json").read_text())
        self.assertFalse(project["baseline_passed"])
        self.assertIn("missing", (output / "baseline.stderr").read_text())
        self.assertIn("no Verus limitation was inferred", (output / "report.md").read_text())

    def test_derives_preserved_without_manufacturing_trait_errors(self):
        source = '#[derive(Clone, Copy, Debug)] pub struct S { pub value: u32 }\npub fn f(s: S) -> u32 { s.value }\n'
        process, output = self.scan({"lib.rs": source})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["counts"], {})
        transformed = (output / report["shards"][0]["directory"] / "round-000/sources/lib.rs").read_text()
        self.assertIn("#[derive(Clone, Copy, Debug)]", transformed)

    def test_signature_peeling_keeps_original_unsafe_impl_evidence(self):
        source = 'pub struct S; unsafe impl Send for S {}\nimpl S { pub fn f(mut self) {} }\n'
        process, output = self.scan({"lib.rs": source})
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["counts"]["language_rejection"], 2)
        self.assertTrue(all(group["confirmed"] for group in report["groups"]))
        self.assertEqual(report["coverage"]["excluded_bodies"], 0)

    def test_module_checking_does_not_leak_to_unrewritten_files(self):
        manifest = '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n'
        files = {
            "Cargo.toml": manifest,
            "src/lib.rs": "mod child;\npub fn f() -> u32 { 1 }\n",
            "src/child.rs": "pub fn looped() { for _ in 0..3 {} }\n",
        }
        process, output = self.scan(files, "--file", "lib.rs")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["counts"], {})

    def test_root_support_loaded_without_polluting_the_namespace(self):
        process, output = self.scan({"lib.rs": "pub mod Ghost { pub fn good() {} }\n"})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        source = (output / report["shards"][0]["directory"] / "round-000/sources/lib.rs").read_text()
        self.assertIn("use vstd::prelude::Ghost as _;", source)
        self.assertNotIn("use vstd::prelude::*;", source)

    def test_macro_import_hygiene_does_not_hide_real_asm_rejection(self):
        files = {
            "Cargo.toml": (
                '[workspace]\nmembers=["arch"]\n[package]\nname="fixture"\n'
                'version="0.1.0"\nedition="2021"\n[dependencies]\narch={path="arch"}\n'
            ),
            "src/lib.rs": 'use ::core::arch;\npub fn f() { unsafe { arch::asm!("nop"); } }\n',
            "arch/Cargo.toml": '[package]\nname="arch"\nversion="0.1.0"\nedition="2021"\n',
            "arch/src/lib.rs": "pub struct Context;\n",
        }
        process, output = self.scan(files)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertNotIn("rust_or_wrapper_blocker", report["counts"])
        self.assertEqual(report["counts"]["language_rejection"], 1)

    def test_trait_signature_excludes_impl_not_invalid_method_attribute(self):
        source = (
            "pub struct S; impl Iterator for S { type Item = u32;\n"
            "fn next(&mut self) -> Option<u32> { None }\n"
            "fn nth(&mut self, _: usize) -> Option<u32> { None } }\n"
        )
        process, output = self.scan({"lib.rs": source})
        self.assertIn(process.returncode, {0, 2}, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertNotIn("instrumentation_failure", report["counts"])
        self.assertTrue(any(
            event["declaration"] == "S" and event["kind"] == "external_declaration"
            for event in report["shards"][0]["events"]
        ))

    def test_trait_signature_excludes_trait_not_invalid_method_attribute(self):
        source = "pub trait T { fn consume(mut self) where Self: Sized {} }\n"
        process, output = self.scan({"lib.rs": source})
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertNotIn("instrumentation_failure", report["counts"])
        self.assertTrue(any(
            event["declaration"] == "T" and event["kind"] == "external_declaration"
            for event in report["shards"][0]["events"]
        ))

    def test_resume_reuses_complete_shards_and_baseline(self):
        process, output = self.scan({"lib.rs": "pub fn f() -> u32 { 1 }\n"})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        baseline_time = (output / "baseline.stdout").stat().st_mtime_ns
        initial = json.loads((output / "report.json").read_text())
        directory = output / initial["shards"][0]["directory"]
        evidence_time = (directory / "round-000/verus.stderr").stat().st_mtime_ns
        resumed = subprocess.run(
            [str(ROOT / "verus-gap"), "--resume", str(output)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual((output / "baseline.stdout").stat().st_mtime_ns, baseline_time)
        self.assertEqual((directory / "round-000/verus.stderr").stat().st_mtime_ns, evidence_time)

    def test_multiple_diagnostics_do_not_escalate_one_body_in_a_single_round(self):
        source = (
            "pub fn f(x: &mut u32) {\n"
            "let _ = core::mem::replace(x, 2);\n"
            "core::mem::forget(*x);\n}\n"
        )
        process, output = self.scan({"lib.rs": source})
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["coverage"]["excluded_bodies"], 1)
        self.assertEqual(report["coverage"]["excluded_declarations"], 0)

    def test_real_ghost_dependency_setup_defers_and_checks_the_root(self):
        from verus_gap.gap import find_verus, identity
        from verus_gap.target import official_sources
        source = official_sources(identity(find_verus(None))["commit"])
        version = tomllib.loads((source / "source/vstd/Cargo.toml").read_text())["package"]["version"]
        files = {
            "Cargo.toml": (
                '[workspace]\nmembers=["context"]\n[package]\nname="fixture"\n'
                'version="0.1.0"\nedition="2021"\n[dependencies]\ncontext={path="context"}\n'
            ),
            "src/lib.rs": "pub fn value() -> u32 { context::make(7).value }\n",
            "context/Cargo.toml": (
                '[package]\nname="context"\nversion="0.1.0"\nedition="2021"\n'
                '[package.metadata.verus]\nverify=true\n[dependencies]\n'
                f'vstd="={version}"\n'
            ),
            "context/src/lib.rs": (
                "use vstd::prelude::*;\nverus! {\n"
                "pub struct Data { pub value: u32 }\n"
                "pub fn make(value: u32) -> (result: Data)\n"
                "ensures result.value == value\n{ Data { value } }\n}\n"
            ),
        }
        process, output = self.scan(files)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        project = json.loads((output / "project.json").read_text())
        self.assertEqual(project["support_mode"], "cargo_verus_ghost_dependencies")
        self.assertTrue(project["dependency_setup"]["captured_root_without_execution"])
        self.assertEqual(project["dependency_setup"]["probe_emit"], "metadata")
        self.assertTrue(any(name.endswith(".vir") for name in project["dependency_artifacts_sha256"]))
        self.assertTrue(project["preparation_check"]["exit_code"] == 0)


if __name__ == "__main__":
    unittest.main()
