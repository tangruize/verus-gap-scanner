---
name: verus-gap
description: Investigate source-bound Verus gaps, distinguish false positives and checking boundaries from support gaps, explain root causes, and assess specification, code-workaround, or upstream solution paths.
---

# Verus gap investigation

Use this skill to identify Rust constructs or library APIs that Verus cannot check in a project's build context. Its role is investigation and interpretation, not implicit authorization to modify the application or Verus. It does not require a particular agent framework.

## Run and summarize

Locate the supplied Verus Gap tool or installed executable and its `README.md`. Follow the README's quick start for setup, scanning, and reading the result; consult its optional sections for command/parameter details only as needed. Paths in those instructions are relative to the tool, not this skill's directory. If the tool or its README is missing, request its location rather than guessing commands.

Preserve the user's requested scope and actual build configuration. Use trusted input because Cargo build scripts and procedural macros execute in the scan copy. Start with `report.md`, even on a nonzero exit. For an ordinary inventory, summarize observed language/API groups, representative original locations, and important coverage limits, then stop. Do not automatically recheck every finding or launch deeper investigation for every partial frontier.

## Optional deeper analysis

Continue when the user requests analysis or a concrete blocker prevents a reliable inventory. Select a bounded set of important or uncertain groups; use existing evidence before adding compiler runs. `agent-tasks.json` can identify a specific unresolved task. Do not run one agent per function.

### Establish whether the claim is real

Inspect `project.json`, `replay.json`, `report.json`, and the relevant round's original sources, prepared overlay, source maps, raw `verus.txt`, and confirmation. Check unchanged-Rust, source-preparation, and support controls, together with the recorded compiler, target, features/cfg, and dependency identities.

Confirm that the diagnostic maps to the original checked construct and repeats under the same prepared code and build context. Compare the overlay with the original: a conversion error can repeat just as reliably as a genuine gap. Repetition alone is not sufficient to rule out a false positive.

| Evidence | Interpretation and next question |
|---|---|
| Native build, imports, generated assets, or dependency artifacts fail | Setup blocker: restore the recorded context, not a different platform or program API |
| Preparation introduces the rejected construct or changes its meaning | Suspected scanner false positive: investigate the transformation, not application support |
| A referenced type/function/trait is ignored, excluded, or outside checking | Checking boundary: what remains unchecked, and could it hide later findings? |
| Verus explicitly rejects an original construct | Candidate language/translation gap: which exact construct or instance is rejected? |
| An external type/API has no recognized model | Candidate model gap in this configuration: is the needed model absent, inactive, or inapplicable to this instance? |
| Mode, binding, visibility, or annotation requirements fail | Migration requirement: does supported code need different verification annotations? |
| Compiler ICE/panic | Compiler failure: retain the trace; do not conclude the whole feature is unsupported |

Warnings alone are not fatal support-gap counts. Use `review.md` only for the relevant failures or unchecked code; do not make users read it routinely or solely because coverage is partial. If controls or attribution are incomplete, label the claim unresolved rather than declaring it genuine or false.

### Explain the root cause and reproduce only as needed

Name the precise Rust construct, API, trait instance, or generic combination, and separate it from the broader diagnostic family. A model not recognized in this configuration does not establish that no model exists anywhere. Distinguish missing library specifications from compiler language support and from local items that were never checked.

When the saved evidence is insufficient, replay the recorded command in a separate copy. A minimal reduction must retain compiler, edition, target, cfg/features, relevant generic types, and the smallest real dependency closure. Run unchanged Rust first, then reproduce the same construct/API and canonical Verus diagnostic. A different error in the same category is not a successful reduction.

Keep reduction provenance, source, commands, diagnostics, and controls separate from original evidence. Preserve Verus annotations and Rust derives; do not silently change the target, disable proof lifetime checks, or add assumptions to obtain a cleaner example.

### Assess solution paths without silently implementing them

Choose recommendations according to the user's goal: making this project verifiable, producing a reusable specification, or improving Verus support. Diagnose a setup or scanner defect before recommending any application workaround.

| Path | When it fits | What to establish |
|---|---|---|
| Existing support or configuration | A suitable model or supported construct already exists | Applicability to the recorded version, target, and type/trait instance; no silent toolchain change |
| Library specification/model | The implementation is available but Verus lacks a usable contract or type model | A reusable, justified specification; distinguish proved obligations from explicitly authorized trust assumptions |
| Application-code workaround | A small supported alternative can unblock this project | Behavior, panic/error paths, ownership, API, and performance implications; remaining verification boundaries |
| Upstream Verus improvement | Language/translation support is missing or the compiler fails | A reproducible version-pinned case, precise diagnosis, and a clear distinction between compiler work and model-library work |

Prefer existing supported solutions or reusable specifications over scattered code changes when they meet the same goal. A behavior-preserving workaround can be appropriate when near-term project verification matters most; it is not evidence that Verus has gained support for the original code.

Compare expected effort, semantic risk, applicability, and verification/trust consequences. If implementing a solution is requested but the intended goal or acceptable behavioral/trust changes are unclear, obtain that decision first. Do not patch production code, change specifications or trust assumptions, or submit upstream issues/patches without authorization. Exclusions and assumed contracts must never be presented as a proved fix.

## Present a bounded analysis

For each selected group, state the assessment (support gap, setup/preparation issue, checking boundary, migration work, compiler failure, or unresolved), the root cause and uncertainty, representative original code/diagnostic, and links to retained evidence or a reduction. If solutions are relevant, give the recommended path, alternatives, and their tradeoffs; distinguish recommendations from implemented changes.

Keep `report.md` as the normal user entry point. Explain selected/dependency scope, pending or partial coverage, and exclusions without requiring users to read all raw artifacts. Counts are canonical diagnostic/API groups, not unique Rust features or proved functions. Include detailed measurements only when requested.

Never call `frontend_clear`, an exclusion-cleared frontier, exit 0, or repeated errors a correctness proof or exhaustive inventory. Generated declarations and unexamined boundaries can hide gaps; processing all selected files does not establish complete recall. Do not claim source restoration unless the saved result verifies it.
