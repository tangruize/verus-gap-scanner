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
./verus-gap "$HOME/tokio-verus-gap/tokio" --features full --jobs 32 --verbose
```

This selects the Tokio library crate, explicitly enables its `full` feature, and shows live progress. **Level 2 is the recommended default**; no level option is needed. Use fewer than 32 jobs on smaller or busy machines.

For your own crate:

```bash
./verus-gap /absolute/path/to/your/crate --jobs 32 --verbose
```

Use trusted projects: Cargo build scripts and procedural macros execute in the scan copy.

**Then open the printed `report.md` path.** It contains the confirmed gaps, source locations, and coverage limits. Exit **1** means gaps were found, not that installation failed. For normal use, this is all you need.

---

## Optional reading

Normal scans need only the steps above. Choose a topic below when you need additional control or troubleshooting.

### Select sources and Cargo options

Scan a file/module or choose Cargo features while retaining the containing crate's build context. See [scope and Cargo options](doc/REFERENCE.md#select-sources-and-cargo-options).

### Optional build configuration

Most projects need none. Use `--config PATH` for missing custom-target or build-script settings; matching profiles can also load automatically. See [configuration usage, format, and the Nanvix example](doc/REFERENCE.md#optional-build-configuration).

### Choose a scan level

Keep level 2 normally. Use levels 1 or 0 only for deeper investigation or dependency findings. See [levels and tradeoffs](doc/REFERENCE.md#choose-a-scan-level).

### Concurrency and live progress

Adjust workers for available resources, or interpret live progress and time budgets. See [concurrency and progress](doc/REFERENCE.md#concurrency-and-live-progress).

### Evidence and exit codes

For partial results, raw compiler evidence, or exit-code interpretation, see [reports and evidence](doc/REFERENCE.md#evidence-and-exit-codes).

### Refresh and resume

Refresh saved reports without rerunning compilers, or continue an unfinished scan. See [refresh, interruption, and resume](doc/REFERENCE.md#refresh-and-resume).

### Scope and safety

A gap inventory is not a correctness proof. For copying restrictions, dependency handling, and unchecked code, see [scope and safety](doc/REFERENCE.md#scope-and-safety).

### Other setup options

For an existing Verus installation, a pinned release, or Python package installation, see [setup options](doc/REFERENCE.md#other-setup-options).

Agents diagnosing a selected gap or crash should use the [diagnostic skill](.github/skills/verus-gap/SKILL.md). Detailed measurements are in [validation notes](doc/VALIDATION.md).
