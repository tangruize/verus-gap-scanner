"""Separate reproducible support gaps from adaptation and coverage blockers."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .configuration import validate_saved_configuration
from .engine import canonical
from .gap import GapError
from .project import Project, scan_level_from, source_configuration
from .source import Cfg, Source

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

MUTABLE_STATIC_ACCESS = "Mutable static access (`static mut`)"


def source_link(place: dict) -> str:
    label = f"{place['file']}:{place['line']}:{place['column']}"
    backticks = max((len(run) for run in re.findall(r"`+", label)), default=0)
    delimiter = "`" * (backticks + 1)
    if backticks:
        label = f" {label} "
    target = quote("originals/" + place["file"], safe="/")
    return f"[{delimiter}{label}{delimiter}]({target}#L{place['line']})"


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


def mutable_static_name(message: str) -> str | None:
    detail = canonical(message).removeprefix(
        "The verifier does not yet support the following Rust feature: "
    )
    match = re.fullmatch(r"Path Def\(Static\s*\{([^}]*)\},\s*DefId\(([^)]+)\)\)", detail)
    if match and re.search(r"\bmutability:\s*Mut\b", match[1]):
        name = match[2].strip()
        if name and not re.fullmatch(r"\d+:\d+", name):
            return name
    return None


def group_key(record: dict, category: str | None = None) -> tuple[str, str, str | None]:
    """Presentation key only; engine confirmation retains individual diagnostics."""
    category = record["category"] if category is None else category
    if category == "language_rejection" and mutable_static_name(record["message"]) is not None:
        return category, MUTABLE_STATIC_ACCESS, None
    return category, canonical(record["message"]), public_api(record)


def language_family(message: str) -> str:
    if message == MUTABLE_STATIC_ACCESS:
        return "Mutable static access"
    detail = message.removeprefix("The verifier does not yet support the following Rust feature: ")
    if detail.startswith("Path Def(Static"):
        return "Mutable static access" if re.search(r"\bmutability:\s*Mut\b", detail) else "Static access"
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
    level = scan_level_from(project.manifest)
    fast = level > 0
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
            key = group_key(record, category)
            group = groups.setdefault(key, {
                "id": hashlib.sha256(repr(key).encode()).hexdigest()[:12],
                "category": category, "message": key[1], "locations": [], "related_locations": [],
                "evidence": [], "raw_example": record["rendered"],
                "confirmed": record["confirmed"], "macro_expansion": record["macro_expansion"],
                "contexts": [],
                "api": key[2],
                "family": language_family(key[1]) if category == "language_rejection" else None,
            })
            entries = [group]
            name = mutable_static_name(record["message"]) if category == "language_rejection" else None
            if name is not None:
                instances = group.setdefault("instances", [])
                instance = next((item for item in instances if item["name"] == name), None)
                if instance is None:
                    instance = {
                        "name": name, "message": canonical(record["message"]),
                        "locations": [], "related_locations": [], "evidence": [], "contexts": [],
                        "raw_example": record["rendered"], "macro_expansion": record["macro_expansion"],
                    }
                    instances.append(instance)
                entries.append(instance)
            evidence = str(Path(shard["directory"]) / record["evidence"])
            context = {
                "focus": shard["focus"], "scope": record["scope"],
                "variant": record["source_variant"], "round": record["round"],
            }
            for entry in entries:
                if name is not None:
                    entry["macro_expansion"] |= record["macro_expansion"]
                for field, places in (("locations", locations), ("related_locations", related_locations)):
                    for place in places:
                        if place not in entry[field]:
                            entry[field].append(place)
                if evidence not in entry["evidence"]:
                    entry["evidence"].append(evidence)
                if context not in entry["contexts"]:
                    entry["contexts"].append(context)
    for group in groups.values():
        instances = group.get("instances", [])
        instances.sort(key=lambda instance: instance["name"])
        for entry in [group, *instances]:
            for field in ("locations", "related_locations"):
                entry[field].sort(key=lambda place: (place["file"], place["line"], place["column"]))
            entry["selected_locations"] = sum(place["file"] in selected_set for place in entry["locations"])
            entry["dependency_locations"] = len(entry["locations"]) - entry["selected_locations"]
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
        "baseline_kind": project.manifest.get("baseline_kind", "rust"),
        "input_kind": project.manifest.get("input_kind", "rust"),
        "source_configuration_origin": project.manifest.get("source_configuration_origin", "legacy"),
        "cargo_args": project.manifest["cargo_args"], "complete_inventory": False,
        "build_configuration": project.manifest.get("build_configuration"),
        "checks": project.manifest["checks"],
        "support_mode": project.manifest.get("support_mode"),
        "jobs": project.manifest.get("jobs", 1),
        "scan_mode": "fast" if fast else "adaptive", "scan_level": level,
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
            "checking_boundary_only_files": sum(
                shard["stop_reason"] == "checking_boundary_only" for shard in shards
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
                "scan_mode": "fast" if fast else "adaptive",
                "scan_level": scan_level_from(shard),
                "dependency_boundary_limited": shard.get("dependency_boundary_limited", False),
            }
            for shard in shards
        ],
        "workspace_restored": project.manifest["workspace_restored"] and project.manifest.get("workers_restored", True),
        "confirmation": "Accepted support gaps reproduced twice with the same source overlay and compiler context.",
        "reporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "outside_scope_evidence": outside,
    }
    if report["coverage"]["pending_files"]:
        report["status"] = "partial"
    if not report["workspace_restored"] and project.manifest.get("scan_progress", {}).get("state") != "running":
        report["status"] = "restoration_failed"
    return report


def active_files(progress: dict, selected: list[str]) -> list[str]:
    if "active_files" in progress:
        names = progress["active_files"]
    else:
        active = progress.get("active_file")
        names = [] if active is None else [active]
    if not isinstance(names, list) or not all(isinstance(name, str) and name in selected for name in names):
        raise GapError("Saved active files are outside the selected scope or invalid.")
    if len(set(names)) != len(names):
        raise GapError("Saved active files contain duplicate frontiers.")
    return names


def scan_snapshot(manifest: dict, shards: list[dict], selected: list[str]) -> dict:
    progress = manifest.get("scan_progress", {})
    names = active_files(progress, selected)
    interrupted = next((shard for shard in shards if shard["stop_reason"] == "interrupted"), None)
    unfinished = next((
        shard for shard in shards if shard["stop_reason"] in {"", "time_budget", "execution_failure"}
    ), None)
    state = progress.get("state")
    if state is None:
        state = "interrupted" if interrupted else (
            "finished" if len(shards) == len(selected) and not unfinished else "incomplete"
        )
        names = [shard["focus"] for shard in shards if shard["stop_reason"] == "interrupted" or not shard["stop_reason"]]
    if state not in {"running", "finished", "interrupted", "stopped", "incomplete"}:
        raise GapError(f"Unknown saved scan progress state: {state!r}.")
    active = names[0] if len(names) == 1 else None
    current = next((shard for shard in shards if shard["focus"] == active), None)
    rounds = current.get("rounds", []) if current else []
    snapshot = {
        "state": state, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "completed_files": sum(
            shard["stop_reason"] not in {"", "interrupted", "time_budget", "execution_failure"}
            and shard["focus"] not in names for shard in shards
        ),
        "selected_files": len(selected), "active_file": active,
        "last_completed_round": rounds[-1]["round"] if rounds else None,
    }
    if len(names) > 1:
        by_file = {shard["focus"]: shard for shard in shards}
        snapshot["active_files"] = [
            {
                "file": name,
                "last_completed_round": by_file[name]["rounds"][-1]["round"]
                if name in by_file and by_file[name]["rounds"] else None,
            }
            for name in selected if name in names
        ]
    if "error" in progress:
        if not isinstance(progress["error"], str):
            raise GapError("Saved scan failure is not a diagnostic message.")
        snapshot["error"] = progress["error"]
    return snapshot


def snapshot_lines(report: dict) -> list[str]:
    snapshot = report.get("scan_progress")
    if snapshot is None:
        return []
    labels = {
        "running": "in progress", "finished": "finished", "interrupted": "interrupted",
        "stopped": "stopped before all selected files finished", "incomplete": "unfinished",
    }
    lines = [
        f"**Scan snapshot:** {labels[snapshot['state']]}; updated `{snapshot['updated_at']}`. "
        f"{snapshot['completed_files']}/{snapshot['selected_files']} file frontiers finished.", "",
    ]
    if report.get("jobs", 1) > 1:
        lines.extend([f"**File worker pool:** `{report['jobs']}`.", ""])
    if snapshot["active_file"] is not None:
        round_number = snapshot["last_completed_round"]
        detail = f"; last completed round `{round_number:03d}`" if round_number is not None else (
            "; no completed round yet"
        )
        lines.extend([f"**Current or interrupted file:** `{snapshot['active_file']}`{detail}.", ""])
    active = snapshot.get("active_files", [])
    if active:
        lines.extend([f"**Current or interrupted files:** {len(active)}.", ""])
        for frontier in active:
            number = frontier["last_completed_round"]
            detail = f"last completed round `{number:03d}`" if number is not None else "no completed round yet"
            lines.append(f"- `{frontier['file']}`; {detail}.")
        lines.append("")
    if snapshot.get("error"):
        lines.extend([f"**Scan stopped with an error:** {snapshot['error']}", ""])
    if snapshot["state"] != "finished":
        lines.extend([
            "This is a snapshot of completed, checkpointed rounds, not a finished scan. A running or interrupted probe/confirmation is not included; new findings count only after their confirmation is saved.", "",
        ])
    excluded = report.get("other_revision_checkpoints", [])
    if excluded:
        lines.extend([
            f"**Other scanner contexts:** {len(excluded)} saved checkpoint(s) are retained but not included in this run's findings; their selected files remain pending.", "",
        ])
    return lines


def markdown(report: dict) -> str:
    coverage = report["coverage"]
    language = report["counts"].get("language_rejection", 0)
    library = report["counts"].get("library_specification", 0)
    level = scan_level_from(report)
    fast = level > 0
    build_label = "Verus compile (SMT disabled)" if report.get("baseline_kind") == "verus_compile" else "Rust build"
    argument_label = "Compiler arguments" if report.get("input_kind") == "verus" else "Cargo configuration"
    argument_default = "default" if report.get("input_kind") == "verus" else "default features"
    lines = [
        "# Verus compatibility inventory", "",
        *snapshot_lines(report),
        f"**{language} reproducible language/translation rejections and {library} library specification gaps.** "
        f"Probed {coverage['probed_files']}/{coverage['selected_files']} selected active files; "
        f"{coverage['partial_files']} file frontiers remain partial.",
        "", f"**Project:** `{report['project']}`",
        f"**Crate:** `{report['package']}`; **Verus:** `{report['verus']['version']}`.",
        f"**Unchanged {build_label}:** {'passed' if report['baseline_passed'] else 'failed'}. "
        f"**{argument_label}:** `{' '.join(report['cargo_args']) or argument_default}`.", "",
        *([
            f"**Build configuration:** `{Path(report['build_configuration']['path']).name}` "
            f"(`{report['build_configuration']['selection']}`); "
            f"[saved TOML]({report['build_configuration']['snapshot']}).", "",
        ] if report.get("build_configuration") else []),
        *([
            "**Input view: existing Verus root.** The supplied proof root, ghost code, contracts, and existing external/trusted boundaries are retained. Both build controls use Verus compilation with SMT disabled; no ordinary Rust baseline or new proof is claimed. Macro-contained functions are not individually indexed or isolated. Clear frontiers mean compiler acceptance in this annotated context, not support for all equivalent unannotated Rust.", "",
        ] if report.get("input_kind") == "verus" else []),
        *([
            "**Source view: Cargo-Verus dependency context.** This crate requires Verus-aware dependencies. Source conditions use the captured Verus driver's configuration to match those dependency artifacts, preserving required proof/spec definitions. The original native build and its preparation control are retained separately; this is not a proof or a claim that every native-only branch was checked.", "",
        ] if report.get("source_configuration_origin") == "cargo_verus" else []),
        *([
            f"**Scan level: {level}{' (fast)' if level == 2 else ''}, selected source only.** The captured build context and controls are retained, but dependency files are not recursively added to Verus checking. Grouped findings require a primary location in the selected source. Uncrossed local boundaries can hide later findings; a dependency-limited frontier is kept partial even if exclusions remove the final compiler errors. Complete raw output remains in the per-round evidence.", "",
        ] if fast else [
            "**Scan level: 0.** Uniquely indexed local dependencies may be added to checking; isolation continues while the frontier makes progress.", "",
        ]),
        *([
            "**Boundary-only early stopping:** level 2 stops before further isolation when the current errors are only checking boundaries. Such frontiers remain partial, with their unresolved diagnostics retained. Later gaps in the selected source may still be hidden; early stopping does not weaken the repeated-confirmation requirement for accepted findings.", "",
        ] if level == 2 else []),
        "These are observed compatibility gaps, not a proof or exhaustive feature inventory. Counts are distinct diagnostic/API groups, not unique Rust features. Mutable static accesses share one group, with affected globals listed separately. Each accepted support diagnostic was reproduced twice in the same recorded checking context. File shards retain the full Rust build context, but local/generated items may remain outside Verus checking; those boundaries are not counted as library gaps. Later passes explicitly exclude rejected bodies or declarations. Raw diagnostics, source snapshots, and every patch are retained.",
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
        f"| Active files in the captured build | {coverage['active_files']} |",
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
        *([
            f"| Files stopped early with only checking boundaries remaining | {coverage['checking_boundary_only_files']} |",
        ] if level == 2 else []),
        f"| Enumerable function declarations (not a count of verified functions) | {coverage['enumerable_functions']} |",
        f"| Explicit body exclusions / declaration exclusions | {coverage['excluded_bodies']} / {coverage['excluded_declarations']} |",
        f"| Macro invocation sites (generated declarations not fully enumerable) | {coverage['macro_sites']} |",
        "", "The active-file denominator comes from the unchanged native Rust build. Ghost-only includes and macro-generated declarations may add context that is not fully indexed. Dependency expansion is source indexing, not a semantic call graph.",
        "", "## Setup and controls", "",
        f"**Syntax-only preparation {'Verus' if report.get('input_kind') == 'verus' else 'Rust'} recheck:** `{'passed' if (report.get('preparation_check') or {}).get('exit_code') == 0 else 'not recorded'}`. "
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
    lines.extend([
        "## Reproducible support gaps", "",
        "Source locations link to the retained original files in `originals/`, with `#L` line anchors. Viewers without line-anchor support still open the file; the line and column remain visible in the link.", "",
    ])
    accepted = {"language_rejection", "library_specification"}
    for category in ("language_rejection", "library_specification"):
        groups = [group for group in report["groups"] if group["category"] == category]
        lines.extend([f"### {LABELS[category]} ({len(groups)})", ""])
        if not groups:
            lines.extend(["None observed. This does not imply complete support.", ""])
        for group in groups:
            instances = group.get("instances", [])
            lines.extend([
                f"#### {('`' + group['api'] + '` (model not recognized)') if group.get('api') else group['message']}", "",
                (f"{len(instances)} affected global(s); " if instances else "") +
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
            if instances:
                lines.extend([
                    "| Global variable | Original source locations | Evidence |",
                    "|---|---|---|",
                ])
                for instance in instances:
                    locations = "; ".join(source_link(place) for place in instance["locations"])
                    lines.append(
                        f"| `{instance['name']}` | {locations} | [raw]({instance['evidence'][0]}) |"
                    )
                lines.append("")
            else:
                lines.extend(f"- {source_link(place)}" for place in group["locations"])
            example = instances[0] if instances else group
            if example["locations"]:
                lines.extend(["", "```rust", example["locations"][0]["source"].strip(), "```", ""])
            lines.extend(["```text", example["raw_example"].rstrip(), "```", ""])
            evidence = example["evidence"][0]
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
    restoration = "not yet verified while workers are active" if (
        not report["workspace_restored"] and report.get("scan_progress", {}).get("state") == "running"
    ) else str(report["workspace_restored"])
    lines.extend(["", f"**Scan sources restored:** `{restoration}`. Input sources were not edited.", ""])
    return "\n".join(lines)


def review_markdown(report: dict) -> str:
    review = ["# Boundaries and blockers", "",
              *snapshot_lines(report),
              "These diagnostics are not counted as confirmed Rust or library support gaps.", "",
              "Source links open retained original files in `originals/` using `#L` line anchors; line navigation depends on the viewer.", ""]
    if report["coverage"]["checking_boundary_only_files"]:
        review.extend([
            "Scan level 2 deliberately stopped before further isolation of these checking boundaries. Affected frontiers remain partial; additional selected-source gaps may be hidden. Start a new level-1 scan for continued selected-source exploration.", "",
        ])
    for group in report["groups"]:
        if group["category"] in {"language_rejection", "library_specification"}:
            continue
        review.extend([f"## {LABELS.get(group['category'], group['category'])}: {group['message']}", ""])
        review.extend(
            f"- {source_link(place)}"
            for place in group["locations"]
        )
        review.extend(["", "```text", group["raw_example"].rstrip(), "```", "",
                       f"[Raw evidence]({group['evidence'][0]})", ""])
    return "\n".join(review)


def atomic_text(path: Path, text: str):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_report(output: Path, report: dict):
    tasks = [
        {
            "file": shard["focus"], "reason": shard["stop_reason"], "scan_level": shard["scan_level"],
            "evidence": str(Path(shard["directory"]) / "report.json"),
            "objective": "Reproduce and resolve this setup/checking blocker in a separate copy without inventing contracts or suppressing original evidence.",
        }
        for shard in report["shards"] if shard["status"] == "partial"
    ]
    atomic_text(output / "report.md", markdown(report))
    atomic_text(output / "review.md", review_markdown(report))
    atomic_text(output / "agent-tasks.json", json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False) + "\n")
    atomic_text(output / "report.json", json.dumps(report, indent=2, ensure_ascii=False) + "\n")


@contextmanager
def report_lock(output: Path):
    with (output / ".report.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _save_report(project: Project, shards: list[dict], selected: list[str], *,
                 other_revision_checkpoints: list[dict] | None = None) -> dict:
    report = aggregate(project, shards, selected)
    report["scan_progress"] = scan_snapshot(project.manifest, shards, selected)
    if other_revision_checkpoints:
        report["other_revision_checkpoints"] = other_revision_checkpoints
    write_report(project.output, report)
    return report


def save_report(project: Project, shards: list[dict], selected: list[str]) -> dict:
    with report_lock(project.output):
        return _save_report(project, shards, selected)


def saved_path(output: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise GapError(f"Invalid saved relative path: {relative!r}.")
    path = (output / relative).resolve()
    if ".." in Path(relative).parts or not path.is_relative_to(output.resolve()):
        raise GapError(f"Saved path escapes the result directory: {relative!r}.")
    return path


def saved_shards(output: Path, manifest: dict, selected: list[str]) -> tuple[list[dict], list[dict]]:
    revision = hashlib.sha256(json.dumps(manifest["tool_sha256"], sort_keys=True).encode()).hexdigest()[:12]
    level = scan_level_from(manifest)
    index = manifest.get("shard_index", {})
    shards = []
    other_revisions = []
    active = set(active_files(manifest.get("scan_progress", {}), selected))
    for focus in selected:
        recorded = index.get(focus)
        base = Path("shards") / hashlib.sha256(focus.encode()).hexdigest()[:12]
        candidates = [recorded] if recorded is not None else [str(base / revision), str(base)]
        found = False
        for relative in candidates:
            directory = saved_path(output, relative)
            checkpoint = directory / "report.json"
            if not checkpoint.is_file():
                continue
            shard = json.loads(checkpoint.read_text())
            if not isinstance(shard, dict) or shard.get("schema") != "verus-gap/shard-v2":
                raise GapError(f"Invalid shard checkpoint: {checkpoint}.")
            if shard.get("scanner_revision") != revision:
                if recorded is not None:
                    progress = manifest.get("scan_progress", {})
                    if progress.get("state") in {"running", "interrupted", "stopped"} and (
                        focus not in active
                    ):
                        other_revisions.append({
                            "file": focus, "checkpoint": str(checkpoint.relative_to(output)),
                            "scanner_revision": shard.get("scanner_revision"),
                        })
                        found = True
                        break
                    raise GapError(f"Saved checkpoint has a different scanner identity: {checkpoint}.")
                continue
            if shard.get("focus") != focus or scan_level_from(shard) != level:
                raise GapError(f"Saved checkpoint has a different source scope or scan level: {checkpoint}.")
            if not all(isinstance(shard.get(key), list) for key in ("scope", "rounds", "observations", "events")):
                raise GapError(f"Incomplete shard checkpoint: {checkpoint}.")
            if shard.get("status") not in {"partial", "frontend_clear", "frontier_cleared_after_exclusions"} or (
                not isinstance(shard.get("stop_reason"), str)
            ):
                raise GapError(f"Invalid frontier status in {checkpoint}.")
            if not all(isinstance(name, str) and name in manifest["active_sources"] for name in shard["scope"]):
                raise GapError(f"Invalid checking scope in {checkpoint}.")
            round_numbers = set()
            for recorded_round in shard["rounds"]:
                if not isinstance(recorded_round, dict) or type(recorded_round.get("round")) is not int:
                    raise GapError(f"Invalid completed round in {checkpoint}.")
                round_numbers.add(recorded_round["round"])
            if not all(isinstance(event, dict) and isinstance(event.get("kind"), str)
                       and isinstance(event.get("file"), str) for event in shard["events"]):
                raise GapError(f"Invalid exclusion/checking events in {checkpoint}.")
            for record in shard["observations"]:
                if not isinstance(record, dict) or not all(key in record for key in (
                    "category", "message", "locations", "related_locations", "rendered",
                    "confirmed", "macro_expansion", "scope", "source_variant", "round", "evidence",
                )):
                    raise GapError(f"Incomplete observation in {checkpoint}.")
                if not all(isinstance(record[key], str) for key in (
                    "category", "message", "rendered", "source_variant", "evidence",
                )) or not all(isinstance(record[key], list) for key in ("locations", "related_locations", "scope")):
                    raise GapError(f"Invalid observation in {checkpoint}.")
                if type(record["confirmed"]) is not bool or type(record["macro_expansion"]) is not bool:
                    raise GapError(f"Invalid observation confirmation in {checkpoint}.")
                if type(record["round"]) is not int or record["round"] not in round_numbers:
                    raise GapError(f"Observation refers to an unfinished round in {checkpoint}.")
                diagnostic = record.get("raw_diagnostic")
                if diagnostic is not None and (
                    not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("children", []), list)
                    or not all(isinstance(child, dict) and isinstance(child.get("message", ""), str)
                               for child in diagnostic.get("children", []))
                ):
                    raise GapError(f"Invalid retained diagnostic in {checkpoint}.")
                evidence = saved_path(directory, record["evidence"])
                if not evidence.is_file():
                    raise GapError(f"Missing retained diagnostic: {evidence}.")
                if record["confirmed"] and record["category"] in {"language_rejection", "library_specification"}:
                    confirmation = evidence.parent / "confirmation" / "verus.txt"
                    if not confirmation.is_file():
                        raise GapError(f"Missing retained confirmation: {confirmation}.")
            shard["directory"] = str(directory.relative_to(output))
            shards.append(shard)
            found = True
            break
        if recorded is not None and not found and focus not in active:
            raise GapError(f"Missing saved checkpoint for {focus!r}.")
    return shards, other_revisions


def _refresh_report(output: Path) -> dict:
    manifest = json.loads((output / "project.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("schema") != "verus-gap/project-v2":
        raise GapError("The result directory does not contain an adaptive scan project record.")
    active = manifest.get("active_sources")
    selected = manifest.get("selected_files")
    if not isinstance(active, dict) or not active or not isinstance(selected, list) or not selected:
        raise GapError("The scan has no captured active-source selection; finish setup before refreshing reports.")
    if any(not isinstance(name, str) or name not in active for name in selected) or len(set(selected)) != len(selected):
        raise GapError("The saved source selection is invalid.")
    if not all(isinstance(manifest.get(key), dict) for key in ("tool_sha256", "verus", "checks")):
        raise GapError("The saved build/compiler identity is incomplete.")
    if not isinstance(manifest.get("rust_cfg"), list) or not all(
        isinstance(value, str) for value in manifest["rust_cfg"]
    ):
        raise GapError("The saved compiler configuration is invalid.")
    if not isinstance(manifest.get("shard_index", {}), dict) or not isinstance(
        manifest.get("scan_progress", {}), dict
    ):
        raise GapError("The saved checkpoint index or scan progress is invalid.")
    if not all(key in manifest for key in (
        "input", "package", "baseline_passed", "cargo_args", "workspace_restored",
    )) or not isinstance(manifest["verus"].get("version"), str):
        raise GapError("The saved project/control metadata is incomplete.")
    if not all(isinstance(manifest[key], str) for key in ("input", "package")) or (
        not isinstance(manifest["cargo_args"], list)
        or not all(isinstance(value, str) for value in manifest["cargo_args"])
        or type(manifest["baseline_passed"]) is not bool or type(manifest["workspace_restored"]) is not bool
    ):
        raise GapError("The saved project/control metadata is invalid.")
    scan_level_from(manifest)
    if "jobs" in manifest and (type(manifest["jobs"]) is not int or manifest["jobs"] < 1):
        raise GapError("The saved worker count is invalid.")
    if "workers_restored" in manifest and type(manifest["workers_restored"]) is not bool:
        raise GapError("The saved worker restoration state is invalid.")
    validate_saved_configuration(output, manifest.get("build_configuration"))
    cfg = source_configuration(manifest)
    sources = {}
    for name, identity in active.items():
        if not isinstance(name, str) or not isinstance(identity, dict) or not isinstance(identity.get("sha256"), str):
            raise GapError("The retained source identity is invalid.")
        original = saved_path(output, str(Path("originals") / name))
        data = original.read_bytes()
        if hashlib.sha256(data).hexdigest() != identity["sha256"]:
            raise GapError(f"Retained original source changed: {name}.")
        sources[name] = Source(name, data, cfg)
    shards, other_revisions = saved_shards(output, manifest, selected)
    for shard in shards:
        for record in shard["observations"]:
            for place in [*record["locations"], *record["related_locations"]]:
                if not isinstance(place, dict) or not isinstance(place.get("file"), str) or (
                    place["file"] not in sources
                ) or not all(
                    key in place for key in ("byte", "line", "column", "source")
                ):
                    raise GapError("A saved diagnostic has no valid original source location.")
                if not all(type(place[key]) is int for key in ("byte", "line", "column")) or (
                    place["byte"] < 0 or place["line"] < 1 or place["column"] < 1
                    or not isinstance(place["source"], str)
                ):
                    raise GapError("A saved diagnostic has invalid original source coordinates.")
                if place["byte"] > len(sources[place["file"]].data):
                    raise GapError("A saved diagnostic points past the retained original source.")
    project = Project(
        output=output, workspace=output / "workspace", cwd=output / "workspace",
        env={}, command=[], sources=sources, manifest=manifest, deadline=float("inf"),
    )
    return _save_report(project, shards, selected, other_revision_checkpoints=other_revisions)


def refresh_report(output: Path) -> dict:
    output = output.expanduser().resolve()
    with report_lock(output):
        return _refresh_report(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verus-gap report",
        description="Refresh reports from saved checkpoints without building, probing, or resuming the scan.",
    )
    parser.add_argument("result", help="adaptive scan result directory")
    args = parser.parse_args(argv)
    output = Path(args.result).expanduser().resolve()
    try:
        refresh_report(output)
    except (GapError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"error: Could not refresh reports: {error}", file=sys.stderr)
        return 2
    print(f"Reports refreshed from saved evidence: {output / 'report.md'}", flush=True)
    return 0
