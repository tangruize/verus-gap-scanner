# Agent instructions

Read `README.md` and `.github/skills/verus-gap/SKILL.md` for current commands and evidence interpretation. Work only on the user's authorized scope; the scanner handles Cargo setup and bulk file investigation.

Run these commands from the tool directory:

```bash
./setup
./verus-gap /absolute/path/to/authorized/project --jobs 32 --verbose
```

Setup is needed once. An installed package provides `verus-gap-setup` and `verus-gap`. Keep the recorded toolchain and actual Cargo dependencies, features, generated assets, and target; a selected file or directory retains its containing crate context.

**Recommend the default level 2, not routine level-1/0 scans.** It investigates selected source and stops when only checking boundaries remain. Use level 1 only for deeper selected-source exploration, or level 0 when needed local-dependency findings are requested. All levels retain the same build controls and repeated confirmation. Boundaries are not missing library models; boundary-only stops remain partial and can hide later gaps.

Measured comparisons show about **65% less end-to-end time**, retaining **over 95% of known gap groups** and **over 90% of their source locations**. These are observed reference-retention figures, not guaranteed speedups or exhaustive coverage. Detailed measurements belong in `VALIDATION.md`, not routine usage guidance.

Recommend **`--jobs 32 --verbose`** for multi-file scans when CPU/memory permit; the CLI default remains one worker. Free workers take the next pending file, largest source first, while reports keep selected-file order and sorted locations. File rounds remain sequential. Verbose readiness and `[dispatch n/m]` are not completed-file counts. New gap notices require saved confirmation evidence. Do not promise linear speedup or a universal worker optimum; the total timeout covers the whole build/scan.

Read `report.md` first, even on nonzero exits; it is the normal user-facing result, including coverage limits. Use `report.json`, `project.json`, and representative raw diagnostics, source overlays, and confirmations for detailed analysis. Consult `review.md` only to investigate failures or unchecked code; do not require users to read it routinely or solely because coverage is partial. Count diagnostic/API groups, not unique Rust features or proved functions. Separate reproduced gaps from setup, preparation, checking boundaries, migration work, and compiler failures. Report scope, pending/partial frontiers, exclusions, source restoration, and toolchain/features/target.

Reports start after setup. Automatic refresh requires both a minute elapsed and a new confirmed group; repeated locations/progress alone do not force a rewrite. Normal exit forces refresh. Ctrl+C waits for active compilers and source restoration, then saves an interrupted snapshot of completed rounds. Scan failures retain explicit errors and evidence; unfinished confirmations are not gaps, and forced termination cannot guarantee restoration.

```bash
./verus-gap report /path/to/verus-gap-results/RESULT
./verus-gap --resume /path/to/verus-gap-results/RESULT --jobs 32 --verbose
```

Manual refresh reads saved evidence without building, probing, or resuming. Preserve `project.json`, `originals/`, and `shards/`; source links target retained originals. Successful refresh exits 0 even if gaps exist. Resume validates identities and preserves the recorded level; jobs, verbosity, and timeout may change. Deliberately finished level-2 boundary-only frontiers are reused without another probe and remain partial. Start a new scan to change level, source, target, features, or Verus version.

Use trusted input because build scripts and macros execute in the scan copy. Do not change production sources, invent contracts, delete derives wholesale, silently change the target, or disable proof lifetime checks. Use `agent-tasks.json` for one bounded follow-up in a separate copy, retaining the exact configuration, diagnostics, and controls. A cleared frontend, exit 0, or repeated error alone is never a correctness proof or exhaustive inventory.
