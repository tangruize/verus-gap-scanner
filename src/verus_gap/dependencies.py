"""Set up actual Verus-aware dependencies without treating missing ghosts as gaps."""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import sys
import time
import tomllib
import uuid
from pathlib import Path

import tomlkit

from .gap import GapError, PACKAGE, capture_wrapper, run, save_run, verus_args
from .project import Project, save_replay
from .target import official_sources


SUPPORT = {"vstd", "verus_builtin", "verus_builtin_macros", "verus_state_machines_macros"}


def requires_ghost_dependencies(package: dict, metadata: dict) -> bool:
    members = {str(Path(item["manifest_path"]).parent.resolve()): item for item in metadata["packages"]}
    pending, visited = [package], set()
    while pending:
        current = pending.pop()
        name = current["manifest_path"]
        if name in visited:
            continue
        visited.add(name)
        if (current.get("metadata") or {}).get("verus", {}).get("verify"):
            return True
        for dependency in current.get("dependencies", []):
            path = dependency.get("path")
            if path and dependency.get("kind") not in {"dev", "build"}:
                member = members.get(str(Path(path).resolve()))
                if member:
                    pending.append(member)
    return False


def align_manifest(text: str, versions: dict[str, str], root: bool = False,
                   no_std: bool = False) -> str:
    document = tomlkit.parse(text)
    tables = [
        document.get("dependencies", {}),
        document.get("build-dependencies", {}),
        document.get("workspace", {}).get("dependencies", {}),
    ]
    for target in document.get("target", {}).values():
        tables.extend([target.get("dependencies", {}), target.get("build-dependencies", {})])
    for table in tables:
        for name, value in list(table.items()):
            package = value.get("package", name) if isinstance(value, dict) else name
            if package not in versions or isinstance(value, dict) and (
                value.get("workspace") or value.get("path") or value.get("git")
            ):
                continue
            if isinstance(value, str):
                table[name] = "=" + versions[package]
            elif "version" in value:
                value["version"] = "=" + versions[package]
    if root:
        package = document["package"]
        metadata = package.setdefault("metadata", tomlkit.table())
        metadata.setdefault("verus", tomlkit.table())["verify"] = True
        dependencies = document.setdefault("dependencies", tomlkit.table())
        if not any(
            (value.get("package", name) if isinstance(value, dict) else name) == "vstd"
            for name, value in dependencies.items()
        ):
            if no_std:
                dependencies["vstd"] = {
                    "version": "=" + versions["vstd"], "default-features": False,
                    "features": ["alloc"],
                }
            else:
                dependencies["vstd"] = "=" + versions["vstd"]
    return tomlkit.dumps(document)


