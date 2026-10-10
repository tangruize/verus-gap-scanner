from __future__ import annotations

import hashlib
import io
import json
import os
import pickle
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verus_gap.cli import ReportProgress, VerboseProgress, main
from verus_gap.gap import GapError, run
from verus_gap.parallel import Work, Worker, Workers, rebind_driver_environment, scan, worker_main
from verus_gap.project import Project
from verus_gap.source import Cfg, Source


def fixture_probe(project, focus, directory, *, progress, **options):
    directory.mkdir(parents=True, exist_ok=True)
    fixture = Path(project.manifest["fixture_root"])
    mode = project.manifest.get("fixture_mode")
    (directory / "worker.json").write_text(json.dumps({
        "pid": os.getpid(), "workspace": str(project.workspace),
        "cwd": str(project.cwd), "command": project.command, "env": project.env,
        "source_identities": {name: source.identity() for name, source in project.sources.items()},
    }))

    def started(name):
        (fixture / name).touch()

    def wait_for(name):
        deadline = time.monotonic() + 10
        while not (fixture / name).exists():
            if time.monotonic() >= deadline:
                raise AssertionError(f"Timed out waiting for {name}")
            time.sleep(0.005)

    if mode == "dynamic":
        if focus == "a.rs":
            started("first-started")
            wait_for("third-started")
        elif focus == "b.rs":
            wait_for("first-started")
        else:
            started("third-started")
    elif mode == "isolation":
        path = project.workspace / "a.rs"
        path.write_bytes(f"private {focus}".encode())
        try:
            started(f"{focus}-started")
            wait_for("b.rs-started" if focus == "a.rs" else "a.rs-started")
            if path.read_bytes() != f"private {focus}".encode():
                raise AssertionError("Worker sources were shared")
            parent = Path(project.manifest["parent_workspace"]) / "a.rs"
            if parent.read_bytes() != project.sources["a.rs"].data:
                raise AssertionError("Coordinator sources were modified")
            if parent.stat().st_ino == path.stat().st_ino:
                raise AssertionError("Writable sources were hardlinked")
        finally:
            path.write_bytes(project.sources["a.rs"].data)
    elif mode == "snapshots":
        summary = {"focus": focus, "rounds": [{"round": 0}]}
        progress("round", {"focus": focus, "summary": summary})
        summary["rounds"].append({"round": 1})
    elif mode == "failure":
        raise ValueError("specific worker failure")
    elif mode == "interrupt":
        if focus == "a.rs":
            wait_for("second-started")
        else:
            started("second-started")
            if not project.cancel_event.wait(10):
                raise AssertionError("Other worker was not cancelled")
            started("second-cancelled")
        raise KeyboardInterrupt
    elif mode in {"exit", "exit-dirty"}:
        if focus == "a.rs":
            if mode == "exit-dirty":
                (project.workspace / "a.rs").write_bytes(b"interrupted overlay")
            os._exit(17)
        project.cancel_event.wait(10)
        raise KeyboardInterrupt
    elif mode == "backpressure":
        for number in range(20):
            progress("round", {"focus": focus, "payload": "x" * 300_000, "number": number})
    return {
        "focus": focus, "status": "frontend_clear", "stop_reason": "no_remaining_errors",
        "observations": [], "rounds": [], "scope": [focus], "events": [],
    }


def fixture_worker(*args):
    with mock.patch("verus_gap.parallel.probe", new=fixture_probe), mock.patch(
        "verus_gap.source.Parser", side_effect=AssertionError("A worker reparsed its cached source"),
    ):
        worker_main(*args)


