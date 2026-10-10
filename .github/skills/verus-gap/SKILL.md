---
name: verus-gap
description: Diagnose user-selected Verus limitations or crashes, test whether scanner findings are false positives, trace root causes, validate specification/code/compiler fixes, and prepare evidence for upstream contributions.
---

# Diagnose Verus limitations

Use this skill when a user asks why a particular API, Rust construct, diagnostic, or compiler crash occurs; whether a reported gap is a tool false positive; or what could make that code pass Verus. The scanner's output is evidence to investigate, not the final diagnosis. The core task is to explain the cause, test competing hypotheses, and identify a concrete repair or an actionable upstream contribution.

For scanning commands and parameters, locate the supplied tool's `README.md` and follow it rather than maintaining another usage guide here. Paths there are relative to the tool, not this skill's directory. Reuse existing results when possible. A user who only wants a gap inventory can stop at `report.md`; when diagnosing a limitation, do not stop at restating that report.

## Select the question and preserve the evidence

Start from the user's named API, diagnostic, original source location, report group, or crash trace. Establish whether the goal is explanation, a tested workaround, a specification, or a Verus fix. If no target is identifiable, ask which limitation matters instead of analyzing every partial file. Generated setup-task lists are not a substitute for the user's question.

Work in a separate investigation copy within the authorized scope. Preserve original code, reports, compiler/dependency identities, and raw evidence. Use trusted projects because build scripts and procedural macros execute. Prototype candidate changes in that copy when permitted; ask before changing production code, accepting behavioral or trust changes, undertaking substantial extra work, or publishing anything.

## Establish a valid failing case and rule out false positives

Read the selected group's original source and its exact checking context in `project.json`, `replay.json`, `report.json`, and the relevant shard/round. Inspect the full raw stdout/stderr, run metadata, prepared source, source maps, edits, and confirmation, not only the grouped diagnostic. Use `review.md` for a relevant crash or blocker; it is not required reading for every partial result.

Check the unchanged native Rust build, the ordinary-Rust preparation control, Verus support controls, and the recorded compiler, edition, target, features/cfg, generated assets, and dependency versions. Verify that the failing construct actually belongs to the requested checked source. An ignored or excluded item is not a missing library specification.

Reproduce the recorded failure in the same context before altering it. Compare original and prepared code to test whether annotations, macros, derives, cfg handling, source adaptation, or artifact dispatch introduced the error. Use a minimally annotated original-code control when feasible, preserving its real types and dependencies. Two identical prepared-code failures establish repeatability, not freedom from scanner false positives.

| Finding | What must be distinguished |
|---|---|
| Setup/preparation issue | Missing context or a manufactured error versus a rejection of valid original code |
| Checking boundary | An item outside checking versus a genuinely missing language/API model; later gaps may remain hidden |
| Language/translation rejection | The exact unsupported construct, trait instance, or generic combination, not an entire Rust feature |
| Library-model rejection | A missing model versus one that is inactive, version-mismatched, or inapplicable to this type/trait instance |
| Migration requirement | Needed supported annotations, modes, visibility, or bindings versus missing Verus support |
| Compiler failure | An ICE/panic versus an ordinary diagnostic, timeout, killed process, or resource failure |

If a control cannot be established, identify the missing evidence and keep the conclusion unresolved. Warnings alone are not fatal support-gap counts. A genuine compiler panic is a compiler defect even if unsupported or invalid input triggers it; that input should receive an appropriate diagnostic, not a crash.

## Trace the root cause and minimize the reproducer

Inspect the matching Verus release's source and specifications, not just current documentation or the diagnostic label. For a model failure, follow API/type/trait resolution and look for relevant `vstd` or external specifications, their bounds, cfg requirements, and registration. For a language rejection, locate its diagnostic emission site and inspect the missing translation case and supported neighboring cases. For a crash, follow the stack and failing invariant as described below.

Trace only the relevant path through macro/attribute rewriting, Rust HIR-to-VIR conversion, mode/trait checking, VIR lowering, erasure, or solver interaction. Explain what assumption or capability is missing and connect it to the user's actual construct. Use source and experiment evidence to distinguish a known cause from a plausible hypothesis; do not merely rename the error.

Reduce the failing input while preserving compiler, edition, target, cfg/features, relevant types, and the smallest real dependency closure. Require the same canonical diagnostic or crash signature and relevant failing frame. A generic nonzero exit or a different error in the same category is not a valid reduction. For a valid-source support claim, retain successful native/preparation controls during reduction.

Use focused changes to test competing explanations, such as the same API with a supported type instance, a direct call without the suspect macro adaptation, or an existing applicable model. Keep each experiment's source, command, context, and output separate. Do not delete derives, add assumptions, switch target, or disable proof lifetime checks simply to obtain a cleaner example.

An isolated comparison with a newer Verus version can establish whether the same reproducer is already fixed. Record both toolchains and results; never change the original result's identity or claim a version fixes the problem without reproducing that case.

## Develop and test a route to Verus acceptance

Recommend a route according to the user's goal, but go beyond a list of possibilities when a small candidate can be tested in the investigation copy. Separate scanner/configuration fixes, specification work, application workarounds, and compiler work:

