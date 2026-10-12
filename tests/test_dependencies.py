from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verus_gap.dependencies import align_manifest, cargo_arguments, fresh_capture, requires_ghost_dependencies
from verus_gap.gap import GapError
from verus_gap.project import resume


class DependencySetupTests(unittest.TestCase):
    def test_resume_rejects_native_source_cfg_paired_with_ghost_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project.json").write_text(json.dumps({
                "schema": "verus-gap/project-v2", "baseline_passed": True,
                "support_mode": "cargo_verus_ghost_dependencies",
                "source_cfg": [], "source_configuration_origin": "native",
            }))
            with self.assertRaisesRegex(GapError, "does not match its Cargo-Verus dependencies"):
                resume(root, 30)

    def test_only_registered_support_dependencies_are_aligned(self):
        old = (
            '# retain this comment\n[package]\nname="example"\nversion="0.1.0"\n'
            '[dependencies]\nvstd={version="=old",default-features=false,features=["alloc"]}\n'
            'alias={package="verus_builtin",version="old"}\nother="1.2"\n'
        )
        changed = align_manifest(old, {"vstd": "new", "verus_builtin": "builtin"}, root=True)
        parsed = tomllib.loads(changed)
        self.assertEqual(parsed["dependencies"]["vstd"]["version"], "=new")
        self.assertFalse(parsed["dependencies"]["vstd"]["default-features"])
        self.assertEqual(parsed["dependencies"]["vstd"]["features"], ["alloc"])
        self.assertEqual(parsed["dependencies"]["alias"]["version"], "=builtin")
        self.assertEqual(parsed["dependencies"]["other"], "1.2")
        self.assertTrue(parsed["package"]["metadata"]["verus"]["verify"])
        self.assertTrue(changed.startswith("# retain this comment"))

    def test_workspace_inheritance_and_local_paths_not_rewritten(self):
        old = (
            '[workspace.dependencies]\nvstd="old"\n'
            '[dependencies]\nvstd={workspace=true}\n'
            'local={package="verus_builtin",path="../builtin",version="old"}\n'
        )
        parsed = tomllib.loads(align_manifest(old, {"vstd": "new", "verus_builtin": "new"}))
        self.assertEqual(parsed["workspace"]["dependencies"]["vstd"], "=new")
        self.assertEqual(parsed["dependencies"]["vstd"], {"workspace": True})
        self.assertEqual(parsed["dependencies"]["local"]["version"], "old")

    def test_cargo_verus_unstable_flags_are_split(self):
        self.assertEqual(
            cargo_arguments(["--features", "full", "-Zbuild-std=core", "-Z", "unstable-options"]),
            ["--features", "full", "-Z", "build-std=core", "-Z", "unstable-options"],
        )

    def test_previous_capture_cannot_satisfy_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ghost-capture.json"
            path.write_text('{"root_execution": "intentionally_deferred"}')
            attempt = fresh_capture(path)
            self.assertFalse(path.exists())
            self.assertEqual(
                path.with_name(f"ghost-capture.previous-{attempt}.json").read_text(),
                '{"root_execution": "intentionally_deferred"}',
            )

    def test_only_reachable_runtime_verus_dependencies_require_ghost_setup(self):
        dependency = {"manifest_path": "/project/context/Cargo.toml", "metadata": {"verus": {"verify": True}}}
        root = {"manifest_path": "/project/Cargo.toml", "dependencies": []}
        metadata = {"packages": [root, dependency]}
        self.assertFalse(requires_ghost_dependencies(root, metadata))
        root["dependencies"] = [{"path": "/project/context", "kind": "dev"}]
        self.assertFalse(requires_ghost_dependencies(root, metadata))
        root["dependencies"][0]["kind"] = None
        self.assertTrue(requires_ghost_dependencies(root, metadata))

    def test_added_root_support_preserves_custom_target_no_std(self):
        manifest = '[package]\nname="fixture"\nversion="0.1.0"\n'
        parsed = tomllib.loads(align_manifest(manifest, {"vstd": "version"}, root=True, no_std=True))
        self.assertEqual(parsed["dependencies"]["vstd"], {
            "version": "=version", "default-features": False, "features": ["alloc"],
        })


if __name__ == "__main__":
    unittest.main()