def cargo_arguments(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value.startswith("-Z") and len(value) > 2:
            result.extend(["-Z", value[2:]])
        else:
            result.append(value)
    return result


def fresh_capture(path: Path) -> str:
    attempt = uuid.uuid4().hex
    if path.exists():
        path.rename(path.with_name(f"{path.stem}.previous-{attempt}{path.suffix}"))
    return attempt


def configure_dependencies(project: Project, package: dict, metadata: dict):
    if not requires_ghost_dependencies(package, metadata):
        return
    verus = Path(project.manifest["verus_executable"])
    cargo_verus = verus.parent / "cargo-verus"
    if not cargo_verus.is_file():
        raise GapError("This Verus-aware crate needs the official cargo-verus executable.")
    output = project.output / "dependency-setup"
    output.mkdir(exist_ok=True)
    previous = project.manifest.get("dependency_setup")
    if previous:
        history = output / "history" / uuid.uuid4().hex
        history.mkdir(parents=True)
        for name in (
            "manifests.diff", "native.stdout", "native.stderr", "native-capture.json",
            "cargo-verus.stdout", "cargo-verus.stderr", "ghost-capture.json",
        ):
            file = output / name
            if file.is_file():
                file.rename(history / name)
        project.manifest.setdefault("dependency_setup_history", []).append({
            **previous, "directory": str(history.relative_to(project.output)),
        })
    print("Preparing ghost-aware dependencies using official Cargo-Verus...", flush=True)
    source = official_sources(project.manifest["verus"]["commit"])
    manifest = tomllib.loads((source / "source/vstd/Cargo.toml").read_text())
    versions = {"vstd": manifest["package"]["version"]}
    versions.update({
        name: value["version"].lstrip("=")
        for name, value in manifest["dependencies"].items() if name in SUPPORT
    })
    root_manifest = project.workspace / Path(package["manifest_path"]).relative_to(
        Path(project.manifest["workspace_root"])
    )
    files = {project.workspace / "Cargo.toml", root_manifest}
    files.update(
        project.workspace / Path(member["manifest_path"]).relative_to(Path(project.manifest["workspace_root"]))
        for member in metadata["packages"]
    )
    patches = []
    for file in sorted(files):
        original = Path(project.manifest["workspace_root"]) / file.relative_to(project.workspace)
        old = original.read_text()
        new = align_manifest(
            old, versions, root=file == root_manifest,
            no_std=project.manifest.get("support_mode") == "target_matching_no_std_alloc",
        )
        if new != old:
            patches.extend(difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile="original/" + str(file.relative_to(project.workspace)),
                tofile="context/" + str(file.relative_to(project.workspace)),
            ))
            file.write_text(new)
    (output / "manifests.diff").write_text("".join(patches))
    project.manifest["dependency_setup"] = {
        "matching_support_versions": versions, "manifest_patch": "dependency-setup/manifests.diff",
        "original_native_baseline_retained": True, "completed": False,
    }
    project.save()
    native_env = dict(project.env, CARGO_TARGET_DIR=str(output / "native-build"),
                      RUSTC_WRAPPER=str(capture_wrapper(output)),
                      VERUS_GAP_ROOT=str(project.workspace / project.manifest["root_source"]),
                      VERUS_GAP_CAPTURE=str(output / "native-capture.json"), CARGO_INCREMENTAL="0")
    kind = ["--bin", project.manifest["target"]] if "--bin" in project.manifest["baseline"]["command"] else ["--lib"]
    native = run([
        "cargo", "check", "--manifest-path", str(root_manifest), "-p", package["name"],
        *kind, "--message-format=json", *project.manifest["cargo_args"],
    ], project.workspace, native_env, project.deadline - time.monotonic())
    project.manifest["dependency_setup"]["native_after_alignment"] = save_run(output, "native", native)
    project.save()
    if native["exit_code"] or native["timed_out"]:
        raise GapError(
            "The isolated project does not compile with the matching Verus support version. "
            "Original build evidence is retained; see dependency-setup/native.stderr and native.stdout."
        )
    proxy = output / "driver"
    proxy.mkdir(exist_ok=True)
    shutil.copy2(cargo_verus, proxy / "cargo-verus")
    script = proxy / "verus"
    script.write_text("#!" + sys.executable + "\n" + (PACKAGE / "bridge.py").read_text().split("\n", 1)[1])
    script.chmod(0o755)
    capture = output / "ghost-capture.json"
    attempt = fresh_capture(capture)
    project.manifest["dependency_setup"]["attempt_id"] = attempt
    project.save()
    env = dict(
        project.env, VERUS_GAP_REAL_VERUS=str(verus),
        VERUS_GAP_ROOT=str(project.workspace / project.manifest["root_source"]),
        VERUS_GAP_CAPTURE=str(capture), VERUS_GAP_SETUP_ID=attempt, CARGO_INCREMENTAL="0",
    )
    for key in ("RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER"):
        env.pop(key, None)
    result = run([
        str(proxy / "cargo-verus"), "verify", "--manifest-path", str(root_manifest),
        "-p", package["name"], "--target-dir", str(output / "ghost-build"),
        *cargo_arguments(project.manifest["cargo_args"]), *kind,
        "--", "--no-verify", "--cap-lints=warn",
    ], project.workspace, env, project.deadline - time.monotonic())
    project.manifest["dependency_setup"]["cargo_verus"] = save_run(output, "cargo-verus", result)
    project.save()
    if not capture.is_file() or result["timed_out"]:
        raise GapError(
            "Ghost-aware dependency setup failed before reaching the chosen crate. "
            "No missing-ghost import was counted as a Verus limitation; see dependency-setup/cargo-verus.stderr."
        )
    captured = json.loads(capture.read_text())
    if captured.get("root_execution") != "intentionally_deferred" or captured.get("setup_id") != attempt:
        raise GapError("Invalid Cargo-Verus capture; root execution was not explicitly deferred.")
    compiler = verus_args(captured["args"], keep_support=True)
    support_name = next(
        (dependency.get("rename") or "vstd" for dependency in package["dependencies"]
         if dependency["name"] == "vstd"), "vstd",
    )
    project.manifest["support_crate_name"] = support_name
    project.command = [
        str(verus), captured["compiler"], *compiler,
        "--error-format=json", "--color=never",
        "--emit=metadata",
        "--out-dir", str(project.output / "verus-build"),
    ]
    project.cwd = Path(captured["cwd"])
    project.env.update(captured["environment"])
    project.manifest["support_mode"] = "cargo_verus_ghost_dependencies"
    project.manifest["dependency_setup"]["completed"] = True
    project.manifest["dependency_setup"]["captured_root_without_execution"] = True
    project.manifest["dependency_setup"]["probe_emit"] = "metadata"
    project.manifest["compiler_args"] = compiler
    artifacts = {
        Path(value.split("=", 1)[1])
        for index, value in enumerate(compiler)
        if index and compiler[index - 1] == "--extern" and "=" in value
    }
    artifacts.update(path.with_suffix(".vir") for path in list(artifacts) if path.with_suffix(".vir").is_file())
    project.manifest["dependency_artifacts_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(artifacts)
    }
    project.save()
    save_replay(project)
