"""Optional project build settings, selected by declarative Cargo metadata."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .gap import GapError, PACKAGE


PROFILES = PACKAGE / "profiles"
CONFIGURATION_HELP = (
    "If this project needs custom build settings, provide --config PATH; "
    "see the optional build configuration section in README.md."
)
SENSITIVE_PARTS = ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")
RESERVED_ENVIRONMENT = {
    "RUSTUP_TOOLCHAIN", "RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
    "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_TARGET_DIR", "CARGO_MAKEFLAGS",
    "OUT_DIR", "CARGO_MANIFEST_DIR", "CARGO_MANIFEST_PATH", "CARGO_CRATE_NAME",
    "CARGO_BIN_NAME", "CARGO_PRIMARY_PACKAGE",
}
RESERVED_CARGO_ARGUMENTS = {
    "--manifest-path", "--target-dir", "--package", "-p", "--workspace", "--all",
    "--lib", "--bin", "--bins", "--example", "--examples", "--test", "--tests",
    "--bench", "--benches", "--all-targets",
}


def validate_cargo_arguments(values: list[str] | tuple[str, ...]):
    targets = 0
    for value in values:
        option = value.split("=", 1)[0]
        if option in RESERVED_CARGO_ARGUMENTS or value.startswith("-p") and value != "--profile":
            raise GapError(
                f"Cargo target override {value}: select the crate with --package or --bin instead."
            )
        if value == "--":
            raise GapError("Cargo build arguments must not contain another -- separator.")
        targets += option == "--target"
    if targets > 1:
        raise GapError("A scan captures one Cargo target; provide only one --target.")


def merge_cargo_arguments(defaults: tuple[str, ...], explicit: list[str]) -> list[str]:
    features = {"--features", "--all-features", "--no-default-features"}
    profiles = {"--profile", "--release"}
    replaced = set()
    for value in explicit:
        option = value.split("=", 1)[0]
        if option in features:
            replaced.update(features)
        elif option in profiles:
            replaced.update(profiles)
        elif option == "--target":
            replaced.add(option)
    result, index = [], 0
    while index < len(defaults):
        value = defaults[index]
        option = value.split("=", 1)[0]
        if option in replaced:
            index += 2 if option in {"--features", "--target", "--profile"} and "=" not in value else 1
        else:
            result.append(value)
            index += 1
    result.extend(explicit)
    validate_cargo_arguments(result)
    return result


def table(document: dict, name: str, allowed: set[str]) -> dict:
    value = document.get(name, {})
    if not isinstance(value, dict):
        raise GapError(f"{name} must be a TOML table.")
    unknown = set(value) - allowed
    if unknown:
        raise GapError(f"Unknown {name} setting(s): {', '.join(sorted(unknown))}.")
    return value


def optional_string(document: dict, name: str) -> str | None:
    if name not in document:
        return None
    value = document[name]
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise GapError(f"{name} must be a nonempty string.")
    return value


def strings(document: dict, name: str) -> tuple[str, ...]:
    value = document.get(name, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or "\0" in item for item in value
    ):
        raise GapError(f"{name} must be an array of nonempty strings.")
    return tuple(value)


@dataclass(frozen=True)
class Match:
    package: str | None
    homepage: str | None
    repository: str | None
    files: tuple[str, ...]

    def applies(self, package: dict | None, workspace: Path) -> bool:
        if package is None:
            return False
        workspace = workspace.resolve()
        for field, expected in (
            ("name", self.package), ("homepage", self.homepage), ("repository", self.repository),
        ):
            if expected is not None and package.get(field) != expected:
                return False
        return all(
            (workspace / name).resolve().is_relative_to(workspace)
            and (workspace / name).is_file()
            for name in self.files
        )


@dataclass(frozen=True)
class EnvironmentValue:
    value: str
    force: bool


@dataclass(frozen=True)
class BuildConfiguration:
    path: Path
    data: bytes
    selection: str
    package: str | None
    binary: str | None
    arguments: tuple[str, ...]
    rustflags: tuple[str, ...]
    environment: dict[str, EnvironmentValue]
    match: Match | None

    def check(self, package: dict | None, workspace: Path):
        if self.match is not None and not self.match.applies(package, workspace):
            subject = package["name"] if package is not None else "standalone source"
            raise GapError(
                f"Build configuration {self.path} does not match {subject}. "
                "Choose an applicable --config, or use --no-config and explicit build options."
            )

    def apply_environment(self, environment: dict[str, str]):
        for key, setting in self.environment.items():
            if setting.force or key not in environment:
                environment[key] = setting.value
        if self.rustflags:
            if "CARGO_ENCODED_RUSTFLAGS" in environment:
                previous = environment["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
                if previous == [""]:
                    previous = []
            else:
                previous = environment.get("RUSTFLAGS", "").split()
            environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join([*previous, *self.rustflags])

    def save(self, output: Path, environment: dict[str, str]) -> dict:
        snapshot = "build-config.toml"
        (output / snapshot).write_bytes(self.data)
        keys = set(self.environment)
        if self.rustflags:
            keys.add("CARGO_ENCODED_RUSTFLAGS")
        return {
            "path": str(self.path), "selection": self.selection,
            "snapshot": snapshot, "sha256": hashlib.sha256(self.data).hexdigest(),
            "environment": {key: environment[key] for key in sorted(keys)},
        }


def load_configuration(path: Path, selection: str = "explicit") -> BuildConfiguration:
    path = path.expanduser().resolve()
    try:
        data = path.read_bytes()
        document = tomllib.loads(data.decode("utf-8"))
        unknown = set(document) - {"version", "cargo", "env", "match"}
        if unknown:
            raise GapError(f"Unknown configuration setting(s): {', '.join(sorted(unknown))}.")
        if type(document.get("version")) is not int or document["version"] != 1:
            raise GapError("Configuration version must be the integer 1.")
        cargo = table(document, "cargo", {"package", "bin", "args", "rustflags"})
        package_name = optional_string(cargo, "package")
        arguments = strings(cargo, "args")
        rustflags = strings(cargo, "rustflags")
        if any("\x1f" in value for value in rustflags):
            raise GapError("cargo.rustflags entries must not contain the encoded-flag separator.")
        validate_cargo_arguments(arguments)
        for index, value in enumerate(arguments):
            if value in {"--target", "--features", "--profile", "-Z"} and (
                index + 1 == len(arguments) or arguments[index + 1].startswith("-")
            ):
                raise GapError(f"Cargo argument {value} requires a value.")
        match = None
        if "match" in document:
            conditions = table(document, "match", {"package", "homepage", "repository", "files"})
            files = strings(conditions, "files")
            for name in files:
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
                    raise GapError("match.files must contain paths inside the Cargo workspace.")
            match = Match(
                optional_string(conditions, "package"), optional_string(conditions, "homepage"),
                optional_string(conditions, "repository"), files,
            )
            if match.package is None or not (match.homepage or match.repository or match.files):
                raise GapError("match requires a package and at least one homepage, repository, or file condition.")
            if package_name is not None and package_name != match.package:
                raise GapError("cargo.package must agree with match.package.")
        environment = document.get("env", {})
        if not isinstance(environment, dict):
            raise GapError("env must be a TOML table.")
        settings = {}
        for key, value in environment.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise GapError(f"Invalid environment variable name: {key}.")
            if key in RESERVED_ENVIRONMENT or key.startswith((
                "VERUS_GAP_", "__VERUS_DRIVER_", "CARGO_PKG_", "CARGO_FEATURE_", "DEP_",
            )):
                raise GapError(f"Environment variable {key} is scanner/Cargo-controlled; use cargo.rustflags for Rust flags.")
            if any(part in key.upper() for part in SENSITIVE_PARTS):
                raise GapError(f"Do not put credentials in build configurations: {key}; inherit them from the shell.")
            if isinstance(value, str):
                setting = EnvironmentValue(value, False)
            elif isinstance(value, dict):
                unknown = set(value) - {"value", "force"}
                if unknown or not isinstance(value.get("value"), str) or type(value.get("force", False)) is not bool:
                    raise GapError(f"env.{key} must be a string or {{ value = \"...\", force = true/false }}.")
                setting = EnvironmentValue(value["value"], value.get("force", False))
            else:
                raise GapError(f"env.{key} must be a string or an environment value table.")
            if "\0" in setting.value:
                raise GapError(f"env.{key} contains a NUL byte.")
            settings[key] = setting
        return BuildConfiguration(
            path, data, selection, package_name, optional_string(cargo, "bin"),
            arguments, rustflags, settings, match,
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, GapError) as error:
        raise GapError(f"Invalid build configuration {path}: {error}") from error


def workspace_configuration(workspace: Path) -> BuildConfiguration | None:
    path = workspace / "verus-gap.toml"
    return load_configuration(path, "workspace") if path.exists() or path.is_symlink() else None


def automatic_configuration(workspace: Path, package: dict) -> BuildConfiguration | None:
    matches = []
    for path in sorted(PROFILES.glob("*.toml")):
        configuration = load_configuration(path, "automatic")
        if configuration.match is not None and configuration.match.applies(package, workspace):
            matches.append(configuration)
    if len(matches) > 1:
        paths = ", ".join(str(configuration.path) for configuration in matches)
        raise GapError(
            f"Multiple build configurations match {package['name']}: {paths}. "
            "Choose one with --config PATH, or disable selection with --no-config."
        )
    return matches[0] if matches else None


def validate_saved_configuration(output: Path, configuration: dict | None):
    if configuration is None:
        return
    if not isinstance(configuration, dict) or (
        configuration.get("snapshot") != "build-config.toml"
        or not isinstance(configuration.get("sha256"), str)
        or not isinstance(configuration.get("path"), str)
        or not isinstance(configuration.get("selection"), str)
        or configuration.get("selection") not in {"explicit", "workspace", "automatic"}
        or not isinstance(configuration.get("environment"), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in configuration["environment"].items()
        )
    ):
        raise GapError("The saved build configuration record is invalid.")
    snapshot = output / "build-config.toml"
    if not snapshot.resolve().is_relative_to(output.resolve()):
        raise GapError("The saved build configuration leaves the result directory.")
    saved = load_configuration(snapshot)
    if hashlib.sha256(saved.data).hexdigest() != configuration["sha256"]:
        raise GapError("The saved build configuration changed; start a new scan.")
    keys = set(saved.environment)
    if saved.rustflags:
        keys.add("CARGO_ENCODED_RUSTFLAGS")
    if set(configuration["environment"]) != keys:
        raise GapError("The saved build configuration environment is incomplete.")