class ParallelTests(unittest.TestCase):
    def project(self, temporary: str):
        output = Path(temporary) / "results"
        workspace = output / "workspace"
        workspace.mkdir(parents=True)
        sources = {}
        for name in ("a.rs", "b.rs", "c.rs"):
            data = f"pub fn {name[0]}() {{}}\n".encode()
            (workspace / name).write_bytes(data)
            sources[name] = Source(name, data, Cfg([]))
        artifact = output / "build/libdependency.rmeta"
        artifact.parent.mkdir()
        artifact.write_bytes(b"immutable dependency")
        manifest = {
            "rust_cfg": [], "root_source": "a.rs", "tool_sha256": {}, "workspace_restored": True,
            "fixture_root": temporary, "parent_workspace": str(workspace),
        }
        return Project(
            output, workspace, workspace,
            {"CARGO_MANIFEST_DIR": str(workspace), "OUT_DIR": str(artifact.parent)},
            ["unused-verus", str(workspace / "a.rs"), "--extern", f"dependency={artifact}",
             "--out-dir", str(output / "verus-build")],
            sources, manifest, time.monotonic() + 30,
        )

    def work(self, project, names=("a.rs", "b.rs", "c.rs")):
        return [
            Work(index, name, project.output / "shards" / name[:-3])
            for index, name in enumerate(names)
        ]

    def worker(self, project):
        workers = Workers(project, 2, 2)
        parent, child = workers.context.Pipe()
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        with workers.cache.open("rb") as stream:
            prepared = pickle.load(stream)
        return workers, Worker(prepared, 0, workers.cancel, child, time.monotonic()), parent

    def contexts(self, project):
        return {
            item.focus: json.loads((item.directory / "worker.json").read_text())
            for item in self.work(project) if (item.directory / "worker.json").is_file()
        }

    def test_free_worker_takes_the_next_file_instead_of_a_fixed_partition(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.manifest["fixture_mode"] = "dynamic"
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker):
                results = list(scan(project, self.work(project), 2, 2, mock.Mock(), mock.Mock()))
            self.assertEqual(len(results), 3)
            contexts = self.contexts(project)
            self.assertEqual(contexts["b.rs"], contexts["c.rs"])
            self.assertNotEqual(contexts["a.rs"], contexts["b.rs"])
            self.assertNotEqual(contexts["a.rs"]["pid"], os.getpid())
            self.assertEqual(contexts["b.rs"]["pid"], contexts["c.rs"]["pid"])
            self.assertNotEqual(contexts["a.rs"]["pid"], contexts["b.rs"]["pid"])
            self.assertTrue(project.manifest["workers_restored"])
            self.assertEqual(len(list((project.output / "workers").glob("*/worker-*/workspace"))), 2)

    def test_pending_work_is_dispatched_largest_first_without_changing_selected_ordinals(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            for name, extra in (("a.rs", 0), ("b.rs", 50), ("c.rs", 100)):
                original = project.sources[name]
                data = original.data + b" " * extra
                project.sources[name] = Source(name, data, original.cfg)
                (project.workspace / name).write_bytes(data)
            work = self.work(project)
            starting = mock.Mock()
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker):
                results = list(scan(project, work, 2, 2, starting, mock.Mock()))
            self.assertEqual([call.args[0].focus for call in starting.call_args_list], ["c.rs", "b.rs", "a.rs"])
            self.assertEqual([call.args[0].number for call in starting.call_args_list], [2, 1, 0])
            self.assertEqual([item.focus for item in work], ["a.rs", "b.rs", "c.rs"])
            self.assertEqual(list(project.sources), ["a.rs", "b.rs", "c.rs"])
            self.assertEqual({item for item, result in results}, set(work))
            self.assertEqual(project.manifest["parallel_runs"][0]["scheduling"], "largest_source_first")
            self.assertTrue(project.manifest["workers_restored"])

    def test_equal_sizes_keep_selected_order_and_only_pending_files_are_dispatched(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            work = self.work(project)
            starting = mock.Mock()
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker):
                results = list(scan(project, [work[2], work[0]], 2, 2, starting, mock.Mock()))
            self.assertEqual([call.args[0] for call in starting.call_args_list], [work[0], work[2]])
            self.assertEqual({item for item, result in results}, {work[0], work[2]})
            self.assertNotIn("b.rs", self.contexts(project))

    def test_worker_sources_and_captured_paths_are_private_but_dependencies_are_shared(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.manifest["fixture_mode"] = "isolation"
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker):
                list(scan(project, self.work(project, ("a.rs", "b.rs")), 32, 0, mock.Mock(), mock.Mock()))
            contexts = list(self.contexts(project).values())
            self.assertEqual(len(contexts), 2)
            self.assertNotEqual(contexts[0]["workspace"], contexts[1]["workspace"])
            for context in contexts:
                workspace = context["workspace"]
                self.assertEqual(context["cwd"], workspace)
                self.assertEqual(context["env"]["CARGO_MANIFEST_DIR"], workspace)
                self.assertEqual(context["command"][1], str(Path(workspace) / "a.rs"))
                self.assertEqual(context["command"][3], project.command[3])
                self.assertEqual(context["env"]["OUT_DIR"], project.env["OUT_DIR"])
            self.assertEqual((project.output / "build/libdependency.rmeta").read_bytes(), b"immutable dependency")
            self.assertEqual(project.manifest["parallel_runs"][0]["jobs"], 2)

    def test_cargo_verus_root_dispatch_keys_follow_the_private_manifest_without_rekeying_dependencies(self):
        original = {
            "CARGO_PKG_NAME": "fixture", "CARGO_PKG_VERSION": "0.1.0",
            "CARGO_MANIFEST_DIR": "/prepared/workspace/fixture",
            "__VERUS_DRIVER_VIA_CARGO__": "1", "__VERUS_DRIVER_VERIFY_dependency": "1",
            " __VERUS_DRIVER_ARGS_FOR_dependency": "unchanged dependency arguments",
        }

        def package(environment):
            path = environment["CARGO_MANIFEST_DIR"] + os.sep + "Cargo.toml"
            return "fixture-0.1.0-" + hashlib.sha256(path.encode()).hexdigest()[:12]

        previous = package(original)
        original["__VERUS_DRIVER_VERIFY_" + previous] = "1"
        original[" __VERUS_DRIVER_ARGS_FOR_" + previous] = "unchanged root arguments"
        copied = dict(original, CARGO_MANIFEST_DIR="/worker/workspace/fixture")
        rebind_driver_environment(original, copied)
        current = package(copied)
        self.assertEqual(copied["__VERUS_DRIVER_VERIFY_" + current], "1")
        self.assertEqual(copied[" __VERUS_DRIVER_ARGS_FOR_" + current], "unchanged root arguments")
        self.assertNotIn("__VERUS_DRIVER_VERIFY_" + previous, copied)
        self.assertNotIn(" __VERUS_DRIVER_ARGS_FOR_" + previous, copied)
        self.assertEqual(copied["__VERUS_DRIVER_VERIFY_dependency"], original["__VERUS_DRIVER_VERIFY_dependency"])
        self.assertEqual(copied[" __VERUS_DRIVER_ARGS_FOR_dependency"], original[" __VERUS_DRIVER_ARGS_FOR_dependency"])
        self.assertIn("__VERUS_DRIVER_VERIFY_" + previous, original)

    def test_unrecognized_cargo_verus_dispatch_identity_is_an_explicit_error(self):
        for environment in (
            {}, {"CARGO_PKG_NAME": "fixture", "CARGO_PKG_VERSION": "0.1.0", "CARGO_MANIFEST_DIR": "/workspace"},
        ):
            with self.subTest(environment=environment), self.assertRaises(GapError):
                rebind_driver_environment(environment, dict(environment))

    def test_progress_snapshots_are_copied_before_the_worker_mutates_its_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)

            project.manifest["fixture_mode"] = "snapshots"
            events = mock.Mock()
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker):
                list(scan(project, self.work(project, ("a.rs",)), 32, 2, mock.Mock(), events))
            rounds = [call.args[1] for call in events.call_args_list if call.args[0] == "round"]
            self.assertEqual(rounds[0]["summary"]["rounds"], [{"round": 0}])

    def test_source_models_are_serialized_once_and_processes_never_reparse_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            for source in project.sources.values():
                source.render(set(), set())
            original = Source.__getstate__
            with mock.patch.object(Source, "__getstate__", autospec=True, side_effect=original) as serializing, mock.patch(
                "verus_gap.parallel.worker_main", new=fixture_worker,
            ):
                list(scan(project, self.work(project), 3, 2, mock.Mock(), mock.Mock()))
            self.assertEqual(serializing.call_count, len(project.sources))
            self.assertEqual(project.manifest["parallel_runs"][0]["cached_sources"], len(project.sources))
            self.assertEqual(project.manifest["parallel_runs"][0]["model"], "process")
            for context in self.contexts(project).values():
                self.assertEqual(
                    context["source_identities"], {name: source.identity() for name, source in project.sources.items()},
                )

    def test_prepared_absolute_and_relative_symlinks_resolve_inside_each_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            (project.workspace / "absolute.rs").symlink_to(project.workspace / "a.rs")
            (project.workspace / "relative.rs").symlink_to("a.rs")
            (project.workspace / "generated").mkdir()
            (project.workspace / "generated/asset").write_text("generated input")
            (project.workspace / "absolute-dir").symlink_to(project.workspace / "generated", target_is_directory=True)

            workers, worker, _ = self.worker(project)
            context = worker.context()
            for name in ("absolute.rs", "relative.rs"):
                self.assertEqual((context.workspace / name).resolve(), context.workspace / "a.rs")
            self.assertEqual(
                (context.workspace / "absolute-dir/asset").resolve(), context.workspace / "generated/asset",
            )
            (context.workspace / "absolute.rs").write_bytes(b"private overlay")
            try:
                self.assertEqual((context.workspace / "relative.rs").read_bytes(), b"private overlay")
                self.assertEqual((project.workspace / "a.rs").read_bytes(), project.sources["a.rs"].data)
            finally:
                (context.workspace / "a.rs").write_bytes(context.sources["a.rs"].data)
            workers.verify()

    def test_copy_failure_never_publishes_a_frontier_or_starts_a_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.workspace.rename(project.output / "missing-workspace")
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), self.assertRaisesRegex(
                GapError, "No such file or directory",
            ):
                list(scan(project, self.work(project, ("a.rs",)), 2, 2, mock.Mock(), mock.Mock()))
            self.assertEqual(list((project.output / "shards").rglob("report.json")), [])
            self.assertEqual(project.manifest["parallel_runs"][0]["created_workers"], 0)
            self.assertIn(
                "FileNotFoundError", next((project.output / "shards").rglob("worker-error.txt")).read_text(),
            )

    def test_an_active_source_cannot_write_through_a_symlink_outside_its_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            outside = Path(temporary) / "outside.rs"
            outside.write_bytes(project.sources["a.rs"].data)
            (project.workspace / "a.rs").unlink()
            (project.workspace / "a.rs").symlink_to(outside)
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), self.assertRaisesRegex(
                GapError, "Active source leaves the private scan workspace",
            ):
                list(scan(project, self.work(project, ("a.rs",)), 2, 2, mock.Mock(), mock.Mock()))
            self.assertEqual(outside.read_bytes(), project.sources["a.rs"].data)
            self.assertFalse(project.manifest["workers_restored"])

    def test_a_second_ctrl_c_during_shutdown_does_not_skip_worker_restoration(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            original = Workers.shutdown
            handler = signal.getsignal(signal.SIGINT)

            def shutdown(workers):
                signal.raise_signal(signal.SIGINT)
                original(workers)

            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), mock.patch.object(
                Workers, "shutdown", new=shutdown,
            ):
                list(scan(project, self.work(project, ("a.rs",)), 2, 2, mock.Mock(), mock.Mock()))
            self.assertTrue(project.manifest["workers_restored"])
            self.assertEqual(signal.getsignal(signal.SIGINT), handler)

    def test_shared_deadline_prevents_any_new_worker_after_the_budget_expires(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.deadline = time.monotonic() - 1
            with mock.patch("verus_gap.parallel.probe") as probing:
                self.assertEqual(list(scan(project, self.work(project), 32, 2, mock.Mock(), mock.Mock())), [])
            probing.assert_not_called()
            self.assertFalse((project.output / "workers").exists())

    def test_worker_failure_stops_the_pool_and_retains_the_actual_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.manifest["fixture_mode"] = "failure"
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker):
                with self.assertRaisesRegex(GapError, "specific worker failure"):
                    list(scan(project, self.work(project), 2, 2, mock.Mock(), mock.Mock()))
            logs = list((project.output / "shards").glob("*/worker-error.txt"))
            self.assertTrue(logs)
            self.assertIn("ValueError: specific worker failure", logs[0].read_text())
            self.assertTrue(project.manifest["workers_restored"])

    def test_interrupt_cancels_other_active_workers_and_does_not_dispatch_more_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.manifest["fixture_mode"] = "interrupt"
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), self.assertRaises(KeyboardInterrupt):
                list(scan(project, self.work(project), 2, 2, mock.Mock(), mock.Mock()))
            self.assertEqual(set(self.contexts(project)), {"a.rs", "b.rs"})
            self.assertTrue((Path(temporary) / "second-cancelled").is_file())
            self.assertTrue(project.manifest["workers_restored"])

    def test_unexpected_process_exit_stops_and_reaps_the_other_workers(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.manifest["fixture_mode"] = "exit"
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), self.assertRaisesRegex(
                GapError, "exited unexpectedly",
            ):
                list(scan(project, self.work(project), 2, 2, mock.Mock(), mock.Mock()))
            run = project.manifest["parallel_runs"][0]
            self.assertIn(17, run["worker_exit_codes"].values())
            for pid in run["worker_pids"]:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            self.assertNotIn("c.rs", self.contexts(project))
            self.assertTrue(project.manifest["workers_restored"])

    def test_cancellation_drains_large_ipc_messages_instead_of_blocking_process_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.manifest["fixture_mode"] = "backpressure"

            def progress(phase, details):
                if phase == "round":
                    raise KeyboardInterrupt

            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), self.assertRaises(KeyboardInterrupt):
                list(scan(project, self.work(project), 2, 2, mock.Mock(), progress))
            self.assertTrue(project.manifest["workers_restored"])
            self.assertEqual(set(project.manifest["parallel_runs"][0]["worker_exit_codes"].values()), {0})

    def test_forced_worker_death_never_claims_unrestored_sources_are_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)
            project.manifest["fixture_mode"] = "exit-dirty"
            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), self.assertRaisesRegex(
                GapError, "exited unexpectedly.*\\nWorker cleanup failed:.*not restored",
            ):
                list(scan(project, self.work(project), 2, 2, mock.Mock(), mock.Mock()))
            self.assertFalse(project.manifest["workers_restored"])
            self.assertFalse(project.manifest["parallel_runs"][0]["workspace_restored"])
            for pid in project.manifest["parallel_runs"][0]["worker_pids"]:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_cancellation_during_initialization_stops_all_spawned_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(temporary)

            def progress(phase, details):
                if phase == "worker" and details["stage"] == "copy":
                    raise KeyboardInterrupt

            with mock.patch("verus_gap.parallel.worker_main", new=fixture_worker), self.assertRaises(KeyboardInterrupt):
                list(scan(project, self.work(project), 2, 2, mock.Mock(), progress))
            self.assertTrue(project.manifest["workers_restored"])
            self.assertNotIn("c.rs", self.contexts(project))
            for pid in project.manifest["parallel_runs"][0]["worker_pids"]:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)


