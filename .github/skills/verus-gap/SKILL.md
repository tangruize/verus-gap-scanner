---
name: verus-gap
description: Identify source-bound Verus language and library-model gaps using real Cargo setup, independent active-file probes, progressive isolation, and reproducible grouped evidence.
---

# Verus gap investigation

Use this skill to answer which Rust constructs or library APIs prevent Verus coverage of a project. It requires no Argus runtime or agent-specific API. Most discovery is performed by the command; use an agent only for a bounded unresolved setup, instrumentation, generated-code, or reduction task.

## Run the human workflow

Locate the supplied tool directory or installed executable. Commands are relative to the **tool**, not the directory where this skill was copied. Read its README.

```bash
./setup
./verus-gap /absolute/path/to/authorized/project
```

Setup creates the Python environment and validates the latest official Verus distribution, matching Rust, vstd and Z3. Register an explicitly requested existing distribution with `./setup --verus PATH`, or pin with `--release VERSION`. Do not silently upgrade between experiments.

The default scanner builds an isolated project copy, captures the actual native Cargo context, rechecks syntax-only source preparation with ordinary Rust, smoke-tests compiler support, and probes active files independently. It reproduces candidate support errors before counting them, records explicit body/declaration exclusions, and follows uniquely resolvable local function/type boundaries transitively without a fixed dependency-file cap. SMT verification is off; proof lifetime checking remains on.

A crate-defined `full` feature is automatically selected unless the user chooses explicit features or no-default-features. Preserve the project's other build options after `--`. A source file or subdirectory inside a crate retains its containing Cargo context; it is not an isolated dependency-free snippet.

Use `--package NAME` only for ambiguous workspaces, `--bin NAME` for a specific binary, or repeat `--file RELATIVE_PATH_OR_GLOB` to bound the scope. Prefer the user's requested scope; do not expand to unrelated repositories.

When the user wants a quicker selected-source investigation rather than transitive dependency findings, add `--fast`. It retains native Cargo context and controls, but does not recursively expand checking into dependency files. The grouped report includes only observations with primary locations in selected files; unchanged raw context diagnostics remain available. Local checking boundaries are not library gaps. If encountered, they keep the focused frontier partial even after exclusions. Do not claim fast mode has the same recall on every project. Resume inherits the original mode; start a new scan to change it.

Build scripts and macros execute in the copy. Use trusted input and preserve the original project.

## Read evidence before drawing a conclusion

Read `report.md`, `report.json`, `review.md`, and `project.json`, even on a nonzero exit. Inspect representative per-round `verus.txt` and the retained source overlay for every diagnostic family.

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

Counts are canonical diagnostic/API groups, not unique language features, total unsupported operations, or proved functions. Report selected files, dependency scope, enumerable declarations, pending/partial frontiers, exclusions, source restoration, compiler version, and cfg/features/target.

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

For compiler panics, preserve the exact trace and context; never generalize a macro-local-constant panic to all constants. Check generated code and source attribution. For library gaps, distinguish absent recognized models from ignored local items and unsupported trait instances. Do not invent contracts or insert assumed specifications just to clear the scan.

Keep a reduction's provenance, source, commands, raw diagnostics, and controls separate from original evidence. Do not patch the user's production sources or add trust without explicit authorization.

## Present a useful, bounded answer

Lead with the observed language/API groups and important setup/coverage limits. Group by error family and named API, show typical original source and raw errors, and link full occurrences and evidence. Describe residual frontiers and why exclusions were necessary.

Never call `frontend_clear`, an exclusion-cleared frontier, a second identical error, or exit 0 a correctness proof or exhaustive inventory. Stable repetition is evidence only together with valid setup, source preparation and checked-source attribution. Macro-generated declarations are not fully enumerated; dependency expansion is syntax indexing, not a semantic call graph.
