# Verus Gap

Find Rust constructs and library APIs that **the installed Verus cannot check in your project's real build context**. The scanner builds a copy, probes active files independently, isolates observed blockers, follows resolvable local dependencies, and writes a grouped report. It never edits the input project or invents specifications.

## Install once

Requires **Python 3.11+ and Rustup/Cargo**. Linux x86-64 is tested; the official installer also supports macOS x86-64/ARM64.

```bash
./setup
```

Setup creates a local `.venv`, installs the declared Python dependencies, downloads the **latest official Verus release** with its published SHA-256, installs the matching Rust toolchain if necessary, and runs a Verus/vstd/Z3 smoke proof. It does not change your default Rust toolchain. The chosen release stays fixed until you run setup again.

Already have a complete Verus distribution? Use `./setup --verus /absolute/path/to/verus`. For reproducibility, use `./setup --release VERSION`.

## Scan

```bash
./verus-gap demo
./verus-gap /absolute/path/to/your/crate
```

The demo intentionally reports missing `std::time::Instant` models and exits with code 1. The real command automatically discovers Cargo, builds the unchanged crate with Verus's matching Rust, and scans its active files. A crate-defined `full` feature is selected automatically; otherwise Cargo's default features are retained.

A file or subdirectory inside a crate selects that scope **without losing the containing crate's dependencies**. The scanner retains the real root, target, features, generated files, and Cargo dependency artifacts; it does not compile the chosen file as a dependency-free snippet. During checking it follows uniquely indexed local function/type boundaries transitively, rather than stopping after a fixed number of dependency files. Selected-source findings and added dependency findings are labeled separately. Ambiguous/generated boundaries and exhausted time budgets remain explicit limits. A standalone `.rs` file works too. Workspaces with ambiguous members list their names; use `--package NAME`. For different features, use `--features FEATURE`, or pass normal Cargo options after `--`.

```bash
./verus-gap /path/to/tokio/tokio --file 'src/sync/*.rs'
./verus-gap /path/to/workspace --package my_crate -- --no-default-features
./verus-gap --resume /path/to/verus-gap-results/RESULT
```

Resume validates source/compiler/dependency identities, reuses the baseline and completed shards, and continues partial frontiers. `--out DIR` sets the result location; `--timeout SECONDS` changes the total build/scan budget (default: one hour). No per-function agent calls are needed.

The scanner caches unchanged source overlays, diffs and source-location indexes, and indexes local dependency names once per frontier. These optimizations do not change the checking scope or isolation order. An already accepted observation keeps its original two-run evidence; seeing it again does not trigger another confirmation unless the round contains a new or still-unconfirmed support observation. New observations and compiler failures still require same-overlay confirmation, and all raw runs and patches are retained.

Use trusted projects: Cargo build scripts and procedural macros execute in the copied workspace.

## Read the result

Open the printed **`report.md`**. Its summary groups language rejections by error family and distinguishes your selected files from added dependency context. Detailed findings use public API names when available, original file locations, source excerpts, and unchanged compiler diagnostics. Unstable compiler definition numbers are normalized only for grouping, and different public APIs are kept separate. Counts are distinct diagnostic/API groups, **not counts of distinct Rust features or proved functions**.

| File | Purpose |
|---|---|
| `report.md` / `report.json` | Reproduced gaps, selected/dependency scope, coverage, and remaining frontiers |
| `review.md` | Local checking boundaries, migration requirements, instrumentation/setup problems, and compiler failures; not counted as support gaps |
| `project.json` / `replay.json` | Build configuration, compiler/support identities, adaptation records, and exact replay context |
| `shards/` | Every round's raw output, copied sources, source maps/edits, and confirmation run |
| `agent-tasks.json` | Bounded follow-up tasks for an optional agent |

**Exit 0:** the selected frontiers cleared without accepted gaps. **Exit 1:** reproduced gaps were found; coverage may still be partial. **Exit 2:** setup failed, the scan budget left files pending, or only unresolved frontiers remain. Always inspect the report, including on nonzero exits.

## What the scanner does—and does not establish

It first validates the unchanged Rust build, then rechecks syntax-only cfg/lifetime preparation with ordinary Rust. It smoke-tests Verus support before interpreting application errors. Each counted support diagnostic must map into the actually checked source and reproduce in a second run with the same overlay. Unsupported bodies/declarations are then explicitly excluded to expose later errors; all exclusions are recorded. Existing top-level `#[verus_verify]` / `#[verus_spec]` items keep their original attribute-based checking instead of being wrapped twice. An otherwise ordinary `impl` containing attribute-verified methods uses one outer attribute-based pass, also checking its ordinary sibling methods. A mixed-rewriting warning is an instrumentation failure: neither the initial nor confirmation run may contain one for a support diagnostic to be accepted. Rust derives remain present; derived-model warnings are reported separately.

For an existing Verus-aware crate or local dependency, it uses official Cargo-Verus to build real ghost metadata and VIR. Registered Verus support versions are aligned **only in the scan copy**, with a manifest diff and a second native control build. Root probes emit metadata, not native executables. For custom targets, matching builtin/vstd support is built from the exact Verus release source; the target is never silently replaced by the host, and proof lifetime checks are never automatically disabled. Nanvix's recognized kernel target/build-std configuration is selected automatically and recorded.

**This is a compatibility investigation, not a proof or exhaustive inventory.** File shards retain the Rust dependency context, but unwrapped/generated items can remain checking boundaries. Scope expansion is conservative source indexing, not a semantic call graph. Early signature/trait/macro/compiler failures and excluded bodies can hide more gaps. Clearing a frontier after exclusions does not mean the original program is supported or verified. A library finding means its model was not recognized in the recorded configuration—not that no model exists anywhere or that the API is inherently unverifiable.

External path dependencies, generated assets, and unusual platform setup may require a self-contained prepared copy. The tool refuses to copy your home/filesystem root or follow symlinks outside the workspace. Failed setup is reported, never relabeled as an application gap.

The optional [agent skill](.github/skills/verus-gap/SKILL.md) works with Copilot or any agent that can run commands and read files. The command itself does not require an agent.

For Python package installation, `pip install .` also provides `verus-gap` and `verus-gap-setup`; its toolchain cache is `$XDG_CACHE_HOME/verus-gap` (or `~/.cache/verus-gap`, override with `VERUS_GAP_HOME`). The checkout's two commands use its local `.tools` directory. `--frontier` retains the old one-pass scanner for diagnostic comparison, not normal inventory use.

See [validation and interpretation](VALIDATION.md) for the tested compiler/platform, the 114-test release checks, real setup/model/instrumentation controls, selected-range experiments, and remaining scientific limits. Per-project evidence is delivered separately in the `verus-gap-reports` bundle.
