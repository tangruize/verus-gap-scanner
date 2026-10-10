# Validation and interpretation

This release was exercised on Linux x86-64 with Python 3.12, official Verus `0.2026.10.04.426d8b0` (`426d8b01e7ffb910a36c15e1f870ff985bb61eef`), and its matching Rust `1.98.1-x86_64-unknown-linux-gnu`. Python 3.11 grammar checks also pass; other Python/platform combinations were not run through the production corpus.

## Release checks

The complete **114-test suite passed from a fresh extracted source package**, including real Rust/Verus controls and Cargo dependency tests. The source launcher passed setup, a supported example, the intentional-gap demo, and identity-checked resume. A fresh installed wheel independently passed actual Cargo capture on Uluru, the same positive/negative examples, and resume. Its result-local Cargo capture wrapper was executable; installed Python sources matched the tested checkout.

The supported example exits 0 without accepted gaps. The intentional demo exits 1 with two observed `Instant` type/API model groups. Exit 1 is an expected diagnostic result, not an installation failure. Neither result is a correctness proof.

## Conservative performance changes

The working-tree scanner adds bounded last-overlay caching, cached diffs and byte/line indexes, and preindexed local dependency resolution. Cache keys include every rendering option and all body/declaration/context exclusions. Dependency resolution retains the previous exact-name, suffix, module-fallback and ambiguity rules, including removal of already checked files before resolving.

Confirmation is skipped only when every current support candidate already has an accepted observation with retained original evidence. It never treats another overlay as a confirmation of a new observation, never skips confirmation for an unconfirmed diagnostic, and still distinguishes the same message at different source positions. Compiler failures retain their repeated-run policy. These changes leave scope expansion and isolation order unchanged; the previously published 0.2.0 archives and project reports remain frozen.

The conservative revision (`4040d79a6756`, commit `7dfba68`) passes **122 tests**, including real Cargo/Verus controls. Same-input Uluru checks retain all eight model groups, the same 24 exploration rounds and the same exclusions; this small single-file crate has essentially unchanged runtime.

## Focused fast mode

The fast-mode revision (`112fdd0b3755`) passes **128 tests**. Fast-mode regressions cover actual Cargo file/directory scopes, retained features, no recursive dependency-body checking, selected-only primary and related locations, unchanged raw diagnostics, explicitly partial dependency-limited frontiers, inherited mode on resume, and refusal to change an existing scan's recorded exploration policy.

Focused checking is a deliberate scope tradeoff, not a semantics-preserving optimization of dependency-expanding exploration. It retains the native build and source-preparation controls, but does not cross local checking boundaries into unselected files. An encountered boundary keeps the frontier partial even if later exclusions remove the final compiler errors. File shards remain independent even when several files or a directory are selected.

The published 114-test source/wheel archives remain unchanged. These conservative and fast-mode revisions are available in the checkout; the old archives do not contain them.

## Scan levels

New scans default to level 2. `--scan-level 0` explicitly selects dependency-expanding investigation, and level 1 retains selected-source-only continued isolation. Level 2 ends a frontier before further isolation when its current error diagnostics are only checking boundaries, excluding compiler summaries. Setup/instrumentation blockers, other errors, timeouts and compiler failures do not satisfy this early-stop condition. New support observations keep the same repeated-confirmation requirement at every level.

The project, shard, grouped report and follow-up tasks record the numeric level. Resume inherits it, rejects a conflicting explicit level, and reuses deliberately finished `checking_boundary_only` frontiers without new compiler calls. Those frontiers retain unresolved raw evidence and remain partial; a higher level can miss support gaps hidden behind the checking boundary. Levels describe exploration depth, not a fixed number of seconds.

The current default-policy and report-refresh regression run passes **91 tests** across `test_cli`, `test_report`, and `test_engine`, including real Cargo/Verus controls. Default and explicit level-2 runs retain identical counts, coverage, exclusions, and round/confirmation states. Resume preserves each saved level rather than applying the new default. Other controls cover rejected unknown options, explicit level-0 dependency expansion, level-1 continuation, boundary-only partial reports, Ctrl+C restoration, and evidence-only manual refresh. Python 3.11 grammar checks pass for all 21 source and test modules.

The recorded targeted 47-test run passed CLI, classification, confirmation, early-stop, reporting, real streamed level-2 output, and real level-1 resume controls. Additional real Cargo/Verus controls exercised level-0 dependency expansion, selected directories, boundary-only partial reports, preserved native features and cached evidence. Python 3.11 grammar checks passed for all seven changed Python files.

