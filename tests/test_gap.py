from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verus_gap.gap import (GapError, active_sources, capture_wrapper, category, diagnostics, location,
                 prefix_end, select_package, snapshot, verus_args, wrap)


class UnitTests(unittest.TestCase):
    def test_capture_wrapper_is_result_local_and_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            wrapper = capture_wrapper(Path(directory))
            self.assertEqual(wrapper.parent, Path(directory))
            self.assertEqual(wrapper.read_bytes(), (ROOT / "src/verus_gap/capture.py").read_bytes())
            self.assertEqual(wrapper.stat().st_mode & 0o111, 0o111)

    def test_inner_attributes_nested_strings_and_comments(self):
        prefix = '// header\n#![cfg_attr(feature = "x", doc = r#"[]""#)]\n//! docs\n/* /* nested */ */\n'
        text = prefix + 'pub fn f() {}\n'
        self.assertEqual(prefix_end(text), len(prefix))

    def test_outer_docs_stay_with_item(self):
        for docs in ('/// function docs\n', '/** function docs */\n'):
            self.assertEqual(prefix_end('// header\n' + docs + 'fn f() {}'), len('// header\n'))

    def test_unclosed_prefix_rejected(self):
        for text in ('/* unclosed', '#![doc = "bad]', '#![cfg(x)'):
            with self.assertRaises(GapError):
                prefix_end(text)

    def test_wrapper_keeps_bytes_and_maps_original_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lib.rs"
            text = '#![no_std]\r\n//! doc\n/// 函数\npub fn f() {}\n'
            path.write_bytes(text.encode())
            entry = wrap(path)
            entry["file"] = "lib.rs"
            body = path.read_text()
            self.assertIn("/// 函数\npub fn f() {}", body)
            byte = path.read_bytes().index(b"pub fn")
            mapped = location({
                "file_name": str(path), "byte_start": byte, "byte_end": byte + 3,
                "line_start": 6, "column_start": 1,
            }, Path(directory), {str(path): entry})
            self.assertEqual(mapped["line"], 4)
            self.assertEqual(mapped["source"], "pub fn f() {}")

    def test_same_line_prefix_column_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lib.rs"
            path.write_text('#![no_std] fn f() {}')
            entry = wrap(path)
            entry["file"] = "lib.rs"
            byte = path.read_bytes().index(b"fn f")
            mapped = location({
                "file_name": str(path), "byte_start": byte, "byte_end": byte + 2,
                "line_start": 3, "column_start": 1,
            }, Path(directory), {str(path): entry})
            self.assertEqual(mapped["line"], 1)
            self.assertEqual(mapped["column"], 12)

    def test_filter_preserves_dependencies_cfg_and_target(self):
        args = [
            "src/lib.rs", "--extern", "arrayvec=/a.rmeta", "--extern", "vstd=/bad.rmeta",
            "--error-format=json", "--json", "diagnostic-rendered-ansi",
            "--out-dir", "/out", "--emit=dep-info,metadata", "-C", "incremental=/old",
            "--target", "/target.json", "--cfg", 'feature="a"',
        ]
        self.assertEqual(verus_args(args), [
            "src/lib.rs", "--extern", "arrayvec=/a.rmeta", "--target", "/target.json",
            "--cfg", 'feature="a"',
        ])

    def test_compile_errors_not_promoted_to_limitations(self):
        self.assertEqual(category({"message": "unsupported", "code": {"code": "E0432"}}), "rust_or_wrapper")
        self.assertEqual(category({"message": "unsupported thing", "code": None}), "frontend_unsupported")
        self.assertEqual(category({"message": "ignored function", "children": [{"message": "use assume_specification"}]}), "missing_library_spec")
        self.assertEqual(category({"message": "cannot use function `local::f` which is ignored",
                                   "children": [{"message": "use assume_specification"}]}), "verification_boundary")
        self.assertEqual(category({"message": "aborting due to 1 previous error"}), "compiler_summary")

    def test_raw_diagnostics_retained(self):
        record = {"level": "error", "message": "unsupported", "spans": [], "rendered": "error: unsupported\n"}
        text = json.dumps(record) + "\npanic detail\n"
        records, raw = diagnostics(text, ROOT, {})
        self.assertEqual(records[0]["raw_diagnostic"], record)
        self.assertEqual(raw, "error: unsupported\npanic detail\n")

    def test_derived_spec_warnings_are_not_lost(self):
        record = {"level": "warning", "message": "continuing, but without adding a specification for the derived Clone impl",
                  "spans": [], "rendered": "warning: no derived spec\n"}
        records, raw = diagnostics(json.dumps(record), ROOT, {})
        self.assertEqual(records[0]["level"], "warning")
        self.assertEqual(records[0]["category"], "missing_derived_spec")
        self.assertEqual(raw, record["rendered"])

    def test_cfg_macro_error_not_promoted_to_language_gap(self):
        self.assertEqual(category({"message": "removing an expression is not supported in this position"}), "wrapper_or_parser")

    def test_external_trait_warning_kept_as_boundary_not_derived_spec(self):
        record = {
            "level": "warning",
            "message": "cannot use external trait local::Buf as a bound without declaring the trait (use #[verifier::external_trait_specification] to declare the trait)",
            "spans": [], "rendered": "warning: undeclared external trait\n",
        }
        ordinary = {"level": "warning", "message": "unused variable", "spans": [],
                    "rendered": "warning: unused variable\n"}
        records, raw = diagnostics(json.dumps(record) + "\n" + json.dumps(ordinary), ROOT, {})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "verification_boundary")
        self.assertEqual(records[0]["level"], "warning")
        self.assertEqual(records[0]["raw_diagnostic"], record)
        self.assertEqual(raw, record["rendered"] + ordinary["rendered"])

    def test_active_sources_escaped_spaces(self):
        with tempfile.TemporaryDirectory(prefix="gap test ") as directory:
            root = Path(directory) / "lib.rs"
            out = Path(directory) / "out"
            out.mkdir()
            root.touch()
            (out / "subject-abc.d").write_text('/target: ' + str(root).replace(" ", "\\ ") + '\n')
            captured = {"args": ["--out-dir", str(out), "--crate-name", "subject", "-C", "extra-filename=-abc"], "cwd": directory}
            self.assertEqual(active_sources(captured, root), [root])

    def test_snapshot_never_modifies_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "lib.rs").write_text("pub fn f() {}")
            (root / "target").mkdir()
            (root / "target" / "cache").touch()
            out = root / "results"
            out.mkdir()
            copied = out / "workspace"
            snapshot(root, copied, out)
            wrap(copied / "src" / "lib.rs")
            self.assertEqual((root / "src" / "lib.rs").read_text(), "pub fn f() {}")
            self.assertFalse((copied / "target").exists())
            self.assertFalse((copied / "results").exists())

    def test_external_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.write_text("external")
            (root / "linked").symlink_to(outside)
            with self.assertRaises(GapError):
                snapshot(root, Path(directory) / "dest", Path(directory) / "dest")

    def test_target_named_source_module_is_not_treated_as_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            (root / "src" / "target").mkdir(parents=True)
            (root / "target").mkdir()
            (root / "src" / "target" / "mod.rs").write_text("pub fn f() {}")
            (root / "target" / "cache").touch()
            destination = Path(directory) / "dest"
            snapshot(root, destination, destination)
            self.assertTrue((destination / "src" / "target" / "mod.rs").is_file())
            self.assertFalse((destination / "target").exists())

    def test_internal_directory_symlink_remains_inside_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            real = root / "shared"
            real.mkdir(parents=True)
            (real / "source.rs").write_text("pub fn f() {}")
            (root / "link").symlink_to(real, target_is_directory=True)
            destination = Path(directory) / "dest"
            snapshot(root, destination, destination)
            self.assertEqual((destination / "link").resolve(), destination / "shared")
            self.assertEqual((destination / "link" / "source.rs").read_text(), "pub fn f() {}")

    def test_workspace_selection_error_lists_choices(self):
        meta = {"workspace_members": ["a", "b"], "workspace_default_members": ["a", "b"],
                "packages": [{"id": "a", "name": "alpha", "manifest_path": "/a/Cargo.toml"},
                             {"id": "b", "name": "beta", "manifest_path": "/b/Cargo.toml"}]}
        with self.assertRaisesRegex(GapError, "alpha, beta"):
            select_package(meta, Path("/Cargo.toml"), None)
        self.assertEqual(select_package(meta, Path("/a/Cargo.toml"), None)["name"], "alpha")


