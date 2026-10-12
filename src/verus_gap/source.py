"""Rust declarations, audited source overlays, and byte-accurate source maps."""

from __future__ import annotations

import bisect
import difflib
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from functools import cached_property

from tree_sitter import Language, Node, Parser, Tree
import tree_sitter_rust

from .gap import GapError, prefix_end
from .more_errors import tokens

LANGUAGE = Language(tree_sitter_rust.language())
ITEMS = {
    "function_item", "function_signature_item", "impl_item", "trait_item",
    "struct_item", "enum_item", "union_item", "type_item", "const_item",
    "static_item", "mod_item", "foreign_mod_item", "macro_definition",
    "macro_invocation", "use_declaration",
}
OWN_ATTRIBUTES = {"field_initializer", "shorthand_field_initializer", "match_arm"}


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str
    reason: str
    declaration: str | None = None
    anchor: int | None = None


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    original: int | None
    anchored: bool = False


@dataclass
class Declaration:
    kind: str
    name: str
    start: int
    end: int
    item_start: int
    body_start: int | None
    body_end: int | None
    line: int
    attributes: list[str]
    parent: str | None
    enabled: bool = True
    existing_boundary: bool = False
    trait_name: str | None = None

    @property
    def id(self) -> str:
        return f"{self.start}:{self.end}:{self.kind}:{self.name}"

    def contains(self, offset: int, body: bool = False) -> bool:
        if body:
            return (
                self.body_start is not None and self.body_end is not None
                and self.body_start < offset < self.body_end
            )
        return self.start <= offset < self.end


class Cfg:
    def __init__(self, values: list[str]):
        self.values = set(values)

    def evaluate(self, expression: str) -> bool:
        parts = [expression[token.start:token.end] for token in tokens(expression)]
        if any(not re.fullmatch(r'"(?:\\.|[^"\\])*"|[A-Za-z_]\w*|[(),=]', part) for part in parts):
            raise GapError(f"Cannot evaluate cfg expression: {expression}")
        index = 0

        def take() -> str:
            nonlocal index
            if index >= len(parts):
                raise GapError(f"Incomplete cfg expression: {expression}")
            value = parts[index]
            index += 1
            return value

        def atom() -> bool:
            name = take()
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                raise GapError(f"Invalid cfg predicate: {expression}")
            if index < len(parts) and parts[index] == "(":
                take()
                operands = []
                while index < len(parts) and parts[index] != ")":
                    operands.append(atom())
                    if index < len(parts) and parts[index] == ",":
                        take()
                    elif index < len(parts) and parts[index] != ")":
                        raise GapError(f"Invalid cfg arguments: {expression}")
                if take() != ")":
                    raise GapError(f"Unclosed cfg predicate: {expression}")
                if name == "all":
                    return all(operands)
                if name == "any":
                    return any(operands)
                if name == "not" and len(operands) == 1:
                    return not operands[0]
                raise GapError(f"Unknown cfg predicate: {name}")
            if index < len(parts) and parts[index] == "=":
                take()
                literal = take()
                try:
                    value = json.loads(literal)
                except json.JSONDecodeError as error:
                    raise GapError(f"Invalid cfg value: {expression}") from error
                return f"{name}={json.dumps(value, ensure_ascii=False)}" in self.values
            return name in self.values

        result = atom()
        if index != len(parts):
            raise GapError(f"Trailing cfg tokens: {expression}")
        return result

    def attribute(self, text: str) -> list[str]:
        match = re.fullmatch(r"#\[\s*cfg_attr\s*\((.*)\)\s*\]", text, re.S)
        if not match:
            return [text]
        stream = tokens(match[1])
        depth, start, arguments = 0, 0, []
        for token in stream:
            if token.value in {"(", "[", "{"}:
                depth += 1
            elif token.value in {")", "]", "}"}:
                depth -= 1
            elif token.value == "," and depth == 0:
                arguments.append(match[1][start:token.start].strip())
                start = token.end
        last = match[1][start:].strip()
        if last:
            arguments.append(last)
        if len(arguments) < 2:
            raise GapError(f"Invalid conditional attribute: {text}")
        if not self.evaluate(arguments[0]):
            return []
        return [
            nested for argument in arguments[1:]
            for nested in self.attribute("#[" + argument + "]")
        ]


