"""Pooled file frontiers with private writable source copies."""

from __future__ import annotations

import copy
import hashlib
import multiprocessing
import os
import pickle
import re
import shutil
import signal
import sys
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from multiprocessing.connection import Connection, wait
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from threading import current_thread, main_thread

from .engine import probe
from .gap import GapError
from .project import Project, save_replay
from .source import Source


@dataclass(frozen=True)
class Work:
    number: int
    focus: str
    directory: Path


@dataclass(frozen=True)
class Prepared:
    root: Path
    output: Path
    workspace: Path
    cwd: Path
    env: dict[str, str]
    command: list[str]
    sources: dict[str, Source]
    manifest: dict
    deadline: float
    jobs: int
    level: int


@dataclass
class Slot:
    number: int
    process: BaseProcess
    connection: Connection
    work: Work | None = None
    readable: bool = True


def rebind_driver_environment(original: dict[str, str], copied: dict[str, str]):
    required = ("CARGO_PKG_NAME", "CARGO_PKG_VERSION", "CARGO_MANIFEST_DIR")
    if not all(isinstance(original.get(key), str) and isinstance(copied.get(key), str) for key in required):
        raise GapError("Captured Cargo-Verus context is missing its package/manifest identity.")

    def package_id(environment: dict[str, str]) -> str:
        manifest = environment["CARGO_MANIFEST_DIR"] + os.sep + "Cargo.toml"
        digest = hashlib.sha256(manifest.encode()).hexdigest()[:12]
        return f"{environment['CARGO_PKG_NAME']}-{environment['CARGO_PKG_VERSION']}-{digest}"

    previous, current = package_id(original), package_id(copied)
    if original.get("__VERUS_DRIVER_VERIFY_" + previous) != "1":
        raise GapError("Captured Cargo-Verus root dispatch identity is not recognized; refusing an unchecked worker replay.")
    # Cargo-Verus hashes the manifest path into its root dispatch keys.
    for prefix in (
        "__VERUS_DRIVER_VERIFY_", " __VERUS_DRIVER_ARGS_FOR_",
        " __VERUS_DRIVER_IS_BUILTIN_", " __VERUS_DRIVER_IS_BUILTIN_MACROS_",
    ):
        key = prefix + previous
        if key in copied:
            copied[prefix + current] = copied.pop(key)


class Worker:
    def __init__(self, prepared: Prepared, number: int, cancel: Event, connection: Connection,
                 started: float):
        self.prepared = prepared
        self.number = number
        self.cancel = cancel
        self.connection = connection
        self.started = started
        self.project: Project | None = None

    def progress(self, phase: str, details: dict):
        if self.cancel.is_set():
            raise KeyboardInterrupt
        self.connection.send(("progress", (phase, details)))

    def initializing(self, stage: str, **details):
        self.progress("worker", {
            "worker": self.number + 1, "jobs": self.prepared.jobs,
            "pid": os.getpid(), "stage": stage, **details,
        })

    def context(self) -> Project:
        if self.project is not None:
            return self.project
        prepared = self.prepared
        output = prepared.root / f"worker-{self.number:03d}"
        workspace = output / "workspace"
        copied = 0
        last_notice = time.monotonic()
        self.initializing("copy", completed=copied)

        def copying(source: str, destination: str):
            nonlocal copied, last_notice
            if self.cancel.is_set():
                raise KeyboardInterrupt
            result = shutil.copy2(source, destination)
            copied += 1
            if time.monotonic() - last_notice >= 2:
                self.initializing("copy", completed=copied)
                last_notice = time.monotonic()
            return result

        shutil.copytree(prepared.workspace, workspace, symlinks=True, copy_function=copying)
        for current, directories, files in os.walk(workspace, followlinks=False):
            if self.cancel.is_set():
                raise KeyboardInterrupt
            for name in [*directories, *files]:
                path = Path(current) / name
                if not path.is_symlink():
                    continue
                target = (prepared.workspace / path.relative_to(workspace)).resolve()
                copied_target = workspace / target.relative_to(prepared.workspace) if (
                    target.is_relative_to(prepared.workspace)
                ) else target
                path.unlink()
                path.symlink_to(os.path.relpath(copied_target, path.parent), target_is_directory=target.is_dir())
        prefix = re.compile(re.escape(str(prepared.workspace)) + r"(?=/|$)")

        def relocate(value: str) -> str:
            return prefix.sub(lambda _: str(workspace), value)

        env = {key: relocate(value) for key, value in prepared.env.items()}
        if prepared.manifest.get("support_mode") == "cargo_verus_ghost_dependencies":
            rebind_driver_environment(prepared.env, env)
        project = Project(
            output=output, workspace=workspace, cwd=Path(relocate(str(prepared.cwd))),
            env=env,
            command=[relocate(value) for value in prepared.command],
            sources=prepared.sources, manifest=prepared.manifest,
            deadline=prepared.deadline, cancel_event=self.cancel,
        )
        project.manifest["worker_workspace"] = str(workspace.relative_to(prepared.output))
        project.verify_originals()
        save_replay(project)
        self.project = project
        self.initializing(
            "ready", seconds=round(time.monotonic() - self.started, 3),
            sources=len(prepared.sources), copied_files=copied,
        )
        return project

    def execute(self, work: Work) -> dict:
        if self.cancel.is_set():
            raise KeyboardInterrupt
        project = self.context()

        return probe(project, work.focus, work.directory, scan_level=self.prepared.level, progress=self.progress)


