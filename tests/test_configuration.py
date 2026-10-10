from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verus_gap.configuration import (
    PROFILES, automatic_configuration, load_configuration, merge_cargo_arguments,
    validate_saved_configuration, workspace_configuration,
)
from verus_gap.gap import GapError, find_verus
from verus_gap.project import prepare, resume, save_replay


class ConfigurationTests(unittest.TestCase):
    def load(self, directory: str, text: str):
        path = Path(directory) / "recipe.toml"
        path.write_text(text)
        return load_configuration(path)

    def package(self, workspace: Path, name="example", homepage="https://example.org/project"):
        return {"name": name, "homepage": homepage, "manifest_path": str(workspace / "Cargo.toml")}

    def test_environment_defaults_forced_values_and_tokenized_rustflags(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = self.load(directory, (
                'version = 1\n[cargo]\nrustflags = ["-C", "panic=abort", "--cfg", "value=\\"a b\\""]\n'
                '[env]\nBUILD_MODE = "default"\nREQUIRED = { value = "new", force = true }\n'
            ))
            environment = {"BUILD_MODE": "existing", "REQUIRED": "old", "RUSTFLAGS": "-C opt-level=1"}
            configuration.apply_environment(environment)
            self.assertEqual(environment["BUILD_MODE"], "existing")
            self.assertEqual(environment["REQUIRED"], "new")
            self.assertEqual(environment["RUSTFLAGS"], "-C opt-level=1")
            self.assertEqual(environment["CARGO_ENCODED_RUSTFLAGS"].split("\x1f"), [
                "-C", "opt-level=1", "-C", "panic=abort", "--cfg", 'value="a b"',
            ])

    def test_encoded_inherited_flags_take_precedence_over_rustflags(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = self.load(directory, 'version=1\n[cargo]\nrustflags=["-C", "panic=abort"]\n')
            environment = {"CARGO_ENCODED_RUSTFLAGS": "--cfg\x1fexisting", "RUSTFLAGS": "--cfg ignored"}
            configuration.apply_environment(environment)
            self.assertEqual(environment["CARGO_ENCODED_RUSTFLAGS"], "--cfg\x1fexisting\x1f-C\x1fpanic=abort")
            environment = {"CARGO_ENCODED_RUSTFLAGS": ""}
            configuration.apply_environment(environment)
            self.assertEqual(environment["CARGO_ENCODED_RUSTFLAGS"], "-C\x1fpanic=abort")

    def test_configuration_is_retained_and_does_not_require_its_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = self.load(directory, 'version=1\n[env]\nBUILD_MODE="default"\n')
            environment = {}
            configuration.apply_environment(environment)
            output = Path(directory) / "results"
            output.mkdir()
            record = configuration.save(output, environment)
            configuration.path.unlink()
            validate_saved_configuration(output, record)
            self.assertEqual(record["selection"], "explicit")
            self.assertEqual(record["environment"], {"BUILD_MODE": "default"})
            self.assertEqual(record["sha256"], hashlib.sha256((output / "build-config.toml").read_bytes()).hexdigest())
            (output / "build-config.toml").write_text('version=1\n[env]\nBUILD_MODE="changed"\n')
            with self.assertRaisesRegex(GapError, "changed"):
                validate_saved_configuration(output, record)

    def test_saved_configuration_requires_its_exact_environment_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = self.load(directory, 'version=1\n[env]\nBUILD_MODE="value"\n')
            record = configuration.save(Path(directory), {"BUILD_MODE": "value"})
            record["environment"] = {}
            with self.assertRaisesRegex(GapError, "environment is incomplete"):
                validate_saved_configuration(Path(directory), record)
            with self.assertRaisesRegex(GapError, "record is invalid"):
                validate_saved_configuration(Path(directory), {"snapshot": "../elsewhere.toml"})
            validate_saved_configuration(Path(directory), None)

    def test_invalid_configuration_is_not_silently_ignored(self):
        cases = [
            "", "version=true", "version=2", 'version="1"', "version=1\nunknown=true",
            'version=1\ncargo="wrong"', "version=1\n[cargo]\nunknown=true",
            'version=1\n[cargo]\nargs="--features full"', 'version=1\n[cargo]\nargs=[true]',
            'version=1\n[cargo]\npackage=""', 'version=1\n[cargo]\nargs=["--target"]',
            'version=1\n[cargo]\nargs=["--target", "--features"]',
            'version=1\n[cargo]\nargs=["--target=a", "--target=b"]',
            'version=1\n[cargo]\nargs=["--manifest-path=elsewhere"]',
            'version=1\n[cargo]\nargs=["-pexample"]',
            'version=1\n[cargo]\nargs=["--"]',
            'version=1\n[match]\npackage="example"',
            'version=1\n[match]\nfiles=["Cargo.toml"]',
            'version=1\n[match]\npackage="example"\nfiles=["../Cargo.toml"]',
            'version=1\n[match]\npackage="example"\nfiles=["/Cargo.toml"]',
            'version=1\n[match]\npackage="example"\nfiles=["."]',
            'version=1\n[match]\npackage="example"\nfiles=["Cargo.toml"]\nunknown=true',
            'version=1\n[cargo]\npackage="other"\n[match]\npackage="example"\nfiles=["Cargo.toml"]',
            'version=1\n[env]\nBUILD_MODE=1',
            'version=1\n[env]\nBUILD_MODE={value="ok",force="false"}',
            'version=1\n[env]\nBUILD_MODE={value="ok",unknown=true}',
            'version=1\n[env]\nRUSTUP_TOOLCHAIN="other"',
            'version=1\n[env]\nRUSTC_WRAPPER="other"',
            'version=1\n[env]\nRUSTFLAGS="other"',
            'version=1\n[env]\nCARGO_ENCODED_RUSTFLAGS="other"',
            'version=1\n[env]\nVERUS_GAP_ROOT="other"',
            'version=1\n[env]\n__VERUS_DRIVER_VERIFY_example="other"',
            'version=1\n[env]\nOUT_DIR="other"',
            'version=1\n[env]\nCARGO_MANIFEST_DIR="other"',
            'version=1\n[env]\nCARGO_PKG_NAME="other"',
            'version=1\n[env]\nAPI_TOKEN="do not record"',
            'version=1\n[env]\napi_password="do not record"',
            'version=1\n[env]\n"bad-name"="value"',
            'version=1\n[cargo]\nrustflags=["\\u001f"]',
            'version=1\n[env]\nBUILD_MODE="\\u0000"',
            "not valid TOML [",
        ]
        with tempfile.TemporaryDirectory() as directory:
            for text in cases:
                with self.subTest(text=text), self.assertRaisesRegex(GapError, "Invalid build configuration"):
                    self.load(directory, text)
            with self.assertRaisesRegex(GapError, "Invalid build configuration"):
                load_configuration(Path(directory) / "missing.toml")
            path = Path(directory) / "recipe.toml"
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(GapError, "Invalid build configuration"):
                load_configuration(path)

    def test_explicit_target_features_and_profile_replace_only_corresponding_defaults(self):
        defaults = (
            "--no-default-features", "--features", "default",
            "--target", "target.json", "-Zbuild-std=core", "--profile=dev",
        )
        cases = [
            ([], list(defaults)),
            (["--features=chosen"], ["--target", "target.json", "-Zbuild-std=core", "--profile=dev", "--features=chosen"]),
            (["--all-features"], ["--target", "target.json", "-Zbuild-std=core", "--profile=dev", "--all-features"]),
            (["--no-default-features"], ["--target", "target.json", "-Zbuild-std=core", "--profile=dev", "--no-default-features"]),
            (["--target", "other"], ["--no-default-features", "--features", "default", "-Zbuild-std=core", "--profile=dev", "--target", "other"]),
            (["--release"], ["--no-default-features", "--features", "default", "--target", "target.json", "-Zbuild-std=core", "--release"]),
            (["--locked"], [*defaults, "--locked"]),
        ]
        for explicit, expected in cases:
            with self.subTest(explicit=explicit):
                self.assertEqual(merge_cargo_arguments(defaults, explicit), expected)
        self.assertEqual(defaults[0], "--no-default-features")

    def test_matching_uses_manifest_metadata_and_workspace_files_not_git_or_directory_name(self):
        with tempfile.TemporaryDirectory(prefix="unrelated-checkout-") as directory:
            workspace = Path(directory)
            (workspace / "Cargo.toml").write_text("")
            (workspace / ".git").mkdir()
            (workspace / ".git/config").write_text('[remote "origin"]\nurl=https://github.com/unrelated/repository\n')
            configuration = self.load(directory, (
                'version=1\n[match]\npackage="example"\nhomepage="https://example.org/project"\n'
                'repository="https://example.org/source"\nfiles=["Cargo.toml"]\n'
            ))
            package = {**self.package(workspace), "repository": "https://example.org/source"}
            configuration.check(package, workspace)
            for change in (
                {"name": "other"}, {"homepage": "https://different.example"},
                {"repository": None},
            ):
                with self.subTest(change=change), self.assertRaisesRegex(GapError, "does not match"):
                    configuration.check({**package, **change}, workspace)
            (workspace / "Cargo.toml").unlink()
            with self.assertRaisesRegex(GapError, "does not match"):
                configuration.check(package, workspace)

    def test_file_match_cannot_follow_a_symlink_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside"
            outside.write_text("")
            (workspace / "marker").symlink_to(outside)
            configuration = self.load(directory, 'version=1\n[match]\npackage="example"\nfiles=["marker"]\n')
            with self.assertRaisesRegex(GapError, "does not match"):
                configuration.check(self.package(workspace), workspace)

    def test_workspace_configuration_is_optional_and_broken_files_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.assertIsNone(workspace_configuration(workspace))
            path = workspace / "verus-gap.toml"
            path.write_text("version=1")
            self.assertEqual(workspace_configuration(workspace).selection, "workspace")
            path.unlink()
            path.symlink_to(workspace / "missing")
            with self.assertRaisesRegex(GapError, "Invalid build configuration"):
                workspace_configuration(workspace)

    def test_bundled_nanvix_recipe_requires_all_declared_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for name in ("build/targets/x86-kernel.json", "build/kernel_config.toml"):
                path = workspace / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            package = self.package(workspace, "kernel", "https://github.com/nanvix")
            configuration = automatic_configuration(workspace, package)
            self.assertEqual(configuration.path, PROFILES / "nanvix.toml")
            self.assertEqual(configuration.selection, "automatic")
            self.assertIn("-Zbuild-std=core,alloc,compiler_builtins", configuration.arguments)
            self.assertIsNone(automatic_configuration(workspace, {**package, "name": "other"}))
            self.assertIsNone(automatic_configuration(workspace, {**package, "homepage": None}))
            (workspace / "build/kernel_config.toml").unlink()
            self.assertIsNone(automatic_configuration(workspace, package))

    def test_automatic_selection_is_generic_and_ambiguity_requires_an_explicit_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "Cargo.toml").write_text("")
            profiles = workspace / "profiles"
            profiles.mkdir()
            text = 'version=1\n[match]\npackage="example"\nfiles=["Cargo.toml"]\n'
            (profiles / "a.toml").write_text(text)
            with mock.patch("verus_gap.configuration.PROFILES", profiles):
                configuration = automatic_configuration(workspace, self.package(workspace))
                self.assertEqual(configuration.path.name, "a.toml")
                (profiles / "b.toml").write_text(text)
                with self.assertRaisesRegex(GapError, "Multiple build configurations match.*--config"):
                    automatic_configuration(workspace, self.package(workspace))

    def test_arbitrary_configured_environment_is_in_replay_without_project_specific_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = self.load(directory, 'version=1\n[env]\nPROJECT_BUILD_MODE="configured"\n')
            environment = {"PROJECT_BUILD_MODE": "configured", "API_TOKEN": "not retained"}
            record = configuration.save(Path(directory), environment)
            project = SimpleNamespace(
                output=Path(directory), cwd=Path(directory), command=["verus"], env=environment,
                manifest={"build_configuration": record},
            )
            save_replay(project)
            replay = json.loads((Path(directory) / "replay.json").read_text())
            self.assertEqual(replay["environment"], {"PROJECT_BUILD_MODE": "configured"})
            project.manifest = {"replay_environment_keys": ["LEGACY_PROJECT_SETTING"]}
            project.env = {"LEGACY_PROJECT_SETTING": "retained"}
            save_replay(project)
            self.assertEqual(
                json.loads((Path(directory) / "replay.json").read_text())["environment"],
                {"LEGACY_PROJECT_SETTING": "retained"},
            )


class PreparationConfigurationTests(unittest.TestCase):
    def fixture(self, directory: str, name="example", homepage="https://example.org/project"):
        workspace = Path(directory) / "project"
        workspace.mkdir()
        (workspace / "Cargo.toml").write_text(f'[package]\nname="{name}"\nversion="0.1.0"\n')
        (workspace / "lib.rs").write_text("pub fn value() -> u32 { 7 }\n")
        package = {
            "id": "fixture", "name": name, "homepage": homepage,
            "manifest_path": str(workspace / "Cargo.toml"),
            "dependencies": [], "features": {"full": []},
            "targets": [{"name": name, "kind": ["lib"], "src_path": str(workspace / "lib.rs")}],
        }
        metadata = {
            "workspace_root": str(workspace), "workspace_members": ["fixture"],
            "workspace_default_members": ["fixture"], "packages": [package],
        }
        args = SimpleNamespace(
            path=str(workspace), verus=None, timeout=30, package=None, bin=None,
            config=None, no_config=False,
        )
        return workspace, package, metadata, args

    def failure(self, directory: str, metadata: dict, args, cargo=None):
        output = Path(directory) / "results"
        calls = []

        def native(command, cwd, environment, timeout):
            calls.append((command, environment))
            return {
                "command": command, "cwd": str(cwd), "seconds": 0.01, "exit_code": 1,
                "timed_out": False, "stdout": "", "stderr": "native fixture failure",
            }

        with mock.patch("verus_gap.project.find_verus", return_value=Path(directory) / "verus"), mock.patch(
            "verus_gap.project.identity", return_value={"toolchain": "fixture", "version": "fixture"},
        ), mock.patch("verus_gap.project.metadata", return_value=metadata), mock.patch(
            "verus_gap.project.run", side_effect=native,
        ), redirect_stdout(io.StringIO()) as terminal:
            with self.assertRaisesRegex(GapError, "unchanged Rust build failed.*") as failure:
                prepare(args, cargo or [], output)
        self.assertIn("--config PATH", str(failure.exception))
        return calls[0], json.loads((output / "project.json").read_text()), terminal.getvalue()

    def test_default_preparation_does_not_guess_full_features_or_use_a_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, metadata, args = self.fixture(directory)
            (command, _), record, terminal = self.failure(directory, metadata, args)
            self.assertNotIn("--features", command)
            self.assertEqual(record["cargo_args"], [])
            self.assertIsNone(record["build_configuration"])
            self.assertNotIn("Using build configuration", terminal)
            self.assertFalse(record["baseline_passed"])

    def test_automatic_nanvix_selection_supplies_the_previously_hardcoded_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, metadata, args = self.fixture(directory, "kernel", "https://github.com/nanvix")
            for name in ("build/targets/x86-kernel.json", "build/kernel_config.toml"):
                path = workspace / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            with mock.patch.dict(os.environ, {"MEMORY_SIZE_BYTES": "12345", "RUSTFLAGS": "-C opt-level=1"}, clear=True):
                (command, environment), record, terminal = self.failure(directory, metadata, args)
            self.assertIn("nanvix.toml (automatic)", terminal)
            self.assertIn("--target", command)
            self.assertIn("build/targets/x86-kernel.json", command)
            self.assertIn("-Zbuild-std=core,alloc,compiler_builtins", command)
            self.assertEqual(environment["MEMORY_SIZE_BYTES"], "12345")
            self.assertEqual(environment["RUSTC_BOOTSTRAP"], "1")
            self.assertEqual(
                environment["CARGO_ENCODED_RUSTFLAGS"].split("\x1f"),
                ["-C", "opt-level=1", "-C", "relocation-model=static", "-C", "prefer-dynamic=no"],
            )
            self.assertEqual(record["build_configuration"]["selection"], "automatic")

    def test_explicit_configuration_overrides_workspace_defaults_and_cargo_overrides_are_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, metadata, args = self.fixture(directory)
            (workspace / "verus-gap.toml").write_text("invalid workspace configuration")
            path = Path(directory) / "explicit.toml"
            path.write_text('version=1\n[cargo]\nargs=["--features", "configured", "--target", "default.json"]\n')
            args.config = str(path)
            (command, _), record, terminal = self.failure(
                directory, metadata, args, ["--target=chosen.json", "--features=chosen"],
            )
            self.assertNotIn("default.json", command)
            self.assertNotIn("configured", command)
            self.assertIn("--target=chosen.json", command)
            self.assertIn("--features=chosen", command)
            self.assertEqual(record["build_configuration"]["selection"], "explicit")
            self.assertIn("explicit.toml (explicit)", terminal)

    def test_workspace_configuration_is_loaded_and_disabled_selection_ignores_it(self):
        for disabled in (False, True):
            with self.subTest(disabled=disabled), tempfile.TemporaryDirectory() as directory:
                workspace, _, metadata, args = self.fixture(directory)
                path = workspace / "verus-gap.toml"
                path.write_text("invalid" if disabled else 'version=1\n[cargo]\nargs=["--features", "configured"]\n')
                args.no_config = disabled
                (command, _), record, terminal = self.failure(directory, metadata, args)
                self.assertEqual("--features" in command, not disabled)
                self.assertEqual(record["build_configuration"] is None, disabled)

    def test_unmatched_workspace_recipe_cannot_redirect_a_selected_crate(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, metadata, args = self.fixture(directory)
            other = workspace / "other"
            other.mkdir()
            (other / "Cargo.toml").write_text('[package]\nname="other"\nversion="0.1.0"\n')
            (other / "lib.rs").write_text("pub fn other() {}\n")
            metadata["packages"].append({
                "id": "other", "name": "other", "manifest_path": str(other / "Cargo.toml"),
                "dependencies": [], "targets": [{"name": "other", "kind": ["lib"], "src_path": str(other / "lib.rs")}],
            })
            metadata["workspace_members"].append("other")
            (workspace / "verus-gap.toml").write_text(
                'version=1\n[cargo]\npackage="other"\nargs=["--features", "configured"]\n'
                '[match]\npackage="other"\nfiles=["Cargo.toml"]\n'
            )
            (command, _), record, terminal = self.failure(directory, metadata, args)
            self.assertEqual(record["package"], "example")
            self.assertNotIn("--features", command)
            self.assertIsNone(record["build_configuration"])
            self.assertNotIn("Using build configuration", terminal)

    def test_configuration_can_select_a_virtual_workspace_package_and_explicit_selector_wins(self):
        for explicit in (None, "other"):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory:
                workspace, package, metadata, args = self.fixture(directory)
                package["manifest_path"] = str(workspace / "example/Cargo.toml")
                metadata["packages"].append({
                    **package, "id": "other", "name": "other",
                    "manifest_path": str(workspace / "other/Cargo.toml"),
                })
                metadata["workspace_members"].append("other")
                metadata["workspace_default_members"].append("other")
                (workspace / "verus-gap.toml").write_text('version=1\n[cargo]\npackage="example"\n')
                args.package = explicit
                (command, _), record, _ = self.failure(directory, metadata, args)
                self.assertEqual(record["package"], explicit or "example")
                self.assertEqual(command[command.index("-p") + 1], explicit or "example")

    def test_configuration_binary_is_a_default_and_cli_binary_takes_precedence(self):
        for explicit in (None, "chosen"):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory:
                workspace, package, metadata, args = self.fixture(directory)
                package["targets"].extend([
                    {"name": name, "kind": ["bin"], "src_path": str(workspace / "lib.rs")}
                    for name in ("configured", "chosen")
                ])
                (workspace / "verus-gap.toml").write_text('version=1\n[cargo]\nbin="configured"\n')
                args.bin = explicit
                (command, _), record, _ = self.failure(directory, metadata, args)
                self.assertEqual(record["target"], explicit or "configured")
                self.assertEqual(command[command.index("--bin") + 1], explicit or "configured")

    def test_metadata_failure_explains_optional_configuration_without_claiming_a_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, args = self.fixture(directory)
            with mock.patch("verus_gap.project.find_verus", return_value=Path(directory) / "verus"), mock.patch(
                "verus_gap.project.identity", return_value={"toolchain": "fixture"},
            ), mock.patch("verus_gap.project.metadata", side_effect=GapError("Cargo metadata failed")), mock.patch(
                "verus_gap.project.run",
            ) as native:
                with self.assertRaisesRegex(GapError, "Cargo metadata failed.*") as failure:
                    prepare(args, [], Path(directory) / "results")
            self.assertIn("--config PATH", str(failure.exception))
            native.assert_not_called()

    def test_resume_checks_configuration_and_replay_before_any_compiler_or_source_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            configuration_path = output / "recipe.toml"
            configuration_path.write_text('version=1\n[env]\nBUILD_MODE="configured"\n')
            configuration = load_configuration(configuration_path)
            record = configuration.save(output, {"BUILD_MODE": "configured"})
            (output / "project.json").write_text(json.dumps({
                "schema": "verus-gap/project-v2", "baseline_passed": True, "build_configuration": record,
            }))
            (output / "replay.json").write_text(json.dumps({"environment": {"BUILD_MODE": "changed"}}))
            with mock.patch("verus_gap.project.identity") as compiler:
                with self.assertRaisesRegex(GapError, "replay no longer matches"):
                    resume(output, 30)
            compiler.assert_not_called()


class RealConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            find_verus(None)
        except GapError as error:
            raise unittest.SkipTest(str(error)) from error

    def scan(self, path: Path, output: Path, *arguments):
        environment = dict(os.environ)
        environment.pop("PROJECT_BUILD_MODE", None)
        return subprocess.run(
            [str(ROOT / "verus-gap"), str(path), "--out", str(output), "--timeout", "120", *arguments],
            capture_output=True, text=True, timeout=150, env=environment,
        )

    def test_real_default_crate_does_not_enable_a_full_feature(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            crate = root / "ordinary"
            crate.mkdir()
            (crate / "Cargo.toml").write_text(
                '[package]\nname="ordinary"\nversion="0.1.0"\nedition="2021"\n'
                '[lib]\npath="lib.rs"\n[features]\nfull=[]\n'
            )
            (crate / "lib.rs").write_text(
                '#[cfg(feature="full")]\ncompile_error!("full must not be inferred");\n'
                'pub fn value() -> u32 { 7 }\n'
            )
            output = root / "results"
            result = self.scan(crate, output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((output / "project.json").read_text())
            self.assertIsNone(manifest["build_configuration"])
            self.assertEqual(manifest["cargo_args"], [])
            self.assertNotIn("Using build configuration", result.stdout)
            self.assertTrue(manifest["baseline_passed"])

    def test_real_configuration_setup_parallel_replay_and_resume_without_original_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            crate = root / "needs-configuration"
            crate.mkdir()
            (crate / "Cargo.toml").write_text(
                '[package]\nname="configured"\nversion="0.1.0"\nedition="2021"\n'
                '[lib]\npath="lib.rs"\n[features]\nenabled=[]\n'
            )
            (crate / "build.rs").write_text(
                'fn main() { assert_eq!(std::env::var("PROJECT_BUILD_MODE").unwrap(), "configured"); }\n'
            )
            (crate / "lib.rs").write_text(
                '#[cfg(not(feature="enabled"))]\ncompile_error!("enabled feature required");\n'
                'pub mod second;\npub fn first() -> u32 { 7 }\n'
            )
            (crate / "second.rs").write_text('pub fn second() -> u32 { 8 }\n')
            path = crate / "verus-gap.toml"
            path.write_text(
                'version=1\n[match]\npackage="configured"\nfiles=["Cargo.toml"]\n'
                '[cargo]\nargs=["--features", "enabled"]\nrustflags=["-C", "opt-level=1"]\n'
                '[env]\nPROJECT_BUILD_MODE={value="configured",force=true}\n'
            )
            failed_output = root / "unconfigured"
            failed = self.scan(crate, failed_output, "--no-config")
            self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
            self.assertIn("--config PATH", failed.stderr)
            self.assertFalse(json.loads((failed_output / "project.json").read_text())["baseline_passed"])
            output = root / "configured-results"
            configured = self.scan(crate, output, "--jobs", "2")
            self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
            self.assertIn("verus-gap.toml (workspace)", configured.stdout)
            manifest = json.loads((output / "project.json").read_text())
            self.assertEqual(manifest["build_configuration"]["selection"], "workspace")
            self.assertTrue(manifest["baseline_passed"])
            self.assertEqual(manifest["preparation_check"]["exit_code"], 0)
            self.assertIn("--features", manifest["cargo_args"])
            replays = list(output.glob("workers/*/worker-*/replay.json"))
            self.assertEqual(len(replays), 2)
            for replay_path in replays:
                replay = json.loads(replay_path.read_text())
                self.assertEqual(replay["environment"]["PROJECT_BUILD_MODE"], "configured")
            path.unlink()
            environment = dict(os.environ)
            environment.pop("PROJECT_BUILD_MODE", None)
            resumed = subprocess.run(
                [str(ROOT / "verus-gap"), "--resume", str(output), "--jobs", "1", "--timeout", "120"],
                capture_output=True, text=True, timeout=150, env=environment,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertIn("Reusing saved build configuration", resumed.stdout)
            self.assertEqual(
                json.loads((output / "replay.json").read_text())["environment"]["PROJECT_BUILD_MODE"],
                "configured",
            )
            self.assertIn("[saved TOML](build-config.toml)", (output / "report.md").read_text())


if __name__ == "__main__":
    unittest.main()
