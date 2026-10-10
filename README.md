# Verus Gap

Find Rust constructs and library APIs that **the installed Verus cannot check in your project's real build context**. The scanner builds a copy, investigates active files, confirms observed gaps, and writes a grouped report with original source locations and compiler evidence. It never edits the input project or invents specifications.

## Install once

Run the commands below from this tool directory. Requires **Python 3.11+ and Rustup/Cargo**. Linux x86-64 is tested; the official installer also supports macOS x86-64/ARM64.

```bash
./setup
```

Setup installs the local Python environment and latest official Verus release with matching Rust, then checks the toolchain. It does not change your default Rust toolchain.

## Scan

Try Tokio after setup, choosing an unused clone destination:

```bash
git clone --depth 1 https://github.com/tokio-rs/tokio.git "$HOME/tokio-verus-gap"
./verus-gap "$HOME/tokio-verus-gap/tokio" --jobs 32 --verbose
```

This selects the Tokio library crate, enables its `full` feature, and shows live progress. **Level 2 is the recommended default**; no level option is needed. Use fewer than 32 jobs on smaller or busy machines.

For your own crate:

```bash
./verus-gap /absolute/path/to/your/crate --jobs 32 --verbose
```

Use trusted projects: Cargo build scripts and procedural macros execute in the scan copy.

**Then open the printed `report.md` path.** It contains the confirmed gaps, source locations, and coverage limits. Exit **1** means gaps were found, not that installation failed. For normal use, this is all you need.

---

## Optional reading

The sections below are reference material for custom scopes, deeper investigation, troubleshooting, or replay. You do not need them for a normal scan.

### Select sources and Cargo options

The scanner discovers Cargo, builds unchanged code with Verus's matching Rust, and scans active source. A crate-defined `full` feature is selected automatically unless you choose features explicitly; otherwise Cargo's default features are retained.

A file or subdirectory inside a crate selects that scope **without losing the containing crate's dependencies**, target, generated files, or build options. It is not treated as a dependency-free snippet. A standalone `.rs` file works too. Use `--package NAME` for an ambiguous workspace, `--bin NAME` for a specific binary, and repeat `--file RELATIVE_PATH_OR_GLOB` to select files. Choose features with `--features FEATURE`, or pass normal Cargo options after `--`.

```bash
./verus-gap /path/to/crate --file 'src/write/*.rs' --jobs 32 --verbose
./verus-gap /path/to/workspace --package my_crate -- --no-default-features
```

For a small built-in check, run `./verus-gap demo`. It intentionally reports missing `std::time::Instant` models and exits 1.

### Choose a scan level

**Keep the default level 2 for a normal gap inventory.** Levels 1 and 0 spend additional time exploring checking obstacles; use them only when you specifically need deeper selected-source investigation or dependency findings.

| Level | What it does | When to choose it |
|---|---|---|
| `2` (default) | Investigate selected source; stop when only checking boundaries remain | Recommended first pass |
| `1` | Stay in selected source and continue working around checking boundaries | Investigate gaps that may be hidden behind a boundary |
| `0` | Also investigate resolvable local dependencies needed to continue checking | Include dependency findings, labeled separately |

