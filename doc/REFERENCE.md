# Command and configuration reference

Read only the sections relevant to your custom scope, build setup, investigation, or replay. For installation and normal scans, use the [README quick start](../README.md). Commands below run from the tool directory, not `doc/`.

## Select sources and Cargo options

The scanner discovers Cargo, builds unchanged code with Verus's matching Rust, and scans active source. Cargo's default features and build configuration are retained unless you choose options explicitly or a build configuration below is selected. Defining a `full` feature does not automatically enable it.

A file or subdirectory inside a crate selects that scope **without losing the containing crate's dependencies**, target, generated files, or build options. It is not treated as a dependency-free snippet. A standalone `.rs` file works too. Use `--package NAME` for an ambiguous workspace, `--bin NAME` for a specific binary, and repeat `--file RELATIVE_PATH_OR_GLOB` to select files. Choose features with `--features FEATURE`, or pass normal Cargo options after `--`.

```bash
./verus-gap /path/to/crate --file 'src/write/*.rs' --jobs 32 --verbose
./verus-gap /path/to/workspace --package my_crate -- --no-default-features
```

For a small built-in check, run `./verus-gap demo`. It intentionally reports missing `std::time::Instant` models and exits 1.

## Optional build configuration

**Use a configuration only when the project needs build settings that ordinary Cargo defaults do not supply**, such as Makefile-provided features, a custom target, `build-std` options, or build-script environment variables. An unchanged-Rust setup failure is not a Verus gap. Read the actual metadata/build error and `baseline.stdout` / `baseline.stderr` first; a configuration cannot repair invalid Rust, missing tools/assets, or an unsupported Verus construct.

Most projects need no extra file. Existing `.cargo/config.toml` settings are used by Cargo normally. The scanner prints every extra configuration it selects, using this order:

| Selection | Behavior |
|---|---|
| `--no-config` | Disable workspace and bundled scanner configurations; retain Cargo's own settings and explicit options |
| `--config PATH` | Load this TOML file instead of automatic selection; validate any declared matching conditions |
| Workspace `verus-gap.toml` | Load this optional file from the Cargo workspace root if any declared matching conditions apply |
| Bundled profile | Select only a profile whose declared conditions all match the selected Cargo package and workspace files |
| No match | Continue with ordinary Cargo defaults and explicit options |

`--config` and `--no-config` are mutually exclusive. Ambiguous or invalid configurations fail explicitly, rather than picking one or falling back silently. If neither Cargo nor a configuration selects a unique workspace package, use `--package` or a crate path.

The bundled [Nanvix kernel profile](../src/verus_gap/profiles/nanvix.toml) matches the Cargo package name, its declared homepage, and two required workspace files. It selects the x86 kernel with `microvm,trace`, matching standard-library build options, and a 256 MiB memory default that an existing environment value can override. It does **not** inspect Git remotes, contact GitHub, or rely on the checkout's directory name, so a fork or source copy can match. This is one explicit build recipe, not automatic discovery of every platform choice or guaranteed compatibility with future project versions.

```bash
./verus-gap /path/to/crate --config /path/to/custom.toml --jobs 32 --verbose
./verus-gap /path/to/crate --no-config --jobs 32 --verbose
```

A minimal custom file is:

```toml
version = 1

[cargo]
args = ["--features", "backend_a"]

[env]
PROJECT_MODE = "test"
```

Optional `cargo.package` and `cargo.bin` provide crate/binary defaults; an explicit crate path or command-line selector takes precedence. A workspace configuration for another selected package is not applied. `cargo.args` is an array of Cargo argument tokens, not a shell command. Explicit target, feature-selection, or profile options replace the corresponding configured defaults; other Cargo arguments are appended and retain Cargo's normal semantics. Use the dedicated package/bin fields instead of putting scanner-owned selection/output flags in `cargo.args`.

Optional `cargo.rustflags` is an array of rustc flag tokens appended to inherited flags, including `CARGO_ENCODED_RUSTFLAGS` when present. An `env` string supplies a default without replacing an existing environment value; use `{ value = "required-value", force = true }` when replacement is genuinely required. Toolchain/wrapper/output controls and Cargo-generated package metadata are managed by the scanner/Cargo. Keep credentials out of configurations; supply them through the normal shell/Cargo environment.

An optional `[match]` table can require `package`, exact Cargo-metadata `homepage` and/or `repository` values, and `files` that must exist. A matching table requires a package plus at least one additional condition; all conditions are ANDed. `repository` means the manifest's metadata, not a Git remote. File conditions and relative Cargo target paths are relative to the **Cargo workspace root**, even when the configuration is stored elsewhere. Strings are not shell-expanded.

The selected TOML is retained as `build-config.toml`, with its source path, hash, selection mode, and effective configured environment in `project.json`; replay includes that environment. Resume reuses the saved configuration and compiler context, without re-detecting profiles or requiring the original configuration file. Start a new scan to change the configuration. Saved-report refresh also validates the retained configuration.

## Choose a scan level

