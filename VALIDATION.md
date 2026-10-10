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

The default-policy and report-refresh revision passed **91 tests** across `test_cli`, `test_report`, and `test_engine`, including real Cargo/Verus controls. Default and explicit level-2 runs retain identical counts, coverage, exclusions, and round/confirmation states. Resume preserves each saved level rather than applying the new default. Other controls cover rejected unknown options, explicit level-0 dependency expansion, level-1 continuation, boundary-only partial reports, Ctrl+C restoration, and evidence-only manual refresh. Python 3.11 grammar checks passed for all 21 source and test modules.

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

## Dynamically pooled file workers

The original jobs revision used a bounded thread pool that dispatched the next selected file to whichever worker became free; it did not prepartition files. Its measurements below are historical thread-worker results, not measurements of the newer process pool. The default is 1. Cargo/build controls run once, each worker has private writable sources and render caches, and dependency/support artifacts stay shared inputs. File exploration and confirmation remain sequential within each independent frontier. Workspace-bound compiler/environment paths and prepared internal source symlinks are rebound to each private copy. Cargo-Verus root dispatch keys also need rebinding: the [recorded official driver](https://github.com/verus-lang/verus/blob/426d8b01e7ffb910a36c15e1f870ff985bb61eef/source/rust_verify/src/cargo_verus.rs) hashes the manifest path into its package identity; simply moving `CARGO_MANIFEST_DIR` would otherwise dispatch ordinary Rust instead of Verus. Rebinding preserves the captured arguments and finalized dependency artifacts, and an unrecognized dispatch identity is an explicit error.

The jobs revision passes **153 targeted tests**: 97 across `test_cli`, `test_report`, and `test_engine`, plus 56 across `test_parallel`, `test_dependencies`, and `test_gap`. Python 3.11 grammar checks pass for all 23 source/test modules. Real controls cover serial versus 32-worker group/location equivalence, dynamically reused workers, generated assets and absolute source aliases, preserved Cargo features, level-0 dependency expansion, shared real Cargo-Verus ghost artifacts, changed-job-count resume without new probes, and Ctrl+C with two active compiler children that are subsequently reaped. Additional controls cover worker/copy errors, the shared deadline, repeated Ctrl+C during pool shutdown, deterministic aggregation, restoration metadata, and atomic completed-checkpoint publication.

Workers write a round's initial and required confirmation evidence before atomically publishing its checkpoint. Tests stage partial raw output and an incomplete temporary checkpoint while concurrent report readers run; those readers retain only previously published evidence. Automatic and manual global writers share the report lock. A real manual refresh during the 32-worker Tokio scan, with Cargo/Verus removed from `PATH`, completed in 7.887 seconds while two files remained active; all 2,672 included support-evidence references had completed, non-timeout confirmation runs. The snapshot remained explicitly running. Worker restoration is not claimed until pool shutdown verifies the private copies.

The full-Tokio result is `tokio-test/parallel-jobs32-20261010T072049-215409Z/results` in the parent checkout. It uses the same 822 original source/manifest hashes, generated dependency lock, `full` features, native cfg, Rust/Verus versions, compiler/support identities and checking policy as the completed level-2 reference. The saved lock was added only to a separate input copy; the original input and reference reports remain unchanged.

| Full-Tokio level-2 scan | Whole-command time | Compiler seconds, summed | Exploration / confirmation calls | Language / model groups |
|---|---:|---:|---:|---:|
| Previous single-worker run | approximately 52 min 58 s | 3,054.956 | 958 / 600 | 22 / 538 |
| Dynamic 32-worker pool | 308.781 s (5 min 9 s) | 3,978.422 | 958 / 600 | 22 / 538 |

All 560 exact canonical diagnostic/public-API group identities and all 2,865 primary group/source coordinates match, with no old-only or new-only findings. The 287 frontier statuses, scopes, round-state sequences and exclusions also match; 217 frontiers remain partial under the same policy. Every accepted support observation passed an independent retained-original, identical-overlay, repeated-diagnostic and compiler-context audit. All 9,184 active worker source copies and the coordinator workspace were restored, and no observed task-launched compiler child remained. The host reached 32 concurrent child commands; peak summed resident sets were approximately 18.3 GiB, which can double-count shared pages.

The observed wall reduction is about 90%, or 10.3x, not 32x. The first compiler probe began 126.2 seconds after scan start while the private worker contexts were initialized; setup, Python orchestration, I/O and long-tail files also contribute. The new whole-command time uses a monotonic subprocess timer; the previous figure is reconstructed from recorded start/final timestamps, and the scanner revisions differ. This is a pinned-input shared-host measurement with exact evidence equivalence, not a dedicated-host same-version A/B or a universal speedup guarantee. The previously published archives remain frozen and do not contain jobs support.

## Process pool and cached source-model startup

The original 64-thread run exposed a real initialization bottleneck: workers repeated all 287 source parses and Python declaration/preparation walks while competing for the GIL, leaving the user without probe updates for approximately 240 seconds. The replacement is a dynamically scheduled, persistent process pool using Python's portable `spawn` start method, not a fork of a potentially multithreaded coordinator. Setup serializes its existing source models once into a run-local cache. Each process loads independent cfg/declaration/preparation state once, without reconstructing the syntax index; tree-sitter handles are not serialized and any later explicit tree access remains lazy and process-local. Mutable render caches are reset on transfer. Private workspaces, original Cargo-Verus root dispatch, shared finalized artifacts and sequential per-file confirmation remain unchanged.

The process/cache revision passes **187 related tests** across `test_parallel`, `test_source`, `test_cli`, `test_engine`, `test_report`, `test_dependencies`, and `test_gap`. Python 3.11 grammar checks pass for all 24 source/test/launcher files. Spawned-worker controls prohibit parsing and verify that models are serialized once, not once per worker. Other controls cover independent mutable state and overlays, dynamic same-process reuse, source aliases, preserved cfg/features and real ghost artifacts, completed IPC snapshots, cancellation during initialization and large messages, repeated Ctrl+C, two actual compiler children being reaped, changed-job resume, and offline report refresh. Unexpected process exit stops the pool explicitly; deliberately killing a worker with a dirty source copy leaves restoration false and retains the original exit error.

The fresh pinned result is `tokio-test/process-jobs64-20261010T075626-309101Z/results` in the parent checkout. It preserves the reference's 822 original source/manifest hashes, dependency lock, native cfg, `full` features, build controls, compiler/support identities and level-2 policy. The original inputs and all three prior single/32/64-worker report sets remain unchanged.

| Full-Tokio process-pool measurement | Observed value |
|---|---:|
| Whole command | 158.016 s (2 min 38 s) |
| First displayed probe update after scan start | 5.5 s |
| All 64 worker-readiness updates visible | 6.2 s |
| Peak simultaneous compiler commands | 64 |
| Selected / processed files | 287 / 287 |
| Language / library-model groups | 22 / 538 |
| Exploration / confirmation calls | 958 / 600 |
| Compiler wall seconds, summed | 4,943.236 s |
| Peak summed scanner/descendant RSS | approximately 29.5 GiB |

All 560 canonical diagnostic/public-API groups and all 2,865 primary group/source coordinates exactly match the reference. The 287 frontier statuses, scopes, round sequences and exclusions also match; all 2,865 accepted observations pass the retained-original, identical-overlay, repeated-diagnostic and compiler-context audit. All 18,368 active worker source copies and the coordinator workspace are restored, and no observed task descendant remains. The 217 partial frontiers are still partial; neither concurrency nor caching turns them into a proof or exhaustive inventory.

These are monotonic whole-command and flushed-progress measurements on the same shared host, not guaranteed runtimes or a dedicated-host experiment. First-probe/readiness timestamps describe coordinator-visible progress, not sub-millisecond child launch timings. The worker count and scanner revision differ from the historical 32-thread run, so its 308.781 seconds versus the new 158.016 seconds is an observed combined improvement, not an isolated measurement of multiprocessing alone. RSS summation can double-count shared pages. The previously published archives remain frozen.

## Largest-source-first dispatch

The process pool now prioritizes pending files by descending original source byte count, breaking ties with the original selected-file ordinal. It reads sizes from the captured source models, without additional filesystem scans, and keeps dynamically assigning the next file to the first free process. The source/selected lists, report ordering, per-file isolation and confirmation remain unchanged. Single-worker scans retain their original order. Resume sorts only pending files; different priority can change the subset finished before a total budget expires. Pooled verbose output distinguishes monotonically increasing `[dispatch n/m]` pending-queue counts from finished-file totals.

The scheduling revision passes **80 related tests** across `test_parallel`, `test_cli`, and `test_report`. New controls cover descending dispatch, original ordinals and source order, equal-size tie breaks, pending subsets, serial behavior, and monotonic verbose dispatch/completion counts. A real nonuniform-size 32-file Cargo fixture runs serially and with 32 processes: dispatch order reverses by size, report files remain sorted, all support groups/source positions match, and resume changes job count without new probes. Existing process isolation, cancellation, failure, deadline, publication and offline-refresh controls remain intact.

The pinned full-Tokio larger-first results are `tokio-test/largest-first-jobs32-20261010T081357-020535Z/results` and `tokio-test/largest-first-jobs64-20261010T081125-608897Z/results` in the parent checkout. The 32-process selected-order control is `tokio-test/selected-order-jobs32-20261010T081737-590517Z/results`, run from an isolated exact copy of committed `771f96d` without altering the main checkout. The 64-process comparison uses the previously audited `process-jobs64-20261010T075626-309101Z` baseline, not a new measurement. All cases use the same 822 original source/manifest hashes, dependency lock, compiler/support identities, native cfg/features, controls and level-2 policy; task scans ran sequentially without overlapping task tests.

| Process workers | Selected-order whole-command seconds | Larger-first whole-command seconds | Time saved | Larger-first peak summed RSS |
|---|---:|---:|---:|---:|
| 32 | 173.004 | 153.067 | 19.937 s (11.52%) | 15.2 GiB |
| 64 | 158.016 | 144.615 | 13.401 s (8.48%) | 28.6 GiB |

All completed cases retain the same 560 support groups, all 2,865 primary group/source coordinates, coverage, 958 exploration/600 confirmation calls, frontier statuses, scopes, round sequences and exclusions. Both larger-first results pass the full retained-original, identical-overlay, repeated-diagnostic, compiler-context and restoration audit; their 9,184/18,368 active worker source copies are restored and no observed task descendant remains. The fresh 32-process control passes the same audit. Actual dispatch order matches descending captured source sizes while report shards remain in selected order. No original inputs, historical reports or user changes were modified.

The previously longest Tokio file, `net/tcp/socket.rs`, receives its first displayed probe at 3.7 seconds rather than 40.1 seconds with 32 processes. The approximate final single-file tail shrinks from 9.3 to 1.4 seconds; with 64 processes, that file still ends last and a roughly 38-second single-file tail remains. These tail estimates use displayed event times, not precise compiler start/finish instrumentation. File size is an imperfect cost heuristic; it moves likely long tasks earlier but does not parallelize a file's sequential exploration rounds.

For this measured workload/server, 32 processes is the balanced recommendation: 64 processes save only another 8.452 seconds (5.52%) while peak summed RSS is about 1.89 times higher. This does not change the CLI default of one worker or establish a universally optimal count. One sample per case on a shared host does not guarantee the same percentage on future runs; summed RSS can double-count shared pages. The user-requested cancellation of a redundant 64-process selected-order recheck restored its sources and retained an interrupted snapshot, which is explicitly excluded from these comparisons. No additional repeated baselines were run afterward.

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
