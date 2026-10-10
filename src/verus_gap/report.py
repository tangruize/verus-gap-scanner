"""Separate reproducible support gaps from adaptation and coverage blockers."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from .engine import canonical
from .project import Project, write_json

LABELS = {
    "language_rejection": "Rust constructs rejected by Verus",
    "library_specification": "Library specifications not recognized",
    "checking_boundary": "Local, generated, or external checking boundaries",
    "migration_requirement": "Verus migration requirements",
    "derived_spec_warning": "Derived implementations without recognized specifications",
    "rust_or_wrapper_blocker": "Rust, cfg, or wrapper blockers",
    "instrumentation_failure": "Instrumentation failures",
    "checking_scope_artifact": "Diagnostics outside the actually rewritten checking scope",
    "compiler_failure": "Verus compiler failures (not a language-support verdict)",
    "dependency_codegen_blocker": "Dependency artifacts missing executable MIR",
}


def public_api(record: dict) -> str | None:
    hints = "\n".join(child.get("message", "") for child in (record.get("raw_diagnostic") or {}).get("children", []))
    match = re.search(r"\bassume_specification([^\n]*)", hints)
    if not match:
        return None
    text = match[1]
    brackets = []
    for index, char in enumerate(text):
        if char == "[":
            brackets.append(index)
        elif char == "]" and brackets:
            start = brackets.pop()
            if not brackets and re.match(r"\s*\(", text[index + 1:]):
                return text[start + 1:index]
    return None


def language_family(message: str) -> str:
    detail = message.removeprefix("The verifier does not yet support the following Rust feature: ")
    if detail.startswith("Path Def(Static"):
        return "Mutable static access" if "mutability: Mut" in detail else "Static access"
    if message == "Verus does not support 'static mut'":
        return "Mutable static declarations"
    if detail.startswith("unsizing operation"):
        return "Unsizing coercions"
    if detail.startswith(("casting a pointer", "Verus does not support this cast:")):
        return "Pointer casts" if "*" in detail or "pointer" in detail else "Unsupported casts"
    if detail.startswith("dereferencing a "):
        return "Pointer dereferences"
    if detail == "mut self":
        return "By-value mutable self receivers"
    return detail


def aggregate(project: Project, shards: list[dict], selected: list[str]) -> dict:
    groups: dict[tuple, dict] = {}
    selected_set = set(selected)
    fast = project.manifest.get("scan_mode", "adaptive") == "fast"
    outside = []
    for shard in shards:
        for record in shard["observations"]:
            locations = record["locations"]
            related_locations = record["related_locations"]
            if fast:
                locations = [place for place in locations if place["file"] in selected_set]
                related_locations = [place for place in related_locations if place["file"] in selected_set]
                if not locations:
                    outside.append({
                        "category": record["category"], "focus": shard["focus"],
                        "evidence": str(Path(shard["directory"]) / record["evidence"]),
                    })
                    continue
            category = record["category"]
            if category in {"language_rejection", "library_specification"} and not record["confirmed"]:
                category = "unconfirmed_support_diagnostic"
            api = public_api(record)
            key = (category, canonical(record["message"]), api)
            group = groups.setdefault(key, {
                "id": hashlib.sha256(repr(key).encode()).hexdigest()[:12],
                "category": category, "message": key[1], "locations": [], "related_locations": [],
                "evidence": [], "raw_example": record["rendered"],
                "confirmed": record["confirmed"], "macro_expansion": record["macro_expansion"],
                "contexts": [],
                "api": api,
                "family": language_family(key[1]) if category == "language_rejection" else None,
            })
            for field, places in (("locations", locations), ("related_locations", related_locations)):
                for place in places:
                    if place not in group[field]:
                        group[field].append(place)
            evidence = str(Path(shard["directory"]) / record["evidence"])
            if evidence not in group["evidence"]:
                group["evidence"].append(evidence)
            context = {
                "focus": shard["focus"], "scope": record["scope"],
                "variant": record["source_variant"], "round": record["round"],
            }
            if context not in group["contexts"]:
                group["contexts"].append(context)
    for group in groups.values():
        for field in ("locations", "related_locations"):
            group[field].sort(key=lambda place: (place["file"], place["line"], place["column"]))
        group["selected_locations"] = sum(place["file"] in selected_set for place in group["locations"])
        group["dependency_locations"] = len(group["locations"]) - group["selected_locations"]
    grouped = sorted(groups.values(), key=lambda group: (group["category"], group["api"] or group["message"]))
    confirmed = [
        group for group in grouped
        if group["category"] in {"language_rejection", "library_specification"}
    ]
    active = project.sources
    focused = {shard["focus"] for shard in shards}
    functions = sum(len(active[name].functions) for name in selected)
    excluded = {
        (event["file"], event["id"], event["kind"])
        for shard in shards for event in shard["events"] if "id" in event
    }
    counts = Counter(group["category"] for group in grouped)
    report = {
        "schema": "verus-gap/inventory-v2", "project": project.manifest["input"],
        "package": project.manifest["package"], "verus": project.manifest["verus"],
        "baseline_passed": project.manifest["baseline_passed"],
        "cargo_args": project.manifest["cargo_args"], "complete_inventory": False,
        "checks": project.manifest["checks"],
        "support_mode": project.manifest.get("support_mode"),
        "scan_mode": project.manifest.get("scan_mode", "adaptive"),
        "preparation_check": project.manifest.get("preparation_check"),
        "dependency_setup": project.manifest.get("dependency_setup"),
        "status": "limitations_observed" if confirmed else "no_confirmed_limitations",
        "coverage": {
            "active_files": len(active), "selected_files": len(selected),
            "probed_files": len(focused), "pending_files": len(selected) - len(focused),
            "enumerable_functions": functions,
            "frontier_clear_files": sum(shard["status"] == "frontend_clear" for shard in shards),
            "frontier_cleared_after_exclusions": sum(
                shard["status"] == "frontier_cleared_after_exclusions" for shard in shards
            ),
            "partial_files": sum(shard["status"] == "partial" for shard in shards),
            "excluded_bodies": sum(kind == "external_body" for _, _, kind in excluded),
            "excluded_declarations": sum(kind != "external_body" for _, _, kind in excluded),
            "macro_sites": sum(
                declaration.kind == "macro_invocation"
                for name in selected for declaration in active[name].declarations
                if declaration.enabled
            ),
            "expanded_context_files": len({
                name for shard in shards for name in shard["scope"]
            } - set(selected)),
            "unindexed_external_active_sources": len(project.manifest.get("external_active_sources", [])),
            "dependency_boundary_limited_files": sum(
                shard.get("dependency_boundary_limited", False) for shard in shards
            ),
            "outside_scope_diagnostics": len(outside),
        },
        "counts": dict(counts), "groups": grouped,
        "selected_scope_counts": dict(Counter(
            group["category"] for group in grouped if group["selected_locations"]
        )),
        "language_families": dict(sorted(Counter(
            group["family"] for group in grouped if group["category"] == "language_rejection"
        ).items())),
        "shards": [
            {
                **{key: shard[key] for key in (
                    "focus", "directory", "status", "stop_reason", "scope", "events",
                )},
                "scan_mode": shard.get("scan_mode", "adaptive"),
                "dependency_boundary_limited": shard.get("dependency_boundary_limited", False),
            }
            for shard in shards
        ],
        "workspace_restored": project.manifest["workspace_restored"],
        "confirmation": "Accepted support gaps reproduced twice with the same source overlay and compiler context.",
        "reporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "outside_scope_evidence": outside,
    }
    if report["coverage"]["pending_files"]:
        report["status"] = "partial"
    if not project.manifest["workspace_restored"]:
        report["status"] = "restoration_failed"
    return report


def markdown(report: dict) -> str:
    coverage = report["coverage"]
    language = report["counts"].get("language_rejection", 0)
    library = report["counts"].get("library_specification", 0)
    fast = report.get("scan_mode") == "fast"
    lines = [
        "# Verus compatibility inventory", "",
        f"**{language} reproducible language/translation rejections and {library} library specification gaps.** "
        f"Probed {coverage['probed_files']}/{coverage['selected_files']} selected active files; "
        f"{coverage['partial_files']} file frontiers remain partial.",
        "", f"**Project:** `{report['project']}`",
        f"**Crate:** `{report['package']}`; **Verus:** `{report['verus']['version']}`.",
        f"**Unchanged Rust build:** {'passed' if report['baseline_passed'] else 'failed'}. "
        f"**Cargo configuration:** `{' '.join(report['cargo_args']) or 'default features'}`.", "",
        *([
            "**Mode: fast, selected source only.** The native Cargo context and controls are retained, but dependency files are not recursively added to Verus checking. Grouped findings require a primary location in the selected source. Uncrossed local boundaries can hide later findings; a dependency-limited frontier is kept partial even if exclusions remove the final compiler errors. Complete raw output remains in the per-round evidence.", "",
        ] if fast else []),
        "These are observed compatibility gaps, not a proof or exhaustive feature inventory. Counts are distinct diagnostic/API groups, not unique Rust features. Each accepted support diagnostic was reproduced twice in the same recorded checking context. File shards retain the full Rust build context, but local/generated items may remain outside Verus checking; those boundaries are not counted as library gaps. Later passes explicitly exclude rejected bodies or declarations. Raw diagnostics, source snapshots, and every patch are retained.",
        "", "## At a glance", "",
        f"Support groups with a primary location in your selected files: "
        f"**{report['selected_scope_counts'].get('language_rejection', 0)} language/translation, "
        f"{report['selected_scope_counts'].get('library_specification', 0)} library models**. " +
        ("No dependency-only findings are included in this focused report." if fast else
         "The overall totals also include checked dependency context reached during scope expansion."), "",
        "| Language/error family | Diagnostic groups |", "|---|---:|",
        *[f"| {family} | {count} |" for family, count in report["language_families"].items()],
        "", "Library groups below are labeled with public API names when the compiler supplies them. Unstable compiler definition numbers are normalized only for grouping; every raw diagnostic is unchanged.",
        "", "## Coverage", "",
        "| Measure | Count |", "|---|---:|",
        f"| Active files in the captured Rust build | {coverage['active_files']} |",
        f"| Independently probed / selected files | {coverage['probed_files']} / {coverage['selected_files']} |",
        f"| Additional files reached through local context expansion | {coverage['expanded_context_files']} |",
        f"| Active generated/external source files outside the editable copy | {coverage['unindexed_external_active_sources']} |",
        f"| Files reaching Verus without explicit exclusions | {coverage['frontier_clear_files']} |",
        f"| Files with no remaining errors after explicit exclusions | {coverage['frontier_cleared_after_exclusions']} |",
        f"| Files with unresolved, compiler, or time-budget frontiers | {coverage['partial_files']} |",
        *([
            f"| Files limited by deliberately uncrossed checking boundaries | {coverage['dependency_boundary_limited_files']} |",
            f"| Outside-scope/unattributed diagnostics retained only in raw evidence | {coverage['outside_scope_diagnostics']} |",
        ] if fast else []),
        f"| Enumerable function declarations (not a count of verified functions) | {coverage['enumerable_functions']} |",
        f"| Explicit body exclusions / declaration exclusions | {coverage['excluded_bodies']} / {coverage['excluded_declarations']} |",
        f"| Macro invocation sites (generated declarations not fully enumerable) | {coverage['macro_sites']} |",
        "", "The active-file denominator comes from the unchanged native Rust build. Ghost-only includes and macro-generated declarations may add context that is not fully indexed. Dependency expansion is source indexing, not a semantic call graph.",
        "", "## Setup and controls", "",
        f"**Syntax-only preparation Rust recheck:** `{'passed' if (report.get('preparation_check') or {}).get('exit_code') == 0 else 'not recorded'}`. "
        f"**Support mode:** `{report.get('support_mode')}`. "
        f"**SMT verification:** `off`; **proof lifetime checks:** `{'on' if report['checks']['proof_lifetimes'] else 'off'}`.", "",
    ]
    setup = report.get("dependency_setup")
    if setup:
        versions = ", ".join(f"{name}={version}" for name, version in setup["matching_support_versions"].items())
        lines.extend([
            "Existing Verus-aware dependencies were built using official Cargo-Verus; the chosen root was deliberately deferred to probes, not reported as built. "
            f"Support versions were aligned only in the copy: `{versions}`. "
            f"[Manifest patch]({setup['manifest_patch']}); native-after-alignment control: `{'passed' if setup['native_after_alignment']['exit_code'] == 0 else 'failed'}`. "
            "Root probes emit metadata rather than native executables.", "",
        ])
    lines.extend(["## Reproducible support gaps", ""])
    accepted = {"language_rejection", "library_specification"}
    for category in ("language_rejection", "library_specification"):
        groups = [group for group in report["groups"] if group["category"] == category]
        lines.extend([f"### {LABELS[category]} ({len(groups)})", ""])
        if not groups:
            lines.extend(["None observed. This does not imply complete support.", ""])
        for group in groups:
            lines.extend([
                f"#### {('`' + group['api'] + '` (model not recognized)') if group.get('api') else group['message']}", "",
                f"{len(group['locations'])} primary occurrence(s); "
                f"{len(group['contexts'])} recorded checking context(s).", "",
            ])
            if group["dependency_locations"]:
                lines.extend([
                    f"**Location scope:** {group['selected_locations']} in selected files; "
                    f"{group['dependency_locations']} in added dependency context.", "",
                ])
            if group["macro_expansion"]:
                lines.extend(["The diagnostic involves macro expansion; the original invocation is retained.", ""])
            for place in group["locations"]:
                lines.append(f"- `{place['file']}:{place['line']}:{place['column']}`")
            if group["locations"]:
                lines.extend(["", "```rust", group["locations"][0]["source"].strip(), "```", ""])
            lines.extend(["```text", group["raw_example"].rstrip(), "```", ""])
            evidence = group["evidence"][0]
            lines.extend([f"[Raw evidence]({evidence}); all contexts and related spans are in `report.json`.", ""])
    lines.extend(["## Not counted as support gaps", "", "| Category | Distinct messages |", "|---|---:|"])
    for category, count in sorted(report["counts"].items()):
        if category not in accepted:
            lines.append(f"| {LABELS.get(category, category)} | {count} |")
    lines.extend(["", "See [review.md](review.md) for grouped boundaries, migration requirements, and blockers.", "",
                  "## File frontiers", "", "| File | Final state | Stop reason | Evidence |",
                  "|---|---|---|---|"])
    for shard in report["shards"]:
        lines.append(
            f"| `{shard['focus']}` | `{shard['status']}` | `{shard['stop_reason']}` | "
            f"[JSON]({shard['directory']}/report.json) |"
        )
    if fast and report.get("outside_scope_evidence"):
        lines.extend(["", "Outside-scope or unattributed diagnostics are not presented as selected-source findings. "
                      "They can still block exploration; their unchanged output is retained here:", ""])
        for item in report["outside_scope_evidence"]:
            lines.append(f"- `{item['category']}`: [raw evidence]({item['evidence']})")
    lines.extend(["", f"**Scan sources restored:** `{report['workspace_restored']}`. Input sources were not edited.", ""])
    return "\n".join(lines)


def save_report(project: Project, shards: list[dict], selected: list[str]) -> dict:
    report = aggregate(project, shards, selected)
    write_json(project.output / "report.json", report)
    (project.output / "report.md").write_text(markdown(report))
    review = ["# Boundaries and blockers", "",
              "These diagnostics are not counted as confirmed Rust or library support gaps.", ""]
    for group in report["groups"]:
        if group["category"] in {"language_rejection", "library_specification"}:
            continue
        review.extend([f"## {LABELS.get(group['category'], group['category'])}: {group['message']}", ""])
        review.extend(
            f"- `{place['file']}:{place['line']}:{place['column']}`"
            for place in group["locations"]
        )
        review.extend(["", "```text", group["raw_example"].rstrip(), "```", "",
                       f"[Raw evidence]({group['evidence'][0]})", ""])
    (project.output / "review.md").write_text("\n".join(review))
    tasks = [
        {
            "file": shard["focus"], "reason": shard["stop_reason"],
            "evidence": str(Path(shard["directory"]) / "report.json"),
            "objective": "Reproduce and resolve this setup/checking blocker in a separate copy without inventing contracts or suppressing original evidence.",
        }
        for shard in report["shards"] if shard["status"] == "partial"
    ]
    write_json(project.output / "agent-tasks.json", {"tasks": tasks})
    return report
