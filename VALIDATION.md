# Validation and interpretation

This release was exercised on Linux x86-64 with Python 3.12, official Verus `0.2026.10.04.426d8b0` (`426d8b01e7ffb910a36c15e1f870ff985bb61eef`), and its matching Rust `1.98.1-x86_64-unknown-linux-gnu`. Python 3.11 grammar checks also pass; other Python/platform combinations were not run through the production corpus.

## Release checks

The complete **114-test suite passed from a fresh extracted source package**, including real Rust/Verus controls and Cargo dependency tests. The source launcher passed setup, a supported example, the intentional-gap demo, and identity-checked resume. A fresh installed wheel independently passed actual Cargo capture on Uluru, the same positive/negative examples, and resume. Its result-local Cargo capture wrapper was executable; installed Python sources matched the tested checkout.

The supported example exits 0 without accepted gaps. The intentional demo exits 1 with two observed `Instant` type/API model groups. Exit 1 is an expected diagnostic result, not an installation failure. Neither result is a correctness proof.

## Conservative performance changes

The working-tree scanner adds bounded last-overlay caching, cached diffs and byte/line indexes, and preindexed local dependency resolution. Cache keys include every rendering option and all body/declaration/context exclusions. Dependency resolution retains the previous exact-name, suffix, module-fallback and ambiguity rules, including removal of already checked files before resolving.

Confirmation is skipped only when every current support candidate already has an accepted observation with retained original evidence. It never treats another overlay as a confirmation of a new observation, never skips confirmation for an unconfirmed diagnostic, and still distinguishes the same message at different source positions. Compiler failures retain their repeated-run policy. These changes leave scope expansion and isolation order unchanged; the previously published 0.2.0 archives and project reports remain frozen.

The complete updated suite passes **122 tests**, including real Cargo/Verus controls. Same-input Uluru checks retain all eight model groups, the same 24 exploration rounds and the same exclusions; this small single-file crate has essentially unchanged runtime.

## Controls against misleading findings

Every production input first passed its unchanged native Rust build and an ordinary-Rust syntax-preparation recheck. Compiler support was smoke-tested before application diagnostics were interpreted. Accepted support observations were audited against a second run with identical source overlays and compiler context, original source locations, raw diagnostic text, compiler/support/dependency hashes, and restored copied sources. Production sources were not edited.

Real attribute-style controls distinguish two instrumentation defects from application limitations: original Rust/Verus code passes; deliberately adding another `verus!` pass manufactures missing-loop-variable errors; the corrected single attribute pass passes and still checks ordinary sibling methods. Instrumentation warnings in either the initial or confirmation run prevent support acceptance, including warning-only successful exits.

An independent model control used both the official host distribution and Nanvix's actual ghost-enabled kernel replay, including its custom target, build-std artifacts and VIR. `Vec::new/push/len` and `Option::is_some` are recognized in both. `Vec::capacity` passes the actual-target native Rust control but is explicitly rejected in both Verus contexts. This rules out an all-models-missing explanation for that observed API diagnostic; it is not a universal assertion about every library model.

## Selected ranges and dependencies

Actual file and directory inputs retain their containing Cargo root and features. A real nine-hop file-scope regression reaches the leaf's `Instant` diagnostic without checking an unrelated unsafe impl; a directory-scope regression retains features and follows two dependency hops. The production Tokio mutex-file experiment retains `full` Cargo context and reaches 69 additional source files. Its selected-source groups are reported separately from the larger checking context.

Local checking expansion follows uniquely indexed function/type boundaries without an arbitrary seven-file cap. It is diagnostic-driven file expansion, **not a semantic call graph or automatic annotation of arbitrary external dependency crates**. Existing Verus-aware dependencies use real Cargo-Verus ghost metadata/VIR. Ordinary dependencies retain their native build artifacts and may still require models or explicit checking boundaries.

## Read the project reports

The separate `verus-gap-reports` bundle provides per-project summaries, grouped original errors, source snapshots, recorded scanner revisions, native/setup controls, integrity audits, scope expansion, exclusions and unresolved frontiers. It covers Uluru, Bytes, Base64, HFS/libhfs, selected OpenVMM components, Nanvix virtual memory, and Tokio experiments. Consult each report's actual scope and code revision; a recorded older bounded experiment is not relabeled as a latest-code rerun.

Project workspaces and native/ghost compiler caches are deliberately not part of the small tool archive. Reports preserve their original result locations and replay identities; the report bundle is evidence, not a self-contained rebuild of every input project.

## Limits

Language and library counts are diagnostic/API groups, not independent Rust features or universally absent specifications. A library model was not recognized in the recorded configuration; another configuration, trait instance, adaptation or user specification may support it. Rust derives remain present, so a special derive rejection must not be generalized to all `Clone` or `Debug`.

Excluded bodies/declarations, ambiguous/generated boundaries, unindexed external source, compiler failures and exhausted budgets can hide later errors. A cleared frontier after exclusions is not support for the original program. Repetition and controls reduce known setup/instrumentation false positives; they do not establish zero false positives or exhaustive recall. SMT is off during inventory, and proof lifetime checks remain on.