@dataclass
class Rendered:
    data: bytes
    segments: list[Segment]
    original: bytes
    edits: list[Edit]
    file: str
    checking_ranges: list[tuple[int, int]] | None = None

    def checked(self, byte: int) -> bool:
        return any(start <= byte < end for start, end in (self.checking_ranges or []))

    @cached_property
    def _segment_starts(self) -> tuple[int, ...]:
        return tuple(segment.start for segment in self.segments)

    @cached_property
    def _line_starts(self) -> tuple[int, ...]:
        return (0, *(match.end() for match in re.finditer(b"\n", self.original)))

    @cached_property
    def _lines(self) -> list[str]:
        return self.original.decode("utf-8").splitlines()

    def location(self, byte: int) -> dict | None:
        index = bisect.bisect_right(self._segment_starts, byte) - 1
        if index < 0:
            return None
        segment = self.segments[index]
        if byte >= segment.end or segment.original is None:
            return None
        original = segment.original if segment.anchored else segment.original + byte - segment.start
        line = bisect.bisect_right(self._line_starts, original)
        column = len(self.original[self._line_starts[line - 1]:original].decode("utf-8", "replace")) + 1
        return {
            "file": self.file, "line": line, "column": column,
            "byte": original, "source": self._lines[line - 1] if line <= len(self._lines) else "",
        }

    def patch(self) -> str:
        return self._patch

    @cached_property
    def _patch(self) -> str:
        return "".join(difflib.unified_diff(
            self.original.decode("utf-8").splitlines(keepends=True),
            self.data.decode("utf-8").splitlines(keepends=True),
            fromfile="original/" + self.file, tofile="scan/" + self.file,
        ))

    @cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @cached_property
    def overlay(self) -> dict:
        return {
            "sha256": self.sha256,
            "edits": [asdict(edit) for edit in self.edits],
            "segments": [asdict(segment) for segment in self.segments],
            "checking_ranges": self.checking_ranges,
        }


def apply_edits(original: bytes, file: str, edits: list[Edit]) -> Rendered:
    segments, chunks, cursor, generated = [], [], 0, 0
    for edit in sorted(edits, key=lambda edit: (edit.start, edit.end)):
        if not cursor <= edit.start <= edit.end <= len(original):
            raise GapError(f"Overlapping or invalid source edits in {file}: {edit.reason}")
        plain = original[cursor:edit.start]
        if plain:
            segments.append(Segment(generated, generated + len(plain), cursor))
            chunks.append(plain)
            generated += len(plain)
        replacement = edit.text.encode("utf-8")
        if replacement:
            segments.append(Segment(
                generated, generated + len(replacement), edit.anchor,
                anchored=edit.anchor is not None,
            ))
            chunks.append(replacement)
            generated += len(replacement)
        cursor = edit.end
    plain = original[cursor:]
    if plain:
        segments.append(Segment(generated, generated + len(plain), cursor))
        chunks.append(plain)
    return Rendered(b"".join(chunks), segments, original, edits, file)