| Route | Concrete work and required checks |
|---|---|
| Correct a scanner/configuration false positive | Fix the responsible transformation or context capture; retain the original construct and add a regression showing both valid preparation and the intended Verus behavior |
| Use existing supported annotations or models | Establish applicability to the recorded API/type instance; verify that the original operation is checked rather than ignored |
| Add a specification/model | Define the relevant abstract state and contracts, including ownership, aliasing, panics, and trait bounds; compare with Rust documentation and implementation, and state any trusted assumptions explicitly |
| Rewrite application code | Test a small supported alternative; check behavior, error/panic paths, ownership/lifetimes, API, performance, and the actual target as well as Verus acceptance |
| Improve Verus | Implement the missing case or repair the failing invariant in the responsible compiler/model layer; test intended acceptance or a proper diagnostic, without bypassing mode, lifetime, or soundness checks |

For each candidate, retain the failing baseline and show what changes after the patch. Run the smallest relevant native tests and Verus checks; add a regression for the actual bug and neighboring or negative cases so a fix cannot merely suppress the failure. A specification for external code may be trusted rather than implementation-verified: do not describe an assumed contract as a proved implementation.

State exactly what "passes" means: the scanner no longer reports the gap, Verus translates/checks the relevant code, or the requested SMT proof obligations succeed. The scanner disables SMT verification, so clearing its frontier does not establish proof success. When proof success is requested, run the relevant verification with SMT enabled and explain any remaining exclusions or trust boundaries.

If a full fix is too large or depends on an unresolved design choice, provide the smallest tested improvement, the specific missing work, and a reasoned feasibility assessment. Label untested patch sketches as hypotheses. Do not silently replace the requested goal with excluding the code or adding trust.

## Investigate Verus crashes as compiler bugs

Retain the complete raw panic/ICE output and exact command/environment. Grouped crash summaries may omit stack frames. Reproduce in an isolated copy, enable `RUST_BACKTRACE=full`, and use the recorded binary's supported diagnostic/logging options. Do not attribute a frontend-only scan crash to SMT unless the evidence shows solver interaction.

Use the first relevant compiler frame, assertion, or query to locate the exact source at the recorded commit. Inspect its callers and data/state invariants. Test which original construct or preparation change triggers it; distinguish a Verus defect from a scanner transformation defect, rustc/dependency failure, or environment problem.

Evaluate a concrete repair: for example, correct initialization order, handle a missing translation case, or replace an unjustified panic with a source-bound diagnostic. Establish why the repair preserves the intended invariant; simply removing an assertion or ignoring the item is not a fix. Distinguish preventing a crash from adding verification support for the triggering construct.

When feasible within the authorized work, build the matching source using its [build instructions](https://github.com/verus-lang/verus/blob/main/BUILD.md), add a focused `rust_verify_test` regression, and compare the original and patched compiler on the same reproducer. Consult the matching checkout's [architecture](https://github.com/verus-lang/verus/blob/main/source/CODE.md) and [contribution/testing guide](https://github.com/verus-lang/verus/blob/main/CONTRIBUTING.md). The documented targeted test form is `vargo test -p rust_verify_test --test <suite> <test>`; intermediate logs can be enabled with `VERUS_EXTRA_ARGS="--log-all"`. Run log-producing tests individually so they do not overwrite each other's artifacts.

For an upstream recording, Verus provides `--record`; check the chosen version's support and use it on the minimized failing invocation. The resulting zip can contain the entire crate and generated inputs. Create and inspect a non-sensitive, shareable reproducer and obtain permission before sharing; if private inputs cannot be removed, keep the recording local. The official [minimizer guide](https://github.com/verus-lang/verus/blob/main/source/tools/minimizers/README.md) can help, but its interestingness predicate must preserve this failure rather than accept any error.

## Prepare an actionable upstream contribution

Check for an existing issue, discussion, or fix before recommending a new one. Under the [Verus contribution policy](https://github.com/verus-lang/verus/blob/main/CONTRIBUTING.md), actionable bugs belong in [Issues](https://github.com/verus-lang/verus/issues); missing-feature designs and architectural changes should start in [Discussions](https://github.com/verus-lang/verus/discussions). A library-model contribution belongs in the appropriate specification library, not automatically in compiler code.

Prepare local technical handoff evidence: minimal source and provenance, compiler/Rust/target/dependency identities, replay instructions, expected versus actual behavior, complete relevant diagnostics/backtrace, controls, and any tested patch and regression results. Explain the proposed change, behavior impact, verification/trust consequences, remaining limitations, and whether a newer version reproduces it. Redact secrets and nonessential private code without breaking the public reproducer.

Keep a proposed patch focused and make its reproducer a regression. For new language support, explain the required translation and checking semantics and consult upstream before undertaking broad design work. For trusted library models, follow the contribution guide's soundness requirements rather than using permissive contracts to make tests pass.

Do not publish source, recordings, issues, or pull requests without authorization. Follow upstream's current AI-disclosure and human-authorship policy: a human contributor must review the work and write the PR description and subsequent PR discussion comments. Provide technical evidence for that handoff, not an automatically submitted contribution.

## Answer the user's question

Lead with a diagnosis of the selected limitation: genuine support gap, false positive, checking boundary, migration requirement, compiler bug, or unresolved. Give the precise cause with original-code and compiler/specification evidence, not just the report's category.

Present a concrete recommended repair or workaround and its tested result, or state exactly why only a hypothesis is available. Compare application, specification, and Verus-side options when relevant, including behavior, effort, and trust tradeoffs. For a crash, include the failing component, reproducer, likely or tested repair, and the appropriate upstream contribution path.

Link retained evidence and state the tested scope and remaining uncertainty. Keep `report.md` as the user's normal inventory entry point; do not require them to read every raw artifact. Counts, exclusions, frontend clearance, repeated errors, and exit codes are not correctness proofs or exhaustive coverage. Do not claim source restoration or successful verification beyond what the saved experiments establish.
