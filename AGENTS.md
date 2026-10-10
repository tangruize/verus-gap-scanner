# Agent instructions

Read `README.md` and `.github/skills/verus-gap/SKILL.md` for setup, scanning and evidence interpretation.

Run `./setup` once, then `./verus-gap /absolute/path/to/project`. Prefer the user's selected scope; the tool handles Cargo setup and bulk probing. An installed wheel provides `verus-gap` and `verus-gap-setup`.

Choose `--scan-level 0` (default) for transitive dependency investigation, `--scan-level 1` for selected-source-only checking with continued isolation, or `--scan-level 2` / `--fast` for selected-source-only checking that stops when only checking-boundary errors remain. Cargo dependencies, native controls and repeated confirmation remain intact at every level. Boundary-only stops stay partial and can hide later selected-source gaps; boundaries are not missing library models or successful coverage. Levels are not fixed runtime guarantees. Resume preserves the original level and reuses deliberately finished level-2 boundary frontiers without new probes.

Add `--verbose` for live round progress and confirmed limitation groups; it is display-only and can be changed on resume. Unconfirmed diagnostics and compiler/setup failures are not announced as limitations. Keep the final report and raw evidence.

Completed rounds and files refresh reports only when a minute has elapsed and a new confirmed diagnostic/API group exists; repeated locations, boundary/progress-only changes, and file completion do not force another rewrite. Normal exit always refreshes reports, and Ctrl+C saves an interrupted report after restoring sources. Use `./verus-gap report RESULT_DIR` for evidence-only manual refresh, including during a running scan; it does not build, probe, resume, or change the recorded scanner/compiler identity. Preserve `project.json`, `originals/`, and `shards/`, and distinguish unfinished snapshots from finished scans.

Read `report.md`, `report.json`, `review.md`, and `project.json`, including on nonzero exits. Inspect original diagnostics before interpreting a family. Separate reproduced support gaps from setup, instrumentation, local checking boundaries, migration requirements, and compiler failures. Never describe a cleared frontend as a proof or exhaustive inventory.

Do not change input sources, invent contracts, drop Rust derives wholesale, change the target silently, or disable proof lifetime checks. Use `agent-tasks.json` only for a bounded hard blocker, working in a separate copy. Resume preserves evidence and validates identities.
