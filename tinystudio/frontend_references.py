"""Grammar-based JavaScript asset references, without Node or application imports.

The Vite manifest remains authoritative for opaque runtime module expressions.
Literal imports, re-exports and nested template expressions are inspected by the
maintained JavaScript grammar rather than token-context heuristics.
"""
from __future__ import annotations


class ModuleReferenceError(ValueError):
    pass


def _escape(source: str, index: int) -> tuple[str, int]:
    if index >= len(source):
        raise ModuleReferenceError("Unterminated JavaScript escape")
    value = source[index]
    if value in "\n\r":
        return "", index + (2 if source[index:index+2] == "\r\n" else 1)
    if value in {"x", "u"}:
        start = index + 1
        if value == "u" and source[start:start+1] == "{":
            end = source.find("}", start+1)
            digits = source[start+1:end] if end >= 0 else ""
            next_index = end+1
        else:
            end = start + (2 if value == "x" else 4)
            digits, next_index = source[start:end], end
        try:
            if not digits or any(c not in "0123456789abcdefABCDEF" for c in digits):
                raise ValueError()
            return chr(int(digits, 16)), next_index
        except (ValueError, OverflowError) as error:
            raise ModuleReferenceError("Invalid JavaScript character escape") from error
    return {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}.get(value, value), index+1



def _walk(root, *, named=True):
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children if named else node.children))


def _parse(source: bytes):
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError as error:
        raise ModuleReferenceError("Frontend validation requires tree-sitter==0.25.2 and tree-sitter-javascript==0.25.0; install the declared TinyStudio build/runtime dependencies") from error
    parser = Parser(Language(tree_sitter_javascript.language()))
    tree = parser.parse(source)
    if not tree.root_node.has_error:
        return tree
    # JS grammar 0.25.0 rejects the legal ModuleExportName `in`. Normalize ONLY
    # the six observed ECharts alias-error forms in a same-length parse copy.
    # TC39: ModuleExportName : IdentifierName. Asset bytes and offsets stay intact.
    copy = bytearray(source)
    errors = [node for node in _walk(tree.root_node, named=False) if node.type == "ERROR" or node.is_missing]
    for node in errors:
        parent, text = node.parent, source[node.start_byte:node.end_byte]
        previous, following = node.prev_named_sibling, node.next_named_sibling
        if (not node.is_missing and text == b"as in" and parent.type == "export_clause"
                and parent.parent.type == "export_statement" and previous is not None
                and previous.type == "export_specifier"
                and previous.child_by_field_name("name") is not None
                and not source[previous.end_byte:node.start_byte].strip()):
            copy[node.end_byte-2:node.end_byte] = b"ix"
        elif (not node.is_missing and text == b"in as" and parent.type == "named_imports"
                and parent.parent.type == "import_clause" and parent.parent.parent.type == "import_statement"
                and following is not None and following.type == "import_specifier"
                and following.child_by_field_name("name") is not None
                and not source[node.end_byte:following.start_byte].strip()):
            copy[node.start_byte:node.start_byte+2] = b"ix"
        else:
            raise ModuleReferenceError(f"JavaScript syntax cannot be verified at {node.start_point}")
    tree = parser.parse(bytes(copy))
    if not errors or tree.root_node.has_error:
        raise ModuleReferenceError("JavaScript syntax cannot be verified after reserved-name compatibility parsing")
    return tree


def _literal(node, source: bytes) -> str | None:
    if node.type not in {"string", "template_string"}:
        return None
    if node.type == "template_string" and any(child.type == "template_substitution" for child in node.named_children):
        return None
    text = source[node.start_byte+1:node.end_byte-1].decode("utf-8")
    value, index = [], 0
    while index < len(text):
        if text[index] == "\\":
            escaped, index = _escape(text, index+1)
            value.append(escaped)
        else:
            value.append(text[index]); index += 1
    return "".join(value)


def reference_literals(source: str) -> tuple[list[str], list[str]]:
    data = source.encode("utf-8")
    tree = _parse(data)
    references, literals = [], []
    for node in _walk(tree.root_node):
        literal = _literal(node, data)
        if literal is not None:
            literals.append(literal)
        if node.type in {"import_statement", "export_statement"}:
            module = node.child_by_field_name("source")
            if module is not None:
                references.append(_literal(module, data))
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            if function is None or function.type != "import":
                continue
            arguments = node.child_by_field_name("arguments")
            values = [child for child in arguments.named_children if child.type != "comment"]
            if not values:
                raise ModuleReferenceError("Dynamic import has no module expression")
            argument = values[0]
            # Preserve checking of literal-leading concatenations from the prior
            # guard; opaque variable lookups remain manifest-authoritative.
            while argument.type in {"binary_expression", "parenthesized_expression"}:
                if argument.type == "binary_expression":
                    argument = argument.child_by_field_name("left")
                else:
                    argument = next(child for child in argument.named_children if child.type != "comment")
            value = _literal(argument, data)
            if argument.type == "template_string" and value is None:
                raise ModuleReferenceError("Computed template module import cannot be verified")
            if value is not None:
                references.append(value)
    return references, literals


def module_references(source: str) -> list[str]:
    return reference_literals(source)[0]
