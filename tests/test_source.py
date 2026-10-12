from __future__ import annotations

import difflib
import pickle
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verus_gap.gap import GapError
from verus_gap.project import source_configuration
from verus_gap.source import Cfg, Edit, Source, apply_edits


class SourceTests(unittest.TestCase):
    def test_verus_ghost_body_conditions_remain_available_to_erasure(self):
        original = (
            "struct S { #[cfg(verus_keep_ghost_body)] ghost: Ghost<u32>, value: u32 }\n"
            "#[cfg(not(verus_keep_ghost_body))] fn erased_only() {}\n"
        ).encode()
        cfg = Cfg(["verus_keep_ghost", "verus_keep_ghost_body"])
        prepared = Source("lib.rs", original, cfg).render(set(), set(), wrap=False).data
        self.assertEqual(prepared, original)
        native = Source("lib.rs", original, Cfg([])).render(set(), set(), wrap=False).data
        self.assertNotIn(b"ghost:", native)
        self.assertIn(b"fn erased_only()", native)

    def test_existing_verus_and_state_machine_macros_are_not_double_wrapped(self):
        for name in ("verus", "vstd::prelude::verus", "state_machine", "tokenized_state_machine"):
            with self.subTest(name=name):
                original = f"{name}! {{ pub fn f() {{}} }}\npub fn ordinary() {{}}\n".encode()
                source = Source("lib.rs", original, Cfg([]))
                rendered = source.render(set(), set())
                self.assertIn(b"\n}\n" + original.split(b"\n")[0], rendered.data)
                self.assertTrue(rendered.checked(original.index(b"pub fn f")))
                self.assertTrue(rendered.checked(original.index(b"pub fn ordinary")))

    def test_source_view_uses_native_cfg_without_disabling_explicit_ghost_builds(self):
        original = (
            "#[cfg(verus_keep_ghost)] use crate::specs;\n"
            "#[cfg_attr(verus_keep_ghost, verus_spec(ensures true))]\n"
            "pub fn f() -> u32 { #[cfg(verus_keep_ghost)] proof! {} 1 }\n"
            "#[cfg(not(verus_keep_ghost))] pub fn runtime_only() {}\n"
        ).encode()
        for flags in (["unix"], ["unix", "verus_keep_ghost"]):
            with self.subTest(flags=flags):
                cfg = source_configuration({"rust_cfg": flags, "source_cfg": flags})
                model = Source("lib.rs", original, cfg)
                rendered = model.render(set(), set(), wrap=False)
                ghost = "verus_keep_ghost" in flags
                self.assertEqual(b"crate::specs" in rendered.data, ghost)
                self.assertEqual(b"verus_spec" in rendered.data, ghost)
                self.assertEqual(b"proof!" in rendered.data, ghost)
                self.assertEqual(b"runtime_only" in rendered.data, not ghost)
                self.assertIn(b"pub fn f()", rendered.data)

    def test_saved_source_cfg_preserves_legacy_evidence_and_rejects_invalid_values(self):
        self.assertIn("verus_keep_ghost", source_configuration({"rust_cfg": []}).values)
        self.assertEqual(source_configuration({"rust_cfg": [], "source_cfg": []}).values, set())
        for value in ("unix", [1], None):
            with self.subTest(value=value), self.assertRaisesRegex(GapError, "source configuration"):
                source_configuration({"rust_cfg": [], "source_cfg": value})

    def test_prepared_models_round_trip_without_parsing_or_rebuilding_the_index(self):
        source = Source("lib.rs", (
            'use ::core::arch;\n'
            'const NAME: &str = "name";\n'
            '#[cfg(windows)] pub fn inactive() { missing(); }\n'
            'trait T { fn required(&self); fn defaulted(&self) {} }\n'
            '#[cfg_attr(unix, inline)] pub fn active() { arch::asm!("nop"); }\n'
        ).encode(), Cfg(["unix", "verus_keep_ghost"]))
        expected = source.identity()
        variants = [
            ({}, {}),
            ({"bodies": {source.functions[-1].id}}, {}),
            ({"excluded": {source.declarations[1].id}}, {}),
            ({}, {"prepare": False, "wrap": False}),
            ({}, {"load_support": True, "support_crate": "support"}),
            ({}, {"unwrapped": {source.functions[-1].id}}),
        ]
        rendered = [
            source.render(state.get("bodies", set()), state.get("excluded", set()), **options)
            for state, options in variants
        ]
        serialized = pickle.dumps(source, protocol=pickle.HIGHEST_PROTOCOL)
        with mock.patch("verus_gap.source.Parser") as parsing, mock.patch.object(
            Source, "_collect",
        ) as collecting, mock.patch.object(Source, "_prepare") as preparing:
            copied = pickle.loads(serialized)
            self.assertEqual(copied.identity(), expected)
            self.assertIsNone(copied._rendered)
            self.assertIsNone(copied._render_key)
            self.assertNotIn("tree", copied.__dict__)
            for (state, options), original in zip(variants, rendered):
                actual = copied.render(state.get("bodies", set()), state.get("excluded", set()), **options)
                self.assertEqual(actual.data, original.data)
                self.assertEqual(actual.overlay, original.overlay)
                for segment in actual.segments:
                    self.assertEqual(actual.location(segment.start), original.location(segment.start))
            parsing.assert_not_called()
            collecting.assert_not_called()
            preparing.assert_not_called()

    def test_transferred_models_have_independent_mutable_state_and_overlay_caches(self):
        source = Source("lib.rs", b"#[inline] pub fn f() {}\n", Cfg(["unix"]))
        original_render = source.render(set(), set())
        serialized = pickle.dumps(source)
        first, second = pickle.loads(serialized), pickle.loads(serialized)
        self.assertIsNot(first.cfg, second.cfg)
        self.assertIsNot(first.declarations[0], second.declarations[0])
        first.cfg.values.add("windows")
        first.declarations[0].attributes.append("#[external]")
        first.preparation.append(Edit(0, 0, "// private\n", "private preparation"))
        first.parse_errors.append({"kind": "private error"})
        first.render({first.functions[0].id}, set())
        self.assertEqual(second.cfg.values, source.cfg.values)
        self.assertEqual(second.declarations, source.declarations)
        self.assertEqual(second.preparation, source.preparation)
        self.assertEqual(second.parse_errors, source.parse_errors)
        self.assertIsNone(second._rendered)
        self.assertEqual(second.render(set(), set()).data, original_render.data)
        self.assertIs(source.render(set(), set()), original_render)

    def test_transferred_syntax_tree_is_lazy_and_process_local(self):
        from verus_gap.source import Parser

        source = Source("lib.rs", b"pub fn f() {}\n", Cfg([]))
        copied = pickle.loads(pickle.dumps(source))
        with mock.patch("verus_gap.source.Parser", wraps=Parser) as parsing:
            self.assertEqual(str(copied.tree.root_node), str(source.tree.root_node))
            self.assertIsNot(copied.tree, source.tree)
            self.assertIs(copied.tree, copied.tree)
            parsing.assert_called_once()

    def test_render_cache_reuses_only_the_same_complete_overlay(self):
        original = b"pub fn f() { let _ = 1; }\n"
        source = Source("lib.rs", original, Cfg([]))
        function = source.functions[0]
        first = source.render(set(), set())
        self.assertIs(source.render(set(), set()), first)
        bodies = {function.id}
        isolated = source.render(bodies, set())
        self.assertIsNot(isolated, first)
        self.assertIs(source.render(set(bodies), set()), isolated)
        self.assertEqual(isolated.overlay["checking_ranges"], isolated.checking_ranges)
        self.assertTrue(first.checked(original.index(b"let")))
        self.assertFalse(isolated.checked(original.index(b"let")))
        bodies.clear()
        restored = source.render(bodies, set())
        self.assertEqual(restored.data, first.data)
        self.assertEqual(restored.overlay, first.overlay)
        external = source.render(set(), {function.id})
        self.assertFalse(external.checked(original.index(b"pub fn")))
        ordinary = source.render(set(), set(), wrap=False, prepare=False)
        self.assertEqual(ordinary.data, original)
        supported = source.render(set(), set(), load_support=True, support_crate="support")
        self.assertIn(b"support::prelude::verus!", supported.data)
        unwrapped = source.render(set(), set(), unwrapped={function.id})
        self.assertFalse(unwrapped.checked(original.index(b"pub fn")))

    def test_rendered_patch_is_cached_without_changing_the_diff(self):
        source = Source("lib.rs", b"pub fn f() {}\n", Cfg([]))
        rendered = source.render(set(), set())
        with mock.patch("verus_gap.source.difflib.unified_diff", wraps=difflib.unified_diff) as diff:
            first = rendered.patch()
            self.assertEqual(rendered.patch(), first)
            diff.assert_called_once()
        self.assertIn("--- original/lib.rs", first)
        self.assertIn("+++ scan/lib.rs", first)

    def test_indexed_locations_match_original_byte_mapping(self):
        original = "// λ\r\npub fn f() { let café = 1; }\n".encode()
        rendered = apply_edits(original, "lib.rs", [
            Edit(0, 0, "// added\n", "prefix"),
            Edit(original.index(b"1"), original.index(b"1") + 1, "10", "anchored", anchor=original.index(b"1")),
        ])
        for segment in rendered.segments:
            for byte in range(segment.start, segment.end):
                place = rendered.location(byte)
                if segment.original is None:
                    self.assertIsNone(place)
                    continue
                offset = segment.original if segment.anchored else segment.original + byte - segment.start
                prefix = original[:offset]
                lines = original.decode().splitlines()
                line = prefix.count(b"\n") + 1
                self.assertEqual(place, {
                    "file": "lib.rs", "line": line,
                    "column": len(prefix.rsplit(b"\n", 1)[-1].decode("utf-8", "replace")) + 1,
                    "byte": offset, "source": lines[line - 1] if line <= len(lines) else "",
                })

    def test_ordinary_unchanged_context_does_not_walk_declarations(self):
        original = b"pub fn f() {}\n"
        source = Source("lib.rs", original, Cfg([]))
        with mock.patch.object(source, "declarations", wraps=source.declarations) as declarations:
            rendered = source.render(set(), set(), prepare=False, wrap=False)
            declarations.__iter__.assert_not_called()
        self.assertEqual(rendered.data, original)
        self.assertEqual(rendered.location(original.index(b"f"))["byte"], original.index(b"f"))
        self.assertFalse(rendered.checked(original.index(b"f")))

    def test_cfg_grammar(self):
        cfg = Cfg(["unix", 'target_os="linux"', 'feature="full"'])
        for expression in ("unix", 'feature = "full"', "all(unix, not(windows))",
                           'any(windows, target_os = "linux",)', "all()"):
            self.assertTrue(cfg.evaluate(expression))
        for expression in ("windows", "any()", "not(unix)", 'feature="missing"'):
            self.assertFalse(cfg.evaluate(expression))
        for expression in ("not()", "foo(unix)", "unix garbage", "all(unix windows)"):
            with self.assertRaises(GapError):
                cfg.evaluate(expression)
        self.assertTrue(cfg.evaluate("all(unix, /* explanation */ not(windows))"))
        self.assertTrue(cfg.evaluate("all(unix, // explanation\nnot(windows))"))

    def test_ast_ignores_macro_tokens_and_finds_trait_methods(self):
        source = Source("lib.rs", (
            'macro_rules! m { () => { fn fake() {} }; }\n'
            'custom! { fn fake2() {} }\n'
            'struct S; impl S { #[inline] pub fn real(&self) {} }\n'
            'trait T { fn declared(&self); fn defaulted(&self) {} }\n'
            'mod m { fn nested<const N: usize>() -> [u8; N] { [0; N] } }\n'
        ).encode(), Cfg([]))
        self.assertEqual([item.name for item in source.functions],
                         ["S::real", "T::defaulted", "m::nested"])
        self.assertEqual(source.parse_errors, [])

    def test_cfg_preparation_removes_inactive_loops_and_items(self):
        source = Source("lib.rs", (
            '#![no_std]\n'
            '#[cfg(windows)] mod m { fn inactive() {} }\n'
            'fn f() { #[cfg(windows)] for _ in 0..1 { missing(); } }\n'
        ).encode(), Cfg(["unix"]))
        rendered = source.render(set(), set())
        self.assertNotIn(b"missing", rendered.data)
        self.assertNotIn(b"inactive", rendered.data)
        self.assertTrue(rendered.data.startswith(b"#![no_std]"))
        self.assertEqual([item.name for item in source.functions], ["f"])

    def test_conditional_cfg_and_enum_commas(self):
        source = Source("lib.rs", (
            'enum E { A, #[cfg(windows)] B, C }\n'
            '#[cfg_attr(unix, cfg(windows))] fn missing() { bad(); }\n'
            '#[cfg_attr(unix, inline)] fn f() {}\n'
        ).encode(), Cfg(["unix"]))
        rendered = source.render(set(), set(), wrap=False)
        self.assertIn(b"enum E { A,  C }", rendered.data)
        self.assertNotIn(b"bad", rendered.data)
        self.assertIn(b"#[inline]", rendered.data)
        self.assertEqual([item.name for item in source.functions], ["f"])

    def test_inactive_braced_statement_macro_preserves_following_expression(self):
        for suffix in (
            "(1, 2)", "(1, 2);", "(1, 2).0", "((1, 2))",
            "(|x| x)(1)", "/* retained */ (1, 2)",
        ):
            with self.subTest(suffix=suffix):
                original = (
                    "fn f() {\n#[cfg(verus_keep_ghost)]\n"
                    "proof! { assert(true); }\n" + suffix + "\n}\n"
                ).encode()
                source = Source("lib.rs", original, Cfg([]))
                rendered = source.render(set(), set(), wrap=False)
                self.assertNotIn(b"proof!", rendered.data)
                self.assertIn(suffix.encode(), rendered.data)
                self.assertEqual(rendered.data.count(b"\n"), original.count(b"\n"))
                offset = rendered.data.index(suffix.encode())
                self.assertEqual(rendered.location(offset)["byte"], original.index(suffix.encode()))

    def test_braced_statement_macro_with_semicolon_and_active_cfg(self):
        for separator in (" ", "\n", ";\n"):
            original = (
                "fn f() { #[cfg(unix)] custom! {}" + separator + "(1, 2) }\n"
            ).encode()
            inactive = Source("lib.rs", original, Cfg([])).render(set(), set(), wrap=False)
            self.assertNotIn(b"custom!", inactive.data)
            self.assertIn(b"(1, 2)", inactive.data)
            active = Source("lib.rs", original, Cfg(["unix"])).render(set(), set(), wrap=False)
            self.assertIn(b"custom! {}" + separator.encode() + b"(1, 2)", active.data)

    def test_inactive_macro_result_calls_in_expression_context_are_removed_completely(self):
        for statement in (
            "let value = custom! {}(1, 2);",
            "return custom! {}(1, 2);",
            "(custom! {})(1, 2);",
            "custom! ()(1, 2);",
            "custom! [](1, 2);",
        ):
            with self.subTest(statement=statement):
                original = (
                    "fn f() { #[cfg(unix)] " + statement + " (3, 4) }\n"
                ).encode()
                rendered = Source("lib.rs", original, Cfg([])).render(set(), set(), wrap=False)
                self.assertNotIn(b"custom!", rendered.data)
                self.assertNotIn(b"(1, 2)", rendered.data)
                self.assertIn(b"(3, 4)", rendered.data)

    def test_retained_macro_suffix_is_indexed_and_prepared_in_nested_block(self):
        original = (
            "fn outer() { {\n#[cfg(verus_keep_ghost)] proof! {}\n"
            "(|| { #[cfg(windows)] missing(); fn nested() {} nested(); })();\n"
            "} }\n"
        ).encode()
        source = Source("lib.rs", original, Cfg([]))
        self.assertEqual([item.name for item in source.functions], ["outer", "outer::nested"])
        rendered = source.render(set(), set(), wrap=False)
        self.assertNotIn(b"proof!", rendered.data)
        self.assertNotIn(b"missing", rendered.data)
        self.assertIn(b"fn nested() {} nested();", rendered.data)

    def test_const_lifetimes_explicit_without_changing_function_types(self):
        source = Source("lib.rs", (
            'const S: &str = "x";\n'
            'const F: fn(&str) = |_| {};\n'
            'const E: &\'static [u8] = &[];\n'
        ).encode(), Cfg([]))
        rendered = source.render(set(), set())
        self.assertIn(b"const S: &'static str", rendered.data)
        self.assertIn(b"const F: fn(&str)", rendered.data)
        self.assertIn(b"const E: &'static [u8]", rendered.data)

    def test_exact_mapping_after_unicode_and_multiple_edits(self):
        original = '// λ\nfn f() { bad(); }\n'.encode()
        source = Source("lib.rs", original, Cfg([]))
        fn = source.functions[0]
        rendered = source.render({fn.id}, set())
        pos = rendered.data.index(b"bad")
        place = rendered.location(pos)
        self.assertEqual(place["file"], "lib.rs")
        self.assertEqual(place["line"], 2)
        self.assertEqual(place["column"], 10)
        self.assertEqual(place["byte"], original.index(b"bad"))
        self.assertIsNone(rendered.location(rendered.data.index(b"external_body")))

    def test_overlapping_edits_rejected(self):
        with self.assertRaises(GapError):
            apply_edits(b"abcd", "lib.rs", [
                Edit(0, 3, "", "first"), Edit(1, 2, "", "second"),
            ])

    def test_attributes_remain_before_boundary_and_item(self):
        source = Source("lib.rs", b"#[inline]\nfn f() { 1; }\n", Cfg([]))
        rendered = source.render({source.functions[0].id}, set())
        self.assertIn(b"#[inline]\n#[verifier::external_body]\nfn f", rendered.data)

    def test_wrapper_precedes_boundary_at_start_of_file(self):
        source = Source("lib.rs", b"fn f() {}\n", Cfg([]))
        rendered = source.render({source.functions[0].id}, set())
        self.assertLess(rendered.data.index(b"verus!"), rendered.data.index(b"external_body"))

    def test_unwrapped_item_at_eof_closes_empty_final_macro(self):
        source = Source("lib.rs", b"mod child;", Cfg([]))
        rendered = source.render(set(), set())
        self.assertEqual(rendered.data.count(b"{"), rendered.data.count(b"}"))
        self.assertTrue(rendered.data.endswith(b"verus! {\n\n}\n"))

    def test_comments_between_cfg_and_item_do_not_duplicate_edits(self):
        source = Source("lib.rs", b"#[cfg(windows)]\n// detail\nfn bad() {}\nfn good() {}\n", Cfg([]))
        rendered = source.render(set(), set())
        self.assertNotIn(b"bad", rendered.data)

    def test_cfg_removes_complete_field_values_and_match_arms(self):
        source = Source("lib.rs", (
            "fn f() { let _ = S { #[cfg(windows)] a: 1, "
            "#[cfg(windows)] b, c: 2 }; "
            "match 1 { #[cfg(windows)] 0 => missing(), _ => 4 }; }\n"
        ).encode(), Cfg([]))
        rendered = source.render(set(), set(), wrap=False)
        self.assertIn(b"S {   c: 2 }", rendered.data)
        self.assertNotIn(b"missing", rendered.data)
        self.assertNotIn(b"=> ,", rendered.data)

    def test_cfg_owner_keeps_active_fields_parameters_and_arms(self):
        source = Source("lib.rs", (
            "struct S { #[cfg(windows)] a: u32, #[cfg(unix)] c: u32 }\n"
            "fn f(#[cfg(windows)] a: u32, #[cfg(unix)] c: u32) -> S {\n"
            "S { #[cfg(windows)] a, #[cfg_attr(unix, cfg(unix))] c: c }\n"
            "}\nfn g(v: u32) -> u32 { match v {\n"
            "#[cfg(windows)] 0 => { fn inactive() {} 0 },\n"
            "#[cfg(unix)] 1 => 2, _ => 3,\n"
            "} }\n"
        ).encode(), Cfg(["unix"]))
        rendered = source.render(set(), set(), wrap=False)
        self.assertNotIn(b"#[cfg", rendered.data)
        self.assertNotIn(b"a:", rendered.data)
        self.assertNotIn(b"inactive", rendered.data)
        self.assertIn(b"c: c", rendered.data)
        self.assertIn(b"1 => 2", rendered.data)
        self.assertEqual([item.name for item in source.functions], ["f", "g"])

    def test_cfg_tuple_fields_include_visibility_and_type(self):
        source = Source("lib.rs", (
            "struct T(#[cfg(windows)] pub(crate) [u32; 2], "
            "#[cfg(unix)] pub u32);\n"
        ).encode(), Cfg(["unix"]))
        rendered = source.render(set(), set(), wrap=False)
        self.assertNotIn(b"[u32; 2]", rendered.data)
        self.assertNotIn(b"pub(crate)", rendered.data)
        self.assertIn(b"pub u32", rendered.data)

    def test_cfg_single_element_tuple_keeps_its_type(self):
        source = Source("lib.rs", (
            "fn f() { let a = (#[cfg(windows)] missing(), 1); "
            "let b = (1, #[cfg(windows)] missing()); }\n"
        ).encode(), Cfg([]))
        rendered = source.render(set(), set(), wrap=False)
        self.assertIn(b"( 1,)", rendered.data)
        self.assertIn(b"(1, )", rendered.data)

    def test_inactive_item_removes_its_leading_documentation(self):
        source = Source("lib.rs", (
            "pub fn good() {}\n"
            "/// Not available on this platform.\n"
            "// Keep an ordinary comment between attributes.\n"
            "#[cfg(windows)]\n/// More documentation.\npub struct Gone;\n"
        ).encode(), Cfg([]))
        rendered = source.render(set(), set(), wrap=False)
        self.assertNotIn(b"///", rendered.data)
        self.assertNotIn(b"Gone", rendered.data)

    def test_absolute_macro_import_is_qualified_without_guessing_shadows(self):
        text = (
            'use ::core::arch;\nfn f() { unsafe { arch::asm!("nop"); } }\n'
            'fn g() { use alternative::arch; arch::asm!("other"); }\n'
        )
        rendered = Source("lib.rs", text.encode(), Cfg([])).render(set(), set(), wrap=False)
        self.assertIn(b'::core::arch::asm!("nop")', rendered.data)
        self.assertIn(b'arch::asm!("other")', rendered.data)
        location = rendered.location(rendered.data.index(b"::core::arch::asm"))
        self.assertEqual(location["byte"], text.index('arch::asm!("nop")'))

    def test_attribute_verified_items_are_not_wrapped_twice(self):
        original = (
            "use vstd::prelude::*;\n"
            "#[verus_verify] pub struct S;\n"
            "#[verus_verify] impl S { #[verus_spec(ensures true)] pub fn f() {} }\n"
            "#[cfg_attr(verus_keep_ghost, verus_spec(ensures true))] pub fn g() {}\n"
            "pub fn ordinary() {}\n"
        ).encode()
        source = Source("lib.rs", original, Cfg(["verus_keep_ghost"]))
        body = next(item for item in source.functions if item.name == "S::f")
        rendered = source.render({body.id}, set())
        self.assertIn(b"}\n#[verus_verify] pub struct S;", rendered.data)
        self.assertIn(b"#[verus_spec(ensures true)] #[verifier::external_body]\npub fn f", rendered.data)
        self.assertTrue(rendered.checked(original.index(b"pub struct S")))
        self.assertTrue(rendered.checked(original.index(b"pub fn g")))
        self.assertTrue(rendered.checked(original.index(b"pub fn ordinary")))

    def test_attribute_methods_promote_the_impl_without_double_rewriting(self):
        original = (
            "impl S { #[verus_spec(ensures true)] pub fn annotated() {} "
            "pub fn ordinary() {} }\n"
        ).encode()
        source = Source("lib.rs", original, Cfg(["verus_keep_ghost"]))
        body = next(item for item in source.functions if item.name == "S::annotated")
        rendered = source.render({body.id}, set())
        self.assertIn(b"}\n#[vstd::prelude::verus_verify]\nimpl S", rendered.data)
        self.assertIn(b"#[verifier::external_body]\npub fn annotated", rendered.data)
        self.assertEqual(rendered.data.count(b"#[vstd::prelude::verus_verify]"), 1)
        self.assertTrue(rendered.checked(original.index(b"pub fn annotated")))
        self.assertTrue(rendered.checked(original.index(b"pub fn ordinary")))

    def test_attribute_method_promotion_respects_cfg_and_ordinary_fallback(self):
        original = b"impl S { #[cfg_attr(verus_keep_ghost, verus_spec(ensures true))] fn f() {} }\n"
        source = Source("lib.rs", original, Cfg(["verus_keep_ghost"]))
        parent = next(item for item in source.declarations if item.kind == "impl_item")
        rendered = source.render(set(), set(), unwrapped={parent.id})
        self.assertNotIn(b"#[vstd::prelude::verus_verify]", rendered.data)
        self.assertIn(b"}\n#[verifier::external]\nimpl S", rendered.data)
        self.assertFalse(rendered.checked(original.index(b"fn f")))
        inactive = Source("lib.rs", original, Cfg([])).render(set(), set())
        self.assertNotIn(b"#[vstd::prelude::verus_verify]", inactive.data)

    def test_attribute_verification_is_disabled_for_an_ordinary_context_boundary(self):
        original = b"#[verus_verify] pub struct S;\n"
        source = Source("lib.rs", original, Cfg(["verus_keep_ghost"]))
        item = source.declarations[0]
        rendered = source.render(set(), set(), unwrapped={item.id})
        self.assertIn(b"}\n#[verifier::external]\n#[verus_verify]", rendered.data)
        self.assertFalse(rendered.checked(original.index(b"pub struct S")))


if __name__ == "__main__":
    unittest.main()
