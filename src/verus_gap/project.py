"""Capture one unchanged build and replay its exact context for all probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from functools import cached_property
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from threading import Event

from .configuration import (
    CONFIGURATION_HELP, automatic_configuration, load_configuration, merge_cargo_arguments,
    validate_cargo_arguments, validate_saved_configuration, workspace_configuration,
)
from .gap import (
    GapError, PACKAGE, active_sources, discover_manifest, find_verus, identity,
    metadata, run, save_run, select_package, select_target, snapshot, verus_args,
    capture_wrapper,
)
from .source import Cfg, Source


DEFAULT_SCAN_LEVEL = 2


def write_json(path: Path, payload: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def scan_level_from(record: dict) -> int:
    if "scan_level" in record:
        level = record["scan_level"]
    else:
        mode = record.get("scan_mode", "adaptive")
        if mode not in ("adaptive", "fast"):
            raise GapError(f"Unknown saved scan mode: {mode!r}.")
        level = 0 if mode == "adaptive" else 1
    if type(level) is not int or level not in (0, 1, 2):
        raise GapError(f"Invalid scan level: {level!r}; expected 0, 1, or 2.")
    return level


@dataclass
class Project:
    output: Path
    workspace: Path
    cwd: Path
    env: dict
    command: list[str]
    sources: dict[str, Source]
    manifest: dict
    deadline: float
    cancel_event: Event | ProcessEvent | None = None

    @cached_property
    def source_paths(self) -> dict[str, Path]:
        paths = {name: (self.workspace / name).resolve() for name in self.sources}
        for name, path in paths.items():
            if not path.is_relative_to(self.workspace):
                raise GapError(f"Active source leaves the private scan workspace: {name}")
        return paths

    def save(self):
        write_json(self.output / "project.json", self.manifest)

    def verify_originals(self):
        for name, path in self.source_paths.items():
            source = self.sources[name]
            if path.read_bytes() != source.data:
                raise GapError(f"Scan copy was not restored: {name}")
        self.manifest["workspace_restored"] = True
        self.save()


def compiler_cfg(args: list[str], cwd: Path, env: dict, output: Path,
                 timeout: float, driver: list[str] | None = None) -> list[str]:
    flags, index = [], 0
    while index < len(args):
        arg = args[index]
        if arg in {"--target", "--cfg", "--sysroot", "-Z", "-C"}:
            flags.extend(args[index:index + 2])
            index += 2
        elif arg.startswith(("--target=", "--cfg=", "--sysroot=", "-Z", "-C")):
            flags.append(arg)
            index += 1
        else:
            index += 1
    result = run([*(driver or ["rustc"]), "--print=cfg", *flags], cwd, env, timeout)
    save_run(output, "cfg", result)
    if result["exit_code"] or result["timed_out"]:
        raise GapError("Cannot determine the actual compiler cfg; see cfg.stderr.")
    return result["stdout"].splitlines()


def prepare(args: argparse.Namespace, cargo_args: list[str], output: Path) -> Project:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        raise GapError(f"Input does not exist: {path}")
    output.mkdir(parents=True, exist_ok=True)
    verus = find_verus(args.verus)
    version = identity(verus)
    input_kind = getattr(args, "input_kind", None) or "rust"
    deadline = time.monotonic() + args.timeout
    env = dict(os.environ, RUSTUP_TOOLCHAIN=version["toolchain"], CARGO_BUILD_JOBS="4")
    for key in ("RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "CARGO_MAKEFLAGS"):
        env.pop(key, None)
    configuration = load_configuration(Path(args.config)) if getattr(args, "config", None) else None
    if configuration is not None:
        configuration.apply_environment(env)
    automatic = not getattr(args, "no_config", False)
    standalone = path.suffix == ".rs"
    ghost_dependencies = False
    manifest_path = None
    if standalone and input_kind != "verus" and not getattr(args, "standalone", False):
        for parent in path.parents:
            if (parent / "Cargo.toml").is_file():
                manifest_path = parent / "Cargo.toml"
                standalone = False
                break
    if standalone:
        source_root, root = path.parent, path
        if input_kind == "verus":
            source_root = next(
                (parent for parent in path.parents if (parent / "Cargo.toml").is_file()),
                source_root,
            )
        package, target = None, None
        if configuration is not None and (
            configuration.package or configuration.binary or configuration.arguments or configuration.rustflags
        ):
            raise GapError("Cargo build settings require a Cargo project, not a standalone .rs file.")
    else:
        manifest_path = manifest_path or discover_manifest(path)
        try:
            meta = metadata(manifest_path, env, deadline - time.monotonic())
        except GapError as error:
            raise GapError(f"{error}\n{CONFIGURATION_HELP}") from error
        source_root = Path(meta["workspace_root"]).resolve()
        if configuration is None and automatic:
            configuration = workspace_configuration(source_root)
            if configuration is not None and configuration.match is not None and not any(
                member["id"] in meta["workspace_members"]
                and configuration.match.applies(member, source_root)
                for member in meta["packages"]
            ):
                configuration = None
        configured_package = configuration.package if configuration is not None and not any(
            member["id"] in meta["workspace_members"]
            and Path(member["manifest_path"]) == manifest_path
            for member in meta["packages"]
        ) else None
        package = select_package(
            meta, manifest_path, args.package or configured_package,
        )
        if configuration is not None and configuration.selection == "workspace" and (
            configuration.match is not None and not configuration.match.applies(package, source_root)
        ):
            configuration = None
        if configuration is None and automatic:
            configuration = automatic_configuration(source_root, package)
        if configuration is not None and configuration.selection != "explicit":
            configuration.apply_environment(env)
        from .dependencies import requires_ghost_dependencies
        ghost_dependencies = requires_ghost_dependencies(package, meta)
        target = select_target(package, args.bin or (configuration.binary if configuration else None))
        root = Path(target["src_path"]).resolve()
        validate_cargo_arguments(cargo_args)
        if configuration is not None:
            cargo_args = merge_cargo_arguments(configuration.arguments, cargo_args)
        for member in meta["packages"]:
            for dependency in member["dependencies"]:
                if dependency.get("path") and not Path(dependency["path"]).resolve().is_relative_to(source_root):
                    raise GapError(
                        f"Path dependency {dependency['name']} is outside the workspace. "
                        "Use a self-contained project copy, or let an agent prepare one."
                    )
    if configuration is not None:
        configuration.check(package, source_root)
        print(f"Using build configuration: {configuration.path} ({configuration.selection}).", flush=True)
    configured = configuration.save(output, env) if configuration is not None else None
    if source_root in {Path.home(), Path(source_root.anchor)}:
        raise GapError("Refusing to copy a filesystem or home root.")
    workspace = output / "workspace"
    print(f"Preparing an isolated build of {package['name'] if package else path.name}...", flush=True)
    hashes = snapshot(source_root, workspace, output)
    copied_root = workspace / root.relative_to(source_root)
    record = {
        "schema": "verus-gap/project-v2", "input": str(path),
        "input_kind": input_kind,
        "baseline_kind": "verus_compile" if input_kind == "verus" else "rust",
        "workspace_root": str(source_root),
        "root_source": str(root.relative_to(source_root)),
        "package": package["name"] if package else root.stem,
        "target": target["name"] if target else root.stem,
        "manifest_relative": str(manifest_path.relative_to(source_root)) if manifest_path else None,
        "ghost_dependencies_required": ghost_dependencies,
        "build_configuration": configured,
        "scan_level": getattr(args, "scan_level", DEFAULT_SCAN_LEVEL),
        "cargo_args": cargo_args, "source_sha256": hashes,
        "verus": version, "verus_executable": str(verus),
        "tool_sha256": {
            file.name: hashlib.sha256(file.read_bytes()).hexdigest()
            for file in PACKAGE.glob("*.py")
        },
        "verus_artifacts_sha256": {
            name: hashlib.sha256((verus.parent / name).read_bytes()).hexdigest()
            for name in ("verus", "rust_verify", "libvstd.rlib", "vstd.vir")
            if (verus.parent / name).is_file()
        },
        "checks": {"smt": False, "proof_lifetimes": True, "native_codegen": False},
        "baseline_passed": False, "workspace_restored": False,
    }
    if not standalone and path.suffix == ".rs":
        record["default_files"] = [str(path.relative_to(source_root))]
    elif not standalone and path.is_dir() and path != Path(package["manifest_path"]).parent and path.is_relative_to(
        Path(package["manifest_path"]).parent
    ):
        record["default_files"] = [str(path.relative_to(source_root)) + "/*.rs"]
    write_json(output / "project.json", record)
    archive_tools(output, record["tool_sha256"])
    if standalone:
        compiler = [
            str(copied_root), "--edition", "2021", "--crate-type=lib",
            "--crate-name", "gap_subject", *cargo_args,
        ]
        baseline_command = [
            "rustc", *compiler, "--emit=dep-info,metadata",
            "--out-dir", str(output / "build"),
        ]
        if input_kind == "verus":
            baseline_command = [
                str(verus), "--no-verify", "--compile", *baseline_command[1:],
            ]
        (output / "build").mkdir()
        baseline_env = env
    else:
        baseline_env = dict(
            env, CARGO_TARGET_DIR=str(output / "build"), CARGO_INCREMENTAL="0",
            RUSTC_WRAPPER=str(capture_wrapper(output)), RUSTC_WORKSPACE_WRAPPER="",
            VERUS_GAP_ROOT=str(copied_root), VERUS_GAP_CAPTURE=str(output / "capture.json"),
        )
        kind = ["--bin", target["name"]] if "bin" in target["kind"] else ["--lib"]
        copied_manifest = workspace / Path(package["manifest_path"]).relative_to(source_root)
        baseline_command = [
            "cargo", "check", "--manifest-path", str(copied_manifest), "-p", package["name"],
            *kind, "--message-format=json", *cargo_args,
        ]
    baseline = run(baseline_command, workspace, baseline_env, deadline - time.monotonic())
    if baseline["exit_code"] and "rust-src" in baseline["stderr"] and "does not exist" in baseline["stderr"]:
        record["baseline_before_rust_src"] = save_run(output, "baseline-initial", baseline)
        component = run(
            ["rustup", "component", "add", "rust-src", "--toolchain", version["toolchain"]],
            workspace, env, min(180, deadline - time.monotonic()),
        )
        record["rust_src_install"] = save_run(output, "rust-src-install", component)
        write_json(output / "project.json", record)
        if component["exit_code"] or component["timed_out"]:
            raise GapError("The required matching Rust source component could not be installed; see rust-src-install.stderr.")
        baseline = run(baseline_command, workspace, baseline_env, deadline - time.monotonic())
    record["baseline"] = save_run(output, "baseline", baseline)
    write_json(output / "project.json", record)
    if baseline["exit_code"] or baseline["timed_out"]:
        build = "Verus compile" if input_kind == "verus" else "Rust build"
        raise GapError(
            f"The unchanged {build} failed; no Verus limitation was inferred. "
            "See baseline.stdout and baseline.stderr for the actual build error.\n"
            + CONFIGURATION_HELP
        )
    record["baseline_passed"] = True
    write_json(output / "project.json", record)
    lock = workspace / "Cargo.lock"
    if lock.is_file():
        record["dependency_lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
    if standalone:
        cwd = workspace
        captured = {
            "args": [*compiler, "--emit=dep-info,metadata", "--out-dir", str(output / "build")],
            "cwd": str(cwd),
        }
    else:
        capture_path = output / "capture.json"
        if not capture_path.is_file():
            raise GapError("Cargo did not invoke the chosen crate; no compiler context was captured.")
        captured = json.loads(capture_path.read_text())
        cwd = Path(captured["cwd"])
        env.update(captured["environment"])
        compiler = verus_args(captured["args"])
    record["crate_name"] = next(
        (compiler[index + 1] for index, value in enumerate(compiler) if value == "--crate-name"),
        record["target"].replace("-", "_"),
    )
    cfg_values = compiler_cfg(compiler, cwd, env, output, deadline - time.monotonic())
    record["rust_cfg"] = cfg_values
    source_cfg = cfg_values
    if input_kind == "verus":
        source_cfg = [*cfg_values, "verus_keep_ghost", "verus_keep_ghost_body", "verus_only"]
    copied_files = active_sources(captured, copied_root)
    outside = [str(file) for file in copied_files if not file.is_relative_to(workspace)]
    record["external_active_sources"] = outside
    record["source_cfg"] = source_cfg
    record["source_configuration_origin"] = "verus_root" if input_kind == "verus" else "native"
    scan_cfg = source_configuration(record)
    sources = {
        str(file.relative_to(workspace)): Source(
            str(file.relative_to(workspace)), file.read_bytes(), scan_cfg,
        )
        for file in copied_files if file.is_relative_to(workspace)
    }
    for name, source in sources.items():
        backup = output / "originals" / name
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(source.data)
    record["active_sources"] = {name: source.identity() for name, source in sources.items()}
    record["native_compiler_args"] = verus_args(captured["args"], keep_support=True)
    for index, argument in enumerate(compiler[:-1]):
        if argument == "--extern" and "=" in compiler[index + 1]:
            artifact = Path(compiler[index + 1].split("=", 1)[1])
            if not artifact.is_absolute():
                artifact = cwd / artifact
            if artifact.is_file() and not artifact.resolve().is_relative_to(workspace):
                record.setdefault("dependency_artifacts_sha256", {})[str(artifact.resolve())] = (
                    hashlib.sha256(artifact.read_bytes()).hexdigest()
                )
    record["native_environment"] = captured.get("environment", {})
    command = [
        str(verus), *compiler, "--no-verify", "--error-format=json", "--color=never",
        "--out-dir", str(output / "verus-build"),
        "--check-cfg=cfg(verus_keep_ghost)", "--check-cfg=cfg(verus_keep_ghost_body)",
        "--check-cfg=cfg(verus_only)", "--cap-lints=warn",
    ]
    record["compiler_args"] = compiler
    record["adaptation_lint_policy"] = "Verus macro-generated Rust lints are warnings; ordinary baseline lints were unchanged."
    project = Project(output, workspace, cwd, env, command, sources, record, deadline)
    project.save()
    save_replay(project)
    check_preparation(project)
    from .target import configure_support
    configure_support(project, verus, compiler)
    if not standalone:
        from .dependencies import configure_dependencies
        configure_dependencies(project, package, meta)
    save_replay(project)
    label = "Verus compile" if input_kind == "verus" else "Rust"
    print(f"{label} baseline passed; {len(sources)} active files, "
          f"{sum(len(source.functions) for source in sources.values())} enumerable functions.", flush=True)
    return project


def archive_tools(output: Path, hashes: dict):
    revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:12]
    destination = output / "scanner" / revision
    destination.mkdir(parents=True, exist_ok=True)
    for name, digest in hashes.items():
        data = (PACKAGE / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise GapError("Scanner source changed while the build context was being captured. Retry with a stable tool directory.")
        saved = destination / name
        if not saved.exists():
            saved.write_bytes(data)


def source_configuration(record: dict) -> Cfg:
    # Old reports used a ghost-enabled source view; keep their evidence interpretable.
    values = record.get("source_cfg", [
        *record["rust_cfg"], "verus_keep_ghost", "verus_keep_ghost_body", "verus_only",
    ])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise GapError("The saved source configuration is invalid.")
    return Cfg(values)


def check_preparation(project: Project):
    output = project.output / "preparation-check" / str(time.time_ns())
    output.mkdir(parents=True)
    verified = project.manifest.get("input_kind") == "verus"
    cfg = source_configuration(project.manifest) if verified else Cfg(project.manifest["rust_cfg"])
    prepared, patches = [], []
    try:
        for name, source in project.sources.items():
            model = Source(name, source.data, cfg)
            rendered = model.render(set(), set(), wrap=False)
            if rendered.data != source.data:
                (project.workspace / name).write_bytes(rendered.data)
                prepared.append(name)
                patches.append(rendered.patch())
        (output / "overlay.diff").write_text("".join(patches))
        env = dict(project.env, **project.manifest["native_environment"])
        driver = [project.command[0], "--no-verify", "--compile"] if verified else ["rustc"]
        result = run([
            *driver, *project.manifest["native_compiler_args"],
            "--emit=metadata", "--cap-lints=warn", "--out-dir", str(output),
        ], project.cwd, env, project.deadline - time.monotonic())
        project.manifest["preparation_check"] = {
            **save_run(output, "rustc", result),
            "directory": str(output.relative_to(project.output)),
            "transformation_sha256": project.manifest["tool_sha256"]["source.py"],
        }
        project.save()
        if result["exit_code"] or result["timed_out"]:
            raise GapError(
                "Syntax-only preparation failed its unchanged-build recheck. "
                f"No preparation errors were counted as Verus limitations; see {output / 'rustc.stderr'}."
            )
    finally:
        for name in prepared:
            (project.workspace / name).write_bytes(project.sources[name].data)


def save_replay(project: Project):
    configuration = project.manifest.get("build_configuration") or {}
    recorded = set(project.manifest.get("replay_environment_keys", [])) | set(
        configuration.get("environment", {})
    )
    write_json(project.output / "replay.json", {
        "cwd": str(project.cwd), "command": project.command,
        "environment": {
            key: value for key, value in project.env.items()
            if (key.startswith(("CARGO_", "DEP_")) or "__VERUS_DRIVER_" in key or key in {
                "OUT_DIR", "RUSTUP_TOOLCHAIN", "RUSTC_BOOTSTRAP", "RUST_MIN_STACK",
            } or key in recorded) and not any(part in key.upper() for part in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL"))
            and key != "CARGO_MAKEFLAGS"
        },
    })


def resume(output: Path, timeout: float) -> Project:
    record = json.loads((output / "project.json").read_text())
    if record["schema"] != "verus-gap/project-v2" or not record["baseline_passed"]:
        raise GapError("This result does not contain a successful reusable build baseline.")
    if "source_cfg" in record and record.get("support_mode") == "cargo_verus_ghost_dependencies" and (
        record.get("source_configuration_origin") != "cargo_verus"
    ):
        raise GapError("Saved source cfg does not match its Cargo-Verus dependencies. Start a new scan.")
    validate_saved_configuration(output, record.get("build_configuration"))
    replay = json.loads((output / "replay.json").read_text())
    configuration = record.get("build_configuration")
    if configuration is not None and any(
        replay["environment"].get(key) != value for key, value in configuration["environment"].items()
    ):
        raise GapError("The saved replay no longer matches its build configuration environment.")
    record["replay_environment_keys"] = sorted(replay["environment"])
    verus = Path(record["verus_executable"])
    if identity(verus) != record["verus"]:
        raise GapError("The Verus installation changed; start a new scan instead of mixing compiler versions.")
    for name, digest in record["verus_artifacts_sha256"].items():
        if hashlib.sha256((verus.parent / name).read_bytes()).hexdigest() != digest:
            raise GapError(f"Verus support artifact changed: {name}. Start a new scan.")
    origin = Path(record["workspace_root"])
    for name, digest in record["source_sha256"].items():
        file = origin / name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise GapError(f"Input changed since this baseline: {name}. Start a new scan.")
    cfg = source_configuration(record)
    sources = {}
    for name, source_record in record["active_sources"].items():
        raw = (output / "originals" / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source_record["sha256"]:
            raise GapError(f"Saved original source changed: {name}.")
        sources[name] = Source(name, raw, cfg)
        (output / "workspace" / name).write_bytes(raw)
    env = dict(os.environ, **replay["environment"])
    for key in ("RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "CARGO_MAKEFLAGS"):
        env.pop(key, None)
    tools = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in PACKAGE.glob("*.py")}
    if record["tool_sha256"] != tools:
        record.setdefault("previous_scanner_revisions", []).append(record["tool_sha256"])
        record["tool_sha256"] = tools
    project = Project(
        output, output / "workspace", Path(replay["cwd"]), env, replay["command"],
        sources, record, time.monotonic() + timeout,
    )
    archive_tools(output, tools)
    if "native_compiler_args" not in record:
        capture = output / "capture.json"
        captured = json.loads(capture.read_text()) if capture.is_file() else {
            "args": record["baseline"]["command"][1:], "environment": {},
        }
        record["native_compiler_args"] = verus_args(captured["args"], keep_support=True)
        record["native_environment"] = captured.get("environment", {})
    if record.get("preparation_check", {}).get("transformation_sha256") != tools["source.py"]:
        check_preparation(project)
    if not record.get("support_mode"):
        from .target import configure_support
        configure_support(project, verus, record["compiler_args"])
        save_replay(project)
    if record.get("ghost_dependencies_required") is None and record["baseline"]["command"][0] == "rustc":
        record["ghost_dependencies_required"] = False
    if record.get("ghost_dependencies_required") is None:
        relative = record.get("manifest_relative")
        if relative:
            original_manifest = origin / relative
        else:
            input_path = Path(record["input"])
            original_manifest = discover_manifest(input_path.parent if input_path.suffix == ".rs" else input_path)
        if original_manifest.is_file():
            meta = metadata(original_manifest, env, project.deadline - time.monotonic())
            package = select_package(meta, original_manifest, record["package"])
            from .dependencies import requires_ghost_dependencies
            record["ghost_dependencies_required"] = requires_ghost_dependencies(package, meta)
            record["manifest_relative"] = str(original_manifest.relative_to(origin))
    if record.get("ghost_dependencies_required") and not record.get("dependency_setup", {}).get("completed"):
        from .dependencies import configure_dependencies
        original_manifest = origin / record["manifest_relative"]
        meta = metadata(original_manifest, env, project.deadline - time.monotonic())
        package = select_package(meta, original_manifest, record["package"])
        configure_dependencies(project, package, meta)
    if record.get("support_mode") == "cargo_verus_ghost_dependencies":
        if "--emit=metadata" not in project.command:
            project.command.append("--emit=metadata")
        record["dependency_setup"]["probe_emit"] = "metadata"
        for name in list(record.get("dependency_artifacts_sha256", {})):
            vir = Path(name).with_suffix(".vir")
            if vir.is_file():
                record["dependency_artifacts_sha256"].setdefault(
                    str(vir), hashlib.sha256(vir.read_bytes()).hexdigest(),
                )
        save_replay(project)
    for name, digest in record.get("target_support_sha256", {}).items():
        if hashlib.sha256((output / "support" / name).read_bytes()).hexdigest() != digest:
            raise GapError(f"Target-specific Verus artifact changed: {name}.")
    for name, digest in record.get("dependency_artifacts_sha256", {}).items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise GapError(f"Captured dependency artifact changed: {name}.")
    project.verify_originals()
    if configuration is not None:
        print(f"Reusing saved build configuration: {configuration['path']}.", flush=True)
    print("Reusing the unchanged Rust build and captured dependencies.", flush=True)
    return project