@unittest.skipUnless((ROOT / ".tools" / "installation.json").is_file(), "run ./setup for real Verus tests")
class RealVerusTests(unittest.TestCase):
    def scan(self, path: Path, *extra: str):
        return subprocess.run([str(ROOT / "verus-gap"), "--frontier", str(path), *extra],
                              capture_output=True, text=True)

    def test_cargo_modules_and_original_source_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "src").mkdir()
            (root / "Cargo.toml").write_text('[package]\nname="gap_fixture"\nversion="0.1.0"\nedition="2021"\n')
            (root / "src" / "lib.rs").write_text('mod child;\npub fn now() -> std::time::Instant { std::time::Instant::now() }\n')
            child = root / "src" / "child.rs"
            child.write_text('pub fn value() -> u32 { 1 }\n')
            out = Path(directory) / "results"
            result = self.scan(root, "--out", str(out))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            report = json.loads((out / "report.json").read_text())
            self.assertEqual(len(report["wrapped_files"]), 2)
            self.assertEqual({d["category"] for d in report["diagnostics"]} - {"compiler_summary"}, {"missing_library_spec"})
            self.assertTrue(all(d["locations"][0]["line"] == 2 for d in report["diagnostics"] if d["category"] == "missing_library_spec"))
            self.assertEqual(child.read_text(), 'pub fn value() -> u32 { 1 }\n')
            self.assertFalse((root / "target").exists())

    def test_single_file_success_and_inner_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            path = root / "lib.rs"
            original = '#![no_std]\n//! test\n/// f\npub fn f() -> u32 { 1 }\n'
            path.write_text(original)
            result = self.scan(path, "--out", str(Path(directory) / "results"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(path.read_text(), original)

    def test_frontend_can_hide_other_error_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            path = source / "lib.rs"
            path.write_text('pub fn asm() { unsafe { core::arch::asm!("nop"); } }\npub fn now() -> std::time::Instant { std::time::Instant::now() }\n')
            out = Path(directory) / "results"
            result = self.scan(path, "--out", str(out))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            report = json.loads((out / "report.json").read_text())
            self.assertFalse(report["complete_inventory"])
            self.assertIn("frontend_unsupported", {d["category"] for d in report["diagnostics"]})
            self.assertNotIn("missing_library_spec", {d["category"] for d in report["diagnostics"]})

    def test_rust_baseline_failure_is_not_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            path = source / "lib.rs"
            path.write_text("pub fn f() { missing_name(); }\n")
            out = Path(directory) / "results"
            result = self.scan(path, "--out", str(out))
            self.assertEqual(result.returncode, 2)
            report = json.loads((out / "report.json").read_text())
            self.assertNotIn("verus", report["runs"])
            self.assertIn("Unchanged Rust baseline failed", report["blockers"][0])
            markdown = (out / "report.md").read_text()
            self.assertIn("baseline.stderr", markdown)
            self.assertNotIn("[verus.stderr]", markdown)

    def test_bad_path_printed_immediately(self):
        result = self.scan(Path("/nonexistent/README-placeholder"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Input does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
