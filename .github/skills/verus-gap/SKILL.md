---
name: verus-gap
description: Diagnose selected Verus gaps and crashes, distinguish scanner false positives, test compiler/code/specification repairs, and escalate architectural decisions.
---

# Diagnose Verus limitations

Select the user's API, construct, source location, diagnostic, or crash and establish the goal: explanation, workaround, specification, or Verus repair. Ask for a target if unclear; generated per-file setup tasks do not choose it. For scanning, follow the supplied tool's `README.md`; its paths are relative to the tool, not a copied skill. Reuse saved results. Inventory-only requests can stop at `report.md`.

Work in an authorized investigation copy and preserve originals, toolchain/dependency identities, and raw evidence. Use trusted inputs because build scripts and procedural macros execute. Ask before production changes, behavioral/trust changes, substantial extra work, or publication.

## Reproduce and classify

Read the selected original source, context in `project.json`/`replay.json`/`report.json`, and relevant shard/round evidence: full stdout/stderr, commands, prepared source, source maps, edits, and confirmation. Use `review.md` for the relevant crash or blocker, not every partial file.

Reproduce before altering the case. Match Verus/Rust versions, edition, target, cfg/features, generated assets, dependencies, and checked scope. Check the unchanged native build, ordinary-Rust preparation control, and Verus support controls. Compare original and prepared code for manufactured errors from annotations, macros/derives, cfg handling, or artifact dispatch; test a minimally annotated original-code control when feasible. Repeated prepared-code failures do not rule out a scanner false positive.

| Finding | Distinguish |
|---|---|
| Setup/preparation failure | Missing context or manufactured error, not a valid-source support gap |
| Checking boundary | Code outside checking, not automatically a missing model; later gaps may be hidden |
| Language rejection | The exact construct/type/trait combination, not an entire Rust feature |
| Library-model rejection | Absent versus inactive, version-mismatched, or inapplicable specification |
| Migration requirement | Missing supported annotations, modes, visibility, or bindings |
| Compiler failure | Verus panic/ICE versus scanner/rustc failure, ordinary diagnostics, timeout, or resource exhaustion |

Keep the diagnosis unresolved when required controls are missing. Warnings alone are not support gaps.

## Trace and minimize

Use the matching Verus release's source. For language errors, locate the diagnostic site and inspect the missing case and supported neighbors. For models, follow API/type/trait resolution through specification bounds, cfg, and registration. Trace the responsible translation/checking layer using the [source layout](https://github.com/verus-lang/verus/blob/main/source/CODE.md), and identify the missing semantic rule or violated invariant.

Reduce while preserving the toolchain, edition, target, cfg/features, relevant types, and necessary dependency closure. Require the same diagnostic/crash signature and relevant failing frame, not merely a nonzero exit. Retain successful native/preparation controls for valid-source support claims.

Test discriminating variants: a supported type instance, a direct call without suspect macro adaptation, or an applicable existing model. Keep each experiment's source, command, context, and output. A newer-version comparison must reproduce the same case and record both toolchains; it does not change the original evidence.

## Choose and validate a repair

First correct demonstrated scanner/context errors or enable applicable existing support. Otherwise use this priority order. A small Verus repair must have understood checking semantics, not merely a short diff.

| Situation | Preferred response |
|---|---|
| Small, semantically clear language-support omission | Repair Verus; add positive, negative, and regression tests |
| Difficult Verus support, simple equivalent code alternative | Use a code workaround; check behavior/API, ownership/lifetimes, errors/panics, performance, and target behavior |
| Missing usable library specification | Derive a project-scoped contract from std/library documentation, implementation, and vstd models |
| Confirmed Verus panic/ICE | Repair Verus with a regression; code avoidance is temporary mitigation |
| Both repairs difficult, or architectural changes needed | Obtain a human decision before implementation |

Implement and test bounded candidates within authorization. Retain the failing baseline and compare patched behavior using targeted native tests and Verus checks. Reject fixes that skip relevant code, drop constructs/derives, disable mode/lifetime checks, remove safety assertions, or add undeclared assumptions.

Distinguish scanner clearance, Verus translation/checking, and SMT proof success. The scanner disables SMT verification; requested proofs require SMT-enabled runs with exclusions and trust boundaries stated. Label untested patches as hypotheses.

## Build a project-scoped library contract

