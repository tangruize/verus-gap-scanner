---
name: verus-gap
description: Identify Rust language and library-model gaps in a project's real Verus build context, with confirmed source-bound findings and explicit coverage limits.
---

# Verus gap investigation

Use this skill when the user wants to identify Rust constructs or library APIs that Verus cannot check in a project's build context. Run the supplied scanner, interpret its retained evidence, and explain remaining coverage limits. It does not require a particular agent framework.

## Run a scan

Locate the supplied tool directory or installed executable and read its README. Commands below are relative to the **tool**, not the directory where this skill was copied. Run setup once, then scan the authorized project:

```bash
./setup
./verus-gap /absolute/path/to/authorized/project --jobs 32 --verbose
```

Setup validates the latest official Verus distribution, matching Rust, vstd, and Z3. Register an explicitly requested existing distribution with `./setup --verus PATH`, or pin with `./setup --release VERSION`. Keep source, dependencies, and toolchain fixed when comparing findings. Installed commands are `verus-gap-setup` and `verus-gap`.

**Recommend the default scan level 2 for a first-pass inventory, with `--jobs 32 --verbose` when CPU/memory permit.** The CLI default is one worker; reduce jobs on smaller or busy machines. Do not routinely choose levels 1 or 0 just to maximize depth.

| Level | Use |
|---|---|
| `2` (default) | Selected-source investigation; stop when only checking boundaries remain |
| `1` | Deeper selected-source investigation past those boundaries, when specifically needed |
| `0` | Include resolvable local dependencies needed to continue checking, when dependency findings are requested |

The README's recorded serial comparisons explain this recommendation: level 2 saved **65.2% whole-command time** on Tokio mutex while retaining **100% of 15 groups and 39 primary group/location records**; Base64 saved **83.6% summed compiler time**, retaining **97.3% of groups (71/73)** and **92.2% of primary group/location records (106/115)**. A full-Tokio scan retained **all 560 known reference groups** (22 language, 538 library/model). Base64/full-Tokio references use different scanner revisions; their comparisons are not same-revision level-only benchmarks. Present these as measured retention, not guaranteed speedups or exhaustive recall.

A checking boundary means a referenced type, function, or trait is outside Verus checking or has been explicitly excluded. It can occur inside selected source or at a dependency and is not automatically a missing library model. Level-2 boundary-only stops remain **partial** and can hide additional gaps. All levels keep the same build controls and repeated confirmation; exploration depth is not finding confidence.

Preserve the requested scope. A file or subdirectory inside a crate retains its containing Cargo dependencies, features, target, and generated assets. Use `--package NAME` for ambiguous workspaces, `--bin NAME` for a binary, or repeated `--file RELATIVE_PATH_OR_GLOB` selections. A crate-defined `full` feature is automatically selected unless the user chooses explicit features or no-default-features; preserve other Cargo options after `--`. Do not expand to unrelated repositories.

Concurrent workers take the next pending file, largest original source first; reports remain in selected-file order with sorted locations. Each file's rounds and confirmations stay sequential and independent. `--verbose` shows worker readiness, dispatch/completion counts, round progress, and newly confirmed groups with original errors and locations. `[dispatch n/m]` counts pending tasks sent, not completed files. Concurrency and verbosity do not change scope or evidence standards; more workers do not guarantee linear speedup. The timeout is a total build/scan budget.

The scanner works in an isolated copy and validates unchanged Rust, prepared source, and Verus support before accepting application findings. Build scripts and procedural macros execute in that copy: use trusted input and never modify the original project. SMT verification is off; proof lifetime checking remains on.

## Read evidence before drawing a conclusion

Read `report.md`, `report.json`, `review.md`, and `project.json`, even on a nonzero exit. Inspect representative per-round `verus.txt`, source overlays, and confirmation evidence before interpreting each diagnostic family. Accepted support findings must map to checked original source and repeat under the same recorded overlay.

Markdown source links target unchanged files under `originals/`, not scan overlays. Keep that directory with the reports; `#L` navigation depends on the viewer. Raw diagnostic text stays unchanged, while original locations are source-mapped.

