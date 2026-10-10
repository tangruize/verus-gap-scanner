# Agent instructions

Use `.github/skills/verus-gap/SKILL.md` and `README.md` for this adaptive scanner. This directory is independent of the older focused, dummy, and wrapper-only tools in its parent.

Run `./setup` once, then `./verus-gap /absolute/path/to/project`. Prefer the user's selected scope; the tool handles Cargo setup and bulk probing. An installed wheel provides `verus-gap` and `verus-gap-setup`.

Read `report.md`, `report.json`, `review.md`, and `project.json`, including on nonzero exits. Inspect original diagnostics before interpreting a family. Separate reproduced support gaps from setup, instrumentation, local checking boundaries, migration requirements, and compiler failures. Never describe a cleared frontend as a proof or exhaustive inventory.

Do not change input sources, invent contracts, drop Rust derives wholesale, change the target silently, or disable proof lifetime checks. Use `agent-tasks.json` only for a bounded hard blocker, working in a separate copy. Resume preserves evidence and validates identities.
