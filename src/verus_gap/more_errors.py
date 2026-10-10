"""Conservative, opt-in body boundaries to expose additional diagnostics."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import shutil
import time
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from .gap import GapError, assess_result, diagnostics, report_markdown, run, save_run, wrap

# cfg_attr expands too late for Verus's loop rewriting to see the boundary.
BOUNDARY = "#[verifier::external_body]\n"
MAX_ROUNDS = 10
BODY_CATEGORIES = {"frontend_unsupported", "missing_library_spec", "verification_boundary"}
IDENTIFIER = re.compile(r"(?:r#)?(?:[^\W\d]|_)\w*", re.UNICODE)
RAW_LITERAL = re.compile(r'(?:br|cr|r)(#*)"')
CHAR_LITERAL = re.compile(r"'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\n])'")


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Function:
    name: str
    start: int
    body_start: int
    body_end: int
    external: bool


def tokens(text: str) -> list[Token]:
    result = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        if text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = len(text) if end == -1 else end + 1
            continue
        if text.startswith("/*", pos):
            depth = 1
            pos += 2
            while pos < len(text) and depth:
                if text.startswith("/*", pos):
                    depth += 1
                    pos += 2
                elif text.startswith("*/", pos):
                    depth -= 1
                    pos += 2
                else:
                    pos += 1
            if depth:
                raise GapError("Cannot locate bodies: unterminated block comment.")
            continue
        raw = RAW_LITERAL.match(text, pos)
        if raw:
            end = text.find('"' + raw.group(1), pos + len(raw.group()))
            if end == -1:
                raise GapError("Cannot locate bodies: unterminated raw string.")
            end += 1 + len(raw.group(1))
            result.append(Token("literal", pos, end))
            pos = end
            continue
        if text[pos] == '"':
            end = pos + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            if end >= len(text):
                raise GapError("Cannot locate bodies: unterminated string.")
            result.append(Token("literal", pos, end + 1))
            pos = end + 1
            continue
        char = CHAR_LITERAL.match(text, pos)
        if char:
            end = pos + len(char.group())
            result.append(Token("literal", pos, end))
            pos = end
            continue
        word = IDENTIFIER.match(text, pos)
        end = word.end() if word else pos + 1
        result.append(Token(text[pos:end], pos, end))
        pos = end
    return result


def functions(text: str) -> list[Function]:
    """Recognize ordinary function declarations, declining ambiguous/macro syntax."""
    stream = tokens(text)
    pairs, parents, stack = {}, [], []
    closers = {")": "(", "]": "[", "}": "{"}
    for index, token in enumerate(stream):
        parents.append(tuple(stack))
        if token.value in {"(", "[", "{"}:
            stack.append(index)
        elif token.value in closers:
            if not stack or stream[stack[-1]].value != closers[token.value]:
                raise GapError("Cannot locate bodies: unbalanced source delimiters.")
            opening = stack.pop()
            pairs[opening] = index
            pairs[index] = opening
    if stack:
        raise GapError("Cannot locate bodies: unbalanced source delimiters.")
    result = []
    qualifiers = {"pub", "async", "unsafe", "const", "extern", "default",
                  "exec", "proof", "spec", "open", "closed", "broadcast", "uninterp"}
    for index, token in enumerate(stream):
        if token.value != "fn" or index + 1 == len(stream):
            continue
        if not IDENTIFIER.fullmatch(stream[index + 1].value):
            continue
        containers = parents[index]
        if any(stream[parent].value != "{" for parent in containers):
            continue
        if any(
            (parent > 0 and stream[parent - 1].value == "!")
            or (parent > 1 and stream[parent - 2].value == "!")
            for parent in containers
        ):
            continue
        start = index
        while start:
            previous = stream[start - 1].value
            if previous in qualifiers:
                start -= 1
            elif previous == "literal" and start > 1 and stream[start - 2].value == "extern":
                start -= 2
            elif previous == ")" and pairs[start - 1] > 0 and stream[pairs[start - 1] - 1].value == "pub":
                start = pairs[start - 1] - 1
            elif previous == "]" and pairs[start - 1] > 0 and stream[pairs[start - 1] - 1].value == "#":
                start = pairs[start - 1] - 1
            else:
                break
        if start and stream[start - 1].value not in {"{", "}", ";"}:
            opening = pairs.get(start - 1, -1)
            if not (
                stream[start - 1].value == "]" and opening >= 2
                and stream[opening - 1].value == "!" and stream[opening - 2].value == "#"
            ):
                continue
        cursor, angles = index + 2, 0
        while cursor < len(stream):
            value = stream[cursor].value
            if value in {"(", "["}:
                closing = pairs[cursor]
                if any(t.value in {"{", "!"} for t in stream[cursor + 1:closing]):
                    break
                cursor = closing + 1
                continue
            if value == "<":
                angles += 1
            elif value == ">" and stream[cursor - 1].value != "-":
                angles = max(0, angles - 1)
            elif value in {";", "!", "="}:
                break
            elif value == "{":
                if angles:
                    break
                attributes = {t.value for t in stream[start:index]}
                result.append(Function(
                    stream[index + 1].value, stream[start].start,
                    stream[cursor].end, stream[pairs[cursor]].start,
                    bool(attributes.intersection({"external_body", "external"})),
                ))
                break
            cursor += 1
    return result


def offset(text: str, place: dict) -> int | None:
    lines = text.splitlines(keepends=True)
    line, column = place["line"], place["column"]
    if not 1 <= line <= len(lines) or not 1 <= column <= len(lines[line - 1]) + 1:
        return None
    return sum(map(len, lines[:line - 1])) + column - 1


def choose(records: list[dict], sources: dict, excluded: set) -> tuple[dict, list[dict]]:
    parsed = {}
    skipped = []
    for file, text in sources.items():
        try:
            parsed[file] = functions(text)
        except GapError as error:
            skipped.append({"file": file, "reason": str(error)})
    signature_errors = set()
    for record in records:
        if record["level"] != "error":
            continue
        for place in record["locations"]:
            file = place["file"]
            if file not in parsed:
                continue
            pos = offset(sources[file], place)
            if pos is not None:
                signature_errors.update(
                    (file, fn.start) for fn in parsed[file] if fn.start <= pos < fn.body_start
                )
    candidates = {}
    for record in records:
        if record["level"] != "error" or record["category"] == "compiler_summary":
            continue
        found = []
        if record["category"] in BODY_CATEGORIES and record["locations"]:
            for place in record["locations"]:
                file = place["file"]
                pos = offset(sources[file], place) if file in sources else None
                bodies = [
                    fn for fn in parsed.get(file, [])
                    if pos is not None and fn.body_start <= pos < fn.body_end
                ]
                if not bodies:
                    found = []
                    break
                fn = min(bodies, key=lambda body: body.body_end - body.body_start)
                key = (file, fn.start)
                if fn.external or key in excluded or key in signature_errors:
                    found = []
                    break
                found.append((key, fn))
        if not found:
            skipped.append({
                "message": record["message"], "locations": record["locations"],
                "reason": "No safe new body boundary: unsupported category, missing/ambiguous body location, signature error, or existing boundary.",
            })
            continue
        for key, fn in found:
            candidate = candidates.setdefault(key, {"function": fn, "errors": []})
            candidate["errors"].append(record["message"])
    return candidates, skipped


def patched_source(text: str, starts: list[int]) -> str:
    for start in sorted(starts, reverse=True):
        text = text[:start] + BOUNDARY + text[start:]
    return text


def remap(place: dict, original: str, starts: list[int]) -> dict | None:
    modified = patched_source(original, starts)
    pos = offset(modified, place)
    if pos is None:
        return None
    shift = 0
    for start in sorted(starts):
        insertion = start + shift
        if insertion <= pos < insertion + len(BOUNDARY):
            return None
        if pos < insertion:
            break
        shift += len(BOUNDARY)
    pos -= shift
    lines = [0] + [index + 1 for index, char in enumerate(original) if char == "\n"]
    index = bisect_right(lines, pos) - 1
    return {
        "file": place["file"], "line": index + 1, "column": pos - lines[index] + 1,
        "source": original.splitlines()[index],
    }


def finding_key(record: dict) -> tuple:
    code = record.get("code")
    return (
        record["level"], record["message"].split(" (note:", 1)[0],
        code.get("code") if isinstance(code, dict) else None,
        tuple((p["file"], p["line"], p["column"]) for p in record["locations"]),
    )


def expand(output: Path, initial: dict, maps: dict, command: list[str] | None,
           cwd: Path | None, env: dict, deadline: float) -> dict:
    directory = output / "more-errors"
    directory.mkdir()
    wrapped = {entry["file"] for entry in initial["wrapped_files"]}
    entries = {path: entry for path, entry in maps.items() if entry["file"] in wrapped}
    sources = {entry["file"]: entry["original_text"] for entry in entries.values()}
    original_wrapped = {path: Path(path).read_bytes() for path in entries}
    current = copy.deepcopy(initial)
    summary = {
        "status": "finished", "stop_reason": "", "complete_inventory": False,
        "round_limit": MAX_ROUNDS, "rounds": [], "boundaries": [], "diagnostics": [],
        "additional_diagnostics": 0, "skipped": [], "blockers": [],
        "source_snapshots": "Each round retains wrapped sources; the initial workspace is restored after expansion.",
    }
    seen, excluded, known_locations = set(), set(), {}

    def retain(index: int, report: dict):
        target = directory / f"round-{index:03d}"
        target.mkdir(exist_ok=True)
        for path, entry in entries.items():
            copied = target / "sources" / entry["file"]
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copied)
        (target / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (target / "report.md").write_text(report_markdown(report))
        summary["rounds"].append({
            "round": index, "report": f"round-{index:03d}/report.md",
            "status": report["status"], "boundaries": len(summary["boundaries"]),
        })
        for record in report["diagnostics"]:
            if record["category"] == "compiler_summary":
                continue
            key = finding_key(record)
            existing = known_locations.get(key[:3])
            is_new = bool(index) and (
                existing is None or any(place not in existing for place in key[3])
            )
            places = {
                (place["file"], place["line"], place["column"])
                for place in record["locations"] + record["related_locations"]
            }
            known_locations.setdefault(key[:3], set()).update(places)
            if key in seen:
                continue
            seen.add(key)
            summary["diagnostics"].append(dict(
                record, first_observed_round=index, newly_observed=is_new,
            ))
            if is_new:
                summary["additional_diagnostics"] += 1

    try:
        zero = directory / "round-000"
        zero.mkdir()
        for name in ("verus.stdout", "verus.stderr", "verus.txt", "baseline.stdout", "baseline.stderr"):
            if (output / name).is_file():
                shutil.copy2(output / name, zero / name)
        retain(0, current)
        if not command or not cwd or initial["blockers"]:
            summary["stop_reason"] = "initial_scan_blocked"
        else:
            for number in range(1, MAX_ROUNDS + 1):
                candidates, skipped = choose(current["diagnostics"], sources, excluded)
                summary["skipped"] = skipped
                if not candidates:
                    summary["stop_reason"] = "no_more_safe_body_boundaries"
                    break
                if time.monotonic() >= deadline:
                    raise GapError("Error expansion exhausted the shared build/scan time budget.")
                for (file, start), candidate in sorted(candidates.items()):
                    excluded.add((file, start))
                    fn = candidate["function"]
                    summary["boundaries"].append({
                        "file": file, "function": fn.name,
                        "line": sources[file][:start].count("\n") + 1,
                        "original_offset": start, "round": number,
                        "attribute": BOUNDARY.strip(), "errors": candidate["errors"],
                    })
                target = directory / f"round-{number:03d}"
                target.mkdir()
                updated_maps, edits, patches = {}, {}, []
                current = copy.deepcopy(initial)
                current["checks"]["shielding"] = True
                current["applied_boundaries"] = copy.deepcopy(summary["boundaries"])
                current["original_source_sha256"] = copy.deepcopy(initial["source_sha256"])
                current["wrapped_files"] = []
                current["runs"] = {}
                for path, entry in entries.items():
                    file = entry["file"]
                    starts = sorted(start for name, start in excluded if name == file)
                    edits[file] = starts
                    modified = patched_source(sources[file], starts)
                    Path(path).write_bytes(modified.encode("utf-8"))
                    mapped = dict(wrap(Path(path)), file=file)
                    updated_maps[path] = mapped
                    current["wrapped_files"].append({k: v for k, v in mapped.items() if k != "original_text"})
                    current["source_sha256"][file] = mapped["sha256"]
                    patches.extend(difflib.unified_diff(
                        sources[file].splitlines(keepends=True), modified.splitlines(keepends=True),
                        fromfile="original/" + file, tofile="with-boundaries/" + file,
                    ))
                updated_maps.update({path: entry for path, entry in maps.items() if path not in entries})
                (target / "boundaries.diff").write_text("".join(patches))
                replay = list(command)
                out_index = max(index for index, arg in enumerate(replay) if arg == "--out-dir")
                replay[out_index + 1] = str(target / "verus-build")
                print(f"Looking for more errors: pass {number}, {len(excluded)} function body boundary/boundaries...", flush=True)
                result = run(replay, cwd, env, deadline - time.monotonic())
                current["runs"]["verus"] = save_run(target, "verus", result)
                records, raw = diagnostics(result["stderr"], cwd, updated_maps)
                stdout_records, stdout_raw = diagnostics(result["stdout"], cwd, updated_maps)
                records.extend(stdout_records)
                for record in records:
                    for field in ("locations", "related_locations"):
                        places = []
                        for place in record[field]:
                            file = place["file"]
                            mapped = remap(place, sources[file], edits[file]) if file in edits else place
                            if mapped is not None and mapped not in places:
                                places.append(mapped)
                        record[field] = places
                current["diagnostics"] = records
                current["status"], current["blockers"] = assess_result(result, records)
                current["transformed_source_sha256"] = {
                    entry["file"]: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    for path, entry in entries.items()
                }
                (target / "verus.txt").write_text(raw + stdout_raw)
                print(raw + stdout_raw, end="")
                retain(number, current)
                if current["blockers"]:
                    summary["status"] = "blocked"
                    summary["blockers"] = current["blockers"]
                    summary["stop_reason"] = "follow_up_scan_blocked"
                    break
                if not any(record["level"] == "error" for record in records):
                    summary["stop_reason"] = "no_errors_after_boundaries"
                    break
            else:
                summary["stop_reason"] = "round_limit_reached"
    except (GapError, OSError, UnicodeError) as error:
        summary["status"] = "blocked"
        summary["stop_reason"] = "expansion_failed"
        summary["blockers"].append(str(error))
        print(f"error expansion blocked: {error}", flush=True)
    finally:
        summary["initial_workspace_restored"] = True
        for path, data in original_wrapped.items():
            try:
                Path(path).write_bytes(data)
            except OSError as error:
                summary["initial_workspace_restored"] = False
                summary["status"] = "blocked"
                summary["stop_reason"] = "workspace_restore_failed"
                summary["blockers"].append(f"Cannot restore {path}: {error}")
                print(f"error expansion blocked: cannot restore {path}: {error}", flush=True)
    lines = [
        "# More Verus errors", "",
        f"**Additional diagnostic records:** {summary['additional_diagnostics']}",
        f"**Bodies excluded from follow-up checking:** {len(summary['boundaries'])}",
        f"**Status:** `{summary['status']}`; **stop reason:** `{summary['stop_reason']}`.", "",
        "This is diagnostic exploration, not a proof or complete inventory. Round 0 is unchanged wrapper-only evidence. Later rounds add explicit external_body boundaries and may introduce context-dependent diagnostics; excluded bodies can still contain unseen gaps. Signatures, calls and dependencies are not deleted. Input locations refer to the original source; each completed round retains raw output, source hashes, snapshots and a cumulative patch.",
        "Additional counts compare canonical messages and original locations, including earlier related spans. Repeated messages with changed hints/categories and already-known context promoted to primary are not counted as new. Counts are diagnostic records, not distinct unsupported features.",
        f"**Original wrapped workspace restored:** {summary['initial_workspace_restored']}.", "",
        "| Pass | Status | Excluded bodies | Evidence |",
        "|---|---|---:|---|",
    ]
    lines.extend(
        f"| {item['round']} | `{item['status']}` | {item['boundaries']} | [report]({item['report']}) |"
        for item in summary["rounds"]
    )
    lines.extend(["", "## Excluded function bodies", ""])
    lines.extend(
        f"- Pass {item['round']}: `{item['file']}:{item['line']}`, `{item['function']}`."
        for item in summary["boundaries"]
    )
    lines.extend(["", "## Observed diagnostics across passes", ""])
    for record in summary["diagnostics"]:
        label = "new observation" if record["newly_observed"] else "initial/already-known observation"
        lines.extend([f"### Pass {record['first_observed_round']}: {record['category']} ({label})", "", record["message"], ""])
        lines.extend(f"- `{p['file']}:{p['line']}:{p['column']}`: `{p['source'].strip()}`" for p in record["locations"])
        lines.extend(
            f"- Related: `{p['file']}:{p['line']}:{p['column']}`: `{p['source'].strip()}`"
            for p in record["related_locations"]
        )
    if summary["skipped"]:
        lines.extend(["", "## Remaining diagnostics not automatically isolated", ""])
        lines.extend(f"- {item.get('message', item.get('file', 'source'))}: {item['reason']}" for item in summary["skipped"])
    if summary["blockers"]:
        lines.extend(["", "## Blockers", "", *summary["blockers"]])
    (directory / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
    (directory / "report.md").write_text("\n".join(lines) + "\n")
    print(f"More errors ({summary['status']}): {summary['additional_diagnostics']} additional diagnostic record(s); {directory / 'report.md'}", flush=True)
    return {
        "status": summary["status"], "stop_reason": summary["stop_reason"],
        "additional_diagnostics": summary["additional_diagnostics"],
        "excluded_bodies": len(summary["boundaries"]), "report": "more-errors/report.md",
    }