| Evidence | Interpretation |
|---|---|
| `language_rejection` | Reproduced Verus translation rejection tied to checked original source |
| `library_specification` | An external type/API model was not recognized in this build configuration |
| `checking_boundary` | Items outside checking or without usable trait coverage; not automatically a support gap |
| `derived_spec_warning` | Derive-related specification warning; not a fatal gap count |
| `migration_requirement` | Mode, binding, visibility, or other migration work; inspect the actual message |
| Rust/wrapper/instrumentation or dependency-artifact categories | Setup/preparation evidence, not established application support gaps |
| `compiler_failure` | Compiler ICE/panic, not a verdict that a Rust feature is unsupported |

Counts are canonical diagnostic/API groups, not unique language features, all unsupported operations, or proved functions. Primary group/location records pair a group with an original source coordinate. Report level, selected/dependency scope, pending and partial frontiers, exclusions, restoration state, compiler version, and cfg/features/target. Processing every selected file does not establish complete recall.

Exit 0 means no accepted gaps and cleared selected frontiers; exit 1 means reproduced gaps with possibly partial coverage; exit 2 indicates setup/scan failure, interruption, budget exhaustion, or unresolved frontiers without accepted gaps. Always inspect the reports rather than treating an exit code as a proof.

## Refresh or resume

Reports begin after setup and automatically refresh after a completed round when both a minute has elapsed and a new confirmed group exists. Repeated locations or progress alone do not trigger a rewrite; normal exit forces a refresh. Only saved, completed rounds enter snapshots, and unfinished confirmations are not accepted gaps.

```bash
./verus-gap report /path/to/verus-gap-results/RESULT
./verus-gap --resume /path/to/verus-gap-results/RESULT --jobs 32 --verbose
```

Manual refresh reads retained originals and checkpoints without Cargo/Verus or resuming. Preserve `project.json`, `originals/`, and `shards/`; it rewrites only the four generated inventory/review/task files. Successful refresh exits 0 even when gaps exist. Distinguish running, interrupted, stopped, and finished snapshots.

Ctrl+C stops active compilers, waits for restoration, and saves an interrupted report of completed rounds, including evidence from active files. Scan failures retain explicit errors and available evidence. Forced termination cannot run restoration handlers; never claim unverified copies are restored.

Resume validates identities and keeps the recorded level. Jobs, verbosity, and timeout may change; deliberately finished level-2 boundary-only frontiers reuse their evidence without another probe and remain partial. Start a new scan to change level, input, features, target, or Verus version.

## Investigate only an actual blocker

Use `agent-tasks.json` to choose one bounded setup, preparation, generated-code, or reduction task. Inspect its final evidence and work in a separate copy; do not run one agent per function or patch production sources without authorization.

For setup failures, replay the recorded command and logs. Restore required generated assets, local dependencies, target JSON, build-std/rust-src, environment, and project tooling without changing the platform or API. Missing imports/resources, mismatched compilers, and stale artifacts are not limitations.

For preparation failures, compare original source, prepared source, boundaries, and source maps. Resolve the actual transformation error rather than hiding it with exclusions. Preserve existing Verus annotations and Rust derives; do not invent contracts, assumed models, or blanket adaptations just to clear diagnostics. Retain the actual custom target and proof lifetime checking.

A reduction must keep compiler, edition, cfg/features, target, relevant generic types, and the smallest real dependency closure. Run unchanged Rust first, then reproduce the same construct/API and canonical diagnostic. A different error in the same broad category is not a successful reduction. Keep reduction source, commands, raw diagnostics, controls, and provenance separate from original evidence.

For compiler failures, preserve the trace and context without labeling the feature unsupported. For library findings, distinguish an unrecognized model in this configuration from an ignored local item or unsupported trait instance; do not claim that no model exists anywhere.

## Present a useful, bounded answer

Lead with observed language/API groups and important setup/coverage limits. Show typical original source and unchanged errors, link occurrences and evidence, and explain residual boundaries and exclusions. When discussing performance, identify the reference and distinguish whole-command time from summed compiler time.

Never call `frontend_clear`, an exclusion-cleared frontier, repetition alone, or exit 0 a correctness proof or exhaustive inventory. Stable repetition requires valid setup, preparation, and checked-source attribution. Generated declarations may not be fully enumerated, and dependency exploration is not a semantic call graph.
