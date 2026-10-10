# Verus Gap

Find Rust constructs and library APIs that **the installed Verus cannot check in your project's real build context**. The scanner builds a copy, investigates active files, confirms observed gaps, and writes a grouped report with original source locations and compiler evidence. It never edits the input project or invents specifications.

## Install once

Requires **Python 3.11+ and Rustup/Cargo**. Linux x86-64 is tested; the official installer also supports macOS x86-64/ARM64.

```bash
./setup
```

Setup creates a local `.venv`, installs the declared Python dependencies, downloads the **latest official Verus release** with its published SHA-256, installs the matching Rust toolchain if necessary, and runs a Verus/vstd/Z3 smoke proof. It does not change your default Rust toolchain. The chosen release stays fixed until you run setup again.

Already have a complete Verus distribution? Use `./setup --verus /absolute/path/to/verus`. For reproducibility, use `./setup --release VERSION`.

## Scan

Try Tokio after setup. Run these commands from the tool directory, choosing an unused clone destination:

```bash
git clone --depth 1 https://github.com/tokio-rs/tokio.git "$HOME/tokio-verus-gap"
./verus-gap "$HOME/tokio-verus-gap/tokio" --jobs 32 --verbose
```

This selects the Tokio library crate inside the cloned workspace, enables its `full` feature, and scans with live progress and up to 32 concurrent file workers. **Scan level 2 is the default and the recommended first pass**; no level option is needed. The clone follows Tokio's default branch, so its findings can differ from the measured examples below.

For your own project or a small built-in example:

```bash
./verus-gap /absolute/path/to/your/crate --jobs 32 --verbose
./verus-gap demo
```

The demo intentionally reports missing `std::time::Instant` models and exits 1. For a real crate, the scanner discovers Cargo, builds the unchanged code with Verus's matching Rust, and scans active source. A crate-defined `full` feature is selected automatically unless you choose features explicitly; otherwise Cargo's default features are retained.

A file or subdirectory inside a crate selects that scope **without losing the containing crate's dependencies**, target, generated files, or build options. It is not treated as a dependency-free snippet. A standalone `.rs` file works too. Use `--package NAME` for an ambiguous workspace, `--bin NAME` for a specific binary, and repeat `--file RELATIVE_PATH_OR_GLOB` to select files. Choose features with `--features FEATURE`, or pass normal Cargo options after `--`.

```bash
./verus-gap /path/to/crate --file 'src/write/*.rs' --jobs 32 --verbose
./verus-gap /path/to/workspace --package my_crate -- --no-default-features
```

## Choose a scan level

**Keep the default level 2 for a normal gap inventory.** Levels 1 and 0 are not recommended for routine first-pass scans: they spend more time exploring checking obstacles and can add relatively few findings. Use them when you specifically need deeper selected-source investigation or dependency findings.

| Level | What it does | When to choose it |
|---|---|---|
| `2` (default) | Investigate selected source; stop when only checking boundaries remain | Recommended first pass |
| `1` | Stay in selected source and continue working around checking boundaries | Investigate gaps that may be hidden behind a boundary |
| `0` | Also investigate resolvable local dependencies needed to continue checking | Include dependency findings, labeled separately |

Recorded serial comparisons show the time/retention tradeoff:

| Selected input and reference | Level-2 time saved | Confirmed groups retained | Primary group/location records retained |
|---|---|---|---|
| Tokio mutex, compared with level 1 | **65.2%** whole-command time: 84.4 to 29.4 s | **100%** (15/15) | **100%** (39/39) |
| Base64, compared with a dependency-expanding reference | **83.6%** summed compiler time: 262.8 to 43.2 s | **97.3%** (71/73) | **92.2%** (106/115) |

A full-Tokio level-2 scan retained **100% of the 560 known reference groups**: 22 language groups and 538 library/API-model groups. Groups are distinct diagnostic families or named APIs, not distinct Rust features; a primary group/location record pairs a group with an original source coordinate. These percentages measure retention of observed reference findings, **not exhaustive coverage of every gap**.

