#include "inputs.hpp"

#include <algorithm>
#include <fstream>
#include <iterator>
#include <optional>

namespace vqr {
namespace fs = std::filesystem;

namespace {
std::string trim(std::string s) {
  const auto begin = s.find_first_not_of(" \t\r");
  return begin == s.npos ? "" : s.substr(begin, s.find_last_not_of(" \t\r") - begin + 1);
}

std::optional<fs::path> manifestEntry(const fs::path &dir) {
  auto path = dir / "package.toml";
  if (!fs::exists(path)) return {};
  std::ifstream input(path);
  if (!input) fail({}, "cannot read " + path.string(), "E301");
  std::string line;
  bool build = false;
  std::optional<fs::path> entry;
  while (std::getline(input, line)) {
    line = trim(line);
    if (line.empty() || line[0] == '#') continue;
    if (line[0] == '[') {
      build = trim(line.substr(0, line.find('#'))) == "[build]";
      continue;
    }
    auto eq = line.find('=');
    if (!build || eq == line.npos || trim(line.substr(0, eq)) != "entry") continue;
    auto value = trim(line.substr(eq + 1));
    if (entry || value.size() < 2 || (value.front() != '"' && value.front() != '\''))
      fail({}, "invalid or duplicate [build] entry in " + path.string(), "E301");
    auto quote = value[0];
    std::string decoded;
    std::size_t i = 1;
    for (; i < value.size() && value[i] != quote; ++i) {
      if (quote == '"' && value[i] == '\\') {
        ++i;
        if (i == value.size() || (value[i] != '"' && value[i] != '\\'))
          fail({}, "invalid escape in build entry", "E301");
      }
      decoded += value[i];
    }
    if (i == value.size()) fail({}, "unterminated build entry", "E301");
    auto trailing = trim(value.substr(i + 1));
    if (!trailing.empty() && trailing[0] != '#') fail({}, "unexpected text after build entry", "E301");
    entry = decoded;
  }
  return entry;
}

bool ignored(const fs::path &path) {
  const auto name = path.filename().string();
  return name.starts_with(".") || name == "build" || name.starts_with("cmake-build-") || name == "target" ||
         name == "vendor" || name == "node_modules" || name == "tmp";
}

std::string pathsMessage(std::string message, const auto &paths) {
  for (const auto &path : paths)
    message += "\n  " + path.string();
  return message;
}
} // namespace

Inputs::Inputs(const Path &executable) {
  for (auto dir : {executable.parent_path(), executable.parent_path().parent_path()}) {
    auto candidate = dir / "share/vaqrin/stdlib";
    if (fs::is_directory(candidate)) {
      standardRoot = fs::canonical(candidate);
      break;
    }
  }
}

Inputs::Outline &Inputs::outline(const Path &path, Span span) {
  auto [it, inserted] = outlines.try_emplace(path);
  auto &out = it->second;
  if (!inserted) return out;
  if (path.extension() != ".vqr" || !fs::is_regular_file(path))
    fail(span, "expected an existing .vqr file: " + path.string(), "E301");
  std::ifstream input(path, std::ios::binary);
  if (!input) fail(span, "cannot read " + path.string(), "E301");
  out.source = {path.string(), std::string(std::istreambuf_iterator<char>{input}, {})};
  out.stem = identifier(path.stem().string());
  auto tokens = lex(out.source, true);
  if (tokens.front().text == "package" && tokens.size() > 1) out.package = tokens[1].text;
  auto token = [&](std::size_t i) -> std::string_view {
    return i < tokens.size() ? std::string_view(tokens[i].text) : std::string_view{};
  };
  unsigned depth = 0;
  for (std::size_t i = 0; i + 1 < tokens.size(); ++i) {
    auto &t = tokens[i];
    if (t.kind == TokenKind::Punctuation) {
      if (t.text == "{" || t.text == "(" || t.text == "[") ++depth;
      if (depth && (t.text == "}" || t.text == ")" || t.text == "]")) --depth;
    }
    if (depth || t.kind != TokenKind::Keyword) continue;
    if (t.text == "export") out.exported = true;
    if (tokens[i + 1].kind != TokenKind::Name) continue;
    if (t.text == "func" || t.text == "type" || t.text == "class" || t.text == "interface" ||
        t.text == "error" || t.text == "enum" || t.text == "var" || t.text == "const") {
      const auto &name = tokens[i + 1].text;
      out.names.insert(name);
      if (t.text == "func" && name == "main") {
        ++out.mainCount;
        auto result = i + (token(i + 4) == "!" ? 5 : 4);
        auto qualifier = i ? token(i - 1) : "";
        out.entrySignature = token(i + 2) == "(" && token(i + 3) == ")" &&
                             (token(result) == "i32" || token(result) == "void") &&
                             token(result + 1) == "{" && qualifier != "pub" && qualifier != "unsafe" &&
                             qualifier != "comptime" && qualifier != "C";
      }
    }
  }
  return out;
}

const std::vector<fs::path> &Inputs::siblings(const Path &dir) {
  auto [it, inserted] = directories.try_emplace(dir);
  if (inserted) {
    std::set<Path> unique;
    for (const auto &p : fs::directory_iterator(dir))
      if (p.path().extension() == ".vqr" && p.is_regular_file()) {
        auto canonical = fs::canonical(p);
        if (canonical.parent_path() == dir) unique.insert(canonical);
      }
    it->second.assign(unique.begin(), unique.end());
  }
  return it->second;
}

File &Inputs::activate(const Path &path, Span span) {
  auto &record = outline(path, span);
  if (importing.contains(path)) fail(span, "cyclic import of " + path.string(), "E301");
  if (record.file) return *record.file;
  auto &f = files.emplace_back(parse(record.source));
  record.file = &f;
  f.anonymous = f.package.empty();
  f.standard = !standardRoot.empty() && path.parent_path().parent_path() == standardRoot;
  f.identity = (f.standard ? path.lexically_relative(standardRoot) : path.lexically_relative(project))
                   .generic_string();
  if (f.anonymous) {
    f.module = "@" + f.identity;
    if (!record.stem.empty()) {
      auto [it, fresh] = normalizedStems.emplace(std::pair{path.parent_path(), record.stem}, path);
      if (!fresh && it->second != path)
        fail(f.span,
             pathsMessage("anonymous file names have the same normalized identifier",
                          std::vector{it->second, path}),
             "E301");
    }
  } else {
    f.module = f.package;
    if (!f.standard && path.parent_path() != project)
      f.module = path.parent_path().lexically_relative(project).generic_string() + "::" + f.package;
  }
  auto [it, fresh] = identities.emplace(f.module + ":" + f.identity, path);
  if (!fresh && it->second != path) fail(span, "conflicting file identities", "E301");
  importing.insert(path);
  std::set<std::string> aliases;
  for (auto &item : f.imports) {
    if (!aliases.insert(item.alias).second || record.names.contains(item.alias))
      fail(item.span, "conflicting import alias '" + item.alias + "'");
    item.path = resolveImport(path.parent_path(), item);
  }
  importing.erase(path);
  return f;
}

std::string Inputs::resolveImport(const Path &dir, const Import &item) {
  if (item.filePath) {
    auto path = fs::weakly_canonical(dir / item.path);
    if (!outline(path, item.span).package.empty())
      fail(item.span, "named packages require a logical import", "E301");
    return activate(path, item.span).module;
  }
  auto logical = item.path.starts_with("std/") ? item.path.substr(4) : item.path;
  auto leaf = fs::path(logical).filename().string();
  Path directory;
  if (!standardRoot.empty() && logical != "collections" && fs::is_directory(standardRoot / logical))
    directory = standardRoot / logical;
  if (directory.empty() && !item.path.starts_with("std/")) {
    for (const auto &root : roots)
      if (fs::is_directory(root / logical)) {
        directory = fs::canonical(root / logical);
        break;
      }
  }
  if (directory.empty()) {
    if (logical == "task" || logical == "channel" || logical == "net")
      fail(item.span, "target runtime for standard package '" + logical + "' is unavailable", "E290");
    fail(item.span, "cannot locate package '" + item.path + "'", "E301");
  }
  std::string module;
  for (const auto &path : siblings(directory))
    if (outline(path, item.span).package == leaf) {
      module = activate(path, item.span).module;
    }
  if (module.empty()) fail(item.span, "no source declares package '" + leaf + "'", "E301");
  return module;
}

bool Inputs::directoryVisible(const File &a, const File &b) const {
  return a.anonymous && b.anonymous &&
         Path(a.span.source->path).parent_path() == Path(b.span.source->path).parent_path();
}

std::string Inputs::qualifier(const File &file, std::string_view alias, Span span) {
  for (const auto &item : file.imports)
    if (item.alias == alias) return item.path;
  if (!file.anonymous) return {};
  std::vector<Path> candidates;
  for (const auto &path : siblings(Path(file.span.source->path).parent_path())) {
    auto &o = outline(path, span);
    if (o.package.empty() && o.stem == alias && o.file != &file) candidates.push_back(path);
  }
  if (candidates.size() > 1)
    fail(span, pathsMessage("ambiguous file qualifier '" + std::string(alias) + "'", candidates));
  if (candidates.empty()) return {};
  return activate(candidates.front(), span).module;
}

std::string Inputs::scope(const File &file, std::string_view name, Span span) {
  if (name == "main") return file.module;
  Path current(file.span.source->path);
  auto &self = outlines.at(current);
  if (file.anonymous && self.names.contains(name)) return file.module;
  std::vector<Path> candidates;
  for (const auto &path : siblings(current.parent_path())) {
    auto &o = outline(path, span);
    if (path != current && o.package == self.package && o.names.contains(name)) candidates.push_back(path);
  }
  if (file.anonymous && candidates.size() > 1)
    fail(span, pathsMessage("ambiguous declaration '" + std::string(name) + "'; qualify with a file alias",
                            candidates));
  std::string scope = file.module;
  for (const auto &p : candidates)
    scope = activate(p, span).module;
  return scope;
}

void Inputs::load(const std::vector<Path> &inputs, const std::vector<Path> &packagePaths, const Path &entry,
                  bool hostedExecutable) {
  bool directory = fs::is_directory(inputs.front());
  if (directory && inputs.size() != 1) fail({}, "use a directory or an explicit list of files", "E301");
  project = fs::weakly_canonical(inputs.front());
  if (!directory) {
    project = project.parent_path();
    for (auto ancestor = project; ancestor != ancestor.root_path(); ancestor = ancestor.parent_path())
      if (fs::is_regular_file(ancestor / "package.toml")) {
        project = ancestor;
        break;
      }
  }
  roots = {project, project.parent_path()};
  if (!directory)
    for (const auto &input : inputs)
      roots.push_back(fs::weakly_canonical(input).parent_path());
  for (auto &p : packagePaths)
    roots.push_back(fs::weakly_canonical(p));
  std::set<Path> paths;
  std::optional<Path> selected;
  if (directory) {
    auto config = entry.empty() && hostedExecutable ? manifestEntry(project) : std::optional<Path>{};
    if (!entry.empty() || config) {
      auto relative = entry.empty() ? *config : entry;
      auto path = fs::weakly_canonical(project / relative);
      auto within = path.lexically_relative(project);
      if (relative.is_absolute() || within.empty() || *within.begin() == "..")
        fail({}, "entry must be relative to and inside the project root", "E301");
      selected = path;
      paths.insert(path);
    } else {
      for (fs::recursive_directory_iterator it(project), end; it != end; ++it) {
        if (it->is_directory() && ignored(it->path())) it.disable_recursion_pending();
        if (it->path().extension() == ".vqr" && it->is_regular_file())
          paths.insert(fs::canonical(it->path()));
      }
    }
  } else {
    for (auto &p : inputs)
      paths.insert(fs::weakly_canonical(p));
    if (!entry.empty()) {
      selected = fs::weakly_canonical(entry);
      if (!paths.contains(*selected)) fail({}, "--entry must name an explicitly listed source", "E301");
    }
  }
  if (paths.empty()) fail({}, "no .vqr source files found", "E301");
  if (!selected && hostedExecutable) {
    std::vector<Path> candidates, malformed;
    for (auto &path : paths) {
      auto &o = outline(path, {});
      if (o.mainCount == 1 && o.entrySignature)
        candidates.push_back(path);
      else if (o.mainCount)
        malformed.push_back(path);
    }
    if (candidates.empty()) candidates.swap(malformed);
    if (candidates.size() != 1)
      fail({},
           pathsMessage(candidates.empty() ? "no main in the selected sources"
                                           : "multiple entry files; specify --entry",
                        candidates),
           "E301");
    selected = candidates.front();
  }
  if (selected) {
    auto &f = activate(*selected, {});
    f.selected = f.explicitInput = true;
    if (!outline(*selected, {}).mainCount) fail(f.span, "selected file has no main", "E301");
    rootName = f.anonymous ? selected->stem().string() : f.package;
  }
  for (const auto &p : paths) {
    if (directory && selected && p != *selected) continue;
    activate(p, {}).explicitInput = true;
  }
  if (rootName.empty())
    rootName = files.front().anonymous ? paths.begin()->stem().string() : files.front().package;
  // Foreign exports in the selected module can be referenced by native objects.
  for (std::size_t i = 0; i < files.size(); ++i) {
    auto &f = files[i];
    if (f.anonymous) continue;
    auto current = Path(f.span.source->path);
    for (auto &p : siblings(current.parent_path())) {
      auto &o = outline(p, {});
      if (o.exported && o.package == outlines.at(current).package) activate(p, {});
    }
  }
}
} // namespace vqr
