---
name: verus-gap
description: Identify source-bound Verus language and library-model gaps using real Cargo setup, independent active-file probes, progressive isolation, and reproducible grouped evidence.
---

# Verus gap investigation

Use this skill to identify Rust constructs or library APIs that Verus cannot check in a project's build context. Run the scanner for discovery, then interpret its evidence. Keep manual follow-up bounded to a specific unresolved setup, instrumentation, generated-code, or reduction task.

## Run a scan

Locate the supplied tool directory or installed executable. Commands are relative to the **tool**, not the directory where this skill was copied. Read its README.

```bash
./setup
./verus-gap /absolute/path/to/authorized/project
```

Setup creates the Python environment and validates the latest official Verus distribution, matching Rust, vstd and Z3. Register an explicitly requested existing distribution with `./setup --verus PATH`, or pin with `--release VERSION`. Keep the toolchain fixed while investigating or comparing findings.

The scanner builds an isolated project copy, captures the actual native Cargo context, rechecks syntax-only source preparation with ordinary Rust, smoke-tests compiler support, and probes active files independently. It reproduces candidate support errors before counting them and records explicit body/declaration exclusions. The default level 2 looks for quick initial findings in the selected code without expanding Verus checking into dependency files. SMT verification is off; proof lifetime checking remains on.

A crate-defined `full` feature is automatically selected unless the user chooses explicit features or no-default-features. Preserve the project's other build options after `--`. A source file or subdirectory inside a crate retains its containing Cargo context; it is not an isolated dependency-free snippet.

Use `--package NAME` only for ambiguous workspaces, `--bin NAME` for a specific binary, or repeat `--file RELATIVE_PATH_OR_GLOB` to bound the scope. Prefer the user's requested scope; do not expand to unrelated repositories.

New scans default to **level 2**; no level option is needed for quick initial findings. Choose a deeper exploration policy with `--scan-level {0,1,2}`. Explain the levels as exploration depth, not finding confidence or fixed runtime:

| Level | User-facing meaning | Scanner policy |
|---|---|---|
| `0` | Investigate selected source and needed local dependencies | Follow uniquely resolvable local dependencies and continue isolation |
| `1` | Keep looking for more gaps in the selected code | No dependency checking expansion; continue local body/declaration isolation |
| `2` (default) | Get quick initial findings from the selected code | No dependency checking expansion; stop before further isolation when only checking-boundary errors remain |

All levels retain native Cargo context, preparation controls and repeated confirmation of new support observations. Levels 1 and 2 require grouped primary locations in selected files; unchanged raw context diagnostics remain available. Do not promise identical findings across levels.

A checking boundary means a referenced type, function, or trait is currently outside Verus checking or has been explicitly excluded. It can occur inside the selected file, not just in dependencies, and is not automatically a language or library-model gap. If encountered, it keeps the focused frontier partial even after exclusions. Level 2 stops only when all remaining error diagnostics, excluding compiler summaries, are checking boundaries and no setup/instrumentation blocker is present. Its `checking_boundary_only` stop deliberately leaves those errors unresolved and can hide additional selected-source gaps. Report this limit explicitly rather than calling the scan complete. Resume inherits the recorded level, not the new-scan default, and reuses a deliberately finished boundary-only frontier without another probe; start a new scan to change levels.

Use `--verbose` with any level when live progress is useful. It flushes round/confirmation/action updates and newly confirmed groups with original errors and locations. Repeated groups are deduplicated, level-1/2 findings stay in selected source, and unconfirmed/setup/compiler diagnostics are not labeled limitations. Verbosity adds no compiler calls and may be changed on resume; still read the final reports and raw evidence.

Use `--jobs N` when concurrent file scanning fits the user's CPU and memory budget; the default is 1 and keeps the sequential path. Multiple workers use a fixed-size pool of persistent Python processes, not threads, so Python analysis does not compete for one shared GIL. It dynamically assigns the next file to a free process, not fixed file partitions. Cargo/setup runs once; the already parsed/cfg-prepared source models are cached once per pool and loaded independently once per process, without repeating parsing/indexing. Processes reuse private writable source copies and render caches, compiler outputs are independent, and dependency/support artifacts remain shared inputs. `--verbose` shows cached-model loading, workspace-copy progress and process PID/readiness; readiness and file dispatch are not finished-file counts. File exploration and confirmation stay independent, while each file's rounds remain sequential. More workers do not change scope or finding confidence and do not guarantee linear speedup. The total timeout is shared; `--jobs` may change on resume without changing the saved scan level. Workers publish atomic checkpoints only after the completed round's initial and required confirmation evidence is fully written; unfinished runs and temporary checkpoint files are excluded. The coordinator alone writes combined reports and checkpoint indices, with a shared report lock for manual refresh. Ctrl+C stops active compilers, waits for restoration, and reports all completed rounds; worker failures exit 2 with stopped snapshots and explicit errors. Forced process death cannot run restoration handlers; unverified or unrestored copies must not be reported as restored.

Pending process-pool tasks are dispatched largest source first, measured by original source bytes, with selected order retained for ties. This heuristic can reduce idle-worker tails but is not a runtime guarantee. Report file/location ordering and each file's evidence remain unchanged; single-worker scans keep their original order and resume reuses completed files before dispatching pending ones. Verbose `[dispatch n/m]` is the current pending queue's dispatch count, not the report's completed-file count.