The mutex comparison holds source, dependency lock, compiler, and scanner revision fixed; see [validation](VALIDATION.md#scan-levels). Base64 and full-Tokio reference comparisons use different scanner revisions, so they are not same-revision level-only benchmarks. Base64's time figure excludes setup and Python/report overhead. Shared-host measurements are examples, not guaranteed speedups or retention rates.

All levels retain the same Cargo build context, source-preparation checks, and second-run confirmation of new support findings. Levels 1 and 2 check and report only selected source. The difference is exploration depth, not finding confidence.

A **checking boundary** means a referenced type, function, or trait is currently outside Verus checking or has been explicitly excluded. It can occur inside a selected file as well as at a dependency and is not automatically a missing language feature or library model. A level-2 boundary-only stop remains **partial**, with unresolved evidence in `review.md`; more gaps can be hidden behind it. Start a new result directory with `--scan-level 1` or `--scan-level 0` only if you need that additional investigation.

## Concurrency and live progress

**Use `--jobs 32 --verbose` as a starting point for multi-file scans on a machine with sufficient CPU and memory.** The CLI default is one worker; choose fewer workers on smaller or busy machines. Free workers take the next pending file, largest original source first, rather than receiving fixed file partitions. Reports retain selected-file order (normally sorted paths) and sorted locations, regardless of dispatch or completion order.

For full Tokio on the measured server with 96 logical CPUs, 32 workers took **153.1 s** with about **15.2 GiB** peak summed RSS; 64 took **144.6 s** with about **28.6 GiB**. Doubling workers saved only another **5.5%** wall time, so 32 is the recommended resource/time balance for that workload. This is not a universal optimum; see [validation](VALIDATION.md#largest-source-first-dispatch).

`--verbose` shows worker readiness, dispatched and completed files, probe/confirmation phases, round timings, and newly confirmed groups with original errors and locations. **Ready workers and dispatched files are not completed files**: `[dispatch n/m]` counts this invocation's pending queue, while `[n/total]` counts finished file frontiers, including reused ones. New gap notices appear only after confirmation evidence has been saved; repeated groups are not announced again.

Concurrency and verbosity do not change the selected scope or confirmation standard. Each file's rounds remain sequential, and more workers do not guarantee linear speedup. `--timeout SECONDS` sets the total build/scan budget (default: one hour), not a separate allowance per worker. A fixed budget can leave different files unfinished at different worker counts; that coverage remains explicit.

## Read the result

Open the printed **`report.md`**, including on a nonzero exit. It groups language rejections by error family and library/model gaps by public API when available, separating selected-source and dependency findings. Locations link to unchanged files under `originals/`, with visible line/column numbers and `#L` anchors; line navigation depends on your Markdown viewer. Keep `originals/` beside the reports when moving them.

| File | Purpose |
|---|---|
| `report.md` / `report.json` | Reproduced gaps, source scope, coverage, and remaining file frontiers |
| `review.md` | Checking boundaries, migration requirements, setup/preparation problems, and compiler failures; not counted as support gaps |
| `project.json` / `replay.json` | Build configuration, toolchain/dependency identities, adaptations, and replay commands |
| `originals/` / `shards/` | Unchanged sources and every round's raw output, source overlay, edits, and confirmation evidence |
| `workers/` | Private concurrent scan copies and their replay context |
| `agent-tasks.json` | Bounded follow-up tasks for an optional agent |

**Exit 0:** no accepted gaps and selected frontiers cleared. **Exit 1:** reproduced gaps found; coverage may still be partial. **Exit 2:** setup/scan failure, interruption, an exhausted budget, or unresolved frontiers without accepted gaps. An exit code is not a proof; inspect both findings and coverage.

## Refresh and resume

Reports are available after setup. During scanning, completed rounds refresh them when **at least one minute has elapsed and a new confirmed group has appeared** since the previous automatic refresh. Repeated locations or progress alone do not trigger a rewrite. Normal exit always refreshes the reports; an in-progress round keeps the previous snapshot, and a partially completed confirmation is never counted as a gap.

**Ctrl+C** stops active compilers, waits for scan-copy restoration, and writes an **interrupted** snapshot of completed rounds, including saved findings from active files. Pending files and unfinished frontiers remain visible. A scan failure exits 2 with its error and available evidence; a forced kill cannot run restoration handlers.

```bash
./verus-gap report /path/to/verus-gap-results/RESULT
./verus-gap --resume /path/to/verus-gap-results/RESULT --jobs 32 --verbose
```

`report` refreshes from retained originals and saved checkpoints, even during a scan. It rewrites only `report.md`, `review.md`, `report.json`, and `agent-tasks.json`; it does not run Cargo/Verus or resume scanning. Keep `project.json`, `originals/`, and `shards/`; the original input and copied build workspace are not required. Successful refresh exits 0 even if the inventory contains gaps; missing or inconsistent evidence is an error.

Resume validates the input, compiler, and dependency identities, reuses eligible saved frontiers, and continues unfinished investigation. You may change jobs, verbosity, or the total timeout, but not the saved scan level. Deliberately finished level-2 boundary-only frontiers are reused without another probe and remain partial. Start a new scan to change level, input, features, target, or Verus version. `--out DIR` chooses a result location.

## Scope and safety

Use trusted projects: Cargo build scripts and procedural macros execute in the copied workspace. The scanner validates unchanged Rust, source preparation, and Verus support before accepting application findings. Each counted support finding maps to checked original source and reproduces in a second run of the same prepared code. Blocked bodies or declarations can be temporarily excluded in the copy to reveal later errors; every exclusion is recorded. Existing Verus annotations and Rust derives are preserved.

Existing Verus-aware dependencies use official Cargo-Verus. Any support-version alignment happens **only in the scan copy**, with a recorded manifest diff and another Rust build check. Custom targets retain their actual target and build options; the tool does not silently switch to the host or disable proof lifetime checks. SMT proof verification is off during scanning.

**This is a compatibility investigation, not a proof or exhaustive inventory.** Generated items, checking boundaries, compiler failures, and exclusions can hide more gaps. Clearing a frontier after exclusions does not mean the original program is supported or verified. A library finding means its model was not recognized in the recorded configuration, not that no model exists anywhere or that the API is inherently unverifiable. Counts are not proof or function-coverage counts.

External path dependencies, generated assets, and unusual platform setup may require a self-contained prepared copy. The tool refuses to copy your home/filesystem root or follow symlinks outside the workspace. Failed setup is reported, never relabeled as an application gap.

Use the optional [agent skill](.github/skills/verus-gap/SKILL.md) to guide an agent through scanning and interpreting the evidence, or run the command directly in your terminal.

For Python package installation, `pip install .` also provides `verus-gap` and `verus-gap-setup`; its toolchain cache is `$XDG_CACHE_HOME/verus-gap` (or `~/.cache/verus-gap`, override with `VERUS_GAP_HOME`). The checkout's two commands use its local `.tools` directory.
