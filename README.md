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

This selects the Tokio library and keeps its default Cargo features. Tokio enables none by default; add `--features full` only when you want to investigate its optional runtime, networking, and other APIs.

**Level 2 is the recommended default**; no level option is needed. Use fewer than 32 jobs on smaller or busy machines.

For your own crate:

```bash
./verus-gap /absolute/path/to/your/crate --jobs 32 --verbose
```

Use trusted projects: Cargo build scripts and procedural macros execute in the scan copy.

**Then open the printed `report.md` path.** It contains the confirmed gaps, source locations, and coverage limits. Exit **1** means gaps were found, not that installation failed. For normal use, this is all you need.

## What the report tells you

Read the confirmed findings together with the coverage summary. A **partial** scan can contain confirmed gaps while some code remains unchecked; additional gaps may be hidden there. Counts are diagnostic/API groups, not a count of distinct unsupported Rust features.

The scanner does not prove program correctness. If it continued by temporarily excluding code, a later clear result does not establish support for the original program. `review.md` is for investigating a particular setup/compiler failure or why code remains unchecked; it is not required reading for every partial report.

## Working with your project

### Scan only the code you care about

Pass a source file or module directory instead of the whole crate, or select files with `--file`. The scanner still builds the containing crate, preserving dependencies, target settings, and generated files rather than treating the selected code as a standalone snippet.

```bash
./verus-gap /path/to/crate --file 'src/mm/*.rs' --jobs 32 --verbose
```

Only files active in the chosen Cargo configuration are scanned. Use `--features FEATURE` to include optional code; `full` is not enabled automatically. For an ambiguous workspace, choose `--package NAME` or pass the crate's directory. Use `--bin NAME` to select a binary.

### Optional build configuration

Some projects rely on a Makefile or CI recipe to supply a custom target, features, or build-script environment variables. If the unchanged Rust build fails, inspect the reported error and `baseline.stdout` / `baseline.stderr`. Missing build context is a setup problem, not a Verus limitation; a configuration can supply those settings but cannot fix invalid Rust or missing tools.

Most projects need no scanner configuration. The tool can automatically use `verus-gap.toml` at the Cargo workspace root or a matching bundled profile, such as the Nanvix kernel recipe, and prints what it selects. To supply your own build recipe:

```bash
./verus-gap /path/to/crate --config /path/to/build-settings.toml --jobs 32 --verbose
```

`--config` replaces automatic selection; `--no-config` disables it without disabling Cargo's own configuration. The [configuration reference](doc/REFERENCE.md#optional-build-configuration) explains the TOML fields, matching rules, and Nanvix example.

## Further reading

The [command reference](doc/REFERENCE.md) covers detailed options and replay procedures. Use the [diagnostic skill](.github/skills/verus-gap/SKILL.md) when asking an agent to explain or repair a selected gap or crash. Performance measurements and evidence audits are kept separately in [validation notes](doc/VALIDATION.md).