1. Read the matching std/core/library implementation and documentation, plus existing vstd models. Identify concrete types/trait instances, input domain, and the callers' required properties. Reuse applicable models; a function contract alone cannot add unsupported type or language semantics.
2. Derive only needed, justified `requires`/`ensures`, recording each clause's source basis. Stronger preconditions and weaker postconditions can narrow the contract. Separate documented guarantees from incidental implementation behavior and desired proof facts.
3. Account for safety, mutation/frame, ownership/aliasing, callbacks, panic/unwind, and invariant effects. Justify nonpanic conditions for a no-unwind contract. For trait APIs, use concrete justified instances or vstd's guarded/`obeys_*` patterns, not assumed laws for arbitrary implementations.
4. Before drafting [assume_specification](https://verus-lang.github.io/verus/guide/reference-assume-specification.html), find a similar function/method/trait-instance declaration in the matching release's `vstd`. Follow its syntax, imports, model helpers, and style, not its API-specific guarantees; consult that release's guide for unfamiliar syntax. Match the API's signature, lifetimes, generics, and trait bounds; express restrictions through preconditions, not signature changes. Check existing declarations and visibility: the specification is not a per-call assumption. A controlled wrapper may narrow the interface, but an unverified body remains trusted. Follow the [vstd soundness guidelines](https://github.com/verus-lang/verus/blob/main/CONTRIBUTING.md#guidelines-for-developing-vstd-contributions).
5. Check the candidate in the real context and verify callers' required properties, including every affected call's preconditions, with SMT enabled. Check for contradictory assumptions or vacuous proofs; exercise edge and negative cases. Tests and client proofs do not prove the assumed contract itself true.

Report the exact trusted facts and their applicability. Narrower assertions reduce the audit/error surface, not the external implementation's TCB dependency. If needed guarantees cannot be justified, escalate rather than inventing a contract that passes.

## Repair Verus crashes

For a confirmed Verus panic/ICE, retain full raw output and command/environment; grouped summaries may omit frames. Reproduce with `RUST_BACKTRACE=full` and supported logging options. Locate the first relevant frame/assertion/query at the recorded commit and inspect its callers and invariant. Do not blame SMT without evidence of solver interaction.

Repair the invariant or produce an appropriate source-bound diagnostic for unsupported input. Invalid or unsupported input should not crash the compiler. Merely removing an assertion is not a fix; preventing a crash does not necessarily add verification support.

Build the matching source/toolchain using its [build instructions](https://github.com/verus-lang/verus/blob/main/BUILD.md). Add a focused `rust_verify_test` regression and compare original/patched compilers on the same reproducer. The [testing guide](https://github.com/verus-lang/verus/blob/main/CONTRIBUTING.md) uses `vargo test -p rust_verify_test --test <suite> <test>` and `VERUS_EXTRA_ARGS="--log-all"` for intermediate logs; run logging tests individually to avoid overwrites.

## Escalate hard or architectural repairs

Pause implementation when both Verus and behavior-preserving code repairs are substantial or unresolved, or a route changes architecture, public API, concurrency/data model, platform assumptions, or significant trust. "Make it pass" does not authorize a redesign.

Present a decision brief:

- **Problem:** the exact blocker, affected verification goal, reproducible evidence, and known versus uncertain causes.
- **Options:** viable Verus/code/specification directions, affected components, effort, semantic/performance impact, coverage, and trust.
- **Recommendation:** the preferred direction, rationale, and smallest next milestone; mark untested feasibility claims.
- **Decision:** ask the human to confirm the problem/goal, choose a direction, and approve scope, compatibility, trust, and resource budget.

Use the host's question tool when available and wait for the decision before architectural implementation. Continue only bounded evidence collection already authorized; report the pending decision rather than claiming a fix.

## Prepare an upstream handoff

Check for an existing issue or fix. Follow the current [contribution policy](https://github.com/verus-lang/verus/blob/main/CONTRIBUTING.md): actionable bugs go to [Issues](https://github.com/verus-lang/verus/issues), feature/architecture designs to [Discussions](https://github.com/verus-lang/verus/discussions), and library models to the appropriate specification library. Consult maintainers before broad design work.

Prepare minimal source/provenance, exact compiler/Rust/target/dependency identities, replay commands, expected/actual behavior, full relevant diagnostics, controls, and patch/regression results. Follow the matching checkout's formatting/build requirements, including a standalone build when changing vstd.

If recording is useful, check version support for `--record`; its zip may contain the entire crate and generated inputs. Inspect a minimized, non-sensitive reproducer before authorized sharing; otherwise keep it local. The [minimizer guide](https://github.com/verus-lang/verus/blob/main/source/tools/minimizers/README.md) can help, but its predicate must preserve the selected failure.

Publication requires approval. Follow upstream's AI-disclosure policy: humans review and own the contribution and must write PR descriptions and subsequent PR discussion comments. Supply technical evidence for that handoff.

## Report the diagnosis

Lead with the classification and precise cause, then the recommended repair, tested outcome, remaining scope/uncertainty, and trusted assumptions. Link retained evidence. For crashes, identify the failing component, reproducer, and upstream route; for escalated cases, present the decision brief and say implementation awaits human direction. Do not claim exhaustive coverage or verified/restored code beyond the evidence.
