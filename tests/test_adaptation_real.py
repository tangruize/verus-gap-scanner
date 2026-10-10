from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verus_gap.gap import find_verus, identity, run
from verus_gap.source import Cfg, Source


@unittest.skipUnless((ROOT / ".tools/installation.json").is_file(), "run ./setup")
class RealAdaptationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verus = find_verus(None)
        cls.env = dict(os.environ, RUSTUP_TOOLCHAIN=identity(cls.verus)["toolchain"])

    def compile(self, source: str, bodies: set[str] | None = None,
                external: set[str] | None = None, prepare: bool = True):
        model = Source("lib.rs", source.encode(), Cfg(["unix", "verus_keep_ghost"]))
        body_ids = {item.id for item in model.declarations if item.name in (bodies or set())}
        excluded = {
            item.id for item in model.declarations
            if item.name in (external or set()) or f"{item.kind}:{item.name}" in (external or set())
        }
        rendered = model.render(body_ids, excluded, prepare=prepare)
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            file = cwd / "lib.rs"
            file.write_bytes(model.data)
            baseline = run(["rustc", str(file), "--crate-type=lib", "--emit=metadata"],
                           cwd, self.env, 30)
            self.assertEqual(baseline["exit_code"], 0, baseline["stderr"])
            file.write_bytes(model.render(set(), set(), wrap=False, prepare=prepare).data)
            prepared = run(["rustc", str(file), "--crate-type=lib", "--emit=metadata"],
                           cwd, self.env, 30)
            self.assertEqual(prepared["exit_code"], 0, prepared["stderr"])
            file.write_bytes(rendered.data)
            result = run([str(self.verus), str(file), "--crate-type=lib", "--no-verify",
                          "--error-format=json"], cwd, self.env, 30)
        records = []
        for line in result["stderr"].splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("level") == "error":
                records.append(record)
        return result, records

    def test_const_lifetime_adaptation(self):
        source = 'const MESSAGE: &str = "x"; pub fn value() -> u32 { 1 }\n'
        original, errors = self.compile(source, prepare=False)
        self.assertNotEqual(original["exit_code"], 0)
        self.assertTrue(any("missing lifetime" in record["message"] for record in errors))
        adapted, _ = self.compile(source)
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_existing_attribute_loop_keeps_its_original_rewriting(self):
        prefix = (
            "#![cfg_attr(not(verus_keep_ghost), feature(stmt_expr_attributes))]\n"
            "#![feature(proc_macro_hygiene)]\nuse vstd::prelude::*;\n"
        )
        function = (
            "#[verus_spec(ensures true)]\npub fn counted() {\n"
            "#[verus_spec(invariant 0 <= i <= 3, decreases 3 - i)]\n"
            "for i in 0usize..3 { }\n}\n"
        )
        original = prefix + function
        model = Source("lib.rs", original.encode(), Cfg(["verus_keep_ghost"]))
        env = dict(self.env, RUSTC_BOOTSTRAP="1")
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            file = cwd / "lib.rs"
            file.write_text(original)
            baseline = run([
                "rustc", str(file), "--crate-type=lib", "--edition=2021", "--emit=metadata",
                "--extern", f"vstd={self.verus.parent / 'libvstd.rlib'}",
                "-L", f"dependency={self.verus.parent}",
            ], cwd, env, 30)
            self.assertEqual(baseline["exit_code"], 0, baseline["stderr"])
            command = [str(self.verus), str(file), "--crate-type=lib", "--no-verify"]
            first = run(command, cwd, env, 30)
            self.assertEqual(first["exit_code"], 0, first["stderr"])
            file.write_text(prefix + "\nverus! {\n" + function + "\n}\n")
            broken = run(command, cwd, env, 30)
            self.assertNotEqual(broken["exit_code"], 0)
            self.assertIn("cannot find value `i`", broken["stderr"])
            file.write_bytes(model.render(set(), set()).data)
            adapted = run(command, cwd, env, 30)
            self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])
            self.assertNotIn("likely used inside a verus!", adapted["stderr"])

    def test_nested_attribute_loop_uses_one_impl_rewriting_style(self):
        original = (
            "#![cfg_attr(not(verus_keep_ghost), feature(stmt_expr_attributes))]\n"
            "#![feature(proc_macro_hygiene)]\nuse vstd::prelude::*;\n"
            "#[verus_verify] pub struct Counter;\nimpl Counter {\n"
            "#[verus_verify] #[verus_spec(ensures true)] pub fn counted() {\n"
            "#[verus_spec(invariant 0 <= i <= 3, decreases 3 - i)]\n"
            "for i in 0usize..3 { }\n}\n"
            "pub fn ordinary() -> u32 { 1 }\n}\n"
        )
        model = Source("lib.rs", original.encode(), Cfg(["verus_keep_ghost"]))
        env = dict(self.env, RUSTC_BOOTSTRAP="1")
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            file = cwd / "lib.rs"
            file.write_text(original)
            baseline = run([
                "rustc", str(file), "--crate-type=lib", "--edition=2021", "--emit=metadata",
                "--extern", f"vstd={self.verus.parent / 'libvstd.rlib'}",
                "-L", f"dependency={self.verus.parent}",
            ], cwd, env, 30)
            self.assertEqual(baseline["exit_code"], 0, baseline["stderr"])
            command = [str(self.verus), str(file), "--crate-type=lib", "--no-verify"]
            first = run(command, cwd, env, 30)
            self.assertEqual(first["exit_code"], 0, first["stderr"])
            file.write_bytes(model.render(set(), set()).data)
            adapted = run(command, cwd, env, 30)
            self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])
            self.assertNotIn("likely used inside a verus!", adapted["stderr"])
            checked_sibling = original.replace(
                "pub fn ordinary() -> u32 { 1 }",
                'pub fn ordinary() -> u32 { unsafe { core::arch::asm!("nop"); } 1 }',
            )
            sibling = Source("lib.rs", checked_sibling.encode(), Cfg(["verus_keep_ghost"]))
            file.write_bytes(sibling.render(set(), set()).data)
            rejected = run(command, cwd, env, 30)
            self.assertIn("inline-asm expressions", rejected["stderr"])
            body = next(item for item in sibling.functions if item.name == "Counter::ordinary")
            file.write_bytes(sibling.render({body.id}, set()).data)
            isolated = run(command, cwd, env, 30)
            self.assertEqual(isolated["exit_code"], 0, isolated["stderr"])

    def test_signature_boundary_preserves_other_functions(self):
        source = 'pub struct S; impl S { pub fn consume(mut self) {} pub fn good() -> u32 { 1 } }\n'
        original, errors = self.compile(source)
        self.assertTrue(any("mut self" in record["message"] for record in errors))
        adapted, _ = self.compile(source, external={"S::consume"})
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_unsafe_marker_impl_boundary(self):
        source = "pub struct S; unsafe impl Send for S {} pub fn good() -> u32 { 1 }\n"
        original, errors = self.compile(source)
        self.assertTrue(any("unsafe impl" in record["message"] for record in errors))
        adapted, _ = self.compile(source, external={"impl_item:S"})
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_body_boundary_works_with_loop_at_byte_zero(self):
        source = 'pub fn bad() { for _ in 0..1 { unsafe { core::arch::asm!("nop"); } } }\n'
        original, errors = self.compile(source)
        self.assertTrue(any("support" in record["message"] for record in errors))
        adapted, _ = self.compile(source, bodies={"bad"})
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_inactive_expression_removed_before_macro_expansion(self):
        source = 'pub fn good() { #[cfg(windows)] for _ in 0..1 { missing(); } }\n'
        adapted, _ = self.compile(source)
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_cfg_field_and_parameter_owners(self):
        source = (
            "pub struct S { #[cfg(windows)] pub a: u32, "
            "#[cfg(windows)] pub b: u32, #[cfg(unix)] pub c: u32 }\n"
            "pub fn make(#[cfg(windows)] a: u32, #[cfg(windows)] b: u32, c: u32) -> S {\n"
            "S { #[cfg(windows)] a: a, #[cfg(windows)] b, "
            "#[cfg_attr(unix, cfg(unix))] c: c }\n}\n"
        )
        adapted, _ = self.compile(source)
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_cfg_match_arm_owners(self):
        source = (
            "pub fn choose(v: u32) -> u32 { match v {\n"
            "#[cfg(windows)] 0 => missing(),\n"
            "#[cfg_attr(unix, cfg(windows))] 1 => missing(),\n"
            "#[cfg(unix)] 2 => 3, _ => 4,\n"
            "} }\n"
        )
        adapted, _ = self.compile(source)
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_cfg_tuple_fields_and_tuple_expression_keep_types(self):
        source = (
            "pub struct T(#[cfg(windows)] pub u32, pub u32);\n"
            "pub fn make() -> T { T(1) }\n"
            "pub fn pair() -> (u32,) { (#[cfg(windows)] missing(), 1) }\n"
        )
        adapted, _ = self.compile(source)
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])

    def test_inactive_item_at_eof_does_not_leave_orphan_doc_comments(self):
        source = "pub fn good() -> u32 { 1 }\n/// Windows only.\n#[cfg(windows)] pub struct Gone;\n"
        adapted, _ = self.compile(source)
        self.assertEqual(adapted["exit_code"], 0, adapted["stderr"])


if __name__ == "__main__":
    unittest.main()
