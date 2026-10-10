from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verus_gap.gap import GapError
from verus_gap.more_errors import BOUNDARY, choose, expand, finding_key, functions, patched_source, remap


def record(file, line, column, category="frontend_unsupported", level="error"):
    return {
        "category": category, "level": level, "message": "unsupported body",
        "locations": [{"file": file, "line": line, "column": column, "source": ""}],
    }


class BoundaryTests(unittest.TestCase):
    def test_function_modifiers_attributes_and_trait_methods(self):
        source = (
            '#![no_std]\n'
            '/// docs\n#[inline] pub(crate) unsafe extern "C" fn a() { 1; }\n'
            'struct S;\nimpl S { pub fn b<T>(&self, x: T) where T: Copy { let _ = x; } }\n'
            'trait C { fn declared(&self); fn c(&self) { 1; } }\n'
        )
        found = functions(source)
        self.assertEqual([fn.name for fn in found], ["a", "b", "c"])
        self.assertEqual(source[found[0].start:found[0].start + 9], "#[inline]")

    def test_strings_raw_strings_chars_and_nested_comments(self):
        source = (
            'fn real() { let _ = r##"} fn fake() {}"##; '
            'let _ = b"fn fake()"; let _ = c"}"; let _ = \'\\u{7b}\'; '
            '/* fn bad() { /* } */ } */ let _ = \'}\'; }\n'
        )
        self.assertEqual([fn.name for fn in functions(source)], ["real"])

    def test_macro_generated_functions_are_not_guessed(self):
        self.assertEqual(functions('macro_rules! make { () => { fn generated() {} }; }\n'), [])
        self.assertEqual(functions('custom! { fn generated() {} }\n'), [])
        self.assertEqual([fn.name for fn in functions('#[attr(fn generated)] fn real() {}')], ["real"])

    def test_const_generic_and_macro_headers_declined(self):
        self.assertEqual(functions('fn f() -> S<{ 1 }> { 1; }'), [])
        self.assertEqual(functions('fn f(x: type_macro!()) { 1; }'), [])
        self.assertEqual(functions('fn f(x: [u8; { 1 }]) { 1; }'), [])

    def test_unbalanced_input_is_explicit(self):
        for source in ('fn f() {', 'fn f() { /* unclosed', 'fn f() { let _ = r#"bad; }'):
            with self.assertRaises(GapError):
                functions(source)

    def test_only_body_errors_are_candidates(self):
        source = 'pub fn f(mut self) { unsupported(); }\n'
        body = record("lib.rs", 1, source.index("unsupported") + 1)
        signature = record("lib.rs", 1, 1)
        chosen, _ = choose([body], {"lib.rs": source}, set())
        self.assertEqual(len(chosen), 1)
        self.assertEqual(choose([signature], {"lib.rs": source}, set())[0], {})
        self.assertEqual(choose([signature, body], {"lib.rs": source}, set())[0], {})

    def test_existing_boundary_warning_and_rust_errors_declined(self):
        source = BOUNDARY + 'fn f() { unsupported(); }\n'
        error = record("lib.rs", 2, 10)
        self.assertEqual(choose([error], {"lib.rs": source}, set())[0], {})
        plain = 'fn f() { unsupported(); }\n'
        for category, level in (("missing_derived_spec", "warning"), ("rust_or_wrapper", "error"),
                                ("wrapper_or_parser", "error"), ("frontend_unsupported", "warning")):
            self.assertEqual(choose([record("lib.rs", 1, 10, category, level)], {"lib.rs": plain}, set())[0], {})

    def test_multi_span_and_unwrapped_locations_declined(self):
        source = 'fn f() { unsupported(); }\n'
        error = record("lib.rs", 1, 10)
        error["locations"].append({"file": "other.rs", "line": 1, "column": 1, "source": ""})
        self.assertEqual(choose([error], {"lib.rs": source}, set())[0], {})

    def test_remap_multiple_same_line_boundaries_unicode_and_crlf(self):
        source = '// λ\r\nfn a() { 1; } fn b() { bad(); }\r\n'
        starts = [fn.start for fn in functions(source)]
        modified = patched_source(source, starts)
        pos = modified.index("bad")
        place = {"file": "lib.rs", "line": modified[:pos].count("\n") + 1,
                 "column": len(modified[:pos].rsplit("\n", 1)[-1]) + 1}
        mapped = remap(place, source, starts)
        self.assertEqual(mapped["line"], 2)
        self.assertEqual(mapped["column"], source.splitlines()[1].index("bad") + 1)
        self.assertEqual(mapped["source"], source.splitlines()[1])
        self.assertIsNone(remap({"file": "lib.rs", "line": 2, "column": 1}, source, starts))

    def test_dedup_ignores_changed_category_and_specification_hints(self):
        first = record("lib.rs", 1, 10, "missing_library_spec")
        second = dict(first, category="verification_boundary",
                      message=first["message"] + " (note: try assume_specification)")
        self.assertEqual(finding_key(first), finding_key(second))


