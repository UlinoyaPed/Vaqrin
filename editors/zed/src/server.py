"""Stdio LSP for Vaqrin. Language diagnostics come from vaqc check."""

from bisect import bisect_right
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
from urllib.parse import urlsplit
from urllib.request import url2pathname

from dataclasses import dataclass, replace

if "debug_hover_frames" not in globals():
    from debug_hover import debug_hover_frames
if "read_packet" not in globals():
    from protocol import read_packet

KEYWORDS = """package import as pub var const comptime func class interface enum error type
static operator embed self extern export unsafe return break continue if else switch
select for in move new catch defer true false null ref""".split()
CONTEXTUAL_KEYWORDS = ("from", "let", "mut", "raw", "while")
TYPES = """bool char str string void never ErrorId Self isize usize c_int c_char
i8 i16 i32 i64 i128 u8 u16 u32 u64 u128 f16 f32 f64""".split()
IDENT = r"[^\W\d]\w*"
MASK = re.compile(r'''//[^\n]*|/\*[\s\S]*?(?:\*/|\Z)|"(?:\\.|[^"\\\r\n])*"?|'(?:\\.|[^'\\\r\n])*'?''')
IMPORT = re.compile(r"\bimport\s*\(([^)]*)\)", re.S)
IMPORT_START = re.compile(r"\bimport\s*\(")
IMPORT_ITEM = re.compile(rf"\s*({IDENT}(?:\s*/\s*{IDENT})*)(?:\s+as\s+({IDENT}))?\s*")
PACKAGE = re.compile(rf"\s*package\s+({IDENT})")
ERROR = re.compile(r"^(.*):(\d+):(\d+): error\[([^]]+)\]: (.+(?:\n  candidate [^\n]+)*)$", re.M)
GLOBAL_ERROR = re.compile(r"^vaqc: error\[([^]]+)\]: (.+)$", re.M)
KINDS = {"func": 3, "class": 7, "interface": 8, "enum": 13, "error": 13, "type": 7, "const": 21, "var": 6}
DECLARED_TYPES = {"class", "interface", "enum", "error", "type"}


def masked(text):
    return MASK.sub(lambda match: re.sub(r"[^\r\n]", " ", match[0]), text)


def import_text(text):
    return MASK.sub(lambda match: match[0] if match[0].startswith('"') else masked(match[0]), text)


def import_items(model):
    for group in IMPORT.finditer(model.code):
        start = group.start(1)
        for part in re.finditer(r"[^,]+", group[1]):
            item = import_text(model.text[start + part.start():start + part.end()])
            if match := IMPORT_ITEM.fullmatch(item):
                name, alias = match.groups()
                name = re.sub(r"\s+", "", name)
                default = name.rsplit("/", 1)[-1]
                if alias or (re.fullmatch(IDENT, default) and default != "_"
                             and (default not in KEYWORDS or default == "ref")):
                    yield name, alias or default


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
        return Path(url2pathname(parts.path)).resolve()


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


def parameter_bindings(code):
    pending = []
    start = 0
    for part in generic_arguments(code):
        offset = code.index(part, start)
        start = offset + len(part)
        match = re.fullmatch(rf"(comptime\s+)?({IDENT})(?:\s*:\s*(.+))?", part, re.S)
        if not match:
            continue
        pending.append((offset + match.start(2), match[2], match[1] or ""))
        if match[3]:
            type_name = " ".join(match[3].split())
            modifier = "comptime " if pending[0][2] else ""
            yield from ((position, name, modifier, type_name) for position, name, _ in pending)
            pending.clear()


def unqualified_type(type_name):
    return re.sub(r"^(?:(?:[!?*^]\s*|(?:ref|const|in|mut|move)\s+))+", "", type_name).strip()


