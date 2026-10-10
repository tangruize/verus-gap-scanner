from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verus_gap.report import aggregate, language_family, markdown, public_api


class ReportTests(unittest.TestCase):
    def test_fast_report_filters_primary_and_related_locations_without_editing_raw_evidence(self):
        source = SimpleNamespace(functions=[], declarations=[])
        project = SimpleNamespace(sources={"selected.rs": source, "dependency.rs": source}, manifest={
            "input": "fixture", "package": "fixture", "verus": {"version": "fixture"}, "baseline_passed": True,
            "cargo_args": [], "checks": {"proof_lifetimes": True}, "workspace_restored": True, "scan_mode": "fast",
        })
        selected = {"file": "selected.rs", "byte": 0, "line": 1, "column": 1, "source": "target()"}
        dependency = dict(selected, file="dependency.rs")
        record = {
            "category": "library_specification", "level": "error", "message": "model not recognized",
            "rendered": "unchanged raw diagnostic", "locations": [selected, dependency],
            "related_locations": [dependency], "confirmed": True, "macro_expansion": False,
            "raw_diagnostic": None, "scope": ["selected.rs"], "source_variant": "prepared",
            "round": 0, "evidence": "round-000/verus.txt",
        }
        shard = {
            "observations": [record, dict(record, message="dependency-only", locations=[dependency])],
            "events": [], "focus": "selected.rs", "directory": "shards/a", "scope": ["selected.rs"],
            "status": "partial", "stop_reason": "focused_dependency_boundary",
            "dependency_boundary_limited": True,
        }
        report = aggregate(project, [shard], ["selected.rs"])
        self.assertEqual(report["scan_mode"], "fast")
        self.assertEqual(report["counts"], {"library_specification": 1})
        self.assertEqual(report["selected_scope_counts"], report["counts"])
        self.assertEqual(report["coverage"]["expanded_context_files"], 0)
        self.assertEqual(report["coverage"]["dependency_boundary_limited_files"], 1)
        self.assertEqual(report["coverage"]["outside_scope_diagnostics"], 1)
        self.assertEqual(report["groups"][0]["locations"], [selected])
        self.assertEqual(report["groups"][0]["related_locations"], [])
        self.assertEqual(report["groups"][0]["dependency_locations"], 0)
        self.assertEqual(report["groups"][0]["raw_example"], record["rendered"])
        self.assertEqual(record["locations"], [selected, dependency])
        self.assertEqual(report["outside_scope_evidence"][0]["evidence"], "shards/a/round-000/verus.txt")
        text = markdown(report)
        self.assertIn("Mode: fast, selected source only", text)
        self.assertNotIn("#### dependency-only", text)
        self.assertNotIn("`dependency.rs:", text)

    def test_public_api_ignores_bracketed_const_bounds(self):
        record = {"raw_diagnostic": {"children": [{
            "message": "pub assume_specification<T, impl [const] FnOnce(T) -> bool + [const] Destruct> "
                       "[std::option::Option::<T>::is_none_or] (_0: Option<T>, _1: F) -> bool;",
        }]}}
        self.assertEqual(public_api(record), "std::option::Option::<T>::is_none_or")

    def test_public_api_preserves_nested_array_brackets_and_missing_hints(self):
        path = "<&[u8; 16] as std::convert::AsRef<[u8]>>::as_ref"
        record = {"raw_diagnostic": {"children": [{
            "message": f"pub assume_specification [{path}] (_0: &[u8; 16]) -> &[u8];",
        }]}}
        self.assertEqual(public_api(record), path)
        self.assertIsNone(public_api({"raw_diagnostic": None}))
        record["raw_diagnostic"]["children"][0]["message"] = "pub assume_specification<T: [const] Destruct>"
        self.assertIsNone(public_api(record))

    def test_language_summary_keeps_raw_feature_details_separate(self):
        self.assertEqual(language_family(
            "The verifier does not yet support the following Rust feature: "
            "Path Def(Static { mutability: Mut }, DefId(kernel::STORAGE))"
        ), "Mutable static access")
        self.assertEqual(language_family(
            "The verifier does not yet support the following Rust feature: "
            "unsizing operation from `&S` to `&dyn T`"
        ), "Unsizing coercions")
        self.assertEqual(language_family("Verus does not support this cast: `usize` to `*mut u8`"),
                         "Pointer casts")

    def test_same_internal_method_name_does_not_merge_distinct_public_apis(self):
        def record(api, file):
            return {
                "category": "library_specification", "level": "error",
                "message": "`core::sync::atomic::impl&%1%default%load` is not supported",
                "rendered": "unchanged raw diagnostic",
                "locations": [{"file": file, "byte": 0, "line": 1, "column": 1, "source": "load()"}],
                "related_locations": [], "confirmed": True, "macro_expansion": False,
                "raw_diagnostic": {"children": [{"message": f"pub assume_specification [{api}] (_0: u32);"}]},
                "scope": ["selected.rs", "dependency.rs"], "source_variant": "prepared",
                "round": 0, "evidence": "round-000/verus.txt",
            }

        source = SimpleNamespace(functions=[], declarations=[])
        project = SimpleNamespace(sources={"selected.rs": source, "dependency.rs": source}, manifest={
            "input": "fixture", "package": "fixture", "verus": {}, "baseline_passed": True,
            "cargo_args": [], "checks": {}, "workspace_restored": True,
        })
        shard = {
            "observations": [record("AtomicBool::load", "selected.rs"), record("AtomicUsize::load", "dependency.rs")],
            "events": [], "focus": "selected.rs", "directory": "shards/a", "scope": ["selected.rs", "dependency.rs"],
            "status": "partial", "stop_reason": "unresolved_frontier",
        }
        report = aggregate(project, [shard], ["selected.rs"])
        self.assertEqual(report["counts"]["library_specification"], 2)
        self.assertEqual(report["selected_scope_counts"]["library_specification"], 1)
        self.assertEqual(report["coverage"]["expanded_context_files"], 1)
        self.assertEqual(report["groups"][1]["dependency_locations"], 1)
        self.assertEqual(report["groups"][0]["raw_example"], "unchanged raw diagnostic")


if __name__ == "__main__":
    unittest.main()