Sequential pinned Tokio mutex runs use the same source, dependency lock, Cargo targets/profiles/features, and compiler/support identities as the earlier comparison:

| Policy | Whole-command seconds | Scan-phase seconds | Compiler seconds | Exploration / confirmation calls | Language / model groups |
|---|---:|---:|---:|---:|---:|
| Level 1, continued selected-source isolation | 84.353 | 70.5 | 69.149 | 34 / 4 | 5 / 10 |
| Level 2, boundary-only early stop | 29.368 | 16.0 | 15.835 | 5 / 4 | 5 / 10 |

Level 1 exactly retains the prior focused policy's 34 state fingerprints, exclusions, diagnostic counts, 39 accepted observation keys, and primary/related source positions. Level 2 retains all those accepted observations, 39 primary and 11 related positions, and the identical first five state fingerprints. It stops at round 004, the first boundary-only round, with 32 body and 13 declaration exclusions instead of 36 and 65. Its recorded checking-boundary groups decrease from 18 to 12 because later boundary cleanup is deliberately not performed, not because those items became supported. Both frontiers remain partial.

Independent repeated-evidence and identity/restoration audits pass both runs. A production level-2 resume inherits level 2, adds no probes or confirmations, and preserves the hashes and modification times of all 29 audited baseline/round/confirmation evidence files. These shared-host timings are measured observations, not fixed level durations; this mutex result does not establish equal recall on other inputs.

## Measured Tokio mutex comparison

The final comparison uses the same Tokio 1.53.2 source, Verus/Rust versions, `full` features, and the exact dependency lock generated by the original scan. The baseline input did not contain a lock, so its generated `Cargo.lock` was explicitly added to a separate identical input copy before the final reruns. All 822 original source/manifest hashes and all 23 native Cargo package targets, profiles and feature sets match. Compiler arguments are compared after normalizing isolated output paths and Cargo artifact disambiguators, not build options.

| Mutex-file experiment | Command wall seconds | Compiler seconds | Exploration / confirmation runs | Total compiler calls | Language / model groups, overall | Language / model groups, mutex only | Extra checked files |
|---|---:|---:|---:|---:|---:|---:|---:|
| Working baseline (`bf663f586eea`) | ~713.425 | 509.849 | 121 / 113 | 234 | 13 / 184 | 5 / 10 | 69 |
| Conservative optimization (`4040d79a6756`) | 495.468 | 439.828 | 121 / 83 | 204 | 13 / 184 | 5 / 10 | 69 |
| Focused fast mode (`112fdd0b3755`) | 84.464 | 69.034 | 34 / 4 | 38 | 5 / 10 | 5 / 10 | 0 |

The conservative comparison retains all 197 support groups, 402 accepted observation keys, 402 primary and 920 related source locations, all 121 round-state fingerprints, scope-expansion events, exclusions, diagnostic category counts, and final status. Its 30 skipped confirmations concern observations whose original exact-repeat evidence remains retained; every accepted observation independently passes the evidence audit.

Fast retains all 15 mutex-local support groups and all 38 original primary locations. It adds one reproduced `UnsafeCell` occurrence on the `Mutex` declaration, not another model group. Eight secondary spans from the broader baseline are not repeated in that role, but all eight coordinates remain primary locations of the same confirmed groups. No original selected-source coordinate is lost, and all grouped primary/related locations stay in the selected file. The 39 accepted fast observations independently pass the same controls and repeated-evidence audit. Its one frontier remains deliberately dependency-limited and partial; this result is not a general equal-recall guarantee.

Wall measurements include native setup/controls and scanning but exclude earlier tool installation. The original wall figure is reconstructed from its result-directory timestamp and final unchanged report timestamp; new figures use a monotonic subprocess timer. The final conservative and fast reruns were sequential in independent build/output directories, without overlapping this task's test suite. This is a shared host, not a controlled dedicated-host benchmark: observed wall reductions of about 31% and 88% are measurements, not universal speedup promises. Preliminary unpinned runs resolved newer `syn`/`smallvec` versions; their evidence is retained but their timings are not used in this same-dependency table.

## Live verbose output

`--verbose` observes existing probe, confirmation, checkpoint and isolation/expansion events. It changes terminal output only; it does not add compiler calls, change source overlays, persist a new scan mode, or replace the final reports. New limitation notices require a stored confirmed support observation, and group identities reuse canonical messages and public API names. Level-0 output labels dependency context separately; level-1/2 output keeps primary locations in selected source. Cached/resumed observations seed counts without being announced again.

