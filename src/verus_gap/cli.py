"""The default command performs an adaptive inventory, not one compiler pass."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .engine import canonical, probe
from .gap import GapError
from .project import prepare, resume, scan_level_from, write_json
from .report import public_api, save_report


class VerboseProgress:
    def __init__(self, selected: list[str], fast: bool):
        self.selected = set(selected)
        self.fast = fast
        self.groups: set[tuple] = set()
        self.selected_groups: set[tuple] = set()
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
            api = public_api(record)
            key = (record["category"], canonical(record["message"]), api)
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
                self.write(
                    f"[{label}][{category}][{scope}] "
                    f"{place['file']}:{place['line']}:{place['column']}: {record['message']}"
                )
                if api:
                    self.write(f"  API: {api}")

    def __call__(self, phase: str, details: dict):
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


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    options = values[:values.index("--")] if "--" in values else values
    if "--frontier" in options:
        if any(option == "--scan-level" or option.startswith("--scan-level=") for option in options):
            print("error: --scan-level is an adaptive scan option; do not combine it with --frontier.", file=sys.stderr)
            return 2
        if "--fast" in options:
            print("error: --fast is an adaptive scan option; do not combine it with --frontier.", file=sys.stderr)
            return 2
        if "--verbose" in options:
            print("error: --verbose is an adaptive scan option; do not combine it with --frontier.", file=sys.stderr)
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
    )
    parser.add_argument("path", nargs="?", default=".", help="crate directory, Cargo.toml, or .rs file")
    parser.add_argument("--features", help="Cargo features (full is automatically used when the crate defines it)")
    parser.add_argument("--package", help="crate name when the workspace is ambiguous")
    parser.add_argument("--bin", help="binary target when there is no unique library")
    parser.add_argument("--file", action="append", default=[], help="file or glob relative to workspace or crate")
    parser.add_argument("--scan-level", type=int, choices=(0, 1, 2),
                        help="0: expand dependencies (default); 1: selected source with continued isolation; "
                             "2: selected source, stop when only checking boundaries remain; resume inherits its level")
    parser.add_argument("--fast", action="store_true",
                        help="alias for --scan-level 2")
    parser.add_argument("--verbose", action="store_true",
                        help="show live round progress and newly confirmed limitation groups")
    parser.add_argument("--out", help="new result directory")
    parser.add_argument("--resume", help="continue a result directory without rebuilding dependencies")
    parser.add_argument("--verus", help="existing complete Verus distribution")
    parser.add_argument("--timeout", type=float, default=3600, help="total build/scan budget in seconds")
    args = parser.parse_args(values)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.fast and args.scan_level not in (None, 2):
        parser.error("--fast is --scan-level 2; do not combine it with a different --scan-level")
    requested_level = 2 if args.fast else args.scan_level
    if args.features:
        cargo.extend(["--features", args.features])
    if args.path == "demo":
        args.path = str(Path(__file__).resolve().parent / "examples" / "gaps.rs")
        args.standalone = True
    if args.resume and args.out:
        parser.error("--resume reuses its result directory; do not also pass --out")
    if args.resume and (args.features or cargo or args.package or args.bin or args.verus):
        parser.error("A resumed scan keeps its original compiler/build context; start a new scan to change it.")
    output = Path(args.resume or args.out).expanduser().resolve() if args.resume or args.out else (
        Path.cwd() / "verus-gap-results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    )
    if not args.resume and output.exists() and any(output.iterdir()):
        print(f"error: Output directory is not empty: {output}", file=sys.stderr)
        return 2
    project = None
    try:
        level = 0 if requested_level is None else requested_level
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
        progress = VerboseProgress(selected, fast=level > 0) if args.verbose else None
        if progress is not None:
            progress.write(f"[scan] level={level}; mode={mode}; {len(selected)} selected file(s); reports: {output}")
        shards = []
        revision = hashlib.sha256(json.dumps(project.manifest["tool_sha256"], sort_keys=True).encode()).hexdigest()[:12]
        for index, name in enumerate(selected):
            if time.monotonic() >= project.deadline:
                break
            if progress is not None:
                progress.write(f"[file {index + 1}/{len(selected)}] {name}")
            base = output / "shards" / hashlib.sha256(name.encode()).hexdigest()[:12]
            directory = base if not base.exists() else base / revision
            saved = project.manifest.get("shard_index", {}).get(name)
            cached_path = output / saved if saved else base
            if args.resume and (cached_path / "report.json").is_file():
                cached = json.loads((cached_path / "report.json").read_text())
                same_scan = (
                    cached.get("scanner_revision") == revision
                    and scan_level_from(cached) == level
                )
                if same_scan and (
                    cached["status"] != "partial"
                    or level == 2 and cached["stop_reason"] == "checking_boundary_only"
                ):
                    result, directory = cached, cached_path
                    if progress is not None:
                        progress("restore", {"observations": cached["observations"]})
                        progress.write(f"[reuse] {name}; using saved frontier without new probes")
                elif same_scan:
                    directory = cached_path
                    result = probe(project, name, directory, scan_level=level, progress=progress)
                else:
                    directory = base / revision
                    result = probe(project, name, directory, scan_level=level, progress=progress)
            else:
                result = probe(project, name, directory, scan_level=level, progress=progress)
            result["scanner_revision"] = revision
            result["directory"] = str(directory.relative_to(output))
            write_json(directory / "report.json", result)
            project.manifest.setdefault("shard_index", {})[name] = result["directory"]
            project.save()
            shards.append(result)
            report = save_report(project, shards, selected)
            accepted = sum(report["counts"].get(key, 0)
                           for key in ("language_rejection", "library_specification"))
            print(f"[{index + 1}/{len(selected)}] {name}: {result['stop_reason']}; "
                  f"{accepted} reproducible support-gap groups so far.", flush=True)
        project.verify_originals()
        report = save_report(project, shards, selected)
        print(f"\n{report['status']}: {output / 'report.md'}", flush=True)
        if report["coverage"]["pending_files"]:
            return 2
        if any(report["counts"].get(category, 0)
               for category in ("language_rejection", "library_specification")):
            return 1
        return 2 if report["coverage"]["partial_files"] else 0
    except (GapError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.md").write_text(
            "# Verus compatibility inventory\n\n"
            "**Blocked before a reliable inventory could be produced.**\n\n"
            + str(error) + "\n\nSee `project.json`, `baseline.stdout`, and `baseline.stderr` if present.\n"
        )
        return 2
    except KeyboardInterrupt:
        print(f"Interrupted; completed evidence is retained in {output}. Resume with --resume.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