def worker_main(cache: Path, number: int, jobs: int, cancel: Event, connection: Connection):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    started = time.monotonic()
    try:
        connection.send(("progress", ("worker", {
            "worker": number + 1, "jobs": jobs, "pid": os.getpid(), "stage": "models",
        })))
        with cache.open("rb") as stream:
            prepared = pickle.load(stream)
        worker = Worker(prepared, number, cancel, connection, started)
        while not cancel.is_set():
            if not connection.poll(0.1):
                continue
            work = connection.recv()
            if work is None:
                return
            connection.send(("result", worker.execute(work)))
    except KeyboardInterrupt:
        connection.send(("interrupted", {}))
    except Exception as error:
        connection.send(("error", {"message": str(error), "traceback": traceback.format_exc()}))
    finally:
        connection.close()


class Workers:
    def __init__(self, project: Project, jobs: int, level: int):
        self.parent = project
        self.context = multiprocessing.get_context("spawn")
        self.cancel = self.context.Event()
        self.slots: list[Slot] = []
        self.root = project.output / "workers" / uuid.uuid4().hex[:12]
        self.record = {
            "directory": str(self.root.relative_to(project.output)), "jobs": jobs,
            "model": "process", "start_method": "spawn", "workspace_restored": False,
            "cached_sources": len(project.sources), "worker_pids": [],
            "scheduling": "largest_source_first",
        }
        project.manifest.setdefault("parallel_runs", []).append(self.record)
        project.manifest["workers_restored"] = False
        project.save()
        prepared = Prepared(
            root=self.root, output=project.output, workspace=project.workspace, cwd=project.cwd,
            env=dict(project.env), command=list(project.command), sources=project.sources,
            manifest=copy.deepcopy(project.manifest), deadline=project.deadline, jobs=jobs, level=level,
        )
        self.root.mkdir(parents=True)
        self.cache = self.root / "prepared.pickle"
        self.cache.write_bytes(pickle.dumps(prepared, protocol=pickle.HIGHEST_PROTOCOL))

    def launch(self, number: int) -> Slot:
        parent, child = self.context.Pipe()
        process = self.context.Process(
            target=worker_main, name=f"verus-gap-worker-{number:03d}",
            args=(self.cache, number, self.record["jobs"], self.cancel, child),
        )
        try:
            process.start()
        except (OSError, RuntimeError):
            parent.close()
            raise
        finally:
            child.close()
        slot = Slot(number, process, parent)
        self.slots.append(slot)
        self.record["worker_pids"].append(process.pid)
        return slot

    def failure(self, slot: Slot, message: str, details: str) -> GapError:
        directory = slot.work.directory if slot.work is not None else self.root / f"worker-{slot.number:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "worker-error.txt"
        log.write_text(details)
        subject = slot.work.focus if slot.work is not None else f"process {slot.process.pid}"
        return GapError(f"Worker failed for {subject}: {message}; see {log}.")

    def receive(self, slot: Slot) -> tuple:
        try:
            return slot.connection.recv()
        except (EOFError, OSError) as error:
            slot.readable = False
            slot.process.join(timeout=0.1)
            message = f"process {slot.process.pid} exited unexpectedly (exit code {slot.process.exitcode})"
            raise self.failure(slot, message, message + "\n" + traceback.format_exc()) from error

    def verify(self):
        self.parent.manifest["workers_restored"] = False
        self.record["created_workers"] = 0
        try:
            for output in sorted(self.root.glob("worker-*")):
                workspace = output / "workspace"
                if not workspace.is_dir():
                    continue
                initialized = (output / "project.json").is_file()
                self.record["created_workers"] += int(initialized)
                for name, source in self.parent.sources.items():
                    copied = workspace / name
                    if not initialized and not copied.exists() and not copied.is_symlink():
                        continue
                    path = copied.resolve()
                    if not path.is_relative_to(workspace):
                        raise GapError(f"Active source leaves the private scan workspace: {name}")
                    if path.read_bytes() != source.data:
                        raise GapError(f"Worker scan copy was not restored: {output.name}/{name}")
            self.parent.manifest["workers_restored"] = True
            self.record["workspace_restored"] = True
        finally:
            self.parent.save()

    def shutdown(self):
        self.cancel.set()
        try:
            # Drain messages during cancellation so large checkpoint sends cannot block exit.
            while any(slot.process.is_alive() for slot in self.slots):
                connections = [slot.connection for slot in self.slots if slot.readable]
                ready = wait(connections, timeout=0.1) if connections else []
                for slot in self.slots:
                    if slot.connection in ready:
                        try:
                            slot.connection.recv()
                        except (EOFError, OSError):
                            slot.readable = False
                    slot.process.join(timeout=0)
            self.verify()
        finally:
            self.record["worker_exit_codes"] = {
                str(slot.process.pid): slot.process.exitcode for slot in self.slots
            }
            self.parent.save()
            for slot in self.slots:
                slot.connection.close()
                slot.process.close()


