"""Pooled file frontiers with private writable source copies."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import signal
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, current_thread, local, main_thread

from .engine import probe
from .gap import GapError
from .project import Project, save_replay
from .source import Cfg, Source


@dataclass(frozen=True)
class Work:
    number: int
    focus: str
    directory: Path


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


class Workers:
    def __init__(self, project: Project, jobs: int, level: int):
        self.parent = project
        self.level = level
        self.cancel = Event()
        self.events: Queue[tuple[str, dict]] = Queue()
        self.local = local()
        self.lock = Lock()
        self.projects: list[Project] = []
        self.next_worker = 0
        self.root = project.output / "workers" / uuid.uuid4().hex[:12]
        self.record = {
            "directory": str(self.root.relative_to(project.output)), "jobs": jobs,
            "workspace_restored": False,
        }
        project.manifest.setdefault("parallel_runs", []).append(self.record)
        project.manifest["workers_restored"] = False
        project.save()
        self.manifest = copy.deepcopy(project.manifest)

    def context(self) -> Project:
        existing = getattr(self.local, "project", None)
        if existing is not None:
            return existing
        with self.lock:
            number = self.next_worker
            self.next_worker += 1
        output = self.root / f"worker-{number:03d}"
        workspace = output / "workspace"

        def copying(source: str, destination: str):
            if self.cancel.is_set():
                raise KeyboardInterrupt
            return shutil.copy2(source, destination)

        shutil.copytree(self.parent.workspace, workspace, symlinks=True, copy_function=copying)
        for current, directories, files in os.walk(workspace, followlinks=False):
            if self.cancel.is_set():
                raise KeyboardInterrupt
            for name in [*directories, *files]:
                path = Path(current) / name
                if not path.is_symlink():
                    continue
                target = (self.parent.workspace / path.relative_to(workspace)).resolve()
                copied_target = workspace / target.relative_to(self.parent.workspace) if (
                    target.is_relative_to(self.parent.workspace)
                ) else target
                path.unlink()
                path.symlink_to(os.path.relpath(copied_target, path.parent), target_is_directory=target.is_dir())
        prefix = re.compile(re.escape(str(self.parent.workspace)) + r"(?=/|$)")

        def relocate(value: str) -> str:
            return prefix.sub(lambda _: str(workspace), value)

        cfg = Cfg([*self.manifest["rust_cfg"], "verus_keep_ghost", "verus_keep_ghost_body", "verus_only"])
        env = {key: relocate(value) for key, value in self.parent.env.items()}
        if self.manifest.get("support_mode") == "cargo_verus_ghost_dependencies":
            rebind_driver_environment(self.parent.env, env)
        sources = {}
        for name, source in self.parent.sources.items():
            if self.cancel.is_set():
                raise KeyboardInterrupt
            sources[name] = Source(name, source.data, cfg)
        project = Project(
            output=output, workspace=workspace, cwd=Path(relocate(str(self.parent.cwd))),
            env=env,
            command=[relocate(value) for value in self.parent.command],
            sources=sources, manifest=copy.deepcopy(self.manifest),
            deadline=self.parent.deadline, cancel_event=self.cancel,
        )
        project.manifest["worker_workspace"] = str(workspace.relative_to(self.parent.output))
        with self.lock:
            self.projects.append(project)
        project.verify_originals()
        save_replay(project)
        self.local.project = project
        return project

    def execute(self, work: Work) -> dict:
        if self.cancel.is_set():
            raise KeyboardInterrupt
        project = self.context()

        def progress(phase: str, details: dict):
            self.events.put((phase, copy.deepcopy(details)))

        return probe(project, work.focus, work.directory, scan_level=self.level, progress=progress)

    def drain(self, progress: Callable[[str, dict], None]):
        while True:
            try:
                phase, details = self.events.get_nowait()
            except Empty:
                return
            progress(phase, details)

    def verify(self):
        self.parent.manifest["workers_restored"] = False
        self.record["created_workers"] = len(self.projects)
        try:
            for project in self.projects:
                project.verify_originals()
            self.parent.manifest["workers_restored"] = True
            self.record["workspace_restored"] = True
        finally:
            self.parent.save()


def scan(project: Project, work: list[Work], jobs: int, level: int,
         start: Callable[[Work], None], progress: Callable[[str, dict], None]) -> Iterator[tuple[Work, dict]]:
    if not work or time.monotonic() >= project.deadline:
        return
    count = min(jobs, len(work))
    workers = Workers(project, count, level)
    executor = ThreadPoolExecutor(max_workers=count, thread_name_prefix="verus-gap")
    futures: dict[Future[dict], Work] = {}
    remaining = iter(work)

    def submit():
        if time.monotonic() >= project.deadline:
            return
        item = next(remaining, None)
        if item is not None:
            start(item)
            futures[executor.submit(workers.execute, item)] = item

    try:
        for _ in range(count):
            submit()
        while futures:
            workers.drain(progress)
            completed = [future for future in futures if future.done()]
            if not completed:
                try:
                    phase, details = workers.events.get(timeout=0.1)
                except Empty:
                    continue
                progress(phase, details)
                continue
            workers.drain(progress)
            for future in sorted(completed, key=lambda item: futures[item].number):
                item = futures.pop(future)
                try:
                    result = future.result()
                except Exception as error:
                    item.directory.mkdir(parents=True, exist_ok=True)
                    log = item.directory / "worker-error.txt"
                    log.write_text(traceback.format_exc())
                    raise GapError(f"Worker failed for {item.focus}: {error}; see {log}.") from error
                yield item, result
                submit()
    finally:
        workers.cancel.set()
        for future in futures:
            future.cancel()
        previous_handler = signal.getsignal(signal.SIGINT) if current_thread() is main_thread() else None
        if previous_handler is not None:
            signal.signal(signal.SIGINT, lambda signum, frame: workers.cancel.set())
        try:
            executor.shutdown(wait=True, cancel_futures=True)
            workers.verify()
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