def call_tokens(code, start=0):
    skip = start
    for token in re.finditer(r"[()\[\]{},<]", code[start:]):
        offset = start + token.start()
        if offset < skip:
            continue
        if token[0] == "<":
            if not re.search(r"\w\s*$", code[max(start, offset - 64):offset]):
                continue
            depth = 1
            for part in re.finditer(r"[<>()[\]{};=]", code[offset + 1:]):
                at = offset + 1 + part.start()
                if at < skip:
                    continue
                symbol = part[0]
                if symbol in "([":
                    skip = matching_delimiter(code, at, symbol, ")" if symbol == "(" else "]") + 1
                elif symbol == "<":
                    depth += 1
                elif symbol == ">":
                    depth -= 1
                    if depth == 0:
                        if code[at + 1:].lstrip().startswith(("(", ".", "{")):
                            skip = at + 1
                        else:
                            skip = offset
                        break
                else:
                    skip = offset
                    break
            else:
                skip = offset
            continue
        yield token[0], offset


def call_head(code, opening):
    head = code[:opening].rstrip()
    arguments = ()
    if head.endswith(">"):
        depth = 1
        for start in range(len(head) - 2, -1, -1):
            if head[start] == ">":
                depth += 1
            elif head[start] == "<":
                depth -= 1
                if not depth:
                    arguments = generic_arguments(head[start + 1:-1])
                    head = head[:start].rstrip()
                    break
    if name := re.search(rf"{IDENT}$", head):
        return name[0], member_qualifier(code, name.start()), arguments, name.start()


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
                type_parameters = tuple(p.split(":", 1)[0].strip().removeprefix("comptime ")
                                        for p in generic_arguments(self.code[bracket + 1:end]))
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
                params = list(parameter_bindings(self.code[bracket + 1:close]))
            public = "pub" in modifiers[1].split()
            if owner:
                outer = next((item for item in self.classes if item.name == owner), None)
                public |= bool(outer and outer.kind == "interface")
            signature = self.text[line + modifiers.start(1):finish].strip()
            labels = tuple(f"{modifier}{name}: {type_name}" for _, name, modifier, type_name in params)
            if kind == "func" and self.code[bracket:bracket + 1] == "(":
                head = " ".join(self.text[line + modifiers.start(1):bracket].split())
                signature = f"{head}({', '.join(labels)})" + (f" {result}" if result else "")
            result_type = re.sub(rf"\s+from\s+{IDENT}$", "", result)
            symbol = Symbol(self.file, name, kind, start, match.end(), signature, owner=owner, type=result_type,
                            public=public, type_parameters=type_parameters,
                            declaration_only=kind == "func" and finish not in self.scopes,
                            parameters=labels)
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
                for position, name, _, type_name in params:
                    p_start = bracket + 1 + position
                    self.symbols.append(Symbol(self.file, name, "var", p_start, p_start + len(name), type_name,
                                               (finish, self.scopes[finish]), type=type_name))

    def parse_bindings(self):
        pattern = re.compile(
            rf"(?m)(?:\b(var|const)\s+&?({IDENT})(?:\s*:\s*([^=;\n{{]+))?\s*(=|;)"
            rf"|(?<!\w)&?({IDENT})\s*:=|(?:^|(?<=[;{{}}]))[ \t]*ref[ \t]+(?:mut[ \t]+)?({IDENT})\s*=(?!=))"
        )
        for match in pattern.finditer(self.code):
            binding = 6 if match[6] else 5 if match[5] else 2
            name, index = match[binding], match.start(binding)
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
        self.signature_offsets = False
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
            *(parent / "build/host" / ("vaqc.exe" if os.name == "nt" else "vaqc")
              for parent in (self.root, *self.root.parents)),
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

    def standard_roots(self):
        compiler = self.compiler()
        roots = [p / "stdlib" for p in (self.root, *self.root.parents) if (p / "stdlib").is_dir()]
        if compiler:
            roots.extend(p / "share/vaqrin/stdlib" for p in list(compiler.parents)[:2])
        if sources := os.environ.get("VAQRIN_STDLIB_DIR"):
            roots.insert(0, Path(sources))
        return roots

    def project_root(self, path):
        return next((p for p in path.parents if (p / "package.toml").is_file()), path.parent)

    def local_dependencies(self, project):
        try:
            manifest = (project / "package.toml").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return {}
        section = re.search(r"(?ms)^[ \t]*\[dependencies\][^\n]*\n(.*?)(?=^[ \t]*\[|\Z)", manifest)
        if not section:
            return {}
        paths = re.findall(rf'''(?m)^\s*({IDENT})\s*=\s*\{{\s*path\s*=\s*("|')([^"'\n]+)\2\s*\}}''',
                           section[1])
        return {name: project / path for name, _, path in paths if not Path(path).is_absolute()}

    def source_paths(self, folder):
        paths = {p for p in folder.glob("*.vqr") if p.is_file()}
        paths.update(p for uri in self.documents if (p := file_path(uri))
                     and p.parent == folder and p.suffix == ".vqr")
        return sorted(paths)

    def imports(self, model):
        for name, alias in import_items(model):
            logical = name.removeprefix("std/")
            package = logical.rsplit("/", 1)[-1]
            folders = [root / logical for root in self.standard_roots()]
            if not name.startswith("std/"):
                project = self.project_root(model.file)
                if dependency := self.local_dependencies(project).get(logical):
                    folders.append(dependency)
                folders.extend(root / logical for root in (project, project.parent, model.file.parent))
            for folder in dict.fromkeys(folders):
                if folder.is_dir():
                    results = []
                    for path in self.source_paths(folder.resolve()):
                        try:
                            header = PACKAGE.match(self.source_index(path).code)
                        except (OSError, UnicodeError):
                            continue
                        if header and header[1] == package:
                            results.append(path.resolve())
                    if results:
                        yield alias, sorted(set(results))
                    break

    def complete_import(self, model, offset, start):
        prefix = model.code[start:offset]
        start += prefix.rfind(",") + 1
        item = import_text(model.text[start:offset]).lstrip()
        if not re.fullmatch(rf"(?:(?:{IDENT}/)*\w*)", item):
            return []
        parent, _, leaf = item.rpartition("/")
        roots = self.standard_roots()
        names = set()
        if parent != "std" and not parent.startswith("std/"):
            project = self.project_root(model.file)
            roots.extend((project, project.parent, model.file.parent))
            if not parent:
                names.update(name for name in self.local_dependencies(project) if name.startswith(leaf))
        parent = parent.removeprefix("std/") if parent != "std" else ""
        for root in dict.fromkeys(roots):
            folder = root / parent
            if folder.is_dir():
                names.update(p.name for p in folder.iterdir() if p.is_dir() and p.name.startswith(leaf)
                             and re.fullmatch(IDENT, p.name) and any(p.glob("*.vqr")))
        return [{"label": name, "kind": 9} for name in sorted(names)]

    def symbols_in(self, paths):
        for path in paths:
            try:
                if path.as_uri() in self.documents or path.stat().st_size < 600_000:
                    yield from self.source_index(path).symbols
            except (OSError, UnicodeError):
                continue

    def package_symbols(self, paths):
        return [symbol for symbol in self.symbols_in(paths) if not symbol.owner and symbol.public
                and (not symbol.scope or symbol.kind in ("class", "interface", "enum", "error"))]

    def local_symbols(self, model, offset):
        header = PACKAGE.match(model.code)
        visible = model.visible(offset)
        if not header:
            return visible
        package = header[1]
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
        def after_postfix(result, tail):
            if "??" in tail or not re.fullmatch(r"(?:[!?]\s*)*(?:\.unwrap\(\))?", tail):
                return ""
            operators = tail.removesuffix(".unwrap()")
            if tail.endswith(".unwrap()"):
                operators += "?"
            for operator in operators:
                if operator.isspace():
                    continue
                if not result.startswith(operator):
                    return ""
                result = result[1:]
            return result

        if bare := re.fullmatch(rf"({IDENT})\s*((?:[!?]\s*)*)", value):
            choices = self.resolve(source, symbol.start, bare[1])
            return after_postfix(self.type_of(source, choices[0], seen), bare[2]) if choices else ""
        call = re.match(rf"({IDENT}(?:\.{IDENT})*)(?:\s*<([^()]*)>)?\s*[({{]", value)
        if call:
            expression = masked(value)
            opening = call.end() - 1
            close = matching_delimiter(expression, opening, call[0][-1], ")" if call[0][-1] == "(" else "}")
            tail = expression[close + 1:].strip()
            if close == len(value):
                return ""
            if call[1] in TYPES:
                return after_postfix(call[1], tail)
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
                return after_postfix(result, tail)
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

        detail = substitute(symbol.detail)
        if arguments and len(arguments) == len(symbol.type_parameters):
            generic = re.search(rf"\bfunc\s+{re.escape(symbol.name)}\s*<", symbol.detail)
            if generic:
                end = matching_delimiter(symbol.detail, generic.end() - 1, "<", ">")
                detail = symbol.detail[:generic.end()] + ", ".join(arguments) + substitute(symbol.detail[end:])
        return replace(symbol, type=substitute(symbol.type), detail=detail,
                       parameters=tuple(substitute(param) for param in symbol.parameters))

    def members(self, model, offset, qualifier, seen=None):
        aliases = dict(self.imports(model))
        if qualifier in aliases:
            return self.package_symbols(aliases[qualifier])
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
        receiver = unqualified_type(self.type_of(model, source, seen))
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

    def debug_value(self, model, target):
        if target.file != model.file or not target.scope or target.owner:
            return None
        matching = []
        lines = model.text.split("\n")
        for frame in debug_hover_frames(self.root):
            row = frame["line"]
            if Path(frame["path"]).resolve() != model.file or not 0 <= row < len(lines):
                continue
            matching.append(frame)
        if len(matching) != 1:
            return None
        frame = matching[0]
        row = frame["line"]
        offset = sum(len(line) + 1 for line in lines[:row]) + position_offset(lines[row], frame["column"])
        live = next((symbol for symbol in model.visible(offset) if symbol.name == target.name), None)
        if live is target and target.start < offset:
            return frame["values"].get(target.name)

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
                    if (value := self.debug_value(model, target)) is not None:
                        detail += " = " + value.replace("\n", r"\n").replace("\r", r"\r")
                fence = "`" * max(3, 1 + max(map(len, re.findall(r"`+", detail)), default=0))
                return {"contents": {"kind": "markdown", "value": f"{fence}vaqrin\n{detail}\n{fence}"},
                        "range": {"start": offset_position(model.text, token.start()),
                                  "end": offset_position(model.text, token.end())}}
            if mode == "typeDefinition":
                inferred = unqualified_type(self.type_of(model, targets[0])).split("<", 1)[0]
                if not inferred or inferred in TYPES:
                    return None
                package, _, name = inferred.rpartition(".")
                targets = [item for item in self.resolve(model, offset, name, package)
                           if item.kind in DECLARED_TYPES]
                if not targets and not package:
                    targets = [item for _, paths in self.imports(model)
                               for item in self.package_symbols(paths)
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

    def call_signatures(self, model, opening):
        head = call_head(model.code, opening)
        if not head:
            return []
        name, qualifier, generics, start = head
        signatures = []
        for item in self.resolve(model, start, name, qualifier):
            if item.kind == "func":
                if item.file == model.file and item.start == start:
                    continue
                if len(generics) <= len(item.type_parameters):
                    signatures.append(self.specialize(item, arguments=generics))
            elif item.kind in ("var", "const"):
                callable_type = self.type_of(model, item)
                if callable_type.startswith("func("):
                    end = matching_delimiter(callable_type, 4, "(", ")")
                    types = generic_arguments(callable_type[5:end])
                    result = callable_type[end + 1:].strip()
                    signatures.append(replace(item, parameters=types,
                                              detail=f"func {name}({', '.join(types)}) {result}".rstrip()))
        return signatures

    def signature_help(self, params):
        model, offset = self.editor_position(params)
        if not model:
            return None
        stack = []
        for symbol, position in call_tokens(model.code):
            if position >= offset:
                break
            if symbol in "([{":
                stack.append([symbol, position, 0])
            elif symbol in ")]}":
                if stack:
                    stack.pop()
            elif stack:
                stack[-1][2] += 1
        for bracket, opening, argument in reversed(stack):
            if bracket == "(" and (functions := self.call_signatures(model, opening)):
                break
        else:
            return None

        depth = count = 0
        last = opening
        for symbol, position in call_tokens(model.code, opening + 1):
            if symbol in "([{":
                depth += 1
            elif symbol in ")]}":
                if not depth:
                    expression = model.text[last + 1:position]
                    if model.code[last + 1:position].strip() or any(
                            token[0].startswith(('"', "'")) for token in MASK.finditer(expression)):
                        count += 1
                    break
                depth -= 1
            elif not depth:
                count += 1
                last = position
        else:
            count = argument + 1
        desired = max(count, argument + (argument > 0))

        def rank(index):
            size = len(functions[index].parameters)
            return (size != desired, size < desired, abs(size - desired))

        signatures = []
        for item in functions:
            labels = ", ".join(item.parameters)
            start = item.detail.find(f"({labels})") + 1
            parameters = []
            for parameter in item.parameters:
                end = start + len(parameter)
                label = [columns(item.detail[:start]), columns(item.detail[:end])] if self.signature_offsets else parameter
                parameters.append({"label": label})
                start = end + 2
            signature = {"label": item.detail, "parameters": parameters}
            if argument < len(parameters):
                signature["activeParameter"] = argument
            signatures.append(signature)
        active = min(range(len(functions)), key=rank)
        result = {"signatures": signatures, "activeSignature": active}
        if "activeParameter" in signatures[active]:
            result["activeParameter"] = argument
        return result

    def complete(self, uri, position):
        if uri not in self.documents:
            return []
        model, offset = self.editor_position({"textDocument": {"uri": uri}, "position": position})
        if not model:
            return []
        masked_token = next((match for match in MASK.finditer(model.text)
                             if match.start() < offset <= match.end()), None)
        if masked_token and not masked_token[0].startswith('"'):
            return []
        for group in IMPORT_START.finditer(model.code, 0, offset):
            if ")" not in model.code[group.end():offset]:
                return self.complete_import(model, offset, group.end())
        if masked_token:
            return []
        prefix = model.code[model.code.rfind("\n", 0, offset) + 1:offset]
        entries = {}
        qualifier = re.search(rf"({IDENT}(?:\.{IDENT})*)\??\.\w*$", prefix)
        if qualifier:
            candidates = self.members(model, offset, qualifier[1])
        else:
            entries.update({name: 14 for name in (*KEYWORDS, *CONTEXTUAL_KEYWORDS)})
            entries.update({name: 7 for name in TYPES})
            entries.update({alias: 9 for _, alias in import_items(model)})
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

    def check_text(self, uri, text, *, symbols=False, workspace=False, replacements=None):
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
            if not symbols:
                syntax = subprocess.run(
                    [compiler, "check", "--syntax-only", "--diagnostics=json", "-"],
                    input=text.encode("utf-8"), cwd=self.root, capture_output=True, timeout=20)
                if syntax.returncode:
                    try:
                        report = json.loads(syntax.stderr)
                    except ValueError:
                        report = None
                    if isinstance(report, dict) and report.get("path") == "<stdin>":
                        item = diagnostic(report["message"], report.get("code"))
                        item["range"] = report.get("range", item["range"])
                        return [item], None
            with tempfile.TemporaryDirectory(prefix="vaqrin-lsp-") as temp:
                overlay = Path(temp) / "buffer.vqr"
                overlay.write_bytes(text.encode("utf-8"))
                with self.lock:
                    documents = dict(self.documents)
                overlays = {str(source): str(overlay)}
                neighboring = {source}
                if workspace:
                    neighboring.update(path.resolve() for path in source.parent.glob("*.vqr") if path.is_file())
                changes = {file_path(other_uri): other_text for other_uri, (_, other_text) in documents.items()}
                changes.update(replacements or {})
                for other, other_text in changes.items():
                    if other is None or other == source or other.suffix != ".vqr" or not other.parent.is_dir():
                        continue
                    replacement = Path(temp) / f"buffer-{len(overlays)}.vqr"
                    replacement.write_bytes(other_text.encode("utf-8"))
                    overlays[str(other)] = str(replacement)
                    if workspace and other.parent == source.parent:
                        neighboring.add(other)
                overlay_flags = ["--overlay", overlay]
                if workspace or len(overlays) > 1:
                    overlay_map = Path(temp) / "overlays.json"
                    overlay_map.write_text(json.dumps(overlays), encoding="utf-8")
                    overlay_flags = ["--overlay-map", overlay_map]
                paths = [source, *sorted(neighboring - {source})]

                def run(*flags):
                    result = subprocess.run([compiler, "check", *flags, *overlay_flags, *paths],
                                            cwd=self.root, capture_output=True, encoding="utf-8",
                                            errors="replace", timeout=20)
                    if not workspace and overlay_flags[0] == "--overlay-map" and "unknown option '--overlay-map'" in result.stderr:
                        overlay_flags[:] = ["--overlay", overlay]
                        return run(*flags)
                    return result
                if symbols:
                    flag = "--editor-workspace-symbols" if workspace else "--editor-symbols"
                    result = run(flag, "--diagnostics=json")
                else:
                    result = run("--editor-hints", "--diagnostics=json")
                    if "unknown option '--diagnostics=json'" in result.stderr:
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
        if symbols:
            return [], None
        try:
            report = json.loads(result.stderr)
        except ValueError:
            report = None
        if isinstance(report, dict) and "message" in report:
            path = report.get("path")
            item = diagnostic(report["message"], report.get("code"))
            if path and Path(path).resolve() == source and "range" in report:
                item["range"] = report["range"]
            elif path:
                item["message"] = f"{path}: {item['message']}"
            return [item], bindings
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

    def format_document(self, params, *, report_failure=True):
        document = self.documents.get(params["textDocument"]["uri"])
        compiler = self.compiler()
        if document is None or not compiler:
            return []
        _, text = document
        try:
            options = params.get("options", {})
            command = [compiler, "fmt", "--diagnostics=json", f"--tab-size={options.get('tabSize', 4)}"]
            if not options.get("insertSpaces", True):
                command.append("--use-tabs")
            payload = text.encode("utf-8")
            result = subprocess.run([*command, "-"], input=payload, cwd=self.root,
                                    capture_output=True, timeout=10)
            if result.returncode and b"unknown format option" in result.stderr:
                result = subprocess.run([compiler, "fmt", "-"], input=payload, cwd=self.root,
                                        capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            if report_failure:
                raise ValueError(f"Vaqrin formatting failed: {error}") from error
            return []
        if result.returncode:
            if report_failure:
                message = result.stderr.decode("utf-8", errors="replace").strip()
                try:
                    diagnostic = json.loads(message)
                    message = diagnostic["message"]
                except (ValueError, KeyError, TypeError):
                    pass
                raise ValueError(f"Vaqrin formatting failed: {message}")
            return []
        formatted = result.stdout.decode("utf-8")
        if formatted == text:
            return []
        return [{"range": {"start": {"line": 0, "character": 0},
                           "end": offset_position(text, len(text))},
                 "newText": formatted}]

    def code_actions(self, params):
        uri = params["textDocument"]["uri"]
        document = self.documents.get(uri)
        if document is None:
            return []
        version, text = document
        requested = params.get("context", {}).get("only", [])

        def includes(kind):
            return not requested or any(kind == item or kind.startswith(item + ".") for item in requested)

        def action(title, kind, edits):
            return {"title": title, "kind": kind, "edit": {"documentChanges": [
                {"textDocument": {"uri": uri, "version": version}, "edits": edits}]}}

        actions = []
        if includes("source.vaqrin.format"):
            edits = self.format_document(params, report_failure=False)
            if edits:
                actions.append(action("Format Vaqrin document", "source.vaqrin.format", edits))
        if not includes("refactor.rewrite"):
            return actions

        diagnostics, bindings = self.check_text(uri, text)
        if diagnostics or not bindings:
            return actions
        selection = params["range"]
        raw = text.encode("utf-8")
        start = (selection["start"]["line"], selection["start"]["character"])
        end = (selection["end"]["line"], selection["end"]["character"])
        for binding in bindings:
            if binding["kind"] != "local":
                continue
            first = offset_position(text, len(raw[:binding["begin"]].decode("utf-8")))
            last = offset_position(text, len(raw[:binding["end"]].decode("utf-8")))
            if (first["line"], first["character"]) > end or (last["line"], last["character"]) < start:
                continue
            insert = ": " + binding["type"]
            at = binding["end"]
            edited = (raw[:at] + insert.encode("utf-8") + raw[at:]).decode("utf-8")
            errors, _ = self.check_text(uri, edited)
            if not errors:
                actions.append(action("Add explicit binding type", "refactor.rewrite", [
                    {"range": {"start": last, "end": last}, "newText": insert}]))
        return actions

    def semantic_symbol(self, params, method):
        uri = params["textDocument"]["uri"]
        source = file_path(uri)
        document = self.documents.get(uri)
        empty = [] if method in ("references", "definition") else None
        if source is None or document is None:
            return empty
        version, text = document
        lines = text.split("\n")
        row = params["position"]["line"]
        if not 0 <= row < len(lines):
            return empty
        offset = sum(len(line) + 1 for line in lines[:row])
        offset += position_offset(lines[row], params["position"]["character"])
        byte_offset = len(text[:offset].encode("utf-8"))

        def index(buffer, workspace, replacements=None):
            _, symbols = self.check_text(uri, buffer, symbols=True, workspace=workspace,
                                         replacements=replacements)
            if symbols is not None:
                return [(*map(Path, (item.get("path", str(source)),
                                    item.get("declarationPath", str(source)))),
                         item["begin"], item["end"], item["declaration"], item["definition"])
                        for item in symbols]
            return None

        workspace = True
        symbols = index(text, workspace)
        if symbols is None:
            workspace = False
            symbols = index(text, workspace)
        if symbols is None:
            return empty
        selected = [item for item in symbols if item[0] == source and item[2] <= byte_offset < item[3]]
        if len(selected) != 1:
            return empty
        declaration = (selected[0][1], selected[0][4])
        occurrences = [item for item in symbols if (item[1], item[4]) == declaration]
        files = {source: (uri, version, text, text.encode("utf-8"))}
        for other_uri, (revision, buffer) in self.documents.items():
            path = file_path(other_uri)
            if path and path != source:
                files[path] = (other_uri, revision, buffer, buffer.encode("utf-8"))

        def file_data(path):
            if path not in files:
                raw = path.read_bytes()
                files[path] = (path.as_uri(), None, raw.decode("utf-8"), raw)
            return files[path]

        def symbol_range(occurrence):
            _, _, buffer, raw = file_data(occurrence[0])
            return {side: offset_position(buffer, len(raw[:idx].decode("utf-8")))
                    for side, idx in zip(("start", "end"), occurrence[2:4])}

        if method in ("references", "definition"):
            include_definition = params.get("context", {}).get("includeDeclaration", False)
            return [{"uri": file_data(item[0])[0], "range": symbol_range(item)} for item in occurrences
                    if (method == "definition" and item[5]) or
                       (method == "references" and (include_definition or not item[5]))]
        if method == "prepareRename":
            return symbol_range(selected[0])
        replacement = params["newName"]
        if not replacement.isidentifier() or replacement in KEYWORDS or replacement == "_":
            raise ValueError("new name must be a Vaqrin identifier")
        encoded = replacement.encode("utf-8")
        root = next((path for path in source.parents if (path / "package.toml").is_file()),
                    source.parent)
        grouped = {}
        for item in occurrences:
            if not item[0].is_relative_to(root):
                raise ValueError("renaming would edit a file outside this project")
            grouped.setdefault(item[0], []).append(item)

        replacements, translations = {}, {}
        for path, items in grouped.items():
            _, _, _, raw = file_data(path)
            parts, ends, shifts = [], [], [0]
            previous = 0
            for _, _, start, end, _, _ in items:
                if start < previous:
                    raise ValueError("symbol resolution differs between instantiations")
                parts.extend((raw[previous:start], encoded))
                previous = end
                ends.append(end)
                shifts.append(shifts[-1] + len(encoded) - (end - start))
            parts.append(raw[previous:])
            replacements[path] = b"".join(parts).decode("utf-8")
            translations[path] = ends, shifts

        after = index(replacements.get(source, text), workspace, replacements)

        def shift(path, offset):
            ends, shifts = translations.get(path, ([], [0]))
            return offset + shifts[bisect_right(ends, offset)]

        expected = {(path, declaration_path, shift(path, begin), shift(path, end),
                     shift(declaration_path, declaration), definition)
                    for path, declaration_path, begin, end, declaration, definition in symbols}
        if after is None or expected != set(after):
            raise ValueError("renaming would change name resolution or fail to compile")
        changes = []
        for path, items in grouped.items():
            target_uri, revision, _, _ = file_data(path)
            changes.append({"textDocument": {"uri": target_uri, "version": revision},
                            "edits": [{"range": symbol_range(item), "newText": replacement}
                                      for item in items]})
        return {"documentChanges": changes}

    def handle(self, method, params):
        if method == "initialize":
            capabilities = params.get("capabilities", {})
            completion = capabilities.get("textDocument", {}).get("completion", {})
            self.snippets = completion.get("completionItem", {}).get("snippetSupport", False)
            signature = capabilities.get("textDocument", {}).get("signatureHelp", {})
            self.signature_offsets = signature.get("signatureInformation", {}).get(
                "parameterInformation", {}).get("labelOffsetSupport", False)
            self.inlay_refresh = capabilities.get("workspace", {}).get("inlayHint", {}).get("refreshSupport", False)
            return {"capabilities": {"positionEncoding": "utf-16",
                                     "textDocumentSync": {"openClose": True, "change": 1, "save": True},
                                     "completionProvider": {"triggerCharacters": [".", "/", '"']},
                                     "signatureHelpProvider": {"triggerCharacters": ["(", ","], "retriggerCharacters": [")"]},
                                     "hoverProvider": True, "definitionProvider": True,
                                     "declarationProvider": True, "implementationProvider": True,
                                     "typeDefinitionProvider": True, "inlayHintProvider": True,
                                     "documentFormattingProvider": True, "referencesProvider": True,
                                     "renameProvider": {"prepareProvider": True},
                                     "codeActionProvider": {"codeActionKinds": [
                                         "refactor.rewrite", "source.vaqrin.format"]}}}
        if method == "shutdown":
            self.stop()
        elif method == "textDocument/formatting":
            return self.format_document(params)
        elif method == "textDocument/codeAction":
            return self.code_actions(params)
        elif method == "textDocument/signatureHelp":
            return self.signature_help(params)
        elif method in ("textDocument/references", "textDocument/prepareRename", "textDocument/rename"):
            return self.semantic_symbol(params, method.rsplit("/", 1)[-1])
        elif method in ("textDocument/hover", "textDocument/definition", "textDocument/declaration",
                        "textDocument/implementation", "textDocument/typeDefinition"):
            mode = method.rsplit("/", 1)[-1]
            if mode in ("definition", "declaration"):
                semantic = self.semantic_symbol(params, "definition")
                if semantic:
                    return semantic
            return self.lookup(params, mode)
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
        while (request := read_packet(sys.stdin.buffer)) is not None:
            method = request.get("method")
            if method is None:
                continue
            if method == "exit":
                return 0 if server.stopped else 1
            try:
                params = request.get("params")
                if params is not None and not isinstance(params, dict):
                    raise ValueError("LSP params must be an object")
                params = params or {}
                if method == "textDocument/inlayHint" and "id" in request:
                    server.request_inlays(request["id"], params)
                    continue
                value = server.handle(method, params)
                if "id" in request:
                    server.send(id=request["id"], result=value)
            except (NotImplementedError, OSError, KeyError, TypeError, ValueError, AttributeError) as error:
                if "id" in request:
                    code = -32601 if isinstance(error, NotImplementedError) else -32602
                    server.send(id=request["id"], error={"code": code, "message": str(error)})
    except ValueError as error:
        print(f"vaqrin lsp: {error}", file=sys.stderr)
        return 1
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
