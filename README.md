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

A file or subdirectory inside a crate selects that scope **without losing the containing crate's dependencies**. The scanner keeps the real target, features, generated files, and Cargo build context; it does not treat the chosen file as a dependency-free snippet. By default, it also investigates resolvable local dependencies that block checking, labeling their findings separately from selected-source findings. A standalone `.rs` file works too. For an ambiguous workspace, use `--package NAME`; for different features, use `--features FEATURE`, or pass normal Cargo options after `--`.

```bash
./verus-gap /path/to/crate --file 'src/write/*.rs'
./verus-gap /path/to/workspace --package my_crate -- --no-default-features
./verus-gap --resume /path/to/verus-gap-results/RESULT
```

Choose the exploration policy with **`--scan-level {0,1,2}`**. The default is **0**; **`--fast` is an alias for level 2**.

| Level | What it does | When to choose it |
|---|---|---|
| `0` | Investigate selected source and local dependencies needed to continue checking | You also want dependency findings |
| `1` | Stay in selected source, trying to work around checking obstacles to find more gaps | You want a deeper look at the selected code |
| `2` / `--fast` | Stay in selected source, stopping when only checking obstacles remain | You want quick initial findings |

```bash
./verus-gap /path/to/crate/src/write/encoder.rs --scan-level 1
./verus-gap /path/to/crate/src/write/encoder.rs --fast --verbose
```

All levels use the same build checks and confirm each new support finding with a second run of the same code copy. Levels 1 and 2 check and report only the selected source, without expanding checking into dependency files. The difference is how long they keep looking, not how strongly they confirm a finding. Levels are not fixed runtime guarantees, and none guarantees that every gap will be found.

A **checking boundary** means Verus cannot currently use a referenced type, function, or trait because it is outside checking or has been explicitly excluded. This can happen inside your selected file as well as at a dependency. It is a checking obstacle, not automatically a Verus language or library-model gap. Level 1 keeps trying to work around these obstacles; level 2 stops when they are the only errors left, with no setup or instrumentation blocker. The report stays **partial** and retains those errors in `review.md`; more gaps may be hidden behind them. Conflicting options such as `--fast --scan-level 1` are rejected.

Add **`--verbose`** to see live probe/confirmation phases, round timings, scope expansion or exclusion actions, cumulative group counts, and newly confirmed limitations with original error messages and source locations:

```bash
./verus-gap /path/to/crate/src/write/encoder.rs --fast --verbose
./verus-gap --resume /path/to/verus-gap-results/RESULT --verbose
```

Output is flushed as progress becomes available, including through a file or pipe. A limitation is announced only after its confirmation and evidence have been saved. Repeated groups are not announced again; selected-source and dependency findings are labeled separately. Setup errors, checking boundaries and compiler failures are not announced as limitations. The final reports and raw evidence are still saved normally. Verbosity changes display only and can be enabled or disabled on resume.

Resume validates the input, compiler and dependency identities, reuses completed work, and continues unfinished scans. `--out DIR` sets the result location; `--timeout SECONDS` changes the total build/scan budget (default: one hour).

Resume inherits the recorded scan level when none is specified and rejects an explicitly different level. Level-2 boundary-only stops are deliberately finished under that policy, although coverage remains partial; resume reuses their evidence without another probe. Start a new output directory to change levels or investigate those boundaries further.

Use trusted projects: Cargo build scripts and procedural macros execute in the copied workspace.

## Read the result

Open the printed **`report.md`**. Its summary groups language rejections by error family and distinguishes your selected files from added dependency context. Detailed findings use public API names when available, original file locations, source excerpts, and unchanged compiler diagnostics. Source locations in `report.md` and `review.md` are clickable links to the unchanged source copies under `originals/`, with `#L` line anchors and visible line/column numbers. Line navigation depends on your Markdown viewer; keep `originals/` beside the reports when moving them. Unstable compiler definition numbers are normalized only for grouping, and different public APIs are kept separate. Counts are distinct diagnostic/API groups, **not counts of distinct Rust features or proved functions**.

| File | Purpose |
|---|---|
| `report.md` / `report.json` | Reproduced gaps, selected/dependency scope, coverage, and remaining frontiers |
| `review.md` | Local checking boundaries, migration requirements, instrumentation/setup problems, and compiler failures; not counted as support gaps |
| `project.json` / `replay.json` | Build configuration, compiler/support identities, adaptation records, and exact replay context |
| `shards/` | Every round's raw output, copied sources, source maps/edits, and confirmation run |
| `agent-tasks.json` | Bounded follow-up tasks for an optional agent |

**Exit 0:** the selected frontiers cleared without accepted gaps. **Exit 1:** reproduced gaps were found; coverage may still be partial. **Exit 2:** setup failed, the scan budget left files pending, or only unresolved frontiers remain. Always inspect the report, including on nonzero exits.

## What the scanner does—and does not establish

The scanner checks that the unchanged Rust project builds, checks its source preparation with ordinary Rust, and smoke-tests Verus support before interpreting application errors. Each counted finding must map to the original checked source and reproduce in a second run of the same prepared code. It temporarily excludes blocked code in the scan copy to reveal later errors, recording every exclusion. Existing Verus annotations and Rust derives are preserved; setup and preparation failures are reported separately, not counted as application support gaps.

Existing Verus-aware dependencies use official Cargo-Verus. Any support-version alignment happens **only in the scan copy**, with a recorded manifest diff and another Rust build check. Custom targets retain their actual target and build options; the tool does not silently switch to the host or disable proof lifetime checks. SMT proof verification is off during scanning.

**This is a compatibility investigation, not a proof or exhaustive inventory.** File shards retain the Rust dependency context, but unwrapped/generated items can remain checking boundaries. Scope expansion is conservative source indexing, not a semantic call graph. Early signature/trait/macro/compiler failures and excluded bodies can hide more gaps. Clearing a frontier after exclusions does not mean the original program is supported or verified. A library finding means its model was not recognized in the recorded configuration—not that no model exists anywhere or that the API is inherently unverifiable.

External path dependencies, generated assets, and unusual platform setup may require a self-contained prepared copy. The tool refuses to copy your home/filesystem root or follow symlinks outside the workspace. Failed setup is reported, never relabeled as an application gap.

Use the optional [agent skill](.github/skills/verus-gap/SKILL.md) to guide an agent through scanning and interpreting the evidence, or run the command directly in your terminal.

For Python package installation, `pip install .` also provides `verus-gap` and `verus-gap-setup`; its toolchain cache is `$XDG_CACHE_HOME/verus-gap` (or `~/.cache/verus-gap`, override with `VERUS_GAP_HOME`). The checkout's two commands use its local `.tools` directory.
