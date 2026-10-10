"""Prove compiler/support compatibility before interpreting project diagnostics."""

from __future__ import annotations

import hashlib
import io
import platform
import tarfile
import time
import urllib.request
from pathlib import Path

from .gap import GapError, HOME, run, save_run
from .project import Project, write_json


def context_flags(compiler: list[str]) -> list[str]:
    flags, index = [], 0
    while index < len(compiler):
        arg = compiler[index]
        if arg in {"--target", "--sysroot", "-L", "-Z"}:
            flags.extend(compiler[index:index + 2])
            index += 2
        elif arg == "--extern":
            value = compiler[index + 1]
            name = value.split("=", 1)[0].rsplit(":", 1)[-1]
            if name in {"core", "alloc", "std", "compiler_builtins"}:
                flags.extend([arg, value])
            index += 2
        elif arg == "-C":
            if compiler[index + 1].split("=", 1)[0] in {
                "target-cpu", "target-feature", "relocation-model", "panic",
            }:
                flags.extend(compiler[index:index + 2])
            index += 2
        elif arg.startswith(("--target=", "--sysroot=", "-L", "-Z", "-Ctarget-", "-Crelocation-model=", "-Cpanic=")):
            flags.append(arg)
            index += 1
        else:
            index += 1
    return flags


def official_sources(commit: str) -> Path:
    destination = HOME / ".tools/sources" / commit
    if (destination / "download.json").is_file():
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    url = f"https://codeload.github.com/verus-lang/verus/tar.gz/{commit}"
    request = urllib.request.Request(url, headers={"User-Agent": "verus-gap-target-setup"})
    with urllib.request.urlopen(request, timeout=120) as response:
        archive = response.read()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            relative = Path(*Path(member.name).parts[1:])
            if not str(relative).startswith(("source/builtin/", "source/vstd/")):
                continue
            path = destination / relative
            if not path.resolve().is_relative_to(destination.resolve()) or member.issym() or member.islnk():
                raise GapError("Official source archive contains an unsafe member.")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                reader = bundle.extractfile(member)
                if reader is None:
                    raise GapError(f"Cannot extract official Verus source: {member.name}")
                path.write_bytes(reader.read())
    write_json(destination / "download.json", {
        "url": url, "commit": commit, "sha256": hashlib.sha256(archive).hexdigest(),
    })
    return destination


def configure_support(project: Project, verus: Path, compiler: list[str]):
    output = project.output / "support"
    output.mkdir(exist_ok=True)
    fixture = output / "smoke.rs"
    fixture.write_text("#![no_std]\nvstd::prelude::verus! { pub fn smoke() -> u32 { 1 } }\n")
    flags = context_flags(compiler)
    base = [
        str(verus), str(fixture), "--crate-type=lib", "--edition=2021", "--no-verify",
        *flags, "--out-dir", str(output / "smoke-build"),
    ]
    initial = run(base, project.cwd, project.env, min(60, project.deadline - time.monotonic()))
    project.manifest["support_smoke"] = save_run(output, "native-smoke", initial)
    project.save()
    if initial["exit_code"] == 0 and not initial["timed_out"]:
        project.manifest["support_mode"] = "official_native_bundle"
        project.save()
        return
    text = initial["stderr"] + initial["stdout"]
    if not any(phrase in text for phrase in ("couldn't find crate", "can't find crate")) or not any(
        arg.startswith("--target") for arg in flags
    ):
        raise GapError(
            "The Verus/support compiler smoke failed before scanning source; "
            "see support/native-smoke.stderr. No application limitation was inferred."
        )
    print("Building matching Verus builtins and no_std+alloc vstd for the captured target...", flush=True)
    source = official_sources(project.manifest["verus"]["commit"])
    env = dict(project.env, RUSTC_BOOTSTRAP="1", RUST_MIN_STACK="10485760")
    builtin = run([
        "rustc", str(source / "source/builtin/src/lib.rs"), "--crate-name=verus_builtin",
        "--edition=2018", "--crate-type=rlib", "--cfg=verus_keep_ghost",
        *flags, "--out-dir", str(output),
    ], project.cwd, env, min(120, project.deadline - time.monotonic()))
    project.manifest["target_builtin_build"] = save_run(output, "builtin", builtin)
    project.save()
    if builtin["exit_code"] or builtin["timed_out"]:
        raise GapError("Target-matching Verus builtin build failed; see support/builtin.stderr.")
    suffix = "dylib" if platform.system() == "Darwin" else "so"
    support = [
        "-L", f"dependency={output}", "-L", f"dependency={verus.parent}",
        "--extern", f"verus_builtin={output / 'libverus_builtin.rlib'}",
        "--extern", f"verus_builtin_macros={verus.parent / ('libverus_builtin_macros.' + suffix)}",
        "--extern", f"verus_state_machines_macros={verus.parent / ('libverus_state_machines_macros.' + suffix)}",
    ]
    vstd = run([
        str(verus), "--internal-test-mode", str(source / "source/vstd/vstd.rs"),
        "--is-vstd", "--crate-type=lib", "--edition=2021", "--crate-name=vstd",
        "--cfg=feature=\"alloc\"", "--compile", "--no-verify",
        "--export", str(output / "vstd.vir"), "--out-dir", str(output),
        *flags, *support,
    ], project.cwd, env, min(300, project.deadline - time.monotonic()))
    project.manifest["target_vstd_build"] = save_run(output, "vstd", vstd)
    project.save()
    if vstd["exit_code"] or vstd["timed_out"]:
        raise GapError(
            "Target-matching vstd could not be built without weakening proof lifetime checks. "
            "See support/vstd.stderr; the target has not been silently replaced with the host."
        )
    support.extend([
        "--extern", f"vstd={output / 'libvstd.rlib'}",
        "--import", f"vstd={output / 'vstd.vir'}",
    ])
    smoke = run([
        str(verus), "--internal-test-mode", *base[1:], *support,
    ], project.cwd, env, min(60, project.deadline - time.monotonic()))
    project.manifest["support_smoke"] = save_run(output, "target-smoke", smoke)
    project.save()
    if smoke["exit_code"] or smoke["timed_out"]:
        raise GapError("Target-matching support smoke failed; see support/target-smoke.stderr.")
    project.command = [str(verus), "--internal-test-mode", *project.command[1:], *support]
    project.env = env
    project.manifest["support_mode"] = "target_matching_no_std_alloc"
    project.manifest["target_support_sha256"] = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in ("libverus_builtin.rlib", "libvstd.rlib", "vstd.vir")
    }
    project.save()
