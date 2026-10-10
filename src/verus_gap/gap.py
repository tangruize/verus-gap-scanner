"""A wrapper-only Verus diagnostic scan. Python standard library only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
CHECKOUT = PACKAGE.parents[1]
HOME = CHECKOUT if (CHECKOUT / "pyproject.toml").is_file() else Path(
    os.environ.get("VERUS_GAP_HOME", str(
        Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "verus-gap"
    ))
).expanduser()
SKIP = {".git", "target", ".venv", "node_modules", "__pycache__", ".argus", ".autors"}
SUPPORT = {"vstd", "verus_builtin", "verus_builtin_macros", "verus_state_machines_macros"}


class GapError(Exception):
    pass


def capture_wrapper(output: Path) -> Path:
    wrapper = output / "capture-wrapper"
    wrapper.write_bytes((PACKAGE / "capture.py").read_bytes())
    wrapper.chmod(0o755)
    return wrapper


def run(command: list[str], cwd: Path, env: dict[str, str], timeout: float) -> dict:
    if timeout <= 0:
        raise GapError("Time budget exhausted before the next command.")
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError as error:
        raise GapError(f"Cannot run {command[0]}: {error}") from error

    def stop(signum: int):
        try:
            os.killpg(proc.pid, signum)
        except ProcessLookupError:
            pass

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop(signal.SIGKILL)
        stdout, stderr = proc.communicate()
    except KeyboardInterrupt:
        stop(signal.SIGTERM)
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            stop(signal.SIGKILL)
            proc.communicate()
        raise
    return {
        "command": command, "cwd": str(cwd), "exit_code": proc.returncode,
        "timed_out": timed_out, "seconds": round(time.monotonic() - start, 3),
        "stdout": stdout.decode("utf-8", "replace"),
        "stderr": stderr.decode("utf-8", "replace"),
    }


def identity(verus: Path) -> dict:
    result = run([str(verus), "--version", "--output-json"], PACKAGE, dict(os.environ), 30)
    if result["exit_code"]:
        raise GapError(
            "Verus cannot start. Run ./setup to install its matching Rust toolchain.\n"
            + result["stderr"] + result["stdout"]
        )
    try:
        version = json.loads(result["stdout"])["verus"]
        version["toolchain"] = version["toolchain"].split()[0]
        return version
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise GapError("Verus did not return a usable --version --output-json record.") from error


def find_verus(explicit: str | None) -> Path:
    selected = explicit or os.environ.get("VERUS_BIN")
    if not selected and (HOME / ".tools" / "installation.json").is_file():
        selected = json.loads((HOME / ".tools" / "installation.json").read_text())["verus"]
    selected = selected or shutil.which("verus")
    if not selected:
        raise GapError("Verus is not configured. Run ./setup once, then retry.")
    path = Path(selected).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise GapError(f"Verus executable does not exist or is not executable: {path}\nRun ./setup.")
    return path


def prefix_end(text: str) -> int:
    """Keep inner attributes/docs outside the macro, without parsing Rust items."""
    pos = 1 if text.startswith("\ufeff") else 0
    if text[pos:].startswith("#!") and not text[pos:].startswith("#!["):
        pos = text.find("\n", pos)
        if pos == -1:
            return len(text)
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
        elif text.startswith("//", pos):
            if text.startswith("///", pos) and not text.startswith("////", pos):
                break
            end = text.find("\n", pos)
            pos = len(text) if end == -1 else end + 1
        elif text.startswith("/*", pos):
            if text.startswith("/**", pos) and not text.startswith("/***", pos):
                break
            depth = 1
            pos += 2
            while depth and pos < len(text):
                if text.startswith("/*", pos):
                    depth += 1
                    pos += 2
                elif text.startswith("*/", pos):
                    depth -= 1
                    pos += 2
                else:
                    pos += 1
            if depth:
                raise GapError("Unterminated leading block comment.")
        elif text.startswith("#!", pos):
            bracket = pos + 2
            while bracket < len(text) and text[bracket].isspace():
                bracket += 1
            if bracket == len(text) or text[bracket] != "[":
                break
            pos = bracket + 1
            depth = 1
            while depth and pos < len(text):
                raw = re.match(r'r(#+)?"', text[pos:])
                if raw:
                    terminator = '"' + (raw.group(1) or "")
                    end = text.find(terminator, pos + len(raw.group()))
                    if end == -1:
                        raise GapError("Unterminated raw string in inner attribute.")
                    pos = end + len(terminator)
                elif text[pos] == '"':
                    pos += 1
                    while pos < len(text) and text[pos] != '"':
                        pos += 2 if text[pos] == "\\" else 1
                    if pos == len(text):
                        raise GapError("Unterminated string in inner attribute.")
                    pos += 1
                elif text.startswith("/*", pos) or text.startswith("//", pos):
                    # Attribute comments are uncommon; reject rather than guess a boundary.
                    raise GapError("Comments inside a leading inner attribute need manual wrapping.")
                else:
                    depth += (text[pos] == "[") - (text[pos] == "]")
                    pos += 1
            if depth:
                raise GapError("Unterminated leading inner attribute.")
        else:
            break
    return pos


def wrap(path: Path) -> dict:
    original = path.read_bytes()
    text = original.decode("utf-8")
    split = prefix_end(text)
    prefix, body = text[:split], text[split:]
    opening = "\nvstd::prelude::verus! {\n"
    closing = "\n}\n"
    path.write_bytes((prefix + opening + body + closing).encode("utf-8"))
    return {
        "sha256": hashlib.sha256(original).hexdigest(),
        "insert_byte": len(prefix.encode("utf-8")),
        "insert_line": prefix.count("\n") + 1,
        "insert_column": len(prefix.rsplit("\n", 1)[-1]) + 1,
        "insert_bytes": len(opening.encode("utf-8")),
        "insert_lines": opening.count("\n"),
        "original_bytes": len(original),
        "original_lines": text.count("\n") + 1,
        "original_text": text,
    }


def snapshot(source: Path, destination: Path, output: Path) -> dict[str, str]:
    hashes = {}
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            path = current_path / name
            is_cache = name in SKIP and (
                name != "target" or current_path == source or (current_path / "Cargo.toml").is_file()
            )
            if is_cache or path == output or output.is_relative_to(path):
                dirs.remove(name)
            elif path.is_symlink():
                resolved = path.resolve()
                if not resolved.is_relative_to(source):
                    raise GapError(f"Directory symlink leaves the copied workspace: {path}")
                target = destination / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                copied_target = destination / resolved.relative_to(source)
                target.symlink_to(os.path.relpath(copied_target, target.parent), target_is_directory=True)
                dirs.remove(name)
        for name in files:
            if name == ".git":
                continue
            path = current_path / name
            if path.is_symlink() and not path.resolve().is_relative_to(source):
                raise GapError(f"File symlink leaves the copied workspace: {path}")
            relative = path.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if path.suffix == ".rs" or name in {"Cargo.toml", "Cargo.lock", "config.toml"}:
                hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def discover_manifest(path: Path) -> Path:
    if path.is_file():
        if path.name != "Cargo.toml":
            raise GapError(f"Expected a .rs file, a directory or Cargo.toml, got {path}")
        return path
    for parent in [path, *path.parents]:
        manifest = parent / "Cargo.toml"
        if manifest.is_file():
            return manifest
    raise GapError(f"No Cargo.toml found in {path} or its parents. Pass a crate directory or a .rs file.")


def metadata(manifest: Path, env: dict, timeout: float) -> dict:
    result = run(
        ["cargo", "metadata", "--no-deps", "--format-version=1",
         "--manifest-path", str(manifest)], manifest.parent, env, timeout,
    )
    if result["exit_code"]:
        raise GapError("Cargo metadata failed:\n" + result["stderr"] + result["stdout"])
    return json.loads(result["stdout"])


def select_package(meta: dict, manifest: Path, name: str | None) -> dict:
    members = set(meta["workspace_members"])
    packages = [package for package in meta["packages"] if package["id"] in members]
    if name:
        candidates = [package for package in packages if package["name"] == name]
    else:
        candidates = [package for package in packages if Path(package["manifest_path"]) == manifest]
        if not candidates:
            defaults = set(meta.get("workspace_default_members", []))
            candidates = [package for package in packages if package["id"] in defaults]
        if len(candidates) != 1 and len(packages) == 1:
            candidates = packages
    if len(candidates) != 1:
        available = ", ".join(sorted(package["name"] for package in packages))
        raise GapError(
            f"Choose one package from this workspace: {available}\n"
            "Run again with --package NAME (or pass that crate's directory)."
        )
    return candidates[0]


def select_target(package: dict, binary: str | None) -> dict:
    libraries = {"lib", "rlib", "dylib", "staticlib", "cdylib"}
    targets = [
        target for target in package["targets"]
        if (binary and "bin" in target["kind"] and target["name"] == binary)
        or (not binary and libraries.intersection(target["kind"]))
    ]
    if not targets and not binary:
        targets = [target for target in package["targets"] if "bin" in target["kind"]]
    if len(targets) != 1:
        choices = ", ".join(t["name"] for t in package["targets"] if "bin" in t["kind"])
        raise GapError(f"Select a library crate or use --bin NAME. Binary choices: {choices or 'none'}")
    return targets[0]


def verus_args(args: list[str], keep_support: bool = False) -> list[str]:
    remove = {"--error-format", "--json", "--emit", "--out-dir", "--color"}
    output = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in remove:
            index += 2
        elif any(arg.startswith(flag + "=") for flag in remove) or arg.startswith("-Cincremental="):
            index += 1
        elif arg == "-C" and args[index + 1].startswith("incremental="):
            index += 2
        elif not keep_support and arg == "--extern" and args[index + 1].split("=")[0] in SUPPORT:
            index += 2
        else:
            output.append(arg)
            index += 1
    return output


def active_sources(capture: dict, root: Path) -> list[Path]:
    args = capture["args"]
    out_dir = Path(args[args.index("--out-dir") + 1])
    crate = args[args.index("--crate-name") + 1]
    extra = ""
    for index, arg in enumerate(args):
        if arg.startswith("extra-filename="):
            extra = arg.split("=", 1)[1]
        elif arg.startswith("-Cextra-filename="):
            extra = arg.split("=", 1)[1]
    depfile = out_dir / f"{crate}{extra}.d"
    if not depfile.is_file():
        raise GapError(f"Cargo did not produce source dependency information: {depfile}")
    first = depfile.read_text().replace("\\\n", " ").splitlines()[0]
    dependencies = shlex.split(first.split(": ", 1)[1])
    paths = {
        (Path(capture["cwd"]) / name).resolve()
        for name in dependencies if name.endswith(".rs")
    }
    paths.add(root.resolve())
    return sorted(paths)


def category(diagnostic: dict) -> str:
    message = diagnostic.get("message", "")
    if re.match(r"(aborting due to|could not compile)", message):
        return "compiler_summary"
    evidence = (message + "\n" + "\n".join(
        child.get("message", "") for child in diagnostic.get("children", [])
    )).lower()
    if diagnostic.get("code"):
        return "rust_or_wrapper"
    if "removing an expression is not supported in this position" in evidence:
        return "wrapper_or_parser"
    if "ignored" in evidence and ("cannot use function" in evidence or "cannot use type" in evidence):
        return "verification_boundary"
    if "cannot use external trait" in evidence and "without declaring the trait" in evidence:
        return "verification_boundary"
    if "external_type_specification" in evidence or "assume_specification" in evidence:
        return "missing_library_spec"
    if any(phrase in evidence for phrase in (
        "the verifier does not yet support", "not yet supported", "not supported",
        "unsupported", "not implemented",
    )):
        return "frontend_unsupported"
    if any(phrase in evidence for phrase in ("expected", "unexpected token", "parse error")):
        return "wrapper_or_parser"
    return "verus_adaptation_or_other"


def location(span: dict, cwd: Path, maps: dict[str, dict]) -> dict | None:
    path = str((cwd / span["file_name"]).resolve())
    entry = maps.get(path)
    if not entry or span["byte_start"] < entry["insert_byte"] + entry["insert_bytes"]:
        if entry and span["byte_end"] <= entry["insert_byte"]:
            return {
                "file": entry["file"], "line": span["line_start"],
                "column": span["column_start"], "source": entry["original_text"].splitlines()[span["line_start"] - 1],
            }
        return None
    original_byte = span["byte_start"] - entry["insert_bytes"]
    if original_byte >= entry["original_bytes"]:
        return None
    line = span["line_start"] - entry["insert_lines"]
    column = span["column_start"]
    if line == entry["insert_line"]:
        column += entry["insert_column"] - 1
    lines = entry["original_text"].splitlines()
    if not 1 <= line <= len(lines):
        return None
    return {"file": entry["file"], "line": line, "column": column, "source": lines[line - 1]}


def diagnostics(text: str, cwd: Path, maps: dict[str, dict]) -> tuple[list[dict], str]:
    records, rendered = [], []
    for line in text.splitlines(keepends=True):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            rendered.append(line)
            continue
        if not isinstance(record, dict):
            rendered.append(line)
            continue
        if record.get("reason") == "compiler-message":
            record = record["message"]
        if record.get("level") not in {"error", "warning", "failure-note"}:
            continue
        raw = record.get("rendered") or record.get("message", "") + "\n"
        rendered.append(raw)
        derived_warning = (
            record["level"] == "warning"
            and "without adding a specification" in record.get("message", "")
        )
        relevant_warning = derived_warning or (
            record["level"] == "warning" and category(record) == "verification_boundary"
        )
        if record["level"] != "error" and not relevant_warning:
            continue
        spans = record.get("spans", [])
        originals, related = [], []
        for span in spans:
            result = location(span, cwd, maps)
            if result is not None:
                group = originals if span.get("is_primary") else related
                if result not in group:
                    group.append(result)
        records.append({
            "category": "missing_derived_spec" if derived_warning else category(record),
            "level": record["level"], "message": record["message"],
            "code": record.get("code"), "locations": originals,
            "related_locations": related,
            "rendered": raw, "raw_diagnostic": record,
        })
    return records, "".join(rendered)


def report_markdown(report: dict) -> str:
    wrapped = len(report["wrapped_files"])
    outside = len(report["excluded_sources"])
    lines = [
        "# Verus gap scan", "", f"**Status:** `{report['status']}`",
        f"**Input:** `{report['input']}`", f"**Verus:** `{report['verus']['version']}`",
        f"**Source files inside the Verus checking scope:** {wrapped}",
        f"**Active source files used only as build context:** {outside}", "",
        (
            "This is a diagnostic follow-up with explicit external_body boundaries. Excluded bodies are not checked; this is not unchanged-source evidence, an exhaustive inventory or a proof. Mapped locations refer to the original input."
            if report["checks"]["shielding"] else
            "This is the first observed compiler frontier, not an exhaustive inventory or a correctness proof. Only `verus! {}` wrappers were added; dependencies and function bodies were not stubbed or removed."
        ), "",
    ]
    if report.get("more_errors"):
        extra = report["more_errors"]
        lines.extend([
            f"**Optional error expansion:** `{extra['status']}`, {extra['additional_diagnostics']} additional diagnostic records, "
            f"{extra['excluded_bodies']} excluded bodies; `{extra['stop_reason']}`. "
            f"[Follow-up report]({extra['report']}). The original result below is unchanged.", "",
        ])
    if report.get("applied_boundaries"):
        lines.extend(["## Bodies excluded from this follow-up", ""])
        lines.extend(
            f"- `{entry['file']}:{entry['line']}`: `{entry['function']}` (added in pass {entry['round']})."
            for entry in report["applied_boundaries"]
        )
        lines.extend(["", "Cumulative changes: [boundaries.diff](boundaries.diff).", ""])
    if "verus" in report["runs"]:
        lines.extend([
            "Raw, unedited output: [verus.stderr](verus.stderr), [verus.stdout](verus.stdout). Human-readable original diagnostics: [verus.txt](verus.txt). Emitted locations refer to the retained `workspace/`; mapped locations below refer to input source.", "",
        ])
    elif "baseline" in report["runs"]:
        lines.extend(["Verus was not run. Build output: [baseline.stdout](baseline.stdout), [baseline.stderr](baseline.stderr).", ""])
    for error in report["blockers"]:
        lines.extend([f"**Blocker:** {error}", ""])
    counts = Counter(d["category"] for d in report["diagnostics"] if d["category"] != "compiler_summary")
    if counts:
        lines.extend(["| Category | Diagnostics |", "|---|---:|"])
        lines.extend(f"| `{kind}` | {count} |" for kind, count in sorted(counts.items()))
        lines.append("")
    for kind in sorted(counts):
        lines.extend([f"## {kind}", ""])
        for diagnostic in report["diagnostics"]:
            if diagnostic["category"] != kind:
                continue
            lines.extend([f"### {diagnostic['message']}", ""])
            for place in diagnostic["locations"]:
                lines.extend([
                    f"`{place['file']}:{place['line']}:{place['column']}`", "",
                    "```rust", place["source"], "```", "",
                ])
            if diagnostic["related_locations"]:
                lines.extend(["Related diagnostic spans (context/use sites, not separate errors):", ""])
                lines.extend(
                    f"- `{place['file']}:{place['line']}:{place['column']}`: `{place['source'].strip()}`"
                    for place in diagnostic["related_locations"]
                )
                lines.append("")
            if not diagnostic["locations"]:
                lines.extend(["No confirmed mapping to input source; inspect the raw diagnostic.", ""])
            lines.extend(["```text", diagnostic["rendered"].rstrip(), "```", ""])
    if outside:
        lines.extend([
            "The build used additional active source files outside this checking scope. "
            "This is expected for a `--file` scan and can also occur for generated or external source boundaries. "
            "The complete machine-readable list remains in `report.json` as `excluded_sources`.",
            "",
        ])
    return "\n".join(lines)


def save_run(output: Path, name: str, result: dict) -> dict:
    for stream in ("stdout", "stderr"):
        (output / f"{name}.{stream}").write_text(result[stream])
    return {key: value for key, value in result.items() if key not in {"stdout", "stderr"}}


def assess_result(result: dict, records: list[dict]) -> tuple[str, list[str]]:
    blockers = []
    if result["timed_out"]:
        blockers.append("Verus timed out; partial diagnostics retained.")
    if any(phrase in (result["stderr"] + result["stdout"]).lower()
           for phrase in ("internal compiler error", "panicked at")):
        blockers.append("Compiler panic/ICE; not an application support-gap verdict.")
    categories = {d["category"] for d in records} - {"compiler_summary"}
    if result["exit_code"] and not categories:
        blockers.append("Verus failed without a classifiable diagnostic; inspect raw output.")
    if categories.intersection({"rust_or_wrapper", "wrapper_or_parser"}):
        blockers.append("Rust/wrapper/parser errors may hide Verus support gaps.")
    return (
        "blocked" if blockers else "diagnostics_observed" if categories else "no_diagnostics_observed",
        blockers,
    )


def scan(args: argparse.Namespace, cargo_args: list[str]) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        raise GapError(f"Input does not exist: {path}. Pass your real crate directory, not the README placeholder.")
    verus = find_verus(args.verus)
    version = identity(verus)
    env = dict(os.environ, RUSTUP_TOOLCHAIN=version["toolchain"])
    env.pop("RUSTC_WRAPPER", None)
    env.pop("RUSTC_WORKSPACE_WRAPPER", None)
    env.pop("CARGO_MAKEFLAGS", None)
    deadline = time.monotonic() + args.timeout
    standalone = path.suffix == ".rs"
    reserved = {"--manifest-path", "--target-dir", "--package", "-p", "--workspace",
                "--all", "--lib", "--bin", "--bins", "--example", "--examples",
                "--test", "--tests", "--bench", "--benches", "--all-targets"}
    for arg in cargo_args:
        if not standalone and (arg.split("=", 1)[0] in reserved or arg.startswith("-p") and arg != "--profile"):
            raise GapError(
                f"Cargo option {arg} overrides the isolated target. "
                "Use the input path, --package/--bin before --, and do not override --target-dir."
            )
    if standalone:
        source = path.parent
        root = path
        package = None
        target = None
    else:
        manifest = discover_manifest(path)
        meta = metadata(manifest, env, deadline - time.monotonic())
        package = select_package(meta, manifest, args.package)
        target = select_target(package, args.bin)
        source = Path(meta["workspace_root"]).resolve()
        root = Path(target["src_path"]).resolve()
        for member in meta["packages"]:
            for dependency in member["dependencies"]:
                if dependency.get("path") and not Path(dependency["path"]).resolve().is_relative_to(source):
                    raise GapError(
                        f"Local dependency {dependency['name']} is outside {source}. "
                        "Create a self-contained workspace copy with that dependency before scanning."
                    )
    if source == Path.home() or source == Path(source.anchor):
        raise GapError("Refusing to snapshot a filesystem/home root. Use a dedicated project directory.")
    output = Path(args.out).expanduser().resolve() if args.out else (
        Path.cwd() / "verus-gap-results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    )
    if output.exists() and any(output.iterdir()):
        raise GapError(f"Output directory is not empty: {output}. Choose a new --out directory.")
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "workspace"
    report = {
        "schema": "verus-gap/v1", "status": "blocked", "input": str(path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace_root": str(source), "package": package["name"] if package else None,
        "target": target["name"] if target else root.stem,
        "verus": version, "verus_executable": str(verus),
        "cargo_args": cargo_args, "wrapped_files": [], "excluded_sources": [],
        "diagnostics": [], "blockers": [], "runs": {}, "complete_inventory": False,
        "checks": {"smt": False, "proof_lifetimes": True, "shielding": False},
        "tool_sha256": {
            name: hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest()
            for name in ("gap.py", "capture.py", "more_errors.py")
        },
        "verus_artifacts_sha256": {
            name: hashlib.sha256((verus.parent / name).read_bytes()).hexdigest()
            for name in ("verus", "rust_verify", "libvstd.rlib", "vstd.vir")
            if (verus.parent / name).is_file()
        },
    }
    maps = {}
    command, cwd = None, None
    try:
        print(f"Copying source to {workspace}", flush=True)
        hashes = snapshot(source, workspace, output)
        report["source_sha256"] = hashes
        copied_root = workspace / root.relative_to(source)
        if standalone:
            compiler_args = [
                str(copied_root), "--edition", args.edition, "--crate-type=lib",
                "--crate-name", "gap_subject", *cargo_args,
            ]
            baseline_command = ["rustc", *compiler_args, "--emit=dep-info,metadata", "--out-dir", str(output)]
            baseline_env = env
        else:
            capture_path = output / "capture.json"
            build = output / "build"
            baseline_env = dict(
                env, CARGO_TARGET_DIR=str(build), CARGO_INCREMENTAL="0",
                RUSTC_WRAPPER=str(capture_wrapper(output)), RUSTC_WORKSPACE_WRAPPER="",
                VERUS_GAP_ROOT=str(copied_root), VERUS_GAP_CAPTURE=str(capture_path),
            )
            copied_manifest = workspace / Path(package["manifest_path"]).relative_to(source)
            kind = ["--bin", target["name"]] if "bin" in target["kind"] else ["--lib"]
            baseline_command = [
                "cargo", "check", "--manifest-path", str(copied_manifest), "-p", package["name"],
                *kind, "--message-format=json", *cargo_args,
            ]
        print("Checking the unchanged Rust copy and building dependencies...", flush=True)
        baseline = run(baseline_command, workspace, baseline_env, deadline - time.monotonic())
        report["runs"]["baseline"] = save_run(output, "baseline", baseline)
        lock = workspace / "Cargo.lock"
        if lock.is_file():
            report["dependency_lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
        if baseline["exit_code"]:
            _, raw = diagnostics(baseline["stdout"], workspace, {})
            print(raw + baseline["stderr"], file=sys.stderr, end="")
            raise GapError(
                "Unchanged Rust baseline failed; no Verus gap was inferred. "
                "Fix the build setup in a copy (features, target, generated assets or toolchain), then retry. "
                "See baseline.stdout and baseline.stderr."
            )
        if not standalone:
            if not capture_path.is_file():
                raise GapError("Cargo did not invoke the selected target compiler; no scan was performed.")
            captured = json.loads(capture_path.read_text())
            env.update(captured["environment"])
            compiler_args = verus_args(captured["args"])
            cwd = Path(captured["cwd"])
            copied_files = active_sources(captured, copied_root)
        else:
            cwd = workspace
            copied_files = active_sources({"args": baseline_command[1:], "cwd": str(cwd)}, copied_root)
        for file in copied_files:
            if file.is_relative_to(workspace):
                data = file.read_bytes()
                text = data.decode("utf-8")
                maps[str(file.resolve())] = {
                    "file": str(file.relative_to(workspace)), "insert_byte": 0,
                    "insert_bytes": 0, "insert_line": 1, "insert_column": 1,
                    "insert_lines": 0, "original_bytes": len(data), "original_text": text,
                }
        if args.file:
            chosen = [(source / name).resolve() for name in args.file]
            if any(not chosen_path.is_relative_to(source) for chosen_path in chosen):
                raise GapError("--file must name source within the copied workspace.")
            selected = {workspace / chosen_path.relative_to(source) for chosen_path in chosen}
            if not selected.issubset(set(copied_files)):
                raise GapError("--file is not active in this Rust build; check the path and Cargo features.")
        else:
            selected = {file for file in copied_files if file.is_relative_to(workspace)}
        report["excluded_sources"] = [str(file) for file in copied_files if file not in selected]
        for file in sorted(selected):
            entry = wrap(file)
            entry["file"] = str(file.relative_to(workspace))
            maps[str(file.resolve())] = entry
            report["wrapped_files"].append({key: value for key, value in entry.items() if key != "original_text"})
        if not selected:
            raise GapError("No active source file was wrapped; no scan was performed.")
        command = [
            str(verus), *compiler_args, "--no-verify", "--error-format=json",
            "--color=never", "--out-dir", str(output / "verus-build"),
            "--check-cfg=cfg(verus_keep_ghost)", "--check-cfg=cfg(verus_keep_ghost_body)",
        ]
        print(f"Running Verus on {len(selected)} wrapped source file(s); no shielding or specifications...", flush=True)
        result = run(command, cwd, env, deadline - time.monotonic())
        report["runs"]["verus"] = save_run(output, "verus", result)
        report["diagnostics"], raw = diagnostics(result["stderr"], cwd, maps)
        stdout_diagnostics, stdout_raw = diagnostics(result["stdout"], cwd, maps)
        report["diagnostics"].extend(stdout_diagnostics)
        (output / "verus.txt").write_text(raw + stdout_raw)
        print(raw + stdout_raw, end="")
        report["status"], report["blockers"] = assess_result(result, report["diagnostics"])
    except (GapError, UnicodeError, OSError, json.JSONDecodeError) as error:
        report["blockers"].append(str(error))
        print(f"error: {error}", file=sys.stderr)
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "report.md").write_text(report_markdown(report))
        if getattr(args, "more_errors", False):
            from .more_errors import expand
            report["more_errors"] = expand(output, report, maps, command, cwd, env, deadline)
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            (output / "report.md").write_text(report_markdown(report))
        print(f"\n{report['status']}: {output / 'report.md'}", flush=True)
    if report.get("more_errors", {}).get("status") == "blocked":
        return 2
    return {"no_diagnostics_observed": 0, "diagnostics_observed": 1, "blocked": 2}[report["status"]]


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in values:
        split = values.index("--")
        values, extra = values[:split], values[split + 1:]
    parser = argparse.ArgumentParser(
        description="Copy Rust source, add only verus! wrappers, and show original Verus diagnostics.",
        epilog="Cargo options go after --, e.g. -- --features foo.",
    )
    parser.add_argument("path", nargs="?", default=".", help="crate directory, Cargo.toml, .rs file, or 'demo'")
    parser.add_argument("--package", help="workspace package; unnecessary for a single crate")
    parser.add_argument("--bin", help="binary target instead of the library")
    parser.add_argument("--file", action="append", default=[], help="wrap only this active file, relative to workspace (repeatable)")
    parser.add_argument("--out", help="new output directory (includes a retained source copy)")
    parser.add_argument("--more-errors", action="store_true",
                        help="look for additional errors by adding explicit external_body boundaries in follow-up passes; original result is retained")
    parser.add_argument("--verus", help="use a complete local Verus distribution")
    parser.add_argument("--timeout", type=float, default=600, help="build + scan time budget, seconds")
    parser.add_argument("--edition", default="2021", choices=["2015", "2018", "2021", "2024"], help="standalone .rs edition only")
    args = parser.parse_args(values)
    if args.path == "demo":
        args.path = str(PACKAGE / "examples" / "gaps.rs")
    try:
        return scan(args, extra)
    except (GapError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