Reports start after setup. Completed rounds and files refresh them only when a minute has elapsed and a new confirmed diagnostic/API group exists; repeated locations and boundary/progress-only changes do not trigger a rewrite. File completion does not bypass these conditions. Normal exit always refreshes them. Ctrl+C restores sources and writes an interrupted snapshot containing only completed, checkpointed rounds. Use `./verus-gap report RESULT_DIR` to refresh manually without Cargo, Verus, resuming, or changing raw evidence and scanner/compiler identities. Keep `originals/`, `project.json`, and `shards/`; the original input and build workspace are not needed. Read snapshot state, pending files, and active/unfinished frontiers before interpreting counts.

Build scripts and macros execute in the copy. Use trusted input and preserve the original project.

## Read evidence before drawing a conclusion

Read `report.md`, `report.json`, `review.md`, and `project.json`, even on a nonzero exit. Inspect representative per-round `verus.txt` and the retained source overlay for every diagnostic family.

Source-location links in the Markdown reports point to retained original files under `originals/`, with `#L` line anchors; viewer support determines whether clicking jumps to the line. Keep that directory with the reports. Inspect scan overlays separately when investigating a transformation; do not confuse them with the linked original source.

| Evidence | Interpretation |
|---|---|
| `language_rejection` | An explicit Verus translation rejection mapped to checked original source and repeated under the recorded overlay |
| `library_specification` | An external type/API's specification was not recognized in this build/support configuration |
| `checking_boundary` | Local/generated/external items remain outside checking or lack usable trait coverage; not automatically a missing external-library model |
| `derived_spec_warning` | A derive implementation lacks a recognized specification; warnings are not fatal gap counts |
| `migration_requirement` | Mode, binding, visibility, or other Verus migration work; inspect the raw message |
| Rust/wrapper/instrumentation categories | Setup or conversion evidence, not established application support gaps |
| `compiler_failure` | An ICE/panic, not a verdict that a whole Rust feature is unsupported |
| `dependency_codegen_blocker` | Dependency artifact/MIR setup issue, not a source migration requirement |

Counts are canonical diagnostic/API groups, not unique language features, total unsupported operations, or proved functions. Report scan level, selected files, dependency scope, enumerable declarations, pending/partial frontiers, exclusions, source restoration, compiler version, and cfg/features/target.

The raw diagnostic text must remain unchanged. Adjacent original locations are source-mapped; rendered compiler excerpts retain the compiler's generated line numbers.

## Resolve only the actual blocker

Start from `agent-tasks.json`, choose one task, inspect its shard's final evidence, and reproduce it in a separate copy. Do not run one agent per function.

For baseline/setup failures, use the exact recorded command and logs. Restore generated assets, local path dependencies, target JSON, build-std/rust-src, required environment and project tooling without changing the platform or program API. Never turn unresolved imports, missing resources, compiler mismatch, or stale artifacts into limitations.

Existing Verus-aware local dependencies use official Cargo-Verus. The tool records aligned support versions, a copied-manifest patch, the native-after-alignment control, real ghost metadata/VIR, and deliberate root deferral. That deferral is not a successful root build. For custom targets, it builds support from the exact release source and retains the target; there is no automatic `--no-lifetime` fallback.

For instrumentation problems, compare unchanged input, syntax preparation, exact wrapper/boundaries, and source maps. Check cfg ownership, inner docs/attributes, out-of-line modules, trait impl/default methods, macros/includes, derives, and nested constants. Fix the transformation rather than repeatedly excluding its fabricated error.

Preserve Rust derives unless a specific safe, documented adaptation is demonstrated. Blanket deleting Debug/Clone can manufacture ordinary Rust errors. Direct `external_body` must be visible before Verus macro loop rewriting; late `cfg_attr` activation is not equivalent.

Existing top-level `#[verus_verify]` and `#[verus_spec]` items keep their attribute-based checking. An ordinary top-level `impl` with attribute-verified methods receives one outer attribute-based pass rather than a surrounding macro; its ordinary sibling methods remain checked. Do not put these items inside another `verus!` block: Verus's two rewriting styles are not designed to compose, and double rewriting can manufacture missing loop-variable errors. A warning about `verus_spec` being used inside `verus!` blocks support acceptance in either the initial or confirmation run and requires inspection of the original and transformed source, not a language-support conclusion.

Resume with `./verus-gap --resume RESULT_DIR` after a tool/setup fix. Resume validates source/compiler/artifact identities and retains revision-specific evidence. Start a new scan to change target, features, input or Verus version.

## Reduce and verify a finding

Keep the same compiler, edition, cfg/features, target, relevant generic types, and smallest real dependency closure. Run unchanged Rust first, then reproduce the same rejected construct/API and canonical Verus diagnostic. A different error in the same broad category is not a successful reduction.

For compiler panics, preserve the exact trace and context; a compiler failure is not evidence that a Rust feature is unsupported. Check generated code and source attribution. For library gaps, distinguish absent recognized models from ignored local items and unsupported trait instances. Do not invent contracts or insert assumed specifications just to clear the scan.

Keep a reduction's provenance, source, commands, raw diagnostics, and controls separate from original evidence. Do not patch the user's production sources or add trust without explicit authorization.

## Present a useful, bounded answer

Lead with the observed language/API groups and important setup/coverage limits. Group by error family and named API, show typical original source and raw errors, and link full occurrences and evidence. Describe residual frontiers and why exclusions were necessary.

Never call `frontend_clear`, an exclusion-cleared frontier, a second identical error, or exit 0 a correctness proof or exhaustive inventory. Stable repetition is evidence only together with valid setup, source preparation and checked-source attribution. Macro-generated declarations are not fully enumerated; dependency expansion is syntax indexing, not a semantic call graph.
