"""Stdio LSP for Vaqrin. Language diagnostics come from vaqc check."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.parse import unquote, urlsplit

from dataclasses import dataclass, replace

KEYWORDS = """package import as pub var const comptime func class interface enum error type public
private static operator embed self extern export unsafe return break continue if else switch
select for in move new catch true false null ref""".split()
TYPES = """bool char str string void never ErrorId Self isize usize c_int c_char
i8 i16 i32 i64 i128 u8 u16 u32 u64 u128 f16 f32 f64""".split()
IDENT = r"[^\W\d]\w*"
MASK = re.compile(r'''//[^\n]*|/\*[\s\S]*?(?:\*/|\Z)|"(?:\\.|[^"\\\r\n])*"?|'(?:\\.|[^'\\\r\n])*'?''')
IMPORT = re.compile(r"\bimport\s*\(([^)]*)\)", re.S)
PACKAGE = re.compile(rf"\s*package\s+({IDENT})")
ERROR = re.compile(r"^(.*):(\d+):(\d+): error\[([^]]+)\]: (.+(?:\n  candidate [^\n]+)*)$", re.M)
GLOBAL_ERROR = re.compile(r"^vaqc: error\[([^]]+)\]: (.+)$", re.M)
KINDS = {"func": 3, "class": 7, "interface": 8, "enum": 13, "error": 13, "type": 7, "const": 21, "var": 6}
DECLARED_TYPES = {"class", "interface", "enum", "error", "type"}


def masked(text):
    return MASK.sub(lambda match: re.sub(r"[^\r\n]", " ", match[0]), text)


def matching_delimiter(code, start, left, right):
    depth = 0
    for match in re.finditer(f"[{re.escape(left + right)}]", code[start:]):
        depth += 1 if match[0] == left else -1
        if depth == 0:
            return start + match.start()
    return len(code)


def columns(text):
    return len(text.encode("utf-16-le")) // 2


def position_offset(text, count):
    for index, char in enumerate(text):
        if count <= 0:
            return index
        count -= 2 if ord(char) > 0xFFFF else 1
    return len(text)


def file_path(uri):
    parts = urlsplit(uri)
    if parts.scheme == "file" and parts.netloc in ("", "localhost"):
        return Path(unquote(parts.path)).resolve()


def read_message():
    headers = {}
    while line := sys.stdin.buffer.readline():
        if not line.strip():
            size = int(headers[b"content-length"])
            return json.loads(sys.stdin.buffer.read(size))
        key, value = line.split(b":", 1)
        headers[key.lower()] = value.strip()


@dataclass
class Symbol:
    file: Path
    name: str
    kind: str
    start: int
    end: int
    detail: str
    scope: tuple | None = None
    owner: str = ""
    type: str = ""
    implements: tuple = ()
    public: bool = True
    initializer: str = ""
    type_parameters: tuple = ()
    declaration_only: bool = False
    parameters: tuple = ()


def location(symbol, text):
    end = symbol.start + len(symbol.name)
    return {"uri": symbol.file.as_uri(), "range": {
        "start": offset_position(text, symbol.start), "end": offset_position(text, end)}}


def offset_position(text, offset):
    line = text.count("\n", 0, offset)
    start = text.rfind("\n", 0, offset) + 1
    return {"line": line, "character": columns(text[start:offset])}


def generic_arguments(text):
    parts = []
    start = depth = 0
    for index, char in enumerate(text):
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return tuple(part for part in parts if part)


def member_qualifier(code, offset):
    dot = re.search(r"\??\.\s*$", code[:offset])
    if not dot:
        return ""
    end = len(code[:dot.start()].rstrip())
    start = end
    if code[end - 1:end] == ">":
        depth = 1
        for index in range(end - 2, -1, -1):
            if code[index] == ">":
                depth += 1
            elif code[index] == "<":
                depth -= 1
                if not depth:
                    start = index
                    break
    name = re.search(rf"{IDENT}(?:\.{IDENT})*\s*$", code[:start])
    return code[name.start():end] if name else ""


class SourceIndex:
    def __init__(self, file, text):
        self.file = file
        self.text = text
        self.code = masked(text)
        self.scopes = {}
        pending = []
        for match in re.finditer(r"[{}]", self.code):
            if match[0] == "{":
                pending.append(match.start())
            elif pending:
                self.scopes[pending.pop()] = match.end()
        self.scopes.update((start, len(text) + 1) for start in pending)
        self.symbols = []
        self.classes = []
        self.parse_declarations()
        self.parse_bindings()

    def scope(self, offset):
        candidates = [(start, end) for start, end in self.scopes.items() if start < offset < end]
        return max(candidates, default=None)

    def owner(self, offset):
        return next((symbol.name for symbol in reversed(self.classes)
                     if symbol.scope[0] < offset < symbol.scope[1]), "")

    def initializer(self, start):
        finish = start
        while finish < len(self.code) and self.code[finish] not in ";\r\n}":
            if self.code[finish] in "([{":
                left = self.code[finish]
                finish = matching_delimiter(self.code, finish, left, {"(": ")", "[": "]", "{": "}"}[left])
            finish += 1
        return self.text[start:finish].strip()

    def parse_declarations(self):
        heads = re.compile(rf"\b(func|class|interface|enum|error|type)\s+(?:C\s+)?({IDENT})")
        for match in heads.finditer(self.code):
            kind, name = match.groups()
            start = match.start(2)
            # A named declaration begins with its keyword, optionally preceded by modifiers.
            line = self.code.rfind("\n", 0, match.start()) + 1
            prefix = self.code[line:match.start()]
            modifiers = re.search(r"(?:^|[;:{}])\s*((?:(?:pub|const|static|extern|export|unsafe|comptime)\s+)*)$", prefix)
            if not modifiers:
                continue
            owner = self.owner(start)
            scope = self.scope(start)
            if kind == "func" and scope and not owner:
                continue
            body = re.search(r"[{;\r\n]", self.code[match.end():])
            finish = match.end() + body.start() if body else len(self.code)
            bracket = match.end() + len(self.code[match.end():]) - len(self.code[match.end():].lstrip())
            type_parameters = ()
            if self.code[bracket:bracket + 1] == "<":
                end = matching_delimiter(self.code, bracket, "<", ">")
                type_parameters = tuple(p.strip().split(":", 1)[0].strip()
                                        for p in self.code[bracket + 1:end].split(","))
                bracket = end + 1
                while self.code[bracket:bracket+1].isspace():
                    bracket += 1
            result = ""
            params = []
            if kind == "func" and self.code[bracket:bracket + 1] == "(":
                close = matching_delimiter(self.code, bracket, "(", ")")
                terminator = re.search(r"[{;\r\n]", self.code[close + 1:])
                finish = close + 1 + terminator.start() if terminator else len(self.code)
                result = self.text[close + 1:finish].split(":", 1)[0].strip()
                params = list(re.finditer(rf"(?:^|,)\s*&?({IDENT})\s*:\s*([^,]+)", self.code[bracket+1:close]))
            public = "pub" in modifiers[1].split()
            if owner:
                outer = next((item for item in self.classes if item.name == owner), None)
                access = self.code[outer.scope[0]:start] if outer else ""
                labels = re.findall(r"(?m)^\s*(public|private):", access)
                public |= bool(outer and outer.kind == "interface" or labels and labels[-1] == "public")
            signature = self.text[line + modifiers.start(1):finish].strip()
            symbol = Symbol(self.file, name, kind, start, match.end(), signature, owner=owner, type=result,
                            public=public, type_parameters=type_parameters,
                            declaration_only=kind == "func" and finish not in self.scopes,
                            parameters=tuple(p[0].lstrip(", ").strip() for p in params))
            self.symbols.append(symbol)
            if finish in self.scopes and kind in ("class", "interface", "enum", "error"):
                symbol.scope = (finish, self.scopes[finish])
                if kind == "class":
                    match_impl = re.search(r"(?m)^\s*#implements\s*:\s*([^;\r\n]+)",
                                           self.code[finish:self.scopes[finish]])
                    if match_impl:
                        symbol.implements = tuple(part.strip() for part in match_impl[1].split(","))
                self.classes.append(symbol)
            if finish in self.scopes and kind == "func":
                for param in params:
                    p_start = bracket + 1 + param.start(1)
                    self.symbols.append(Symbol(self.file, param[1], "var", p_start, p_start + len(param[1]),
                                               param[2].strip(), (finish, self.scopes[finish]), type=param[2].strip()))

    def parse_bindings(self):
        pattern = re.compile(rf"(?m)(?:\b(var|const)\s+&?({IDENT})(?:\s*:\s*([^=;\n{{]+))?\s*(=|;)|(?<!\w)&?({IDENT})\s*:=)")
        for match in pattern.finditer(self.code):
            if match[5]:
                name, index = match[5], match.start(5)
            else:
                name, index = match[2], match.start(2)
            scope = self.scope(index)
            declared = (match[3] or "").strip()
            initializer = self.initializer(match.end()) if match[4] != ";" else ""
            owner = self.owner(index)
            if owner and scope and not any(item.scope == scope for item in self.classes):
                owner = ""
            self.symbols.append(Symbol(self.file, name, match[1] or "var", index, index + len(name),
                                       declared, scope, owner, declared, public=True, initializer=initializer))

    def visible(self, offset):
        result = []
        for symbol in self.symbols:
            if symbol.owner:
                continue
            if symbol.kind in ("var", "const") and symbol.scope:
                if symbol.start <= offset and symbol.scope[0] < offset < symbol.scope[1]:
                    result.append(symbol)
            else:
                result.append(symbol)
        return sorted(result, key=lambda item: (item.scope[0] if item.kind in ("var","const") and item.scope else -1,
                                                item.start), reverse=True)


class Server:
    def __init__(self, root):
        self.root = root.parent if root.is_file() else root
        self.documents = {}
        self.indices = {}
        self.timers = {}
        self.checked_bindings = {}
        self.binding_types = {}
        self.pending_inlays = {}
        self.requested_inlays = set()
        self.inlay_refresh = False
        self.refresh_id = 0
        self.snippets = False
        self.thread = ThreadPoolExecutor(max_workers=2)
        self.lock = threading.Lock()
        self.output = threading.Lock()
        self.stopped = False

    def send(self, **message):
        data = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode()
        with self.output:
            sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
            sys.stdout.buffer.flush()

    def compiler(self):
        explicit = os.environ.get("VAQC")
        choices = [explicit] if explicit else [
            *(parent / "build/host/vaqc" for parent in (self.root, *self.root.parents)),
            os.environ.get("VAQRIN_PATH_COMPILER"), shutil.which("vaqc"),
        ]
        for option in filter(None, choices):
            path = (self.root / Path(option).expanduser()).resolve()
            if path.is_file() and os.access(path, os.X_OK):
                return path

    def source_index(self, path):
        uri = path.as_uri()
        if uri in self.documents:
            key, text = self.documents[uri]
            stamp = ("open", key, text)
        else:
            info = path.stat()
            stamp = (info.st_mtime_ns, info.st_size)
            text = None
        if path in self.indices and self.indices[path][0] == stamp:
            return self.indices[path][1]
        if text is None:
            text = path.read_text(encoding="utf-8")
        model = SourceIndex(path, text)
        self.indices[path] = (stamp, model)
        return model

    def roots(self, path):
        compiler = self.compiler()
        roots = [p / "stdlib" for p in (self.root, *self.root.parents) if (p / "stdlib").is_dir()]
        if compiler:
            roots.extend(p / "share/vaqrin/stdlib" for p in list(compiler.parents)[:2])
        package = next((p for p in path.parents if (p / "package.toml").is_file()), self.root)
        return [*roots, package, package.parent, path.parent]

    def imports(self, model):
        for group in IMPORT.finditer(model.code):
            original = model.text[group.start(1):group.end(1)]
            for item in re.finditer(rf'"([^"\n]+)"(?:\s+as\s+({IDENT}))?|((?:{IDENT}/)*{IDENT})(?:\s+as\s+({IDENT}))?', original):
                filename, file_alias, name, package_alias = item.groups()
                if filename:
                    target = (model.file.parent / filename).resolve()
                    if target.is_file():
                        yield file_alias or target.stem, [target]
                else:
                    for root in self.roots(model.file):
                        folder = root / name.removeprefix("std/")
                        paths = sorted(p for p in folder.glob("*.vqr") if p.is_file())
                        if paths:
                            yield package_alias or name.split("/")[-1], paths
                            break

    def symbols_in(self, paths):
        for path in paths:
            try:
                if path.as_uri() in self.documents or path.stat().st_size < 600_000:
                    yield from self.source_index(path).symbols
            except (OSError, UnicodeError):
                continue

    def package_symbols(self, model, package):
        paths = dict(self.imports(model)).get(package, [])
        return [symbol for symbol in self.symbols_in(paths) if not symbol.owner and symbol.public
                and (not symbol.scope or symbol.kind in ("class", "interface", "enum", "error"))]

    def local_symbols(self, model, offset):
        header = PACKAGE.match(model.code)
        package = header[1] if header else None
        visible = model.visible(offset)
        for symbol in self.symbols_in(p for p in model.file.parent.glob("*.vqr") if p != model.file):
            if symbol.owner or (symbol.scope and symbol.kind not in DECLARED_TYPES):
                continue
            sibling = PACKAGE.match(self.source_index(symbol.file).code)
            if package == (sibling[1] if sibling else None):
                visible.append(symbol)
        return visible

    def type_of(self, model, symbol, seen=None):
        if binding_types := self.binding_types.get(symbol.file.as_uri()):
            byte = len(self.source_index(symbol.file).text[:symbol.start].encode())
            if inferred := binding_types.get(byte):
                return inferred
        if symbol.type:
            return symbol.type
        if symbol.kind in ("class", "interface", "enum", "error", "type"):
            return symbol.name
        seen = seen if seen is not None else set()
        key = (symbol.file, symbol.start)
        if key in seen:
            return ""
        seen.add(key)
        source = self.source_index(symbol.file)
        value = symbol.initializer
        if value.startswith('"'):
            return "string"
        if value.startswith("'"):
            return "char"
        if value in ("true", "false"):
            return "bool"
        if match := re.fullmatch(r"[-+]?[0-9][0-9_]*(\.[0-9_]+)?([iuf]\d+|[iu]size)?", value):
            return match[2] or ("f64" if match[1] else "i32")
        if re.fullmatch(IDENT, value):
            choices = self.resolve(source, symbol.start, value)
            return self.type_of(source, choices[0], seen) if choices else ""
        call = re.match(rf"({IDENT}(?:\.{IDENT})*)(?:\s*<([^()]*)>)?\s*[({{]", value)
        if call:
            expression = masked(value)
            opening = call.end() - 1
            close = matching_delimiter(expression, opening, call[0][-1], ")" if call[0][-1] == "(" else "}")
            tail = expression[close + 1:].strip()
            if close == len(value) or tail not in ("", "!", ".unwrap()", "!.unwrap()"):
                return ""
            if call[1] in TYPES:
                return call[1]
            path = call[1].split(".")
            choices = self.resolve(source, symbol.start, path[-1], ".".join(path[:-1]), seen)
            if choices:
                origin = choices[0]
                result = self.type_of(source, origin, seen)
                if origin.kind in ("class", "enum", "type"):
                    result = call[1]
                    if call[2]:
                        result += f"<{call[2]}>"
                elif call[2] and origin.type_parameters:
                    result = self.specialize(origin, arguments=generic_arguments(call[2])).type
                if tail.startswith("!"):
                    result = result.removeprefix("!")
                if tail.endswith(".unwrap()"):
                    result = result[1:] if result.startswith("?") else ""
                return result
        return ""

    def specialize(self, symbol, receiver="", arguments=()):
        substitutions = {}
        if symbol.owner:
            source = self.source_index(symbol.file)
            owner = next((item for item in source.classes if item.name == symbol.owner), None)
            if owner:
                if not receiver:
                    receiver = owner.name
                    if owner.type_parameters:
                        receiver += "<" + ", ".join(owner.type_parameters) + ">"
                if "<" in receiver and receiver.endswith(">"):
                    actual = generic_arguments(receiver.split("<", 1)[1][:-1])
                    substitutions.update(zip(owner.type_parameters, actual))
                substitutions["Self"] = receiver
        for name in symbol.type_parameters:
            substitutions.pop(name, None)
        substitutions.update(zip(symbol.type_parameters, arguments))
        if not substitutions:
            return symbol

        def substitute(text):
            return re.sub(IDENT, lambda token: substitutions.get(token[0], token[0]), text)

        return replace(symbol, type=substitute(symbol.type), detail=substitute(symbol.detail),
                       parameters=tuple(substitute(param) for param in symbol.parameters))

    def members(self, model, offset, qualifier, seen=None):
        aliases = dict(self.imports(model))
        if qualifier in aliases:
            return self.package_symbols(model, qualifier)
        head, generic, arguments = qualifier.partition("<")
        base = head.rstrip().split(".")
        if qualifier == "self" and model.owner(offset):
            candidates = [item for item in model.classes if item.name == model.owner(offset)]
        elif len(base) > 1:
            candidates = self.resolve(model, offset, base[-1], ".".join(base[:-1]), seen)
        else:
            candidates = self.resolve(model, offset, qualifier, seen=seen)
        if not candidates:
            return []
        source = candidates[0]
        receiver = self.type_of(model, source, seen).lstrip("!?*& ").removeprefix("const ").strip()
        if generic and source.kind in DECLARED_TYPES:
            receiver = head.rstrip() + "<" + arguments
        name = receiver.split("<", 1)[0]
        if not name:
            return []
        if "." in name:
            prefix, name = name.rsplit(".", 1)
            paths = aliases.get(prefix) or [p for files in aliases.values() for p in files if p.parent.name == prefix]
        else:
            paths = [source.file]
            if source.kind not in ("class", "interface", "enum", "error"):
                paths += [model.file, *(p for paths in aliases.values() for p in paths)]
        return [self.specialize(item, receiver if generic or source.kind not in DECLARED_TYPES else "")
                for item in self.symbols_in(dict.fromkeys(paths)) if item.owner == name
                and (item.public or model.owner(offset) == name)]

    def resolve(self, model, offset, word, qualifier="", seen=None):
        candidates = self.members(model, offset, qualifier, seen) if qualifier else self.local_symbols(model, offset)
        if not qualifier:
            local_member = next((item for item in model.symbols if item.owner and item.start == offset), None)
            if local_member:
                return [self.specialize(local_member)]
        hits = [item for item in candidates if item.name == word]
        if not qualifier and hits:
            if hits[0].kind in ("var", "const") and hits[0].scope:
                return hits[:1]
            hits = [item for item in hits if item.file == model.file] or hits
        return hits

    def editor_position(self, params):
        uri = params["textDocument"]["uri"]
        path = file_path(uri)
        if not path or (uri not in self.documents and not path.is_file()):
            return None, 0
        model = self.source_index(path)
        lines = model.text.split("\n")
        row = params["position"]["line"]
        if not 0 <= row < len(lines):
            return model, len(model.text)
        return model, sum(len(line) + 1 for line in lines[:row]) + position_offset(
            lines[row], params["position"]["character"])

    def lookup(self, params, mode):
        model, offset = self.editor_position(params)
        if not model:
            return None
        for token in re.finditer(IDENT, model.code):
            if not token.start() <= offset <= token.end():
                continue
            qualifier = member_qualifier(model.code, token.start())
            targets = self.resolve(model, token.start(), token[0], qualifier)
            if not targets and token[0] in ("Self", "self"):
                owner = model.owner(token.start())
                targets = [item for item in model.classes if item.name == owner]
                if targets and mode == "hover":
                    detail = owner
                    if targets[0].type_parameters:
                        detail += "<" + ", ".join(targets[0].type_parameters) + ">"
                    return {"contents": {"kind": "markdown", "value": f"```vaqrin\n{token[0]}: {detail}\n```"}}
            if not targets:
                return None
            if mode == "hover":
                target = targets[0]
                tail = token.end() + len(model.code[token.end():]) - len(model.code[token.end():].lstrip())
                if target.kind == "func" and model.code[tail:tail + 1] == "<":
                    end = matching_delimiter(model.code, tail, "<", ">")
                    if end < len(model.code) and model.code[end + 1:].lstrip().startswith("("):
                        args = generic_arguments(model.code[tail + 1:end])
                        if len(args) == len(target.type_parameters):
                            target = self.specialize(target, arguments=args)
                inferred = self.type_of(model, target)
                detail = target.detail
                if target.kind in ("var", "const"):
                    detail = f"{target.kind} {target.name}" + (f": {inferred}" if inferred else "")
                return {"contents": {"kind": "markdown", "value": f"```vaqrin\n{detail}\n```"},
                        "range": {"start": offset_position(model.text, token.start()),
                                  "end": offset_position(model.text, token.end())}}
            if mode == "typeDefinition":
                inferred = self.type_of(model, targets[0]).lstrip("!?*& ").removeprefix("const ").split("<", 1)[0]
                if not inferred or inferred in TYPES:
                    return None
                package, _, name = inferred.rpartition(".")
                targets = [item for item in self.resolve(model, offset, name, package)
                           if item.kind in DECLARED_TYPES]
                if not targets and not package:
                    targets = [item for alias, _ in self.imports(model)
                               for item in self.package_symbols(model, alias)
                               if item.name == name and item.kind in DECLARED_TYPES]
                    if len({(item.file, item.start) for item in targets}) > 1:
                        return None
            if mode == "implementation":
                target = targets[0]
                target_source = self.source_index(target.file)
                interface = target if target.kind == "interface" else next(
                    (item for item in target_source.classes if item.kind == "interface" and item.name == target.owner), None)
                if not interface:
                    return None
                paths = set([model.file, *(file for _, files in self.imports(model) for file in files)])
                for folder in (self.root / "stdlib", self.root / "examples", model.file.parent):
                    paths.update(folder.glob("*/*.vqr"))
                    paths.update(folder.glob("*.vqr"))
                classes = []
                for item in self.symbols_in(sorted(paths)):
                    for implemented in item.implements:
                        package, _, name = implemented.split("<", 1)[0].strip().rpartition(".")
                        if name != interface.name:
                            continue
                        source = self.source_index(item.file)
                        if interface in self.resolve(source, item.start, name, package):
                            classes.append(item)
                            break
                targets = ([method for item in classes for method in self.source_index(item.file).symbols
                            if method.owner == item.name and method.name == token[0]]
                           if target.owner else classes)
            if mode in ("definition", "declaration"):
                preferred = [item for item in targets if item.declaration_only == (mode == "declaration")]
                targets = preferred or targets
            result = []
            for target in targets:
                source = self.source_index(target.file)
                value = location(target, source.text)
                if value not in result:
                    result.append(value)
            return result
        return None

    def inlay_hints(self, params):
        uri = params["textDocument"]["uri"]
        path = file_path(uri)
        if not path:
            return []
        model = self.source_index(path)
        first, last = ((value["line"], value["character"]) for value in
                       (params["range"]["start"], params["range"]["end"]))
        hints = []
        covered = set()

        def hint(position, inferred, binding_kind):
            if binding_kind == "short":
                return {"position": position, "label": inferred, "kind": 1, "paddingLeft": True}
            prefix = ":" if binding_kind in ("loop", "pattern") else ": "
            return {"position": position, "label": prefix + inferred, "kind": 1}

        def is_short(end):
            return re.match(rf"(?:\s*,\s*&?{IDENT})*\s*:=", model.code[end:]) is not None

        if (bindings := self.checked_bindings.get(uri)) is not None:
            types_by_end = {}
            for binding in bindings:
                types_by_end.setdefault(binding["end"], set()).add((binding["type"], binding.get("kind")))
            start_byte = 0
            line_start = 0
            for row, line in enumerate(model.text.split("\n")):
                end_byte = start_byte + len(line.encode())
                if first[0] <= row <= last[0]:
                    character = 0
                    offset = start_byte
                    for index, char in enumerate(line, 1):
                        offset += len(char.encode())
                        character += 2 if ord(char) > 0xFFFF else 1
                        types = types_by_end.get(offset)
                        if types and first <= (row, character) <= last:
                            covered.add((row, character))
                            if len(types) == 1:
                                inferred, kind = next(iter(types))
                                if kind is None and is_short(line_start + index):
                                    kind = "short"
                                hints.append(hint({"line": row, "character": character}, inferred, kind))
                start_byte = end_byte + 1
                line_start += len(line) + 1
        for symbol in model.symbols:
            if symbol.kind not in ("var", "const") or symbol.type or not symbol.initializer:
                continue
            position = offset_position(model.text, symbol.end)
            key = (position["line"], position["character"])
            if first <= key <= last and key not in covered and (inferred := self.type_of(model, symbol)):
                hints.append(hint(position, inferred, "short" if is_short(symbol.end) else "local"))
        return hints

    def request_inlays(self, identifier, params):
        uri = params["textDocument"]["uri"]
        with self.lock:
            self.requested_inlays.add(uri)
            if uri in self.documents and uri not in self.checked_bindings:
                self.pending_inlays.setdefault(uri, []).append((identifier, params))
            else:
                self.send(id=identifier, result=self.inlay_hints(params))

    def signature_help(self, params):
        model, offset = self.editor_position(params)
        if not model:
            return None
        stack = []
        for token in re.finditer(r"[()\[\]{},]", model.code[:offset]):
            symbol = token[0]
            if symbol in "([{":
                stack.append([symbol, token.start(), 0])
            elif symbol in ")]}":
                if stack:
                    stack.pop()
            elif stack:
                stack[-1][2] += 1
        if not stack or stack[-1][0] != "(":
            return None
        _, opening, argument = stack[-1]
        call = re.search(rf"({IDENT}(?:\.{IDENT})*)(?:\s*<[^()]*>)?\s*$", model.code[:opening])
        if not call:
            return None
        package, _, name = call[1].rpartition(".")
        functions = [item for item in self.resolve(model, call.start(), name, package) if item.kind == "func"]
        if not functions:
            return None
        signatures = [{"label": item.detail, "parameters": [{"label": p} for p in item.parameters]}
                      for item in functions]
        active = next((index for index, item in enumerate(functions) if argument < len(item.parameters)), 0)
        return {"signatures": signatures, "activeSignature": active, "activeParameter": argument}

    def complete(self, uri, position):
        if uri not in self.documents:
            return []
        _, text = self.documents[uri]
        lines = text.split("\n")
        row = position["line"]
        if not 0 <= row < len(lines):
            return []
        # Keep code-point offsets when comments include supplementary-plane characters.
        index = position_offset(lines[row], position["character"])
        clean = masked(text)
        prefix = clean.split("\n")[row][:index]
        if lines[row][:index].rstrip() and prefix != prefix.rstrip():
            offset = sum(len(line) + 1 for line in lines[:row]) + index
            if any(match.start() < offset <= match.end() for match in MASK.finditer(text)):
                return []
        model, offset = self.editor_position({"textDocument": {"uri": uri}, "position": position})
        if not model:
            return []
        entries = {}
        qualifier = re.search(rf"({IDENT}(?:\.{IDENT})*)\??\.\w*$", prefix)
        if qualifier:
            candidates = self.members(model, offset, qualifier[1])
        elif re.search(r"\bimport\s*\([^)]*$", clean[:offset]):
            for root in self.roots(model.file):
                if root.is_dir():
                    entries.update({p.name: 9 for p in root.iterdir() if p.is_dir() and any(p.glob("*.vqr"))})
            candidates = []
        else:
            entries.update({name: 14 for name in KEYWORDS})
            entries.update({name: 7 for name in TYPES})
            candidates = self.local_symbols(model, offset)
        items = {name: {"label": name, "kind": kind} for name, kind in entries.items()}
        for symbol in reversed(candidates):
            item = {"label": symbol.name, "kind": KINDS[symbol.kind]}
            if symbol.kind == "func":
                item["detail"] = symbol.detail
            items[symbol.name] = item
        suffix = re.match(r"\w*", model.code[offset:]).end()
        followed_by_call = model.code[offset + suffix:].lstrip().startswith(("(", "<"))
        for name, item in items.items():
            if item["kind"] == KINDS["func"] and self.snippets and not followed_by_call:
                item["insertTextFormat"] = 2
                item["insertText"] = name + "($0)"
        return [item for _, item in sorted(items.items())]

    def publish(self, uri, diagnostics, version=None):
        params = {"uri": uri, "diagnostics": diagnostics}
        if version is not None:
            params["version"] = version
        self.send(method="textDocument/publishDiagnostics", params=params)

    def check(self, uri, version, text):
        with self.lock:
            if self.stopped or self.documents.get(uri) != (version, text):
                return
        diagnostics, bindings = self.check_text(uri, text)
        types = {}
        for binding in bindings or ():
            types.setdefault(binding["begin"], set()).add(binding["type"])
        with self.lock:
            if not self.stopped and self.documents.get(uri) == (version, text):
                self.checked_bindings[uri] = bindings
                self.binding_types[uri] = {start: next(iter(names)) for start, names in types.items() if len(names) == 1}
                self.publish(uri, diagnostics, version)
                for identifier, params in self.pending_inlays.pop(uri, []):
                    self.send(id=identifier, result=self.inlay_hints(params))
                if bindings and self.inlay_refresh and uri not in self.requested_inlays:
                    self.refresh_id += 1
                    self.send(id=f"vaqrin-inlays-{self.refresh_id}", method="workspace/inlayHint/refresh")

    def check_text(self, uri, text):
        def diagnostic(message, code=None, row=0, start=0, end=0, severity=1):
            return {"message": message, "source": "vaqc", "code": code, "severity": severity,
                    "range": {"start": {"line": row, "character": start},
                              "end": {"line": row, "character": end}}}

        source = file_path(uri)
        if not source or source.suffix != ".vqr":
            return [], None
        compiler = self.compiler()
        if not compiler:
            return [diagnostic("Build vaqc or set VAQC/PATH to enable compiler diagnostics.", severity=2)], None
        try:
            with tempfile.TemporaryDirectory(prefix="vaqrin-lsp-") as temp:
                overlay = Path(temp) / "buffer.vqr"
                overlay.write_text(text, encoding="utf-8")
                def run(*flags):
                    return subprocess.run([compiler, "check", *flags, "--overlay", overlay, source],
                                          cwd=self.root, capture_output=True, encoding="utf-8",
                                          errors="replace", timeout=20)
                result = run("--editor-hints")
                if "unknown option '--editor-hints'" in result.stderr:
                    result = run()
        except (OSError, subprocess.TimeoutExpired) as error:
            return [diagnostic(str(error), severity=2)], None
        try:
            bindings = json.loads(result.stdout) if result.stdout else None
        except ValueError:
            bindings = None
        if result.returncode == 0:
            return [], bindings
        lines = text.split("\n")
        output = []
        for path, row, col, code, message in ERROR.findall(result.stderr):
            row, col = int(row) - 1, int(col) - 1
            if Path(path).resolve() != source or not 0 <= row < len(lines):
                output.append(diagnostic(f"{path}:{row + 1}:{col + 1}: {message}", code))
            else:
                output.append(diagnostic(message, code, row, columns(lines[row][:col]),
                                         columns(lines[row][:col + 1])))
        output.extend(diagnostic(message, code) for code, message in GLOBAL_ERROR.findall(result.stderr))
        return output or [diagnostic(result.stderr.strip() or f"vaqc exited with {result.returncode}")], bindings

    def schedule(self, uri, version, text):
        with self.lock:
            first_check = uri not in self.documents
            if self.documents.get(uri) != (version, text):
                self.checked_bindings.pop(uri, None)
                self.binding_types.pop(uri, None)
                self.requested_inlays.discard(uri)
            self.documents[uri] = (version, text)
            old = self.timers.pop(uri, None)
            if old:
                old.cancel()

            def enqueue():
                with self.lock:
                    if not self.stopped and self.documents.get(uri) == (version, text):
                        self.timers.pop(uri, None)
                        self.thread.submit(self.check, uri, version, text)

            timer = threading.Timer(0 if first_check else 0.08, enqueue)
            self.timers[uri] = timer
            timer.start()

    def stop(self):
        with self.lock:
            self.stopped = True
            for timer in self.timers.values():
                timer.cancel()
        self.thread.shutdown(wait=False, cancel_futures=True)

    def handle(self, method, params):
        if method == "initialize":
            capabilities = params.get("capabilities", {})
            completion = capabilities.get("textDocument", {}).get("completion", {})
            self.snippets = completion.get("completionItem", {}).get("snippetSupport", False)
            self.inlay_refresh = capabilities.get("workspace", {}).get("inlayHint", {}).get("refreshSupport", False)
            return {"capabilities": {"positionEncoding": "utf-16",
                                     "textDocumentSync": {"openClose": True, "change": 1, "save": True},
                                     "completionProvider": {"triggerCharacters": [".", "/"]},
                                     "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
                                     "hoverProvider": True, "definitionProvider": True,
                                     "declarationProvider": True, "implementationProvider": True,
                                     "typeDefinitionProvider": True, "inlayHintProvider": True}}
        if method == "shutdown":
            self.stop()
        elif method == "textDocument/signatureHelp":
            return self.signature_help(params)
        elif method in ("textDocument/hover", "textDocument/definition", "textDocument/declaration",
                        "textDocument/implementation", "textDocument/typeDefinition"):
            return self.lookup(params, method.rsplit("/", 1)[-1])
        elif method == "textDocument/completion":
            return self.complete(params["textDocument"]["uri"], params["position"])
        elif method in ("textDocument/didOpen", "textDocument/didChange", "textDocument/didSave"):
            document = params["textDocument"]
            uri = document["uri"]
            if method == "textDocument/didOpen":
                self.schedule(uri, document.get("version"), document["text"])
            elif method == "textDocument/didChange" and params["contentChanges"]:
                self.schedule(uri, document.get("version"), params["contentChanges"][-1]["text"])
            elif method == "textDocument/didSave" and uri in self.documents:
                self.schedule(uri, *self.documents[uri])
        elif method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            with self.lock:
                self.documents.pop(uri, None)
                self.checked_bindings.pop(uri, None)
                self.binding_types.pop(uri, None)
                self.requested_inlays.discard(uri)
                for identifier, _ in self.pending_inlays.pop(uri, []):
                    self.send(id=identifier, result=[])
                timer = self.timers.pop(uri, None)
                if timer:
                    timer.cancel()
                self.publish(uri, [])
        elif method == "$/cancelRequest":
            with self.lock:
                for uri, requests in self.pending_inlays.items():
                    self.pending_inlays[uri] = [request for request in requests if request[0] != params["id"]]
        elif method != "initialized":
            raise NotImplementedError(method)


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    server = Server(root)
    try:
        while request := read_message():
            method = request.get("method")
            if method is None:
                continue
            if method == "exit":
                return 0 if server.stopped else 1
            try:
                params = request.get("params") or {}
                if method == "textDocument/inlayHint" and "id" in request:
                    server.request_inlays(request["id"], params)
                    continue
                value = server.handle(method, params)
                if "id" in request:
                    server.send(id=request["id"], result=value)
            except (NotImplementedError, OSError, KeyError, TypeError, ValueError) as error:
                if "id" in request:
                    code = -32601 if isinstance(error, NotImplementedError) else -32602
                    server.send(id=request["id"], error={"code": code, "message": str(error)})
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