class Source:
    def __init__(self, file: str, data: bytes, cfg: Cfg):
        self.file, self.data, self.cfg = file, data, cfg
        self.text = data.decode("utf-8")
        self.declarations: list[Declaration] = []
        self.preparation: list[Edit] = []
        self.parse_errors: list[dict] = []
        self._collect(self.tree.root_node)
        self._prepare(self.tree.root_node)
        self._render_key: tuple | None = None
        self._rendered: Rendered | None = None

    @cached_property
    def tree(self) -> Tree:
        return Parser(LANGUAGE).parse(self.data)

    def __getstate__(self) -> dict:
        # Transfer the prepared model, not process-local trees or overlay caches.
        return {
            key: value for key, value in self.__dict__.items()
            if key not in {"tree", "_render_key", "_rendered"}
        }

    def __setstate__(self, state: dict):
        self.__dict__.update(state)
        self._render_key = None
        self._rendered = None

    def _text(self, node: Node | None) -> str:
        return self.data[node.start_byte:node.end_byte].decode("utf-8") if node else ""

    def _attrs(self, node: Node) -> list[Node]:
        if node.type in OWN_ATTRIBUTES:
            result = []
            for child in node.named_children:
                if child.type == "attribute_item":
                    result.append(child)
                elif child.type not in {"line_comment", "block_comment"}:
                    break
            return result
        if node.parent is not None and node.parent.type in OWN_ATTRIBUTES:
            return []
        previous = node.prev_named_sibling
        result = []
        while previous is not None and previous.type in {
            "attribute_item", "line_comment", "block_comment",
        }:
            if previous.type == "attribute_item":
                result.append(previous)
            previous = previous.prev_named_sibling
        return list(reversed(result))

    def _leading_start(self, node: Node) -> int:
        start = node.start_byte
        previous = node.prev_named_sibling
        while previous is not None and previous.type in {
            "attribute_item", "line_comment", "block_comment",
        }:
            if previous.type == "attribute_item" or self._text(previous).startswith(("///", "/**")):
                start = previous.start_byte
            previous = previous.prev_named_sibling
        return start

    def _enabled(self, attributes: list[str]) -> bool:
        for attribute in (
            resolved for text in attributes for resolved in self.cfg.attribute(text)
        ):
            match = re.fullmatch(r"#\[\s*cfg\s*\((.*)\)\s*\]", attribute, re.S)
            if match and not self.cfg.evaluate(match[1]):
                return False
        return True

    def _inactive_end(self, node: Node) -> int:
        if node.parent is None or node.parent.type != "block":
            return node.end_byte
        expression = node
        if expression.type == "expression_statement":
            expression = next(
                (child for child in expression.named_children
                 if child.type not in {"attribute_item", "line_comment", "block_comment"}),
                expression,
            )
        end = expression.end_byte
        while expression.type in {
            "call_expression", "field_expression", "index_expression",
            "try_expression", "await_expression",
        }:
            field = "function" if expression.type == "call_expression" else "value"
            child = expression.child_by_field_name(field)
            child = child if child is not None else next(iter(expression.named_children), None)
            if child is None:
                break
            expression = child
        if expression.type == "macro_invocation" and expression.end_byte < end:
            tokens = next(
                (child for child in expression.named_children if child.type == "token_tree"), None,
            )
            if tokens is not None and self.data[tokens.start_byte:tokens.start_byte + 1] == b"{":
                # Rust ends a braced statement macro here; tree-sitter can absorb the next tuple.
                return expression.end_byte
        return node.end_byte

    def _collect(self, node: Node, parent: Declaration | None = None, enabled: bool = True,
                 inactive_until: int | None = None):
        if inactive_until is not None and node.start_byte >= inactive_until:
            enabled, inactive_until = True, None
        attributes = self._attrs(node)
        attrs = [self._text(attribute) for attribute in attributes]
        if enabled and not self._enabled(attrs):
            enabled = False
            end = self._inactive_end(node)
            inactive_until = end if end < node.end_byte else None
        if node.type in {"ERROR"} or node.is_missing:
            self.parse_errors.append({
                "start": node.start_byte, "end": node.end_byte,
                "line": node.start_point.row + 1, "kind": node.type,
            })
        if node.type in {"macro_definition", "macro_invocation", "token_tree"}:
            if node.type == "token_tree":
                return
            recurse = False
        else:
            recurse = True
        current = parent
        if node.type in ITEMS:
            name = self._text(node.child_by_field_name("name"))
            if node.type == "impl_item":
                name = self._text(node.child_by_field_name("type"))
            if node.type == "macro_invocation":
                name = self._text(node.child_by_field_name("macro"))
            name = name or f"<{node.type}>"
            qualified = f"{parent.name}::{name}" if parent else name
            body = node.child_by_field_name("body")
            current = Declaration(
                node.type, qualified,
                self._leading_start(node),
                node.end_byte, node.start_byte,
                body.start_byte if body else None, body.end_byte - 1 if body else None,
                node.start_point.row + 1, attrs, parent.id if parent else None, enabled,
                bool(re.search(
                    r"\b(?:external_body|external)\b", " ".join(attrs),
                )),
                self._text(node.child_by_field_name("trait")) or None,
            )
            self.declarations.append(current)
        if recurse:
            for child in node.named_children:
                self._collect(child, current, enabled, inactive_until)

    def _prepare(self, node: Node):
        if any(
            edit.reason == "inactive cfg before Verus macro expansion"
            and edit.start <= node.start_byte and node.end_byte <= edit.end
            for edit in self.preparation
        ):
            return
        if node.type in {"token_tree", "line_comment", "block_comment", "attribute_item"}:
            return
        attrs = self._attrs(node)
        if "verus_keep_ghost" in self.cfg.values:
            # Verus changes this flag between checking and ghost-erasure passes.
            attrs = [
                attribute for attribute in attrs
                if not re.search(r"\bverus_keep_ghost_body\b", self._text(attribute))
            ]
        if attrs and not self._enabled([self._text(attribute) for attribute in attrs]):
            last = node
            if node.type == "visibility_modifier" and node.parent.type == "ordered_field_declaration_list":
                last = node.next_named_sibling
                while last is not None and last.type in {"line_comment", "block_comment"}:
                    last = last.next_named_sibling
                if last is None:
                    raise GapError(f"Cannot resolve a cfg tuple field in {self.file}.")
            end = self._inactive_end(last)
            if last.next_sibling is not None and last.next_sibling.type == ",":
                end = last.next_sibling.end_byte
            start = min(attrs[0].start_byte, self._leading_start(node))
            self.preparation.append(Edit(
                start, end, "\n" * self.data[
                    start:end
                ].count(b"\n"), "inactive cfg before Verus macro expansion",
            ))
            if end < node.end_byte:
                for child in node.named_children:
                    if child.end_byte > end:
                        self._prepare(child)
            return
        for attribute in attrs:
            text = self._text(attribute)
            match = re.fullmatch(r"#\[\s*cfg\s*\((.*)\)\s*\]", text, re.S)
            if match:
                self.preparation.append(Edit(
                    attribute.start_byte, attribute.end_byte, "",
                    "resolved active cfg before Verus macro expansion",
                ))
            elif self.cfg.attribute(text) != [text]:
                replacement = "\n".join(
                    resolved for resolved in self.cfg.attribute(text)
                    if not re.fullmatch(r"#\[\s*cfg\s*\((.*)\)\s*\]", resolved, re.S)
                )
                self.preparation.append(Edit(
                    attribute.start_byte, attribute.end_byte, replacement,
                    "resolved conditional attribute before Verus macro expansion",
                ))
        if node.type == "macro_invocation":
            path = node.child_by_field_name("macro")
            parts = self._text(path).split("::")
            if path and len(parts) > 1 and parts[0]:
                scope = node.parent
                while scope:
                    if scope.type in {"source_file", "declaration_list", "block"}:
                        bindings = []
                        shadowed = False
                        for item in scope.named_children:
                            if not self._enabled([self._text(attr) for attr in self._attrs(item)]):
                                continue
                            name = self._text(item.child_by_field_name("name"))
                            if name == parts[0] and item.type in {
                                "mod_item", "struct_item", "enum_item", "type_item", "trait_item",
                            }:
                                shadowed = True
                            if item.type != "use_declaration":
                                continue
                            text = self._text(item)
                            match = re.fullmatch(
                                r"(?:pub(?:\([^)]*\))?\s+)?use\s+(::[\w:]+)(?:\s+as\s+(\w+))?\s*;",
                                text,
                            )
                            if match and (match[2] or match[1].split("::")[-1]) == parts[0]:
                                bindings.append(match[1])
                            elif not match and (re.search(r"\b" + re.escape(parts[0]) + r"\b", text) or "*" in text):
                                shadowed = True
                        if shadowed:
                            break
                        if len(bindings) == 1:
                            self.preparation.append(Edit(
                                path.start_byte, path.end_byte,
                                bindings[0] + "::" + "::".join(parts[1:]),
                                "preserve an explicit absolute macro import across Verus expansion",
                                anchor=path.start_byte,
                            ))
                            break
                    scope = scope.parent
            return
        if node.type == "macro_definition":
            return
        if node.type == "const_item":
            ty = node.child_by_field_name("type")

            def references(typ: Node):
                if typ.type == "function_type":
                    return
                if typ.type == "reference_type" and not any(
                    child.type == "lifetime" for child in typ.named_children
                ):
                    self.preparation.append(Edit(
                        typ.start_byte + 1, typ.start_byte + 1, "'static ",
                        "make Rust's implicit const reference lifetime explicit",
                    ))
                for child in typ.named_children:
                    references(child)

            if ty:
                references(ty)
        for child in node.named_children:
            if child.type != "attribute_item":
                self._prepare(child)
        if node.type == "tuple_expression":
            remaining = [
                child for child in node.named_children
                if child.type not in {"attribute_item", "line_comment", "block_comment"}
                and not any(
                    edit.reason == "inactive cfg before Verus macro expansion"
                    and edit.start <= child.start_byte and child.end_byte <= edit.end
                    for edit in self.preparation
                )
            ]
            if len(remaining) == 1:
                item = remaining[0]
                if item.next_sibling is None or item.next_sibling.type != ",":
                    self.preparation.append(Edit(
                        item.end_byte, item.end_byte, ",",
                        "preserve a one-element tuple after cfg removal",
                    ))

    @property
    def functions(self) -> list[Declaration]:
        return [
            declaration for declaration in self.declarations
            if declaration.kind == "function_item" and declaration.enabled
        ]

    def enclosing(self, byte: int, body: bool = False) -> Declaration | None:
        candidates = [
            declaration for declaration in self.declarations
            if declaration.enabled and declaration.contains(byte, body)
        ]
        return min(candidates, key=lambda item: item.end - item.start) if candidates else None

    def render(self, bodies: set[str], excluded: set[str],
               prepare: bool = True, wrap: bool = True,
               unwrapped: set[str] | None = None, load_support: bool = False,
               support_crate: str = "vstd") -> Rendered:
        key = (
            frozenset(bodies), frozenset(excluded), prepare, wrap,
            frozenset(unwrapped or ()), load_support, support_crate,
        )
        if key != self._render_key or self._rendered is None:
            self._rendered = self._render(
                bodies, excluded, prepare, wrap, unwrapped, load_support, support_crate,
            )
            self._render_key = key
        return self._rendered

    def _render(self, bodies: set[str], excluded: set[str],
                prepare: bool, wrap: bool,
                unwrapped: set[str] | None, load_support: bool,
                support_crate: str) -> Rendered:
        edits = list(self.preparation) if prepare else []
        if not wrap and not load_support and not bodies and not excluded:
            return apply_edits(self.data, self.file, edits)
        inactive = [(edit.start, edit.end) for edit in edits if edit.start != edit.end]
        verified = {
            item.id for item in self.declarations if item.enabled
            and any(
                re.match(r"#\[\s*(?:\w+::)*verus_(?:verify|spec)\b", attr)
                for text in item.attributes for attr in self.cfg.attribute(text)
            )
        }
        verified_parents = {item.parent for item in self.declarations if item.id in verified}
        promoted = {
            item.id for item in self.declarations if item.enabled and item.parent is None
            and item.kind == "impl_item" and item.id in verified_parents and item.id not in verified
        }
        attributed = {
            item.id for item in self.declarations if item.parent is None and item.id in verified
        } | promoted
        attributed |= {
            item.id for item in self.declarations if item.enabled and item.parent is None
            and item.kind == "macro_invocation"
            and item.name.split("::")[-1] in {"verus", "state_machine", "tokenized_state_machine"}
        }
        checked_outside = attributed - (unwrapped or set())
        outside = [
            item for item in self.declarations if item.enabled
            and item.parent is None and (
                item.id in (unwrapped or set())
                or item.id in attributed
                or item.kind == "mod_item" and item.body_start is None
            )
        ]
        for declaration in self.declarations:
            if not declaration.enabled or any(
                start <= declaration.start < end for start, end in inactive
            ):
                continue
            if wrap and any(
                item.id not in checked_outside
                and item.start <= declaration.start and declaration.end <= item.end
                for item in outside
            ):
                continue
            ancestors = [
                item for item in self.declarations
                if item.id in excluded and item.start <= declaration.start
                and declaration.end <= item.end
            ]
            if ancestors and declaration.id not in excluded:
                continue
            if declaration.id in excluded:
                edits.append(Edit(
                    declaration.item_start, declaration.item_start,
                    "#[verifier::external]\n", "exclude a rejected declaration",
                    declaration.id,
                ))
            elif declaration.id in bodies:
                edits.append(Edit(
                    declaration.item_start, declaration.item_start,
                    "#[verifier::external_body]\n", "isolate an observed function body",
                    declaration.id,
                ))
        if wrap or load_support:
            split = len(self.text[:prefix_end(self.text)].encode("utf-8"))
        support = f"use {support_crate}::prelude::Ghost as _;\n" if load_support else ""
        if wrap:
            edits = [
                Edit(split, split, f"\n{support}\n{support_crate}::prelude::verus! {{\n", "Verus checking scope"),
                *edits,
            ]
            for item in outside:
                boundary = ""
                if item.id not in checked_outside and (item.id in excluded or item.id in attributed):
                    boundary = "#[verifier::external]\n"
                elif item.id in promoted:
                    boundary = f"#[{support_crate}::prelude::verus_verify]\n"
                edits.extend([
                    Edit(item.start, item.start,
                         "\n}\n" + boundary,
                         ("retain original attribute-based Verus checking" if item.id in checked_outside
                          else "retain rejected item as ordinary Rust context"), item.id),
                    Edit(item.end, item.end, f"\n{support_crate}::prelude::verus! {{\n",
                         "resume checking after an ordinary Rust item", item.id),
                ])
            edits.append(Edit(len(self.data), len(self.data), "\n}\n", "Verus checking scope"))
        elif load_support:
            edits = [
                Edit(split, split, "\n" + support, "load builtin metadata without a glob import"),
                *edits,
            ]
        rendered = apply_edits(self.data, self.file, edits)
        if wrap:
            omitted = [(item.start, item.end) for item in outside if item.id not in checked_outside]
            for item in self.declarations:
                if item.id in excluded:
                    omitted.append((item.start, item.end))
                elif item.id in bodies and item.body_start is not None:
                    omitted.append((item.body_start + 1, item.body_end))
            cursor, ranges = split, []
            for start, end in sorted(omitted):
                if cursor < start:
                    ranges.append((cursor, start))
                cursor = max(cursor, end)
            if cursor < len(self.data):
                ranges.append((cursor, len(self.data)))
            rendered.checking_ranges = ranges
        return rendered

    def identity(self) -> dict:
        return {
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "functions": [asdict(declaration) for declaration in self.functions],
            "declarations": len(self.declarations), "parse_errors": self.parse_errors,
            "preparation": [asdict(edit) for edit in self.preparation],
        }