class CancellationTests(unittest.TestCase):
    def test_cancelled_command_does_not_start_a_compiler(self):
        cancel = Event()
        cancel.set()
        with mock.patch("verus_gap.gap.subprocess.Popen") as launching, self.assertRaises(KeyboardInterrupt):
            run(["unused"], Path("."), {}, 10, cancel_event=cancel)
        launching.assert_not_called()

    def test_cancellation_stops_and_reaps_an_already_started_compiler(self):
        cancel = Event()
        child = mock.Mock(pid=1234)

        def communicating(*, timeout=None):
            if not cancel.is_set():
                cancel.set()
                raise subprocess.TimeoutExpired("fixture", timeout)
            return b"", b""

        child.communicate.side_effect = communicating
        with mock.patch("verus_gap.gap.subprocess.Popen", return_value=child), mock.patch(
            "verus_gap.gap.os.killpg",
        ) as killing, self.assertRaises(KeyboardInterrupt):
            run(["fixture"], Path("."), {}, 10, cancel_event=cancel)
        killing.assert_called_once_with(1234, signal.SIGTERM)
        self.assertEqual(child.communicate.call_count, 2)

    def test_polling_keeps_the_original_command_timeout_and_retained_output(self):
        result = run(
            [sys.executable, "-c", "import os,time; print(os.getpid(),flush=True); time.sleep(30)"],
            ROOT, dict(os.environ), 0.4, cancel_event=Event(),
        )
        self.assertTrue(result["timed_out"])
        self.assertLess(result["seconds"], 3)
        pid = int(result["stdout"].strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class JobsCliTests(unittest.TestCase):
    def test_invalid_job_counts_fail_before_setup(self):
        for value in ("0", "-1", "many"):
            with self.subTest(value=value), mock.patch("verus_gap.cli.prepare") as preparing:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                    main(["--jobs", value])
                self.assertEqual(failure.exception.code, 2)
                preparing.assert_not_called()

    def test_job_option_cannot_be_applied_to_frontier_but_cargo_options_are_preserved(self):
        with redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--frontier", "--jobs", "32"]), 2)
        self.assertIn("do not combine", errors.getvalue())
        with mock.patch("verus_gap.gap.main", return_value=0) as frontier:
            self.assertEqual(main(["--frontier", "--", "--jobs", "32"]), 0)
        frontier.assert_called_once_with(["--", "--jobs", "32"])

    def test_parallel_scan_performs_setup_once_and_uses_the_requested_pool(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results"
            project = SimpleNamespace(
                output=output, manifest={"tool_sha256": {}}, sources={"a.rs": None, "b.rs": None},
                deadline=time.monotonic() + 30, save=mock.Mock(), verify_originals=mock.Mock(),
            )

            def scanning(project, pending, jobs, level, start, reporting):
                self.assertEqual(jobs, 32)
                self.assertEqual(level, 2)
                for item in reversed(pending):
                    start(item)
                    item.directory.mkdir(parents=True)
                    yield item, {"status": "frontend_clear", "stop_reason": "no_remaining_errors"}

            report = {"counts": {}, "status": "no_confirmed_limitations", "coverage": {"pending_files": 0, "partial_files": 0}}
            with mock.patch("verus_gap.cli.prepare", return_value=project) as preparing, mock.patch(
                "verus_gap.cli.parallel_scan", side_effect=scanning,
            ), mock.patch("verus_gap.cli.probe") as probing, mock.patch(
                "verus_gap.cli.save_report", return_value=report,
            ), redirect_stdout(io.StringIO()) as messages:
                self.assertEqual(main(["fixture", "--out", str(output), "--jobs", "32", "--verbose"]), 0)
            preparing.assert_called_once()
            probing.assert_not_called()
            self.assertEqual(project.manifest["jobs"], 32)
            self.assertEqual(project.manifest["selected_files"], ["a.rs", "b.rs"])
            self.assertIn("[dispatch 1/2] b.rs", messages.getvalue())
            self.assertIn("[dispatch 2/2] a.rs", messages.getvalue())
            self.assertIn("[1/2] b.rs: no_remaining_errors", messages.getvalue())
            self.assertIn("[2/2] a.rs: no_remaining_errors", messages.getvalue())

    def test_single_worker_keeps_selected_order_and_original_file_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results"
            project = SimpleNamespace(
                output=output, manifest={"tool_sha256": {}}, sources={"a.rs": None, "b.rs": None},
                deadline=time.monotonic() + 30, save=mock.Mock(), verify_originals=mock.Mock(),
            )

            def probing(project, focus, directory, **options):
                directory.mkdir(parents=True)
                return {"status": "frontend_clear", "stop_reason": "no_remaining_errors", "observations": []}

            report = {"counts": {}, "status": "no_confirmed_limitations", "coverage": {"pending_files": 0, "partial_files": 0}}
            with mock.patch("verus_gap.cli.prepare", return_value=project), mock.patch(
                "verus_gap.cli.probe", side_effect=probing,
            ) as probing, mock.patch("verus_gap.cli.parallel_scan") as scanning, mock.patch(
                "verus_gap.cli.save_report", return_value=report,
            ), redirect_stdout(io.StringIO()) as messages:
                self.assertEqual(main(["fixture", "--out", str(output), "--verbose"]), 0)
            self.assertEqual([call.args[1] for call in probing.call_args_list], ["a.rs", "b.rs"])
            scanning.assert_not_called()
            self.assertIn("[file 1/2] a.rs", messages.getvalue())
            self.assertIn("[file 2/2] b.rs", messages.getvalue())
            self.assertNotIn("[dispatch", messages.getvalue())

    def test_main_worker_error_keeps_a_stopped_snapshot_instead_of_a_setup_failure_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results"
            project = SimpleNamespace(
                output=output, manifest={"tool_sha256": {}}, sources={"a.rs": None},
                deadline=time.monotonic() + 30, save=mock.Mock(), verify_originals=mock.Mock(),
            )

            def failing(project, pending, jobs, level, start, reporting):
                start(pending[0])
                raise GapError("specific execution failure")
                yield

            report = {"counts": {}, "status": "partial", "coverage": {"pending_files": 1, "partial_files": 0}}
            with mock.patch("verus_gap.cli.prepare", return_value=project), mock.patch(
                "verus_gap.cli.parallel_scan", side_effect=failing,
            ), mock.patch("verus_gap.cli.save_report", return_value=report) as reporting, redirect_stderr(io.StringIO()):
                self.assertEqual(main(["fixture", "--out", str(output), "--jobs", "32"]), 2)
            self.assertEqual(project.manifest["scan_progress"]["state"], "stopped")
            self.assertIn("specific execution failure", project.manifest["scan_progress"]["error"])
            self.assertGreaterEqual(reporting.call_count, 2)

    def test_last_file_stopped_by_the_global_budget_does_not_mark_the_scan_finished(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results"
            project = SimpleNamespace(
                output=output, manifest={"tool_sha256": {}}, sources={"a.rs": None},
                deadline=time.monotonic() + 30, save=mock.Mock(), verify_originals=mock.Mock(),
            )

            def scanning(project, pending, jobs, level, start, reporting):
                item = pending[0]
                start(item)
                item.directory.mkdir(parents=True)
                yield item, {"status": "partial", "stop_reason": "time_budget"}

            report = {"counts": {}, "status": "partial", "coverage": {"pending_files": 0, "partial_files": 1}}
            with mock.patch("verus_gap.cli.prepare", return_value=project), mock.patch(
                "verus_gap.cli.parallel_scan", side_effect=scanning,
            ), mock.patch("verus_gap.cli.save_report", return_value=report), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["fixture", "--out", str(output), "--jobs", "32"]), 2)
            self.assertEqual(project.manifest["scan_progress"]["state"], "stopped")


class ParallelProgressTests(unittest.TestCase):
    def project(self, output):
        return SimpleNamespace(
            output=output, manifest={"scan_level": 2}, save=mock.Mock(), verify_originals=mock.Mock(),
        )

    def test_verbose_initialization_reports_process_readiness_without_counting_findings(self):
        progress = VerboseProgress(["a.rs", "b.rs"], fast=True)
        with mock.patch("verus_gap.cli.print") as printing:
            progress("pool", {"jobs": 2})
            for worker in (1, 2):
                base = {"worker": worker, "jobs": 2, "pid": 100 + worker}
                progress("worker", {**base, "stage": "models"})
                progress("worker", {**base, "stage": "copy", "completed": 17})
                progress("worker", {**base, "stage": "ready", "seconds": 0.7, "sources": 287})
        output = "\n".join(call.args[0] for call in printing.call_args_list)
        self.assertIn("process workers", output)
        self.assertIn("largest sources first", output)
        self.assertIn("dispatched files are not completed files", output)
        self.assertIn("no reparsing", output)
        self.assertIn("17 files copied", output)
        self.assertIn("2/2 workers ready", output)
        self.assertIn("287 cached source models reused", output)
        self.assertTrue(all(call.kwargs["flush"] for call in printing.call_args_list))
        self.assertEqual(progress.groups, set())
        self.assertNotIn("[limitation]", output)

    def test_multiple_active_rounds_share_the_new_group_refresh_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(Path(temporary))
            with mock.patch("verus_gap.cli.time.monotonic", return_value=0) as clock, mock.patch(
                "verus_gap.cli.save_report", return_value={"counts": {}},
            ) as writing:
                reporting = ReportProgress(project, ["a.rs", "b.rs"], [], None)
                reporting.refresh()
                for name in ("a.rs", "b.rs"):
                    reporting.start(name, project.output / "shards" / name)
                self.assertEqual(project.manifest["scan_progress"]["active_files"], ["a.rs", "b.rs"])
                for now, name in ((59, "a.rs"), (60, "b.rs")):
                    clock.return_value = now
                    record = {
                        "category": "language_rejection", "message": f"Verus does not support {name}",
                        "confirmed": True, "raw_diagnostic": None,
                        "locations": [{"file": name, "line": 1, "column": 1}],
                    }
                    reporting("round", {
                        "focus": name, "summary": {"focus": name, "rounds": [{"round": 0}]},
                        "observations": [record],
                    })
                self.assertEqual(writing.call_count, 2)
                self.assertEqual([shard["focus"] for shard in writing.call_args.args[1]], ["a.rs", "b.rs"])
                clock.return_value = 120
                reporting("round", {"focus": "a.rs", "summary": {"focus": "a.rs"}, "observations": [record]})
                self.assertEqual(writing.call_count, 2)

    def test_completion_order_does_not_change_report_shard_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(Path(temporary))
            with mock.patch("verus_gap.cli.save_report", return_value={"counts": {}}) as writing:
                reporting = ReportProgress(project, ["a.rs", "b.rs"], [{"focus": "b.rs"}, {"focus": "a.rs"}], None)
                reporting.refresh()
            self.assertEqual([shard["focus"] for shard in writing.call_args.args[1]], ["a.rs", "b.rs"])

    def test_interrupt_recovers_every_active_checkpoint_and_counts_finished_files_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = self.project(Path(temporary))
            with mock.patch("verus_gap.cli.save_report", return_value={"counts": {}}) as writing:
                reporting = ReportProgress(project, ["a.rs", "b.rs"], [], None)
                for name, reason in (("a.rs", "no_remaining_errors"), ("b.rs", "interrupted")):
                    directory = project.output / "shards" / name
                    directory.mkdir(parents=True)
                    (directory / "report.json").write_text(json.dumps({
                        "focus": name, "stop_reason": reason, "observations": [], "rounds": [{"round": 0}],
                    }))
                    reporting.start(name, directory)
                reporting.interrupt()
            self.assertEqual([shard["focus"] for shard in writing.call_args.args[1]], ["a.rs", "b.rs"])
            self.assertEqual(len(reporting.shards), 1)
            self.assertEqual(project.manifest["scan_progress"]["active_file"], "b.rs")
            self.assertEqual(project.manifest["scan_progress"]["state"], "interrupted")


@unittest.skipUnless((ROOT / ".tools/installation.json").is_file(), "run ./setup")
class RealParallelTests(unittest.TestCase):
    def fixture(self, root: Path, count: int):
        project = root / "input"
        (project / "src/selected").mkdir(parents=True)
        (project / "Cargo.toml").write_text('[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n')
        (project / "src/lib.rs").write_text("mod selected;\n")
        (project / "src/selected/mod.rs").write_text("".join(f"mod f{number:02d};\n" for number in range(count)))
        for number in range(count):
            (project / f"src/selected/f{number:02d}.rs").write_text(
                "pub fn gap() { let _ = std::time::Instant::now(); }\n"
            )
        return project

    def identities(self, report):
        return {
            (group["category"], group["message"], group.get("api"), place["file"], place["byte"])
            for group in report["groups"] for place in group["locations"]
        }

    def test_thirty_two_worker_result_matches_serial_and_resume_changes_pool_without_probes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.fixture(root, 32)
            for number, path in enumerate(sorted((project / "src/selected").glob("f*.rs"))):
                path.write_text(path.read_text() + "//" + "x" * number + "\n")
            reports = []
            for jobs in (1, 32):
                output = root / f"jobs-{jobs}"
                process = subprocess.run(
                    [str(ROOT / "verus-gap"), str(project), "--file", "src/selected/f*.rs",
                     "--jobs", str(jobs), "--verbose", "--out", str(output)],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
                report = json.loads((output / "report.json").read_text())
                self.assertEqual(report["jobs"], jobs)
                self.assertEqual(report["coverage"]["probed_files"], 32)
                self.assertEqual(report["coverage"]["pending_files"], 0)
                self.assertTrue(report["workspace_restored"])
                self.assertEqual(
                    [shard["focus"] for shard in report["shards"]],
                    [f"src/selected/f{number:02d}.rs" for number in range(32)],
                )
                if jobs == 32:
                    self.assertIn("[dispatch 1/32] src/selected/f31.rs", process.stdout)
                    self.assertIn("[dispatch 2/32] src/selected/f30.rs", process.stdout)
                reports.append(report)
            serial, parallel = reports
            self.assertEqual(serial["counts"], parallel["counts"])
            self.assertEqual(serial["coverage"], parallel["coverage"])

            self.assertEqual(self.identities(serial), self.identities(parallel))
            output = root / "jobs-32"
            self.assertEqual(len(list((output / "workers").glob("*/worker-*/workspace"))), 32)
            before = {
                path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for path in (output / "shards").rglob("*") if path.is_file()
            }
            resumed = subprocess.run(
                [str(ROOT / "verus-gap"), "--resume", str(output), "--jobs", "4", "--verbose"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(resumed.returncode, 1, resumed.stdout + resumed.stderr)
            self.assertNotIn("[probe]", resumed.stdout)
            self.assertIn("[reuse]", resumed.stdout)
            self.assertEqual(json.loads((output / "report.json").read_text())["jobs"], 4)
            for path, (digest, timestamp) in before.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, str(path))
                self.assertEqual(path.stat().st_mtime_ns, timestamp, str(path))
            self.assertFalse((project / "target").exists())

    def test_level_zero_retains_build_assets_absolute_source_aliases_and_cargo_features(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.fixture(root, 2)
            (project / "Cargo.toml").write_text(
                '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n'
                '[features]\ndefault=["enabled"]\nenabled=[]\n'
            )
            (project / "build.rs").write_text(
                'fn main() {\n'
                '    let manifest = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());\n'
                '    std::os::unix::fs::symlink(manifest.join("src/helpers"), manifest.join("src/alias")).unwrap();\n'
                '    std::fs::write(std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("asset.txt"), "asset").unwrap();\n'
                '}\n'
            )
            (project / "src/lib.rs").write_text(
                'mod selected;\n#[path="alias/helper.rs"] mod helper;\nmod leaf;\nmod unrelated;\n'
            )
            (project / "src/helpers").mkdir()
            (project / "src/helpers/helper.rs").write_text("pub fn helper_value() -> usize { crate::leaf::leaf_value() }\n")
            (project / "src/leaf.rs").write_text(
                'pub fn leaf_value() -> usize { let _ = std::time::Instant::now(); '
                'let _ = include_str!(concat!(env!("OUT_DIR"), "/asset.txt")); 1 }\n'
            )
            (project / "src/unrelated.rs").write_text("pub struct Unrelated; unsafe impl Send for Unrelated {}\n")
            for path in (project / "src/selected").glob("f*.rs"):
                path.write_text('#[cfg(feature="enabled")] pub fn run() -> usize { crate::helper::helper_value() }\n')
            reports = []
            for jobs in (1, 2):
                output = root / f"jobs-{jobs}"
                process = subprocess.run(
                    [str(ROOT / "verus-gap"), str(project), "--file", "src/selected/f*.rs",
                     "--scan-level", "0", "--jobs", str(jobs), "--out", str(output)],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
                report = json.loads((output / "report.json").read_text())
                manifest = json.loads((output / "project.json").read_text())
                self.assertIn('feature="enabled"', manifest["rust_cfg"])
                self.assertEqual(report["coverage"]["expanded_context_files"], 2)
                self.assertNotIn("language_rejection", report["counts"])
                self.assertGreater(report["counts"].get("library_specification", 0), 0)
                self.assertTrue(report["workspace_restored"])
                self.assertTrue(all("src/unrelated.rs" not in shard["scope"] for shard in report["shards"]))
                reports.append(report)
            self.assertEqual(reports[0]["counts"], reports[1]["counts"])
            self.assertEqual(reports[0]["coverage"], reports[1]["coverage"])
            self.assertEqual(self.identities(reports[0]), self.identities(reports[1]))
            for serial, parallel in zip(reports[0]["shards"], reports[1]["shards"]):
                self.assertEqual(serial["scope"], parallel["scope"])
                self.assertEqual(serial["events"], parallel["events"])
                self.assertEqual(serial["stop_reason"], parallel["stop_reason"])
                a = json.loads((root / "jobs-1" / serial["directory"] / "report.json").read_text())
                b = json.loads((root / "jobs-2" / parallel["directory"] / "report.json").read_text())
                self.assertEqual(
                    [{key: value for key, value in round.items() if key != "seconds"} for round in a["rounds"]],
                    [{key: value for key, value in round.items() if key != "seconds"} for round in b["rounds"]],
                )
            self.assertFalse((project / "src/alias").exists())

    def test_parallel_workers_share_finalized_ghost_dependency_artifacts(self):
        from verus_gap.gap import find_verus, identity
        from verus_gap.target import official_sources
        import tomllib

        sources = official_sources(identity(find_verus(None))["commit"])
        version = tomllib.loads((sources / "source/vstd/Cargo.toml").read_text())["package"]["version"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.fixture(root, 2)
            (project / "Cargo.toml").write_text(
                '[workspace]\nmembers=["context"]\n[package]\nname="fixture"\n'
                'version="0.1.0"\nedition="2021"\n[dependencies]\ncontext={path="context"}\n'
            )
            (project / "context/src").mkdir(parents=True)
            (project / "context/Cargo.toml").write_text(
                '[package]\nname="context"\nversion="0.1.0"\nedition="2021"\n'
                '[package.metadata.verus]\nverify=true\n[dependencies]\n'
                f'vstd="={version}"\n'
            )
            (project / "context/src/lib.rs").write_text(
                'use vstd::prelude::*;\nverus! {\n'
                'pub struct Data { pub value: u32 }\n'
                'pub fn make(value: u32) -> (result: Data) ensures result.value == value { Data { value } }\n}\n'
            )
            for path in (project / "src/selected").glob("f*.rs"):
                path.write_text("pub fn value() -> u32 { context::make(7).value }\n")
            output = root / "results"
            process = subprocess.run(
                [str(ROOT / "verus-gap"), str(project), "--file", "src/selected/f*.rs",
                 "--jobs", "2", "--out", str(output)],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            manifest = json.loads((output / "project.json").read_text())
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(manifest["support_mode"], "cargo_verus_ghost_dependencies")
            self.assertTrue(manifest["dependency_setup"]["captured_root_without_execution"])
            self.assertEqual(manifest["dependency_setup"]["probe_emit"], "metadata")
            self.assertEqual(report["coverage"]["probed_files"], 2)
            self.assertTrue(report["workspace_restored"])
            replay = json.loads((output / "replay.json").read_text())
            parent_dependencies = [value for value in replay["command"] if value.startswith("context=")]
            self.assertTrue(parent_dependencies)
            workers = list((output / "workers").glob("*/worker-*"))
            self.assertEqual(len(workers), 2)
            for worker in workers:
                captured = json.loads((worker / "replay.json").read_text())
                self.assertEqual(
                    [value for value in captured["command"] if value.startswith("context=")], parent_dependencies,
                )
            for name, digest in manifest["dependency_artifacts_sha256"].items():
                self.assertEqual(hashlib.sha256(Path(name).read_bytes()).hexdigest(), digest)

    def test_ctrl_c_restores_all_workers_and_report_refresh_does_not_need_worker_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.fixture(root, 4)
            output = root / "results"
            with subprocess.Popen(
                [str(ROOT / "verus-gap"), str(project), "--file", "src/selected/f*.rs",
                 "--jobs", "2", "--verbose", "--out", str(output)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            ) as process:
                prefix = []
                children = []
                try:
                    self.assertIsNotNone(process.stdout)
                    files = set()
                    for line in process.stdout:
                        prefix.append(line)
                        if "[probe]" in line:
                            files.add(line.split("[probe] ", 1)[1].split(" round ", 1)[0])
                        if len(files) == 2:
                            if Path("/proc").is_dir():
                                deadline = time.monotonic() + 5
                                while time.monotonic() < deadline:
                                    children = []
                                    manifest = json.loads((output / "project.json").read_text())
                                    for worker in manifest["parallel_runs"][-1]["worker_pids"]:
                                        for child_index in Path(f"/proc/{worker}/task").glob("*/children"):
                                            try:
                                                children.extend(int(pid) for pid in child_index.read_text().split())
                                            except FileNotFoundError:
                                                continue
                                    if len(children) >= 2:
                                        break
                                    time.sleep(0.01)
                                self.assertGreaterEqual(len(children), 2, "".join(prefix))
                            process.send_signal(signal.SIGINT)
                            break
                    else:
                        self.fail("Two active file probes were not observed")
                    remaining, _ = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 2, "".join(prefix) + remaining)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            for pid in children:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["scan_progress"]["state"], "interrupted")
            self.assertEqual(len(report["scan_progress"]["active_files"]), 2)
            self.assertTrue(report["workspace_restored"])
            manifest = json.loads((output / "project.json").read_text())
            for workspace in [output / "workspace", *list((output / "workers").glob("*/worker-*/workspace"))]:
                for name, identity in manifest["active_sources"].items():
                    self.assertEqual(hashlib.sha256((workspace / name).read_bytes()).hexdigest(), identity["sha256"])
            (output / "workspace").rename(output / "unused-workspace")
            (output / "workers").rename(output / "unused-workers")
            refreshed = subprocess.run(
                [sys.executable, str(ROOT / "verus-gap"), "report", str(output)],
                env={**os.environ, "PATH": str(root / "no-tools")},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
            current = json.loads((output / "report.json").read_text())
            self.assertEqual(current["groups"], report["groups"])
            self.assertEqual(current["scan_progress"]["active_files"], report["scan_progress"]["active_files"])
            (output / "unused-workspace").rename(output / "workspace")
            resumed = subprocess.run(
                [str(ROOT / "verus-gap"), "--resume", str(output), "--jobs", "4"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(resumed.returncode, 1, resumed.stdout + resumed.stderr)
            final = json.loads((output / "report.json").read_text())
            self.assertEqual(final["coverage"]["pending_files"], 0)
            self.assertEqual(final["counts"]["library_specification"], 2)
            self.assertEqual(final["scan_progress"]["state"], "finished")


if __name__ == "__main__":
    unittest.main()
