"""Independent frontiers with feedback-driven isolation and scope expansion."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .gap import GapError, run, save_run
from .project import Project, write_json
from .source import Declaration, Rendered


def classify(record: dict, package: str) -> str:
    message = record["message"]
    evidence = message + "\n" + "\n".join(
        child.get("message", "") for child in record.get("children", [])
    )
    if re.match(r"aborting due to|could not compile", message):
        return "compiler_summary"
    if any(text in message for text in (
        "cannot find attribute `verus_verify`", "cannot find attribute `verifier`",
        "custom attributes cannot be applied to expressions",
        "The verus_builtin crate was not imported",
        "#[verus_spec] is likely used inside a verus!",
    )):
        return "instrumentation_failure"
    if message.startswith("missing optimized MIR"):
        return "dependency_codegen_blocker"
    if record.get("code"):
        return "rust_or_wrapper_blocker"
    if re.match(r"(?:expected\b|unexpected token\b|parse error\b)", message, re.I):
        return "rust_or_wrapper_blocker"
    if "which is ignored because" in message or "cannot use external trait" in message:
        return "checking_boundary"
    if record["level"] == "warning" and "without adding a specification" in message:
        return "derived_spec_warning"
    if "assume_specification" in evidence or "external_type_specification" in evidence:
        names = re.findall(r"`([^`]+)`", message)
        if any(name.startswith((package.replace("-", "_") + "::", "crate::")) for name in names):
            return "checking_boundary"
        return "library_specification"
    if "removing an expression is not supported" in message:
        return "rust_or_wrapper_blocker"
    if "The verifier does not yet support the following Rust feature:" in message:
        return "language_rejection"
    if re.match(r"unsafe impl for .* is not allowed", message):
        return "language_rejection"
    if re.match(r"Verus does not (?:yet )?support\b", message):
        return "language_rejection"
    return "migration_requirement"


def canonical(message: str) -> str:
    message = message.split(" (note:", 1)[0]
    message = re.sub(r"impl&%\d+%default%(\w+)", r"<impl>::\1", message)
    message = re.sub(
        r"DefId\(\d+:\d+\s*~\s*([^)]+)\)",
        lambda match: "DefId(" + re.sub(r"\[[0-9a-f]+\]", "", match[1]) + ")",
        message,
    )
    return re.sub(r"impl&%[\w%&]+", "<impl>", message).strip()


def record_key(record: dict) -> tuple:
    return (
        record["category"], record["level"], canonical(record["message"]),
        tuple(sorted((place["file"], place["byte"]) for place in record["locations"])),
    )


def compiler_panic(result: dict, project: Project, state: State) -> dict | None:
    text = result["stderr"] + "\n" + result["stdout"]
    if not any(phrase in text.lower() for phrase in (
        "internal compiler error", "panicked at", "internal verus error", "verus internal error",
    )):
        return None
    places = []
    paths = re.findall(r"~ [^\]]+\]::([^)]+)\)", text)
    for path in paths:
        parts = [part for part in path.split("::") if not part.startswith("{")]
        if parts and parts[-1].isupper():
            parts.pop()
        matches = []
        for name, source in project.sources.items():
            for fn in source.functions:
                declared = re.sub(r"<[^<>]*>", "", fn.name).split("::")
                if fn.body_start is not None and len(parts) >= len(declared) and parts[-len(declared):] == declared:
                    matches.append((name, fn))
        if len(matches) == 1:
            name, fn = matches[0]
            byte = fn.body_start + 1
            place = project.sources[name].render(set(), set(), wrap=False, prepare=False).location(byte)
            if place:
                place["in_checking_scope"] = name in state.scope
                place["location_origin"] = "unique_panic_defpath_match"
                if place not in places:
                    places.append(place)
    nested_const = "VerusErasureCtxt has not been initialized" in text
    beginning = re.search(r"(?:^|\n)(?:thread .* panicked at|error: internal compiler error)", text)
    trace = text[beginning.start():] if beginning else text
    return {
        "category": "compiler_failure", "level": "error",
        "message": (
            "Verus panicked while checking nested constants before initializing its erasure context"
            if nested_const else "Verus compiler panic; inspect the original panic trace"
        ),
        "locations": places, "related_locations": [], "macro_expansion": False,
        "rendered": "\n".join(trace.splitlines()[:30]), "raw_diagnostic": None,
    }


def parse_output(text: str, project: Project, maps: dict[str, Rendered]) -> tuple[list[dict], str]:
    records, rendered = [], []

    def original(span: dict) -> tuple[dict | None, bool]:
        path = str((project.cwd / span["file_name"]).resolve())
        mapping = maps.get(path)
        if mapping:
            place = mapping.location(span["byte_start"])
            if place:
                place["in_checking_scope"] = mapping.checked(place["byte"])
                return place, bool(span.get("expansion"))
        expansion = span.get("expansion")
        if isinstance(expansion, dict) and expansion.get("span"):
            place, _ = original(expansion["span"])
            return place, True
        return None, False

    for line in text.splitlines(keepends=True):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            rendered.append(line)
            continue
        if not isinstance(raw, dict):
            rendered.append(line)
            continue
        if raw.get("reason") == "compiler-message":
            raw = raw["message"]
        if raw.get("level") not in {"error", "warning", "failure-note"}:
            continue
        rendered.append(raw.get("rendered") or raw.get("message", "") + "\n")
        if raw["level"] == "failure-note":
            continue
        category = classify(raw, project.manifest["crate_name"])
        if raw["level"] == "warning" and category not in {
            "checking_boundary", "derived_spec_warning", "instrumentation_failure",
        }:
            continue
        places, related, generated = [], [], False
        for span in raw.get("spans", []):
            place, expanded = original(span)
            generated |= expanded
            if place:
                target = places if span.get("is_primary") else related
                if place not in target:
                    target.append(place)
        if category in {"language_rejection", "library_specification"} and not any(
            place["in_checking_scope"] for place in places
        ):
            category = "checking_scope_artifact"
        if re.match(r"An individual item of a trait(?: impl)? cannot be marked external", raw["message"]):
            category = "instrumentation_failure"
        records.append({
            "category": category, "level": raw["level"], "message": raw["message"],
            "locations": places, "related_locations": related, "macro_expansion": generated,
            "rendered": raw.get("rendered") or raw["message"],
            "raw_diagnostic": raw,
        })
    return records, "".join(rendered)


@dataclass
class State:
    scope: list[str]
    bodies: dict[str, set[str]] = field(default_factory=dict)
    external: dict[str, set[str]] = field(default_factory=dict)
    unwrapped: dict[str, set[str]] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    def identity(self) -> str:
        return hashlib.sha256(json.dumps({
            "scope": sorted(self.scope),
            "bodies": {name: sorted(values) for name, values in sorted(self.bodies.items())},
            "external": {name: sorted(values) for name, values in sorted(self.external.items())},
            "unwrapped": {name: sorted(values) for name, values in sorted(self.unwrapped.items())},
        }, sort_keys=True).encode()).hexdigest()


def context_modules(project: Project, scope: list[str]) -> dict[str, set[str]]:
    if project.manifest.get("support_mode") != "cargo_verus_ghost_dependencies":
        return {}
    graph: dict[str, list[tuple[str, str]]] = {}
    root = project.manifest["root_source"]
    for name, source in project.sources.items():
        edges = []
        for item in source.declarations:
            if not item.enabled:
                continue
            if item.kind == "macro_invocation" and item.name.split("::")[-1] == "include":
                text = source.data[item.item_start:item.end].decode()
                match = re.search(r'include!\s*\(\s*"([^"]+)"', text)
                if match:
                    child = str((Path(name).parent / match[1]))
                    if child in project.sources:
                        edges.append(("", child))
            if item.kind != "mod_item" or item.body_start is not None:
                continue
            file = Path(name)
            directory = file.parent
            if name != root and file.stem not in {"mod", "lib", "main"}:
                directory /= file.stem
            parents = []
            parent_id = item.parent
            while parent_id:
                parent = next(decl for decl in source.declarations if decl.id == parent_id)
                if parent.kind == "mod_item":
                    parents.append(parent.name.split("::")[-1])
                parent_id = parent.parent
            for part in reversed(parents):
                directory /= part
            attrs = "\n".join(
                resolved for attr in item.attributes for resolved in source.cfg.attribute(attr)
            )
            path_attr = re.search(r'#\[\s*path\s*=\s*"([^"]+)"\s*\]', attrs)
            stem = item.name.split("::")[-1]
            paths = [directory / path_attr[1]] if path_attr else [
                directory / (stem + ".rs"), directory / stem / "mod.rs",
            ]
            children = [
                str((project.workspace / candidate).resolve().relative_to(project.workspace))
                for candidate in paths
                if (project.workspace / candidate).resolve().is_relative_to(project.workspace)
                and str((project.workspace / candidate).resolve().relative_to(project.workspace)) in project.sources
            ]
            if len(children) == 1:
                edges.append((item.id, children[0]))
        graph[name] = edges

    def descendants(name: str, seen: set[str]) -> set[str]:
        if name in seen:
            return set()
        seen.add(name)
        return {name}.union(*(descendants(child, seen) for _, child in graph.get(name, [])))

    result = {}
    for name, edges in graph.items():
        for declaration, child in edges:
            if declaration and not set(scope).intersection(descendants(child, set())):
                result.setdefault(name, set()).add(declaration)
    return result


def execute(project: Project, state: State, directory: Path, timeout: float) -> tuple[dict, list[dict], str]:
    directory.mkdir(parents=True, exist_ok=True)
    maps: dict[str, Rendered] = {}
    modified = []
    modules = context_modules(project, state.scope)
    try:
        for name, source in project.sources.items():
            scoped = name in state.scope
            mapped = source.render(
                state.bodies.get(name, set()), state.external.get(name, set()) | modules.get(name, set()),
                wrap=scoped, prepare=scoped, unwrapped=state.unwrapped.get(name, set()),
                load_support=name == project.manifest["root_source"],
                support_crate=project.manifest.get("support_crate_name", "vstd"),
            )
            path = project.workspace / name
            maps[str(path.resolve())] = mapped
            if mapped.data != source.data:
                path.write_bytes(mapped.data)
                modified.append(name)
                copied = directory / "sources" / name
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(mapped.data)
        (directory / "overlay.diff").write_text("".join(
            mapping.patch() for mapping in maps.values() if mapping.file in modified
        ))
        write_json(directory / "overlay.json", {
            name: {"sha256": maps[str((project.workspace / name).resolve())].sha256,
                   "edits": [asdict(edit) for edit in maps[str((project.workspace / name).resolve())].edits],
                   "segments": [asdict(segment) for segment in maps[str((project.workspace / name).resolve())].segments],
                   "checking_ranges": maps[str((project.workspace / name).resolve())].checking_ranges}
            for name in modified
        })
        command = list(project.command)
        index = max(index for index, value in enumerate(command) if value == "--out-dir")
        command[index + 1] = str(directory / "verus-build")
        Path(command[index + 1]).mkdir(exist_ok=True)
        result = run(command, project.cwd, project.env, min(timeout, project.deadline - time.monotonic()))
        metadata = save_run(directory, "verus", result)
        records, human = parse_output(result["stderr"] + "\n" + result["stdout"], project, maps)
        (directory / "verus.txt").write_text(human)
        write_json(directory / "diagnostics.json", {
            "run": metadata, "state": state.identity(), "records": records,
        })
        return result, records, human
    finally:
        for name in modified:
            (project.workspace / name).write_bytes(project.sources[name].data)


def declaration_for(project: Project, place: dict, body: bool = False) -> Declaration | None:
    source = project.sources.get(place["file"])
    if not source:
        return None
    if body:
        matches = [fn for fn in source.functions if fn.contains(place["byte"], body=True)]
        return min(matches, key=lambda fn: fn.end - fn.start) if matches else None
    return source.enclosing(place["byte"])


def refine(project: Project, state: State, records: list[dict], round_number: int) -> bool:
    added = False
    previous_bodies = {name: set(values) for name, values in state.bodies.items()}
    previous_external = {name: set(values) for name, values in state.external.items()}
    for record in records:
        if record["level"] != "error" or record["category"] == "compiler_summary":
            continue
        if not record["locations"] or record["category"] == "instrumentation_failure":
            continue
        for place in record["locations"]:
            name = place["file"]
            if name not in state.scope:
                continue
            fn = declaration_for(project, place, body=True)
            if fn and not fn.existing_boundary and fn.id not in previous_bodies.get(name, set()):
                if fn.id not in state.bodies.get(name, set()):
                    state.bodies.setdefault(name, set()).add(fn.id)
                    state.events.append({
                        "round": round_number, "file": name, "declaration": fn.name,
                        "id": fn.id, "line": fn.line, "kind": "external_body",
                        "trigger": record["message"],
                    })
                    added = True
                continue
            item = declaration_for(project, place)
            if not item or item.existing_boundary:
                continue
            parent = next(
                (declaration for declaration in project.sources[name].declarations
                 if declaration.id == item.parent), None,
            )
            if parent and (
                parent.kind == "trait_item"
                or parent.kind == "impl_item" and parent.trait_name
            ):
                item = parent
            if item.id not in previous_external.get(name, set()):
                if item.id not in state.external.get(name, set()):
                    state.external.setdefault(name, set()).add(item.id)
                    state.events.append({
                        "round": round_number, "file": name, "declaration": item.name,
                        "id": item.id, "line": item.line, "kind": "external_declaration",
                        "trigger": record["message"],
                    })
                    added = True
                continue
            parents = [
                declaration for declaration in project.sources[name].declarations
                if declaration.parent is None
                and declaration.start <= item.start and item.end <= declaration.end
            ]
            if len(parents) == 1 and parents[0].id not in state.unwrapped.get(name, set()):
                parent = parents[0]
                state.unwrapped.setdefault(name, set()).add(parent.id)
                state.events.append({
                    "round": round_number, "file": name, "declaration": parent.name,
                    "id": parent.id, "line": parent.line, "kind": "ordinary_rust_context",
                    "trigger": record["message"],
                })
                added = True
    return added


def dependencies(project: Project, state: State, records: list[dict]) -> list[str]:
    crate = project.manifest["crate_name"]
    found = set()
    src_root = Path(project.manifest["root_source"]).parent
    for record in records:
        if record["category"] != "checking_boundary":
            continue
        match = re.search(r"cannot use (function|type) `([^`]+)`", record["message"])
        if not match or not match[2].startswith(crate + "::"):
            continue
        parts = match[2].split("::")[1:]
        matches, exact, modules = set(), set(), set()
        for name, source in project.sources.items():
            if name in state.scope:
                continue
            file = Path(name)
            if file.is_relative_to(src_root):
                relative = file.relative_to(src_root)
                module = list(relative.parts[:-1])
                if file.stem not in {"lib", "main", "mod"}:
                    module.append(file.stem)
            else:
                module = []
            if module and parts[:-1] == module:
                modules.add(name)
            for declaration in source.declarations:
                if not declaration.enabled:
                    continue
                declared = re.sub(r"<[^<>]*>", "", declaration.name).split("::")
                if match[1] == "function" and declaration.kind != "function_item":
                    continue
                if match[1] == "type" and declaration.kind not in {
                    "struct_item", "enum_item", "union_item", "type_item", "trait_item",
                }:
                    continue
                if module + declared == parts:
                    exact.add(name)
                elif len(parts) >= len(declared) and parts[-len(declared):] == declared:
                    matches.add(name)
        candidates = exact or matches or modules
        if len(candidates) == 1:
            found.update(candidates)
    return sorted(found)


def probe(project: Project, focus: str, output: Path, max_rounds: int | None = None,
          per_run: float = 60, expand_scope: bool = True) -> dict:
    state = State([focus])
    summary = {
        "schema": "verus-gap/shard-v2", "focus": focus, "scope": [focus],
        "status": "partial", "stop_reason": "", "rounds": [], "observations": [],
        "events": [], "complete_inventory": False, "workspace_restored": False,
    }
    revision = hashlib.sha256(json.dumps(project.manifest["tool_sha256"], sort_keys=True).encode()).hexdigest()[:12]
    checkpoint = output / "report.json"
    if checkpoint.is_file():
        previous = json.loads(checkpoint.read_text())
        if previous.get("scanner_revision") == revision and previous["focus"] == focus:
            summary = previous
            state.scope = list(previous["scope"])
            state.events = list(previous["events"])
            for event in state.events:
                field_name = {
                    "external_body": "bodies", "external_declaration": "external",
                    "ordinary_rust_context": "unwrapped",
                }.get(event["kind"])
                if field_name:
                    getattr(state, field_name).setdefault(event["file"], set()).add(event["id"])
    summary["scanner_revision"] = revision
    seen = {record_key(record) for record in summary["observations"]}
    states = {record["state"] for record in summary["rounds"][:-1]}
    start = len(summary["rounds"])
    budget = max_rounds if max_rounds is not None else 4 + 3 * sum(
        len(source.declarations) for source in project.sources.values()
    )
    try:
        for number in range(start, start + budget):
            if time.monotonic() >= project.deadline:
                summary["stop_reason"] = "time_budget"
                break
            fingerprint = state.identity()
            if fingerprint in states:
                summary["stop_reason"] = "no_progress"
                break
            states.add(fingerprint)
            directory = output / f"round-{number:03d}"
            result, records, _ = execute(project, state, directory, per_run)
            panic = compiler_panic(result, project, state)
            has_panic = panic is not None
            if panic:
                records.append(panic)
            candidates = [
                record for record in records
                if record["category"] in {"language_rejection", "library_specification"}
                and record["level"] == "error" and record["locations"] and not has_panic
                and not result["timed_out"]
                and not any(item["category"] == "instrumentation_failure" for item in records)
            ]
            confirmed = set()
            if candidates or panic:
                second, repeated, _ = execute(project, state, directory / "confirmation", per_run)
                repeated_panic = compiler_panic(second, project, state)
                if panic and repeated_panic and not second["timed_out"]:
                    confirmed.add(record_key(repeated_panic))
                elif not second["timed_out"] and not repeated_panic and not any(
                    record["category"] == "instrumentation_failure" for record in repeated
                ):
                    confirmed = {record_key(record) for record in repeated}
            for record in records:
                if record["category"] == "compiler_summary":
                    continue
                key = record_key(record)
                if key not in seen:
                    seen.add(key)
                    summary["observations"].append(dict(
                        record, round=number, evidence=f"round-{number:03d}/verus.txt",
                        scope=list(state.scope), confirmed=key in confirmed,
                        source_variant="prepared" if not state.events else "isolated",
                    ))
            blockers = sorted({
                record["category"] for record in records
                if (record["level"] == "error" or record["category"] == "instrumentation_failure")
                and record["category"] in {
                    "rust_or_wrapper_blocker", "instrumentation_failure", "dependency_codegen_blocker",
                }
            })
            summary["rounds"].append({
                "round": number, "scope": list(state.scope), "exit_code": result["exit_code"],
                "seconds": result["seconds"], "records": len(records), "blockers": blockers,
                "evidence": f"round-{number:03d}/verus.txt",
                "state": fingerprint, "confirmed": len(confirmed),
            })
            summary["events"] = list(state.events)
            summary["scope"] = list(state.scope)
            write_json(output / "report.json", summary)
            if result["timed_out"]:
                summary["stop_reason"] = "per_run_timeout"
                break
            if panic:
                if refine(project, state, [panic], number + 1):
                    continue
                summary["stop_reason"] = "compiler_failure"
                break
            if not any(record["level"] == "error" for record in records) and result["exit_code"] == 0:
                if blockers:
                    summary["stop_reason"] = "instrumentation_failure"
                    break
                exclusions = any(event["kind"] != "scope_expansion" for event in state.events)
                summary["status"] = "frontier_cleared_after_exclusions" if exclusions else "frontend_clear"
                summary["stop_reason"] = "no_remaining_errors"
                break
            additions = dependencies(project, state, records) if expand_scope else []
            if not additions and expand_scope:
                additions = sorted({
                    place["file"] for record in records
                    if record["category"] == "checking_scope_artifact"
                    for place in record["locations"] if place["file"] not in state.scope
                })
            if additions and not blockers:
                next_file = additions[0]
                state.scope.append(next_file)
                state.events.append({
                    "round": number + 1, "file": next_file, "kind": "scope_expansion",
                    "trigger": "resolve a uniquely indexed local checking boundary",
                })
                continue
            if not refine(project, state, records, number + 1):
                summary["stop_reason"] = "unresolved_frontier"
                break
        else:
            summary["stop_reason"] = "round_limit"
    except (GapError, OSError, UnicodeError) as error:
        summary["stop_reason"] = "execution_failure"
        summary["error"] = str(error)
    finally:
        summary["events"] = list(state.events)
        summary["scope"] = list(state.scope)
        project.verify_originals()
        summary["workspace_restored"] = True
        write_json(output / "report.json", summary)
    return summary
