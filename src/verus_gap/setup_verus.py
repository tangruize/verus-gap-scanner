"""Install a complete official Verus release and test the matching Rust toolchain."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .gap import GapError, HOME, identity, run

DEFAULT_RELEASE = "latest"
RELEASE_PATTERN = re.compile(r"0\.\d{4}\.\d{2}\.\d{2}\.[a-f0-9]+")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "verus-gap-setup"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def release_metadata(requested: str) -> tuple[str, dict]:
    if requested == "latest":
        url = "https://api.github.com/repos/verus-lang/verus/releases/latest"
    else:
        if not RELEASE_PATTERN.fullmatch(requested):
            raise GapError("Use 'latest' or a release version such as 0.2026.10.04.426d8b0.")
        url = (
            "https://api.github.com/repos/verus-lang/verus/releases/tags/"
            + urllib.parse.quote("release/" + requested, safe="")
        )
    metadata = json.loads(fetch(url))
    tag = metadata.get("tag_name", "")
    resolved = tag.removeprefix("release/")
    if not RELEASE_PATTERN.fullmatch(resolved) or tag != "release/" + resolved:
        raise GapError(f"GitHub returned an unexpected Verus release tag: {tag!r}.")
    if requested != "latest" and resolved != requested:
        raise GapError(f"Requested Verus {requested}, but GitHub returned {resolved}.")
    return resolved, metadata


def install(requested: str) -> Path:
    system, machine = platform.system(), platform.machine()
    suffix = {
        ("Linux", "x86_64"): "x86-linux",
        ("Darwin", "x86_64"): "x86-macos",
        ("Darwin", "arm64"): "arm64-macos",
    }.get((system, machine))
    if not suffix:
        raise GapError("No supported official binary for this platform. Build Verus, then run ./setup --verus PATH.")
    release, metadata = release_metadata(requested)
    destination = HOME / ".tools" / release
    if (destination / "verus").is_file():
        return destination / "verus"
    asset = next((asset for asset in metadata["assets"]
                  if asset["name"] == f"verus-{release}-{suffix}.zip"), None)
    if not asset:
        raise GapError(f"Official release {release} has no binary for {suffix}.")
    print(f"Downloading {asset['name']} from verus-lang/verus...", flush=True)
    archive = fetch(asset["browser_download_url"])
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    if not asset.get("digest") or asset["digest"] != digest:
        raise GapError("Missing or mismatched GitHub asset SHA-256; installation not activated.")
    HOME.joinpath(".tools").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=HOME / ".tools") as temporary:
        staging = Path(temporary)
        zipped = staging / "release.zip"
        zipped.write_bytes(archive)
        with zipfile.ZipFile(zipped) as bundle:
            for member in bundle.infolist():
                path = staging / member.filename
                if not path.resolve().is_relative_to(staging.resolve()):
                    raise GapError("Release archive has an unsafe path.")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise GapError("Release archive has a symbolic link.")
                bundle.extract(member, staging)
                if path.is_file() and mode:
                    path.chmod(stat.S_IMODE(mode))
        executables = list(staging.glob("*/verus"))
        if len(executables) != 1:
            raise GapError("Release archive does not contain exactly one Verus distribution.")
        shutil.move(str(executables[0].parent), destination)
    (destination / "download.json").write_text(json.dumps({
        "requested": requested, "release": release, "tag": metadata["tag_name"],
        "url": asset["browser_download_url"], "sha256": digest,
    }, indent=2) + "\n")
    return destination / "verus"


def ensure_toolchain(verus: Path) -> None:
    result = run([str(verus), "--version", "--output-json"], verus.parent, dict(os.environ), 30)
    if result["exit_code"] == 0:
        return
    required = re.search(r"required rust toolchain ([^\s]+) not found", result["stderr"] + result["stdout"])
    if not required:
        raise GapError("Verus could not start:\n" + result["stderr"] + result["stdout"])
    toolchain = required.group(1)
    print(f"Installing matching Rust toolchain {toolchain} (not changing your default)...", flush=True)
    subprocess.run(["rustup", "toolchain", "install", toolchain, "--profile", "minimal"], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install and smoke-test a complete Verus distribution.")
    parser.add_argument("--verus", help="validate/register an existing executable instead of downloading")
    parser.add_argument("--release", default=DEFAULT_RELEASE,
                        help="official release version, or 'latest' (default)")
    args = parser.parse_args(argv)
    try:
        if not shutil.which("rustup") or not shutil.which("cargo"):
            raise GapError("Rustup/Cargo are required. Install them from https://rustup.rs, then rerun ./setup.")
        verus = Path(args.verus).expanduser().resolve() if args.verus else install(args.release)
        ensure_toolchain(verus)
        version = identity(verus)
        env = dict(os.environ, RUSTUP_TOOLCHAIN=version["toolchain"])
        with tempfile.TemporaryDirectory(prefix="verus-gap-smoke-") as temporary:
            directory = Path(temporary)
            fixture = directory / "smoke.rs"
            fixture.write_text("use vstd::prelude::*;\nverus! { proof fn smoke() { assert(1 + 1 == 2); } }\n")
            result = run([str(verus), str(fixture), "--crate-type=lib", "--no-cheating"],
                         directory, env, 120)
            if result["exit_code"] or result["timed_out"] or "1 verified, 0 errors" not in result["stdout"]:
                raise GapError("Verus/vstd/Z3 smoke failed:\n" + result["stderr"] + result["stdout"])
        tools = HOME / ".tools"
        tools.mkdir(parents=True, exist_ok=True)
        record = {"verus": str(verus), "version": version, "smoke": "1 verified, 0 errors"}
        temporary_record = tools / "installation.json.tmp"
        temporary_record.write_text(json.dumps(record, indent=2) + "\n")
        temporary_record.replace(tools / "installation.json")
        print(f"Ready: Verus {version['version']} / Rust {version['toolchain']}")
        print("Try: ./verus-gap demo  (exit 1 is expected: it shows missing Instant specifications)")
        return 0
    except (GapError, OSError, subprocess.CalledProcessError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"setup error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