def scan(project: Project, work: list[Work], jobs: int, level: int,
         start: Callable[[Work], None], progress: Callable[[str, dict], None]) -> Iterator[tuple[Work, dict]]:
    if not work or time.monotonic() >= project.deadline:
        return
    count = min(jobs, len(work))
    progress("pool", {"jobs": count, "stage": "starting", "model": "process"})
    workers = Workers(project, count, level)
    remaining = iter(sorted(
        work, key=lambda item: (-len(project.sources[item.focus].data), item.number),
    ))

    def submit(slot: Slot):
        if time.monotonic() >= project.deadline:
            return
        item = next(remaining, None)
        if item is not None:
            start(item)
            slot.work = item
            try:
                slot.connection.send(item)
            except OSError as error:
                raise workers.failure(slot, str(error), traceback.format_exc()) from error

    try:
        for number in range(count):
            if time.monotonic() >= project.deadline:
                break
            submit(workers.launch(number))
        while any(slot.work is not None for slot in workers.slots):
            ready = wait([
                handle for slot in workers.slots
                for handle in (slot.connection, slot.process.sentinel)
            ], timeout=0.1)
            for slot in sorted(
                workers.slots, key=lambda slot: slot.work.number if slot.work is not None else len(work),
            ):
                if slot.connection in ready:
                    kind, details = workers.receive(slot)
                    if kind == "progress":
                        progress(*details)
                    elif kind == "error":
                        raise workers.failure(slot, details["message"], details["traceback"])
                    elif kind == "interrupted":
                        raise KeyboardInterrupt
                    elif kind == "result" and slot.work is not None:
                        item, slot.work = slot.work, None
                        yield item, details
                        submit(slot)
                    else:
                        raise workers.failure(slot, f"Invalid worker message: {kind}", repr((kind, details)))
                elif slot.process.sentinel in ready:
                    message = f"process {slot.process.pid} exited unexpectedly (exit code {slot.process.exitcode})"
                    raise workers.failure(slot, message, message)
    finally:
        original_error = sys.exception()
        workers.cancel.set()
        previous_handler = signal.getsignal(signal.SIGINT) if current_thread() is main_thread() else None
        if previous_handler is not None:
            signal.signal(signal.SIGINT, lambda signum, frame: workers.cancel.set())
        try:
            try:
                workers.shutdown()
            except (GapError, OSError) as error:
                if original_error is not None:
                    raise GapError(f"{original_error}\nWorker cleanup failed: {error}") from original_error
                raise
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