Related CLI, engine/report and actual Cargo/Verus regressions pass. A pipe-based real fixture receives a flushed limitation before its frontier finishes and verifies that both initial/confirmation evidence and the accepted checkpoint already exist. Quiet and verbose runs retain identical counts, exclusions, scope events and round/confirmation states. Additional controls cover non-support/unconfirmed filtering, exact raw messages, public-API grouping, immutable records, cached resume with verbosity enabled then disabled, and ordinary dependency expansion in verbose mode.

The recorded pinned Tokio mutex focused verbose run retained all 15 support groups, 39 accepted observations, 39 primary and 11 related positions, all 34 round-state fingerprints, the same exclusions and deliberately partial frontier, and the same 38 compiler calls (34 probes and four confirmations). Every live group matched its unchanged accepted diagnostic and selected-source primary location; no group was announced twice. Its first limitation appeared 3.5 seconds after scanning started, all 15 groups were visible by 10.5 seconds, and the final round finished at 70.2 seconds. Those progress timestamps exclude native setup; the full command took 83.715 seconds on the shared host. Independent result-equivalence and repeated-evidence audits passed. This is a live-output check, not another general speedup claim.

## Controls against misleading findings

Every production input first passed its unchanged native Rust build and an ordinary-Rust syntax-preparation recheck. Compiler support was smoke-tested before application diagnostics were interpreted. Accepted support observations were audited against a second run with identical source overlays and compiler context, original source locations, raw diagnostic text, compiler/support/dependency hashes, and restored copied sources. Production sources were not edited.

Real attribute-style controls distinguish two instrumentation defects from application limitations: original Rust/Verus code passes; deliberately adding another `verus!` pass manufactures missing-loop-variable errors; the corrected single attribute pass passes and still checks ordinary sibling methods. Instrumentation warnings in either the initial or confirmation run prevent support acceptance, including warning-only successful exits.

An independent model control used both the official host distribution and Nanvix's actual ghost-enabled kernel replay, including its custom target, build-std artifacts and VIR. `Vec::new/push/len` and `Option::is_some` are recognized in both. `Vec::capacity` passes the actual-target native Rust control but is explicitly rejected in both Verus contexts. This rules out an all-models-missing explanation for that observed API diagnostic; it is not a universal assertion about every library model.

## Selected ranges and dependencies

Actual file and directory inputs retain their containing Cargo root and features. Explicit level-0 regressions follow nine dependency hops from a selected file and two from a selected directory, exposing the leaf's `Instant` diagnostic without checking an unrelated unsafe impl. The recorded dependency-expanding Tokio mutex-file experiment retains `full` Cargo context and reaches 69 additional source files. Its selected-source groups are reported separately from the larger checking context.

Local checking expansion follows uniquely indexed function/type boundaries without an arbitrary seven-file cap. It is diagnostic-driven file expansion, **not a semantic call graph or automatic annotation of arbitrary external dependency crates**. Existing Verus-aware dependencies use real Cargo-Verus ghost metadata/VIR. Ordinary dependencies retain their native build artifacts and may still require models or explicit checking boundaries.

## Read the project reports

The separate `verus-gap-reports` bundle provides per-project summaries, grouped original errors, source snapshots, recorded scanner revisions, native/setup controls, integrity audits, scope expansion, exclusions and unresolved frontiers. It covers Uluru, Bytes, Base64, HFS/libhfs, selected OpenVMM components, Nanvix virtual memory, and Tokio experiments. Consult each report's actual scope and code revision; a recorded older bounded experiment is not relabeled as a latest-code rerun.

Project workspaces and native/ghost compiler caches are deliberately not part of the small tool archive. Reports preserve their original result locations and replay identities; the report bundle is evidence, not a self-contained rebuild of every input project.

## Limits

Language and library counts are diagnostic/API groups, not independent Rust features or universally absent specifications. A library model was not recognized in the recorded configuration; another configuration, trait instance, adaptation or user specification may support it. Rust derives remain present, so a special derive rejection must not be generalized to all `Clone` or `Debug`.

Excluded bodies/declarations, ambiguous/generated boundaries, unindexed external source, compiler failures and exhausted budgets can hide later errors. A cleared frontier after exclusions is not support for the original program. Repetition and controls reduce known setup/instrumentation false positives; they do not establish zero false positives or exhaustive recall. SMT is off during inventory, and proof lifetime checks remain on.
