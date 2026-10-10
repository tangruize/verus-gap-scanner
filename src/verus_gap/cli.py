"""The default command performs an adaptive inventory, not one compiler pass."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .engine import probe
from .gap import GapError
from .project import prepare, resume, write_json
from .report import save_report


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if "--frontier" in values:
        if "--fast" in values:
            print("error: --fast is an adaptive scan mode; do not combine it with --frontier.", file=sys.stderr)
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
    parser.add_argument("--fast", action="store_true",
                        help="explore and report selected source only, without recursive checking expansion")
    parser.add_argument("--out", help="new result directory")
    parser.add_argument("--resume", help="continue a result directory without rebuilding dependencies")
    parser.add_argument("--verus", help="existing complete Verus distribution")
    parser.add_argument("--timeout", type=float, default=3600, help="total build/scan budget in seconds")
    args = parser.parse_args(values)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
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
        if args.resume:
            previous = json.loads((output / "project.json").read_text())
            previous_mode = previous.get("scan_mode", "adaptive")
            if previous_mode not in {"adaptive", "fast"}:
                print(f"error: Unknown saved scan mode: {previous_mode}", file=sys.stderr)
                return 2
            if args.fast and previous_mode != "fast":
                print("error: A resumed scan keeps its original mode; start a new --fast scan.", file=sys.stderr)
                return 2
        project = resume(output, args.timeout) if args.resume else prepare(args, cargo, output)
        mode = project.manifest.get("scan_mode", "adaptive") if args.resume else (
            "fast" if args.fast else "adaptive"
        )
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
        shards = []
        revision = hashlib.sha256(json.dumps(project.manifest["tool_sha256"], sort_keys=True).encode()).hexdigest()[:12]
        for index, name in enumerate(selected):
            if time.monotonic() >= project.deadline:
                break
            base = output / "shards" / hashlib.sha256(name.encode()).hexdigest()[:12]
            directory = base if not base.exists() else base / revision
            saved = project.manifest.get("shard_index", {}).get(name)
            cached_path = output / saved if saved else base
            if args.resume and (cached_path / "report.json").is_file():
                cached = json.loads((cached_path / "report.json").read_text())
                same_scan = (
                    cached.get("scanner_revision") == revision
                    and cached.get("scan_mode", "adaptive") == mode
                )
                if same_scan and cached["status"] != "partial":
                    result, directory = cached, cached_path
                elif same_scan:
                    directory = cached_path
                    result = probe(project, name, directory, expand_scope=mode != "fast")
                else:
                    directory = base / revision
                    result = probe(project, name, directory, expand_scope=mode != "fast")
            else:
                result = probe(project, name, directory, expand_scope=mode != "fast")
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