Measured comparisons show about **65% less end-to-end time**, with **over 95% of known gap groups** and **over 90% of their source locations** retained. Actual results vary by project; these are observed results, not an exhaustive coverage guarantee. See [measurement details](VALIDATION.md#scan-levels) if needed.

All levels retain the same Cargo build context, source-preparation checks, and second-run confirmation of new support findings. Levels 1 and 2 check and report only selected source. The difference is exploration depth, not finding confidence.

A **checking boundary** means a referenced type, function, or trait is currently outside Verus checking or has been explicitly excluded. It can occur inside a selected file as well as at a dependency and is not automatically a missing language feature or library model. A level-2 boundary-only stop remains **partial**, with unresolved evidence in `review.md`; more gaps can be hidden behind it. Start a new result directory with `--scan-level 1` or `--scan-level 0` only if you need that additional investigation.

### Concurrency and live progress

**Use `--jobs 32 --verbose` as a starting point for multi-file scans on a machine with sufficient CPU and memory.** The CLI default is one worker; choose fewer workers on smaller or busy machines. Free workers take the next pending file, largest original source first, rather than receiving fixed file partitions. Reports retain selected-file order (normally sorted paths) and sorted locations, regardless of dispatch or completion order.

`--verbose` shows worker readiness, dispatched and completed files, probe/confirmation phases, round timings, and newly confirmed groups with original errors and locations. **Ready workers and dispatched files are not completed files**: `[dispatch n/m]` counts this invocation's pending queue, while `[n/total]` counts finished file frontiers, including reused ones. New gap notices appear only after confirmation evidence has been saved; repeated groups are not announced again.

Concurrency and verbosity do not change the selected scope or confirmation standard. Each file's rounds remain sequential, and more workers do not guarantee linear speedup. `--timeout SECONDS` sets the total build/scan budget (default: one hour), not a separate allowance per worker. A fixed budget can leave different files unfinished at different worker counts; that coverage remains explicit.

### Evidence and exit codes

`report.md` groups language rejections by error family and library/model gaps by public API, separating selected-source and dependency findings. Counts are diagnostic/API groups, not distinct Rust features or proved functions.

**`review.md` is optional troubleshooting material**, not routine reading. Open it when investigating a setup/compiler failure or why particular code remains unchecked; a partial scan alone does not require reading it. The other files below retain evidence for deeper analysis and replay.

Locations link to unchanged files under `originals/`, with visible line/column numbers and `#L` anchors; line navigation depends on your Markdown viewer. Keep `originals/` beside the reports when moving them.

| File | Purpose |
|---|---|
| `report.md` / `report.json` | Reproduced gaps, source scope, coverage, and remaining file frontiers |
| `review.md` | Optional diagnostics for checking boundaries, migration work, setup/preparation problems, and compiler failures |
| `project.json` / `replay.json` | Build configuration, toolchain/dependency identities, adaptations, and replay commands |
| `originals/` / `shards/` | Unchanged sources and every round's raw output, source overlay, edits, and confirmation evidence |
| `workers/` | Private concurrent scan copies and their replay context |
| `agent-tasks.json` | Bounded follow-up tasks for an optional agent |

**Exit 0:** no accepted gaps and selected frontiers cleared. **Exit 1:** reproduced gaps found; coverage may still be partial. **Exit 2:** setup/scan failure, interruption, an exhausted budget, or unresolved frontiers without accepted gaps. An exit code is not a proof; inspect both findings and coverage.

### Refresh and resume

Reports are available after setup. During scanning, completed rounds refresh them when **at least one minute has elapsed and a new confirmed group has appeared** since the previous automatic refresh. Repeated locations or progress alone do not trigger a rewrite. Normal exit always refreshes the reports; an in-progress round keeps the previous snapshot, and a partially completed confirmation is never counted as a gap.

**Ctrl+C** stops active compilers, waits for scan-copy restoration, and writes an **interrupted** snapshot of completed rounds, including saved findings from active files. Pending files and unfinished frontiers remain visible. A scan failure exits 2 with its error and available evidence; a forced kill cannot run restoration handlers.

```bash
./verus-gap report /path/to/verus-gap-results/RESULT
./verus-gap --resume /path/to/verus-gap-results/RESULT --jobs 32 --verbose
```

`report` refreshes from retained originals and saved checkpoints, even during a scan. It rewrites only `report.md`, `review.md`, `report.json`, and `agent-tasks.json`; it does not run Cargo/Verus or resume scanning. Keep `project.json`, `originals/`, and `shards/`; the original input and copied build workspace are not required. Successful refresh exits 0 even if the inventory contains gaps; missing or inconsistent evidence is an error.

Resume validates the input, compiler, and dependency identities, reuses eligible saved frontiers, and continues unfinished investigation. You may change jobs, verbosity, or the total timeout, but not the saved scan level. Deliberately finished level-2 boundary-only frontiers are reused without another probe and remain partial. Start a new scan to change level, input, features, target, or Verus version. `--out DIR` chooses a result location.

### Scope and safety

Use trusted projects: Cargo build scripts and procedural macros execute in the copied workspace. The scanner validates unchanged Rust, source preparation, and Verus support before accepting application findings. Each counted support finding maps to checked original source and reproduces in a second run of the same prepared code. Blocked bodies or declarations can be temporarily excluded in the copy to reveal later errors; every exclusion is recorded. Existing Verus annotations and Rust derives are preserved.

Existing Verus-aware dependencies use official Cargo-Verus. Any support-version alignment happens **only in the scan copy**, with a recorded manifest diff and another Rust build check. Custom targets retain their actual target and build options; the tool does not silently switch to the host or disable proof lifetime checks. SMT proof verification is off during scanning.

**This is a compatibility investigation, not a proof or exhaustive inventory.** Generated items, checking boundaries, compiler failures, and exclusions can hide more gaps. Clearing a frontier after exclusions does not mean the original program is supported or verified. A library finding means its model was not recognized in the recorded configuration, not that no model exists anywhere or that the API is inherently unverifiable. Counts are not proof or function-coverage counts.

External path dependencies, generated assets, and unusual platform setup may require a self-contained prepared copy. The tool refuses to copy your home/filesystem root or follow symlinks outside the workspace. Failed setup is reported, never relabeled as an application gap.

For AI-assisted investigation, the optional [agent skill](.github/skills/verus-gap/SKILL.md) follows this README for usage and adds false-positive/root-cause analysis and solution-path assessment. A normal terminal scan needs no agent.

### Other setup options

Setup validates the official release's published SHA-256 and runs a Verus/vstd/Z3 smoke proof. The chosen release stays fixed until you run setup again.

Already have a complete Verus distribution? Use `./setup --verus /absolute/path/to/verus`. To pin a release, use `./setup --release VERSION`.

For Python package installation, `pip install .` also provides `verus-gap` and `verus-gap-setup`; its toolchain cache is `$XDG_CACHE_HOME/verus-gap` (or `~/.cache/verus-gap`, override with `VERUS_GAP_HOME`). The checkout's two commands use its local `.tools` directory.