**Keep the default level 2 for a normal gap inventory.** Levels 1 and 0 spend additional time exploring checking obstacles; use them only when you specifically need deeper selected-source investigation or dependency findings.

| Level | What it does | When to choose it |
|---|---|---|
| `2` (default) | Investigate selected source; stop when only checking boundaries remain | Recommended first pass |
| `1` | Stay in selected source and continue working around checking boundaries | Investigate gaps that may be hidden behind a boundary |
| `0` | Also investigate resolvable local dependencies needed to continue checking | Include dependency findings, labeled separately |

Measured comparisons show about **65% less end-to-end time**, with **over 95% of known gap groups** and **over 90% of their source locations** retained. Actual results vary by project; these are observed results, not an exhaustive coverage guarantee. See [measurement details](VALIDATION.md#scan-levels) if needed.

All levels retain the same Cargo build context, source-preparation checks, and second-run confirmation of new support findings. Levels 1 and 2 check and report only selected source. The difference is exploration depth, not finding confidence.

A **checking boundary** means a referenced type, function, or trait is currently outside Verus checking or has been explicitly excluded. It can occur inside a selected file as well as at a dependency and is not automatically a missing language feature or library model. A level-2 boundary-only stop remains **partial**, with unresolved evidence in `review.md`; more gaps can be hidden behind it. Start a new result directory with `--scan-level 1` or `--scan-level 0` only if you need that additional investigation.

## Concurrency and live progress

**Use `--jobs 32 --verbose` as a starting point for multi-file scans on a machine with sufficient CPU and memory.** The CLI default is one worker; choose fewer workers on smaller or busy machines. Free workers take the next pending file, largest original source first, rather than receiving fixed file partitions. Reports retain selected-file order (normally sorted paths) and sorted locations, regardless of dispatch or completion order.

`--verbose` shows worker readiness, dispatched and completed files, probe/confirmation phases, round timings, and newly confirmed groups with original errors and locations. **Ready workers and dispatched files are not completed files**: `[dispatch n/m]` counts this invocation's pending queue, while `[n/total]` counts finished file frontiers, including reused ones. New gap notices appear only after confirmation evidence has been saved; repeated groups are not announced again.

Concurrency and verbosity do not change the selected scope or confirmation standard. Each file's rounds remain sequential, and more workers do not guarantee linear speedup. `--timeout SECONDS` sets the total build/scan budget (default: one hour), not a separate allowance per worker. A fixed budget can leave different files unfinished at different worker counts; that coverage remains explicit.

## Evidence and exit codes

`report.md` groups language rejections by error family and library/model gaps by public API, separating selected-source and dependency findings. Mutable static accesses share one group, with each affected global's locations and raw evidence listed separately. Counts are diagnostic/API groups, not distinct Rust features or proved functions.

**`review.md` is optional troubleshooting material**, not routine reading. Open it when investigating a setup/compiler failure or why particular code remains unchecked; a partial scan alone does not require reading it. The other files below retain evidence for deeper analysis and replay.

Locations link to unchanged files under `originals/`, with visible line/column numbers and `#L` anchors; line navigation depends on your Markdown viewer. Keep `originals/` beside the reports when moving them.

| File | Purpose |
|---|---|
| `report.md` / `report.json` | Reproduced gaps, source scope, coverage, and remaining file frontiers |
| `review.md` | Optional diagnostics for checking boundaries, migration work, setup/preparation problems, and compiler failures |
| `project.json` / `replay.json` | Build configuration, toolchain/dependency identities, adaptations, and replay commands |
| `build-config.toml` | Exact optional scanner configuration selected for this run, when one was used |
| `originals/` / `shards/` | Unchanged sources and every round's raw output, source overlay, edits, and confirmation evidence |
| `workers/` | Private concurrent scan copies and their replay context |
| `agent-tasks.json` | Generated per-file blocker hints, not diagnoses of individual gaps |

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

Use the [agent skill](../.github/skills/verus-gap/SKILL.md) to diagnose a limitation or Verus crash, investigate possible false positives, test fixes, or prepare upstream contribution evidence. Routine scanning follows the [README](../README.md); this reference covers advanced commands. A normal terminal scan needs no agent.

## Other setup options

Python must include `venv` and `ensurepip`; on Ubuntu/Debian, install `python3-venv` before running `./setup`. System-wide pip is not required. If setup fails, read the error, install any missing prerequisites, and rerun the same command. Setup checks its local pip and bootstraps it when an earlier attempt left an incomplete `.venv`; it does not delete existing packages or require you to remove the environment.

Setup validates the official release's published SHA-256 and runs a Verus/vstd/Z3 smoke proof. The chosen release stays fixed until you run setup again.

Already have a complete Verus distribution? Use `./setup --verus /absolute/path/to/verus`. To pin a release, use `./setup --release VERSION`.

For Python package installation, `pip install .` also provides `verus-gap` and `verus-gap-setup`; its toolchain cache is `$XDG_CACHE_HOME/verus-gap` (or `~/.cache/verus-gap`, override with `VERUS_GAP_HOME`). The checkout's two commands use its local `.tools` directory.