@unittest.skipUnless((ROOT / ".tools/installation.json").is_file(), "run ./setup for real Verus tests")
class ExpansionTests(unittest.TestCase):
    def scan(self, source: str, *options):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        project = root / "project"
        project.mkdir()
        path = project / "lib.rs"
        path.write_text(source)
        output = root / "results"
        process = subprocess.run([str(ROOT / "verus-gap"), "--frontier", str(path), "--out", str(output), *options],
                                 capture_output=True, text=True)
        self.assertEqual(path.read_text(), source)
        return process, output, json.loads((output / "report.json").read_text())

    def test_default_still_does_not_add_boundaries(self):
        source = (ROOT / "tests/fixtures/frontier/lib.rs").read_text()
        process, output, report = self.scan(source)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertNotIn("more_errors", report)
        self.assertFalse((output / "more-errors").exists())
        self.assertFalse(report["checks"]["shielding"])

    def test_hidden_specs_exposed_and_initial_evidence_preserved(self):
        source = (ROOT / "tests/fixtures/frontier/lib.rs").read_text()
        process, output, initial = self.scan(source, "--more-errors")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        summary = json.loads((output / "more-errors/report.json").read_text())
        self.assertEqual(summary["additional_diagnostics"], 2)
        self.assertEqual(len(summary["boundaries"]), 1)
        self.assertEqual(summary["boundaries"][0]["function"], "unsupported")
        self.assertEqual(len(summary["rounds"]), 2)
        self.assertEqual({d["category"] for d in initial["diagnostics"]} - {"compiler_summary"}, {"frontend_unsupported"})
        final = json.loads((output / "more-errors/round-001/report.json").read_text())
        self.assertTrue(final["checks"]["shielding"])
        self.assertFalse(final["complete_inventory"])
        self.assertEqual({d["category"] for d in final["diagnostics"]} - {"compiler_summary"}, {"missing_library_spec"})
        self.assertEqual([d["locations"][0]["line"] for d in final["diagnostics"] if d["category"] != "compiler_summary"], [6, 8])
        self.assertTrue(summary["initial_workspace_restored"])
        original_copy = (output / "workspace/lib.rs").read_bytes()
        self.assertNotIn(b"external_body", original_copy)
        self.assertEqual(original_copy, (output / "more-errors/round-000/sources/lib.rs").read_bytes())
        self.assertIn(b"external_body", (output / "more-errors/round-001/sources/lib.rs").read_bytes())
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), initial["source_sha256"]["lib.rs"])
        self.assertIn("unsupported", (output / "more-errors/round-001/boundaries.diff").read_text())

    def test_asm_only_does_not_report_unchanged_code_success(self):
        process, output, initial = self.scan('pub fn f() { unsafe { core::arch::asm!("nop"); } }\n', "--more-errors")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        summary = json.loads((output / "more-errors/report.json").read_text())
        self.assertEqual(initial["status"], "diagnostics_observed")
        self.assertEqual(summary["stop_reason"], "no_errors_after_boundaries")
        self.assertEqual(summary["additional_diagnostics"], 0)
        self.assertFalse(summary["complete_inventory"])
        self.assertEqual(summary["rounds"][-1]["status"], "no_diagnostics_observed")

    def test_body_boundary_precedes_loop_rewriting(self):
        source = (ROOT / "tests/fixtures/unsizing/lib.rs").read_text().replace(
            "    space(store)\n", "    for _ in 0..1 { let _ = space(store); }\n    0\n"
        )
        source += '\npub fn now() -> std::time::Instant { std::time::Instant::now() }\n'
        process, output, initial = self.scan(source, "--more-errors")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        summary = json.loads((output / "more-errors/report.json").read_text())
        self.assertEqual(summary["additional_diagnostics"], 2)
        self.assertEqual(summary["boundaries"][0]["function"], "call")
        self.assertEqual(summary["boundaries"][0]["attribute"], "#[verifier::external_body]")
        self.assertNotIn("loop_isolation_boundary", process.stdout + process.stderr)
        self.assertEqual(summary["status"], "finished")
        self.assertTrue(summary["initial_workspace_restored"])
        self.assertFalse(initial["checks"]["shielding"])

    def test_signature_errors_are_not_hidden(self):
        source = 'pub struct S; impl S { pub fn f(mut self) { let _ = &mut self; } }\n'
        process, output, initial = self.scan(source, "--more-errors")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        summary = json.loads((output / "more-errors/report.json").read_text())
        self.assertEqual(summary["boundaries"], [])
        self.assertEqual(len(summary["rounds"]), 1)
        self.assertIn("mut self", initial["diagnostics"][0]["message"])

    def test_cargo_module_isolation_preserves_dependency_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "Cargo.toml").write_text('[package]\nname="more_errors_fixture"\nversion="0.1.0"\nedition="2021"\n')
            original = 'mod child;\npub fn now() -> std::time::Instant { let _ = child::value(); std::time::Instant::now() }\n'
            child = 'pub fn value() -> u32 { unsafe { core::arch::asm!("nop"); } 1 }\n'
            (project / "src/lib.rs").write_text(original)
            (project / "src/child.rs").write_text(child)
            output = root / "results"
            process = subprocess.run([str(ROOT / "verus-gap"), "--frontier", str(project), "--more-errors", "--out", str(output)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            initial = json.loads((output / "report.json").read_text())
            summary = json.loads((output / "more-errors/report.json").read_text())
            self.assertEqual(summary["additional_diagnostics"], 2)
            self.assertEqual([b["file"] for b in summary["boundaries"]], ["src/child.rs"])
            self.assertEqual(initial["runs"]["baseline"]["exit_code"], 0)
            self.assertEqual((project / "src/lib.rs").read_text(), original)
            self.assertEqual((project / "src/child.rs").read_text(), child)
            self.assertFalse((project / "target").exists())
            for place in summary["diagnostics"][1:]:
                self.assertEqual(place["locations"][0]["line"], 2)
                self.assertEqual(place["locations"][0]["file"], "src/lib.rs")

    def test_existing_related_spans_do_not_inflate_additional_count(self):
        source = (
            'pub fn first(x: &mut u32) -> u32 { core::mem::replace(x, 1) }\n'
            'pub fn second(x: &mut u32) -> u32 { core::mem::replace(x, 2) }\n'
        )
        process, output, initial = self.scan(source, "--more-errors")
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        first = next(d for d in initial["diagnostics"] if d["category"] == "missing_library_spec")
        self.assertTrue(first["related_locations"])
        summary = json.loads((output / "more-errors/report.json").read_text())
        self.assertEqual(summary["additional_diagnostics"], 0)
        self.assertEqual(len(summary["boundaries"]), 2)
        self.assertTrue(all(not d["newly_observed"] for d in summary["diagnostics"]))
        self.assertEqual(summary["stop_reason"], "no_errors_after_boundaries")

    def test_multiple_passes_and_round_limit_preserve_remaining_errors(self):
        source = (
            'pub fn first(x: &mut u32) -> u32 { core::mem::replace(x, 1) }\n'
            'pub fn second(x: &mut u32) -> u32 { core::mem::replace(x, 2) }\n'
        )
        _, output, initial = self.scan(source)
        path = output / "workspace/lib.rs"
        maps = {str(path): dict(initial["wrapped_files"][0], original_text=source)}
        with patch("verus_gap.more_errors.MAX_ROUNDS", 1):
            result = expand(output, initial, maps, initial["runs"]["verus"]["command"],
                            path.parent, dict(os.environ, RUSTUP_TOOLCHAIN=initial["verus"]["toolchain"]),
                            time.monotonic() + 60)
        summary = json.loads((output / "more-errors/report.json").read_text())
        self.assertEqual(result["stop_reason"], "round_limit_reached")
        self.assertEqual(len(summary["rounds"]), 2)
        self.assertEqual(len(summary["boundaries"]), 1)
        self.assertFalse(summary["complete_inventory"])
        self.assertTrue(summary["initial_workspace_restored"])

    def test_follow_up_panic_is_not_success_and_workspace_is_restored(self):
        source = 'pub fn f() { unsafe { core::arch::asm!("nop"); } }\n'
        _, output, initial = self.scan(source)
        path = output / "workspace/lib.rs"
        original = path.read_bytes()
        maps = {str(path): dict(initial["wrapped_files"][0], original_text=source)}
        failed = {
            "command": initial["runs"]["verus"]["command"], "cwd": str(path.parent),
            "exit_code": 1, "timed_out": False, "seconds": 0.01,
            "stdout": "", "stderr": "internal compiler error: simulated panic\n",
        }
        with patch("verus_gap.more_errors.run", return_value=failed):
            result = expand(output, initial, maps, failed["command"], path.parent, {},
                            time.monotonic() + 60)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stop_reason"], "follow_up_scan_blocked")
        self.assertEqual(path.read_bytes(), original)
        summary = json.loads((output / "more-errors/report.json").read_text())
        self.assertTrue(summary["initial_workspace_restored"])
        self.assertTrue(summary["blockers"])

    def test_wrapper_blocker_and_bad_baseline_do_not_expand(self):
        for source in (
            'pub fn f() { missing(); }\n',
            'pub fn f() { #[cfg(windows)] for _ in 0..1 { missing_windows(); } }\n',
        ):
            with self.subTest(source=source):
                process, output, initial = self.scan(source, "--more-errors")
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                summary = json.loads((output / "more-errors/report.json").read_text())
                self.assertEqual(summary["stop_reason"], "initial_scan_blocked")
                self.assertEqual(summary["boundaries"], [])
                self.assertEqual(len(summary["rounds"]), 1)
                self.assertEqual(initial["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
