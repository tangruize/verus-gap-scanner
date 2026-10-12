"""The default command performs an adaptive inventory, not one compiler pass."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .engine import probe
from .gap import GapError
from .parallel import Work, scan as parallel_scan
from .project import DEFAULT_SCAN_LEVEL, Project, prepare, resume, scan_level_from, write_json
from .report import (
    atomic_text, group_key, main as report_main, mutable_static_name, report_lock, save_report,
)


class ReportProgress:
    def __init__(self, project: Project, selected: list[str], shards: list[dict],
                 verbose: VerboseProgress | None):
        self.project = project
        self.selected = selected
        self.shards = shards
        self.verbose = verbose
        self.counts = verbose or VerboseProgress(selected, fast=scan_level_from(project.manifest) > 0)
        self.directories: dict[str, Path] = {}
        self.active: dict[str, dict] = {}
        self.last_refresh = time.monotonic()
        self.report: dict | None = None
        self.published_groups: set[tuple] = set()

    def start(self, focus: str, directory: Path):
        self.directories[focus] = directory
        self.active.pop(focus, None)
        relative = str(directory.relative_to(self.project.output))
        self.project.manifest.setdefault("shard_index", {})[focus] = relative
        self.save_progress("running")

    def save_progress(self, state: str, error: str | None = None):
        names = [name for name in self.selected if name in self.directories]
        progress: dict = {"state": state}
        if len(names) == 1:
            progress.update(active_file=names[0], active_shard=str(
                self.directories[names[0]].relative_to(self.project.output),
            ))
        elif names:
            progress["active_files"] = names
        if error is not None:
            progress["error"] = error
        self.project.manifest["scan_progress"] = progress
        self.project.save()

    def refresh(self) -> dict:
        shards = list(self.shards)
        completed = {shard.get("focus") for shard in shards}
        shards.extend(shard for focus, shard in self.active.items() if focus not in completed)
        order = {name: index for index, name in enumerate(self.selected)}
        shards.sort(key=lambda shard: order.get(shard.get("focus"), len(order)))
        report = save_report(self.project, shards, self.selected)
        self.report = report
        self.published_groups = set(self.counts.groups)
        self.last_refresh = time.monotonic()
        return report

    def refresh_due(self) -> bool:
        return bool(self.counts.groups - self.published_groups) and (
            time.monotonic() - self.last_refresh >= 60
        )

    def finish_file(self, focus: str | None = None) -> dict:
        if self.shards:
            self.counts.observe(self.shards[-1].get("observations", []), announce=False)
            focus = focus or self.shards[-1].get("focus")
        if focus is None and len(self.directories) == 1:
            focus = next(iter(self.directories))
        if focus is not None:
            self.active.pop(focus, None)
            self.directories.pop(focus, None)
        self.save_progress("running")
        if self.report is None or self.refresh_due():
            return self.refresh()
        return self.report

    def finish(self, state: str) -> dict:
        self.save_progress(state)
        return self.refresh()

    def recover(self, state: str, error: str | None = None) -> dict:
        completed = {shard["focus"] for shard in self.shards}
        for focus, directory in list(self.directories.items()):
            if focus in completed:
                self.active.pop(focus, None)
                self.directories.pop(focus, None)
                continue
            checkpoint = directory / "report.json"
            if checkpoint.is_file():
                shard = dict(
                    json.loads(checkpoint.read_text()),
                    directory=str(directory.relative_to(self.project.output)),
                )
                self.counts.observe(shard.get("observations", []), announce=False)
                if shard["stop_reason"] and shard["stop_reason"] != "interrupted":
                    self.shards.append(shard)
                    self.active.pop(focus, None)
                    self.directories.pop(focus, None)
                else:
                    self.active[focus] = shard
        self.project.verify_originals()
        self.save_progress(state, error)
        return self.refresh()

    def interrupt(self) -> dict:
        return self.recover("interrupted")

    def __call__(self, phase: str, details: dict):
        focus = details.get("focus")
        if focus is None and len(self.directories) == 1:
            focus = next(iter(self.directories))
        if phase in {"restore", "round"} and focus in self.directories:
            self.active[focus] = dict(
                details["summary"], directory=str(self.directories[focus].relative_to(self.project.output)),
            )
        if self.verbose is not None:
            self.verbose(phase, details)
        elif phase in {"restore", "round"}:
            self.counts.observe(details.get("observations", []), announce=False)
        if phase == "round" and self.refresh_due():
            self.refresh()


class VerboseProgress:
    def __init__(self, selected: list[str], fast: bool):
        self.selected = set(selected)
        self.fast = fast
        self.groups: set[tuple] = set()
        self.selected_groups: set[tuple] = set()
        self.ready_workers: set[int] = set()
        self.started = time.monotonic()

    def write(self, message: str):
        print(f"[{time.monotonic() - self.started:.1f}s] {message}", flush=True)

    def observe(self, records: list[dict], announce: bool):
        for record in records:
            if not record["confirmed"] or record["category"] not in {
                "language_rejection", "library_specification",
            }:
                continue
            locations = [
                place for place in record["locations"]
                if not self.fast or place["file"] in self.selected
            ]
            if not locations:
                continue
            key = group_key(record)
            new_group = key not in self.groups
            self.groups.add(key)
            selected_locations = [place for place in locations if place["file"] in self.selected]
            new_selected = bool(selected_locations) and key not in self.selected_groups
            if selected_locations:
                self.selected_groups.add(key)
            if announce and (new_group or new_selected):
                place = (selected_locations or locations)[0]
                scope = "selected" if selected_locations else "dependency"
                category = "language" if record["category"] == "language_rejection" else "model"
                label = "limitation" if new_group else "selected-location"
                message = record["message"]
                name = mutable_static_name(message) if record["category"] == "language_rejection" else None
                if name is not None:
                    message = f"{key[1]}; global: {name}"
                self.write(
                    f"[{label}][{category}][{scope}] "
                    f"{place['file']}:{place['line']}:{place['column']}: {message}"
                )
                api = key[2]
                if api:
                    self.write(f"  API: {api}")

    def __call__(self, phase: str, details: dict):
        if phase == "pool":
            self.write(
                f"[pool] starting {details['jobs']} process workers; largest sources first; "
                "dispatched files are not completed files"
            )
            return
        if phase == "worker":
            label = f"[worker {details['worker']}/{details['jobs']} pid={details['pid']}]"
            if details["stage"] == "models":
                self.write(f"{label} loading cached source models; no reparsing")
            elif details["stage"] == "copy":
                self.write(f"{label} copying private workspace; {details['completed']} files copied")
            elif details["stage"] == "ready":
                self.ready_workers.add(details["worker"])
                self.write(
                    f"{label} ready in {details['seconds']:.2f}s; "
                    f"{len(self.ready_workers)}/{details['jobs']} workers ready; "
                    f"{details['sources']} cached source models reused"
                )
            else:
                raise ValueError(f"Unknown worker initialization stage: {details['stage']}")
            return
        if phase == "restore":
            self.observe(details["observations"], announce=False)
            return
        prefix = f"{details['focus']} round {details['round']:03d}"
        if phase == "probe":
            self.write(f"[probe] {prefix}; {len(details['scope'])} checked file(s)")
        elif phase == "confirmation":
            reason = "compiler failure" if details["compiler_failure"] else (
                f"{details['candidates']} new support observation(s), not yet confirmed"
            )
            self.write(f"[confirm] {prefix}; repeating the same overlay for {reason}")
        elif phase == "round":
            self.observe(details["observations"], announce=True)
            total = Counter(key[0] for key in self.groups)
            selected = Counter(key[0] for key in self.selected_groups)
            timing = f"probe {details['seconds']:.2f}s"
            timing += (
                f", confirmation {details['confirmation_seconds']:.2f}s"
                if details["confirmation_performed"] else ", confirmation not run"
            )
            counts = (
                f"confirmed groups: {total['language_rejection']} language / "
                f"{total['library_specification']} models"
            )
            if not self.fast:
                counts += (
                    f"; selected: {selected['language_rejection']} language / "
                    f"{selected['library_specification']} models"
                )
            blockers = f"; blockers: {', '.join(details['blockers'])}" if details["blockers"] else ""
            self.write(f"[progress] {prefix}; {timing}; {counts}{blockers}")
        elif phase == "action":
            actions = Counter(event["kind"] for event in details["events"])
            for event in details["events"]:
                if event["kind"] == "scope_expansion":
                    self.write(f"[scope] {prefix}; adding dependency context: {event['file']}")
            if any(kind != "scope_expansion" for kind in actions):
                self.write(
                    f"[isolate] {prefix}; {actions['external_body']} body / "
                    f"{actions['external_declaration']} declaration / "
                    f"{actions['ordinary_rust_context']} ordinary-context exclusion(s)"
                )
        else:
            raise ValueError(f"Unknown progress phase: {phase}")


def plan_frontier(project: Project, name: str, revision: str, level: int, resuming: bool) -> tuple[Path, dict | None]:
    base = project.output / "shards" / hashlib.sha256(name.encode()).hexdigest()[:12]
    directory = base if not base.exists() else base / revision
    saved = project.manifest.get("shard_index", {}).get(name)
    cached_path = project.output / saved if saved else base
    if resuming and (cached_path / "report.json").is_file():
        cached = json.loads((cached_path / "report.json").read_text())
        if cached.get("scanner_revision") == revision and scan_level_from(cached) == level:
            if cached["status"] != "partial" or level == 2 and cached["stop_reason"] == "checking_boundary_only":
                return cached_path, cached
            return cached_path, None
        directory = base / revision
    return directory, None


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values[:1] == ["report"]:
        return report_main(values[1:])
    options = values[:values.index("--")] if "--" in values else values
    if "--frontier" in options:
        if any(option == "--scan-level" or option.startswith("--scan-level=") for option in options):
            print("error: --scan-level is an adaptive scan option; do not combine it with --frontier.", file=sys.stderr)
            return 2
        if "--verbose" in options:
            print("error: --verbose is an adaptive scan option; do not combine it with --frontier.", file=sys.stderr)
            return 2
        if any(option == "--jobs" or option.startswith("--jobs=") for option in options):
            print("error: --jobs is an adaptive scan option; do not combine it with --frontier.", file=sys.stderr)
            return 2
        if any(
            option in {"--config", "--no-config"} or option.startswith("--config=")
            for option in options
        ):
            print("error: Build configurations are adaptive scan options; do not combine them with --frontier.", file=sys.stderr)
            return 2
        values.remove("--frontier")
        from .gap import main as frontier
        return frontier(values)
    cargo = []
    if "--" in values:
        index = values.index("--")
        values, cargo = values[:index], values[index + 1:]
    parser = argparse.ArgumentParser(
        description="Find reproducible Verus support gaps in an isolated Rust project copy.",
        epilog="Use 'verus-gap report RESULT_DIR' to refresh saved reports without scanning.",
    )
    parser.add_argument("path", nargs="?", default=".", help="crate directory, Cargo.toml, or .rs file")
    parser.add_argument("--features", help="Cargo features (otherwise keep Cargo or selected configuration defaults)")
    configuration = parser.add_mutually_exclusive_group()
    configuration.add_argument("--config", help="explicit TOML build configuration; overrides automatic selection")
    configuration.add_argument("--no-config", action="store_true",
                               help="disable project and bundled build configurations; keep Cargo defaults")
    parser.add_argument("--package", help="crate name when the workspace is ambiguous")
    parser.add_argument("--bin", help="binary target when there is no unique library")
    parser.add_argument("--input-kind", choices=("rust", "verus"),
                        help="rust: scan the native build (default); verus: use an existing .rs proof root and its annotations")
    parser.add_argument("--file", action="append", default=[], help="file or glob relative to workspace or crate")
    parser.add_argument("--scan-level", type=int, choices=(0, 1, 2),
                        help="0: expand dependencies; 1: selected source with continued isolation; "
                             "2: selected source, stop when only checking boundaries remain "
                             "(default for new scans); resume inherits its level")
    parser.add_argument("--verbose", action="store_true",
                        help="show live round progress and newly confirmed limitation groups")
    parser.add_argument("--jobs", type=int, default=1,
                        help="pooled file processes (default: 1); private sources and cached source models")
    parser.add_argument("--out", help="new result directory")
    parser.add_argument("--resume", help="continue a result directory without rebuilding dependencies")
    parser.add_argument("--verus", help="existing complete Verus distribution")
    parser.add_argument("--timeout", type=float, default=3600, help="total build/scan budget in seconds")
    args = parser.parse_args(values)
    if args.input_kind == "verus":
        if Path(args.path).suffix != ".rs":
            parser.error("--input-kind verus requires an explicit .rs proof root")
        if args.features or args.package or args.bin:
            parser.error("--input-kind verus accepts Rust compiler arguments after --, not Cargo selectors")
        args.standalone = True
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.jobs <= 0:
        parser.error("--jobs must be positive")
    requested_level = args.scan_level
    if args.features:
        cargo.extend(["--features", args.features])
    if args.path == "demo":
        args.path = str(Path(__file__).resolve().parent / "examples" / "gaps.rs")
        args.standalone = True
    if args.resume and args.out:
        parser.error("--resume reuses its result directory; do not also pass --out")
    if args.resume and (
        args.features or cargo or args.package or args.bin or args.verus or args.config or args.no_config
        or args.input_kind
    ):
        parser.error("A resumed scan keeps its original compiler/build context; start a new scan to change it.")
    output = Path(args.resume or args.out).expanduser().resolve() if args.resume or args.out else (
        Path.cwd() / "verus-gap-results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    )
    if not args.resume and output.exists() and any(output.iterdir()):
        print(f"error: Output directory is not empty: {output}", file=sys.stderr)
        return 2
    project = None
    reporting = None
    try:
        level = DEFAULT_SCAN_LEVEL if requested_level is None else requested_level
        if args.resume:
            previous = json.loads((output / "project.json").read_text())
            try:
                previous_level = scan_level_from(previous)
            except GapError as error:
                print(f"error: {error}", file=sys.stderr)
                return 2
            if requested_level is not None and requested_level != previous_level:
                print("error: A resumed scan keeps its original scan level; start a new scan to change it.", file=sys.stderr)
                return 2
            level = previous_level
        args.scan_level = level
        project = resume(output, args.timeout) if args.resume else prepare(args, cargo, output)
        mode = "adaptive" if level == 0 else "fast"
        project.manifest["scan_level"] = level
        project.manifest["scan_mode"] = mode
        project.manifest["jobs"] = args.jobs
        project.manifest.pop("workers_restored", None)
        selected = list(project.sources)
        if args.resume and not args.file and project.manifest.get("selected_files"):
            selected = project.manifest["selected_files"]
        patterns = args.file or project.manifest.get("default_files", [])
        if patterns:
            crate_root = str(Path(project.manifest["root_source"]).parent)
            selected = [
                name for name in selected if any(
                    fnmatch.fnmatchcase(name, pattern)
                    or fnmatch.fnmatchcase(name, crate_root + "/" + pattern)
                    for pattern in patterns
                )
            ]
            if not selected:
                raise GapError("No requested source file is active in this Cargo configuration.")
        project.manifest["selected_files"] = selected
        project.save()
        project.verify_originals()
        progress = VerboseProgress(selected, fast=level > 0) if args.verbose else None
        if progress is not None:
            progress.write(
                f"[scan] level={level}; mode={mode}; jobs={args.jobs}; "
                f"{len(selected)} selected file(s); reports: {output}"
            )
        shards = []
        reporting = ReportProgress(project, selected, shards, progress)
        project.manifest["scan_progress"] = {"state": "running"}
        project.save()
        reporting.refresh()
        revision = hashlib.sha256(json.dumps(project.manifest["tool_sha256"], sort_keys=True).encode()).hexdigest()[:12]
        dispatched = 0

        def start(item: Work):
            nonlocal dispatched
            dispatched += 1
            if progress is not None:
                label = f"dispatch {dispatched}/{len(pending)}" if args.jobs > 1 else (
                    f"file {item.number + 1}/{len(selected)}"
                )
                progress.write(f"[{label}] {item.focus}")
            reporting.start(item.focus, item.directory)

        def complete(item: Work, result: dict, *, cached: bool = False):
            result["focus"] = item.focus
            result["scanner_revision"] = revision
            result["directory"] = str(item.directory.relative_to(output))
            if not cached:
                write_json(item.directory / "report.json", result)
            project.manifest.setdefault("shard_index", {})[item.focus] = result["directory"]
            shards.append(result)
            reporting.finish_file(item.focus)
            accepted = len(reporting.counts.groups)
            print(f"[{len(shards)}/{len(selected)}] {item.focus}: {result['stop_reason']}; "
                  f"{accepted} reproducible support-gap groups so far.", flush=True)
            if result["stop_reason"] == "execution_failure":
                raise GapError(f"Execution failed for {item.focus}: {result.get('error', 'see its shard checkpoint')}.")

        pending = []
        for index, name in enumerate(selected):
            if time.monotonic() >= project.deadline:
                break
            directory, cached = plan_frontier(project, name, revision, level, bool(args.resume))
            item = Work(index, name, directory)
            if cached is not None:
                if progress is not None:
                    progress("restore", {"observations": cached["observations"]})
                    progress.write(f"[reuse] {name}; using saved frontier without new probes")
                complete(item, cached, cached=True)
            else:
                pending.append(item)
        if args.jobs == 1:
            for item in pending:
                if time.monotonic() >= project.deadline:
                    break
                start(item)
                complete(item, probe(project, item.focus, item.directory, scan_level=level, progress=reporting))
        else:
            with closing(parallel_scan(project, pending, args.jobs, level, start, reporting)) as results:
                for item, result in results:
                    complete(item, result)
        project.verify_originals()
        finished = len(shards) == len(selected) and not any(
            shard["stop_reason"] == "time_budget" for shard in shards
        )
        report = reporting.finish("finished" if finished else "stopped")
        print(f"\n{report['status']}: {output / 'report.md'}", flush=True)
        if not finished:
            return 2
        if any(report["counts"].get(category, 0)
               for category in ("language_rejection", "library_specification")):
            return 1
        return 2 if report["coverage"]["partial_files"] else 0
    except (GapError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        if reporting is not None:
            try:
                reporting.recover("stopped", str(error))
            except (GapError, OSError, UnicodeError, json.JSONDecodeError) as report_error:
                print(f"error: Could not refresh stopped reports: {report_error}", file=sys.stderr)
            return 2
        output.mkdir(parents=True, exist_ok=True)
        try:
            with report_lock(output):
                atomic_text(
                    output / "report.md",
                    "# Verus compatibility inventory\n\n"
                    "**Blocked before a reliable inventory could be produced.**\n\n"
                    + str(error) + "\n\nSee `project.json`, `baseline.stdout`, and `baseline.stderr` if present.\n",
                )
        except OSError as report_error:
            print(f"error: Could not write the blocked report: {report_error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if reporting is not None:
            try:
                reporting.interrupt()
            except (GapError, OSError, UnicodeError, json.JSONDecodeError) as error:
                print(f"error: Could not refresh interrupted reports: {error}", file=sys.stderr)
            else:
                print(f"Interrupted report: {output / 'report.md'}", file=sys.stderr)
        print(f"Interrupted; completed evidence is retained in {output}. Resume with --resume.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
