#include "inputs.hpp"
#include <cstdlib>
#include <llvm/ADT/StringExtras.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/Support/JSON.h>
#include <llvm/Support/Program.h>
#include <llvm/Support/SHA256.h>
#include <llvm/Support/raw_ostream.h>

#include <algorithm>
#include <fstream>
#include <iterator>
#include <iomanip>
#include <sstream>
#include <optional>

namespace vqr {
namespace fs = std::filesystem;

namespace {
std::string trim(std::string s) {
  const auto begin = s.find_first_not_of(" \t\r");
  return begin == s.npos ? "" : s.substr(begin, s.find_last_not_of(" \t\r") - begin + 1);
}

struct Dependency {
  fs::path path;
  std::string git, revision, version;
};

struct Manifest {
  std::optional<fs::path> entry;
  std::string registry;
  std::map<std::string, Dependency> dependencies;
};

bool validRevision(std::string_view value) {
  return (value.size() == 40 || value.size() == 64) &&
         std::ranges::all_of(value, [](char ch) { return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'); });
}

std::string takeManifestString(std::string &text, const fs::path &manifest) {
  text = trim(std::move(text));
  const auto invalid = [&] { fail({}, "invalid quoted value in " + manifest.string(), "E301"); };
  if (text.size() < 2 || (text.front() != '"' && text.front() != '\'')) invalid();
  auto quote = text.front();
  std::string decoded;
  std::size_t i = 1;
  for (; i < text.size() && text[i] != quote; ++i) {
    if (quote == '"' && text[i] == '\\') {
      if (++i == text.size() || (text[i] != '"' && text[i] != '\\')) invalid();
    }
    decoded += text[i];
  }
  if (i == text.size() || decoded.empty()) invalid();
  text = trim(text.substr(i + 1));
  return decoded;
}

Manifest readManifest(const fs::path &dir, bool readEntry = false) {
  auto path = dir / "package.toml";
  if (!fs::exists(path)) return {};
  std::ifstream input(path);
  if (!input) fail({}, "cannot read " + path.string(), "E301");
  Manifest manifest;
  std::string line, section;
  while (std::getline(input, line)) {
    line = trim(line);
    if (line.empty() || line[0] == '#') continue;
    if (line[0] == '[') {
      section = trim(line.substr(0, line.find('#')));
      continue;
    }
    const auto eq = line.find('=');
    const auto name = trim(line.substr(0, eq));
    if (section == "[build]" && readEntry && eq != line.npos && name == "entry") {
      auto value = line.substr(eq + 1);
      if (manifest.entry) fail({}, "duplicate [build] entry in " + path.string(), "E301");
      manifest.entry = takeManifestString(value, path);
      if (!value.empty() && value.front() != '#')
        fail({}, "unexpected text after build entry in " + path.string(), "E301");
    }
    if (section == "[registry]" && eq != line.npos && name == "index") {
      if (!manifest.registry.empty()) fail({}, "duplicate registry index in " + path.string(), "E301");
      auto rest = line.substr(eq + 1);
      manifest.registry = takeManifestString(rest, path);
      if (!rest.empty() && rest.front() != '#')
        fail({}, "unexpected text after registry index in " + path.string(), "E301");
    }
    if (section != "[dependencies]") continue;
    const auto asciiLetter = [](char ch) { return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z'); };
    if (eq == line.npos || name.empty() || !(asciiLetter(name.front()) || name.front() == '_') ||
        !std::ranges::all_of(name, [&](char ch) {
          return asciiLetter(ch) || ch == '_' || (ch >= '0' && ch <= '9');
        }) || identifier(name) != name)
      fail({}, "invalid dependency name in " + path.string(), "E301");
    auto value = trim(line.substr(eq + 1));
    auto invalid = [&] { fail({}, "invalid dependency '" + name + "' in " + path.string(), "E301"); };
    if (!value.starts_with('{')) invalid();
    value = trim(value.substr(1));
    std::map<std::string, std::string> fields;
    for (;;) {
      auto field = value.find('=');
      if (field == value.npos) invalid();
      auto fieldName = trim(value.substr(0, field));
      value = value.substr(field + 1);
      auto fieldValue = takeManifestString(value, path);
      if (!fields.emplace(fieldName, std::move(fieldValue)).second) invalid();
      if (value.starts_with('}')) break;
      if (!value.starts_with(',')) invalid();
      value = trim(value.substr(1));
    }
    value = trim(value.substr(1));
    if (!value.empty() && value.front() != '#') invalid();
    Dependency dep;
    if (fields.contains("path")) dep.path = fields["path"];
    if (dep.path.has_root_path()) invalid();
    if (fields.contains("git")) {
      if (fields.size() != (dep.path.empty() ? 2 : 3) || !fields.contains("rev") ||
          !validRevision(fields["rev"])) invalid();
      dep.git = fields["git"];
      dep.revision = fields["rev"];
    } else if (fields.contains("version")) {
      if (fields.size() != 1) invalid();
      dep.version = fields["version"];
    } else if (fields.size() != 1 || dep.path.empty()) {
      invalid();
    }
    if (!manifest.dependencies.emplace(name, std::move(dep)).second)
      fail({}, "duplicate dependency '" + name + "' in " + path.string(), "E301");
  }
  return manifest;
}

std::string sha256(llvm::StringRef input) {
  llvm::SHA256 hash;
  hash.update(input);
  return llvm::toHex(hash.final(), true);
}

std::string digest(const fs::path &file) {
  auto buffer = llvm::MemoryBuffer::getFile(file.string(), /*IsText=*/false);
  if (!buffer) fail({}, "cannot read dependency file " + file.string(), "E301");
  return sha256((*buffer)->getBuffer());
}

bool within(const fs::path &path, const fs::path &directory) {
  auto relative = path.lexically_relative(directory);
  return !relative.empty() && *relative.begin() != "..";
}

fs::path packagesCache(const fs::path &project) {
  for (const char *key : {"VAQRIN_PACKAGE_CACHE", "XDG_CACHE_HOME", "LOCALAPPDATA", "HOME"})
    if (auto *value = std::getenv(key); value && *value && fs::path(value).is_absolute()) {
      if (std::string_view(key) == "VAQRIN_PACKAGE_CACHE") return fs::path(value);
      if (std::string_view(key) == "HOME") return fs::path(value) / ".cache/vaqrin/packages";
      return fs::path(value) / "vaqrin/packages";
    }
  return project / ".vaqrin-cache";
}

struct TempDirectory {
  fs::path path;
  ~TempDirectory() {
    std::error_code ignored;
    if (!path.empty()) fs::remove_all(path, ignored);
  }
};

TempDirectory dependencyStage(const fs::path &root) {
  llvm::SmallString<256> result;
  auto error = llvm::sys::fs::createUniqueDirectory((root / ".fetch").string(), result);
  if (error) fail({}, "cannot create temporary dependency directory: " + error.message(), "E301");
  return {result.str().str()};
}

void dependencyTool(llvm::StringRef program, const std::vector<std::string> &args,
                    const std::optional<fs::path> &output = {}) {
  auto tool = llvm::sys::findProgramByName(program);
  if (!tool) fail({}, "required dependency tool is unavailable: " + program.str(), "E301");
  llvm::SmallVector<llvm::StringRef> command{*tool};
  for (const auto &arg : args) command.push_back(arg);
  const auto destination = output ? output->string() : std::string{};
  std::array<std::optional<llvm::StringRef>, 3> redirects{
      llvm::StringRef(), destination, std::nullopt};
  auto status = llvm::sys::ExecuteAndWait(*tool, command, std::nullopt, redirects, 120);
  if (status != 0)
    fail({}, "dependency command " + program.str() + " failed (" + std::to_string(status) + ")", "E301");
}

void git(const fs::path &checkout, std::initializer_list<std::string> args, const fs::path &out = {}) {
  std::vector<std::string> command{"-C", checkout.string(), "-c", "core.autocrlf=false",
      "-c", "credential.interactive=never", "-c", "protocol.ext.allow=never"};
  command.insert(command.end(), args);
  dependencyTool("git", command, out.empty() ? std::nullopt : std::optional(out));
}

fs::path fetchGit(const fs::path &cache, const fs::path &owner, const Dependency &dep, bool fetch) {
  if (dep.git.empty() || dep.git.starts_with('-') || dep.git.find_first_of("\r\n") != dep.git.npos ||
      !validRevision(dep.revision))
    fail({}, "invalid Git dependency source", "E301");
  const auto key = sha256(dep.git + "\n" + dep.revision);
  auto checkout = cache / key;
  if (!fetch) {
    if (!fs::is_directory(checkout))
      fail({}, "Git dependency is missing from the offline cache. Run vaqc lock to fetch it", "E301");
    checkout = fs::canonical(checkout);
    if (!within(checkout, fs::canonical(cache)))
      fail({}, "Git dependency checkout escapes the cache", "E301");
    return checkout;
  }
  fs::create_directories(cache);
  auto stage = dependencyStage(cache);
  if (!fs::is_directory(checkout)) {
    auto repo = stage.path / "repo";
    fs::create_directory(repo);
    auto origin = dep.git;
    if (origin.find(':') == origin.npos && !origin.starts_with('/'))
      origin = fs::weakly_canonical(owner / origin).string();
    if (dep.revision.size() == 64) git(repo, {"init", "--quiet", "--object-format=sha256"});
    else git(repo, {"init", "--quiet"});
    git(repo, {"fetch", "--quiet", "--no-tags", "--depth=1", "--", origin, dep.revision});
    git(repo, {"checkout", "--quiet", "--detach", dep.revision});
    std::error_code error;
    fs::rename(repo, checkout, error);
    if (error && !fs::is_directory(checkout))
      fail({}, "cannot publish Git dependency cache: " + error.message(), "E301");
  }
  if (!within(fs::canonical(checkout), fs::canonical(cache)))
    fail({}, "Git dependency checkout escapes the cache", "E301");
  auto listing = stage.path / "git-status";
  git(checkout, {"status", "--porcelain", "--untracked-files=all", "--ignored"}, listing);
  if (fs::file_size(listing) != 0) fail({}, "Git dependency checkout has local changes: " + checkout.string(), "E301");
  git(checkout, {"rev-parse", "HEAD"}, listing);
  std::ifstream head(listing);
  std::string revision;
  head >> revision;
  if (revision != dep.revision) fail({}, "Git dependency checkout has the wrong revision", "E301");
  return fs::canonical(checkout);
}

using RegistryKey = std::tuple<std::string, std::string, std::string>;

std::map<RegistryKey, Dependency> pinnedVersions(llvm::StringRef contents) {
  std::map<RegistryKey, Dependency> result;
  std::istringstream input(contents.str());
  std::string line;
  while (std::getline(input, line)) {
    if (!line.starts_with("registry ")) continue;
    std::istringstream record(line.substr(9));
    std::string owner, name, version, path;
    Dependency dep;
    if (!(record >> std::quoted(owner) >> std::quoted(name) >> std::quoted(version) >>
          std::quoted(dep.git) >> std::quoted(dep.revision) >> std::quoted(path)))
      fail({}, "invalid registry record in package.lock", "E301");
    dep.path = path;
    if (!result.emplace(RegistryKey{owner, name, version}, std::move(dep)).second)
      fail({}, "duplicate registry record in package.lock", "E301");
  }
  return result;
}

llvm::json::Value registryIndex(const fs::path &project, const fs::path &cache, llvm::StringRef location) {
  if (location.empty()) fail({}, "version dependencies require a [registry] index", "E301");
  std::unique_ptr<llvm::MemoryBuffer> bytes;
  if (location.starts_with("https://")) {
    fs::create_directories(cache);
    auto stage = dependencyStage(cache);
    auto index = stage.path / "index.json";
    dependencyTool("curl", {"--fail", "--silent", "--show-error", "--location", "--proto", "=https",
                            "--proto-redir", "=https", "--max-filesize", "8388608", "--output",
                            index.string(), "--", location.str()});
    auto file = llvm::MemoryBuffer::getFile(index.string());
    if (file) bytes = std::move(*file);
  } else {
    const auto index = project / location.str();
    auto file = llvm::MemoryBuffer::getFile(index.string());
    if (file) bytes = std::move(*file);
  }
  if (!bytes) fail({}, "cannot read registry index", "E301");
  auto parsed = llvm::json::parse(bytes->getBuffer());
  if (!parsed) fail({}, llvm::toString(parsed.takeError()), "E301");
  return std::move(*parsed);
}

Dependency lookupVersion(const llvm::json::Value &index, llvm::StringRef name, llvm::StringRef version) {
  auto *packages = index.getAsObject();
  auto *versions = packages ? packages->getObject(name) : nullptr;
  auto *release = versions ? versions->getObject(version) : nullptr;
  if (!release) fail({}, "registry has no matching version of " + name.str() + " " + version.str(), "E301");
  auto git = release->getString("git");
  auto rev = release->getString("rev");
  auto path = release->getString("path");
  if (!git || !rev || !validRevision(*rev))
    fail({}, "registry release is missing its pinned Git source", "E301");
  return {path ? fs::path(path->str()) : fs::path{}, git->str(), rev->str(), {}};
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

Inputs::Inputs(const Path &executable, Overlays overlays)
    : cache(executable), overlays(std::move(overlays)) {
  const char *sources = std::getenv("VAQRIN_STDLIB_DIR");
  const char *runtime = std::getenv("VAQRIN_RUNTIME_DIR");
  if (sources && runtime && fs::is_directory(sources) && fs::is_directory(runtime)) {
    standardRoot = fs::canonical(sources);
    runtimeRoot = fs::canonical(runtime);
    return;
  }
  for (auto dir : {executable.parent_path(), executable.parent_path().parent_path()}) {
    auto candidate = dir / "share/vaqrin/stdlib";
    directoryProbes.insert(candidate);
    if (fs::is_directory(candidate)) {
      standardRoot = fs::canonical(candidate);
      runtimeRoot = candidate.parent_path() / "runtime";
      break;
    }
  }
}

std::set<Inputs::Path> Inputs::dependencyFiles() const {
  auto result = fileProbes;
  for (const auto &[path, _] : outlines) result.insert(path);
  if (!standardRoot.empty()) {
    result.insert(taskRuntimeSource());
    result.insert(ioRuntimeSource());
    result.insert(processRuntimeSource());
    result.insert(netRuntimeSource());
    result.insert(http2RuntimeSource());
    result.insert(gzipRuntimeSource());
    result.insert(tlsRuntimeSource());
    result.insert(decimalRuntimeSource());
    result.insert(runtimeRoot / "reactor_linux.h");
  }
  return result;
}

std::set<Inputs::Path> Inputs::dependencyDirectories() const {
  auto result = directoryProbes;
  for (const auto &[path, _] : directories) result.insert(path);
  return result;
}

void Inputs::resolveDependencies(bool writeLock) {
  const auto lock = project / "package.lock";
  std::ostringstream snapshot;
  std::set<Path> visiting, visited;
  auto cachedLock = llvm::MemoryBuffer::getFile(lock.string(), /*IsText=*/false);
  auto selections = cachedLock && !writeLock ? pinnedVersions((*cachedLock)->getBuffer())
                                              : std::map<RegistryKey, Dependency>{};
  const auto cache = packagesCache(project);
  std::optional<llvm::json::Value> index;
  const auto indexLocation = readManifest(project).registry;
  bool remote = false, registry = false;
  auto walk = [&](auto &&visit, const Path &owner) -> void {
    if (visiting.contains(owner)) fail({}, "cyclic path dependency at " + owner.string(), "E301");
    if (!visited.insert(owner).second) return;
    visiting.insert(owner);
    fileProbes.insert(owner / "package.toml");
    for (const auto &[name, specification] : readManifest(owner).dependencies) {
      auto dep = specification;
      auto ownerID = dependencyIdentity(owner);
      if (!dep.version.empty()) {
        registry = true;
        const auto key = RegistryKey{ownerID, name, dep.version};
        if (writeLock) {
          if (!index) index = registryIndex(project, cache, indexLocation);
          dep = lookupVersion(*index, name, specification.version);
        } else {
          auto pinned = selections.find(key);
          if (pinned == selections.end())
            fail({}, "version dependency is missing from package.lock. Run vaqc lock", "E301");
          dep = pinned->second;
        }
        snapshot << "registry " << std::quoted(ownerID) << ' ' << std::quoted(name) << ' '
                 << std::quoted(specification.version) << ' ' << std::quoted(dep.git) << ' '
                 << std::quoted(dep.revision) << ' ' << std::quoted(dep.path.generic_string()) << '\n';
      }
      if (dep.path.has_root_path()) fail({}, "dependency path must be relative", "E301");
      Path target;
      if (dep.git.empty()) {
        target = fs::weakly_canonical(owner / dep.path);
        for (const auto &[root, _] : remoteDependencies)
          if (within(owner, root) && !within(target, root))
            fail({}, "a Git dependency's path dependency escapes its checkout", "E301");
        snapshot << "dependency " << std::quoted(ownerID) << ' ' << std::quoted(name) << ' '
                 << std::quoted(dep.path.generic_string()) << '\n';
      } else {
        remote = true;
        auto checkout = fetchGit(cache, specification.version.empty() ? owner : project, dep, writeLock);
        remoteDependencies.emplace(checkout, "git/" + checkout.filename().generic_string());
        target = fs::weakly_canonical(checkout / dep.path);
        if (!within(target, checkout)) fail({}, "Git dependency path escapes its checkout", "E301");
        snapshot << "git " << std::quoted(ownerID) << ' ' << std::quoted(name) << ' '
                 << std::quoted(dep.git) << ' ' << std::quoted(dep.revision) << ' '
                 << std::quoted(dep.path.generic_string()) << '\n';
      }
      if (!fs::is_directory(target))
        fail({}, "missing dependency '" + name + "' at " + target.string(), "E301");
      if (!standardRoot.empty() && fs::is_directory(standardRoot / name))
        fail({}, "dependency '" + name + "' conflicts with a standard package", "E301");
      auto [record, inserted] = pathDependencies.emplace(name, target);
      if (!inserted && record->second != target)
        fail({}, "conflicting paths for dependency '" + name + "'", "E301");
      visit(visit, target);
    }
    if (owner != project) {
      directoryProbes.insert(owner);
      if (siblings(owner).empty())
        fail({}, "path dependency has no .vqr sources at " + owner.string(), "E301");
      std::set<Path> sources;
      for (fs::recursive_directory_iterator it(owner), end; it != end; ++it) {
        if (it->is_directory()) {
          if (ignored(it->path())) it.disable_recursion_pending();
          else directoryProbes.insert(it->path());
        }
        if (it->path().extension() != ".vqr" || !it->is_regular_file()) continue;
        auto source = fs::canonical(it->path());
        if (source.parent_path() == it->path().parent_path()) sources.insert(source);
      }
      for (const auto &file : sources) {
        if (!lockedSources.insert(file).second) continue;
        fileProbes.insert(file);
        snapshot << "source " << std::quoted(dependencyIdentity(file)) << ' '
                 << digest(file) << '\n';
      }
    }
    visiting.erase(owner);
  };
  walk(walk, project);
  fileProbes.insert(lock);
  std::ostringstream final;
  final << (remote ? "vaqrin-package-lock-v2\n" : "vaqrin-path-lock-v1\n");
  if (registry) final << "registry-index " << std::quoted(indexLocation) << '\n';
  final << snapshot.str();
  if (writeLock) {
    auto file = llvm::sys::fs::TempFile::create((project / ".package.lock-%%%%%%").string());
    if (!file) fail({}, llvm::toString(file.takeError()), "E301");
    llvm::raw_fd_ostream output(file->FD, false);
    output << final.str();
    output.flush();
    if (output.has_error()) {
      output.clear_error();
      llvm::consumeError(file->discard());
      fail({}, "cannot write " + lock.string(), "E301");
    }
    if (auto error = file->keep(lock.string()))
      fail({}, llvm::toString(std::move(error)), "E301");
  } else if (!pathDependencies.empty()) {
    if (!cachedLock || (*cachedLock)->getBuffer() != final.str())
      fail({}, "missing or stale " + lock.string() + "; run 'vaqc lock " + project.string() + "'", "E301");
  }
}

std::string Inputs::dependencyIdentity(const Path &path) const {
  for (const auto &[root, id] : remoteDependencies)
    if (within(path, root))
      return (fs::path(id) / path.lexically_relative(root)).lexically_normal().generic_string();
  return path.lexically_relative(project).generic_string();
}

void Inputs::writeLock(const Path &directory) {
  if (!fs::is_directory(directory) || !fs::is_regular_file(directory / "package.toml"))
    fail({}, "vaqc lock expects a directory with package.toml", "E301");
  project = fs::canonical(directory);
  resolveDependencies(true);
}

Inputs::Outline &Inputs::outline(const Path &path, Span span) {
  auto [it, inserted] = outlines.try_emplace(path);
  auto &out = it->second;
  if (!inserted) return out;
  const auto replacement = overlays.find(path);
  const auto &contents = replacement != overlays.end() ? replacement->second : path;
  if (path.extension() != ".vqr" || !fs::is_regular_file(contents))
    fail(span, "expected an existing .vqr file: " + path.string(), "E301");
  std::ifstream input(contents, std::ios::binary);
  if (!input) fail(span, "cannot read " + path.string(), "E301");
  out.source = {path.string(), std::string(std::istreambuf_iterator<char>{input}, {})};
  out.stem = identifier(path.stem().string());
  // Standard modules have no hosted entry. A cached parse tree also
  // supplies their declaration index without lexing the source again.
  if (replacement == overlays.end() && !standardRoot.empty() &&
      path.parent_path().parent_path() == standardRoot) {
    out.cacheEntry = cache.entry(out.source);
    out.cached = cache.load(out.cacheEntry, out.source);
    if (out.cached) {
      out.package = out.cached->package;
      for (const auto &declaration : out.cached->declarations) {
        out.names.insert(declaration.name);
        out.exported |= declaration.exported;
      }
      return out;
    }
  }
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
    // A return type can include *const T or []const T outside parameter parentheses.
    if (t.text == "const" && i && (token(i - 1) == "*" || token(i - 1) == "]")) continue;
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
  // Large generated modules dominate discovery. Reuse complete token
  // streams during parsing and let the full lexer report any earlier error.
  if (out.source.bytes.size() >= 32768 && tokens.back().span.begin == out.source.bytes.size())
    out.tokens = std::move(tokens);
  return out;
}

const std::vector<fs::path> &Inputs::siblings(const Path &dir) {
  auto [it, inserted] = directories.try_emplace(dir);
  if (inserted) {
    std::set<Path> unique;
    for (const auto &[path, _] : overlays)
      if (path.parent_path() == dir) unique.insert(path);
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
  const bool cached = record.cached.has_value();
  auto &f = files.emplace_back(cached ? std::move(*record.cached) : parse(record.source, std::move(record.tokens)));
  record.cached.reset();
  if (!cached && record.mainCount == 0 && !f.package.empty()) cache.store(record.cacheEntry, record.source, f);
  deriveSerde(f);
  record.file = &f;
  f.anonymous = f.package.empty();
  f.standard = !standardRoot.empty() && path.parent_path().parent_path() == standardRoot;
  f.identity = f.standard ? path.lexically_relative(standardRoot).generic_string()
                          : dependencyIdentity(path);
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
      f.module = dependencyIdentity(path.parent_path()) + "::" + f.package;
  }
  auto [it, fresh] = identities.emplace(f.module + ":" + f.identity, path);
  if (!fresh && it->second != path) fail(span, "conflicting file identities", "E301");
  importing.insert(path);
  std::set<std::string> aliases;
  for (auto &item : f.imports) {
    if (!aliases.insert(item.alias).second)
      fail(item.span, "duplicate import alias '" + item.alias + "'");
    if (record.names.contains(item.alias))
      fail(item.span, "conflicting import alias '" + item.alias + "'");
    item.path = resolveImport(path.parent_path(), item);
  }
  importing.erase(path);
  return f;
}

void Inputs::loadStandard(std::string package, Span span) {
  resolveImport({}, Import{false, "std/" + package, package, span});
}

bool Inputs::lockedDirectory(const Path &directory) const {
  return std::ranges::any_of(pathDependencies, [&](const auto &entry) {
    auto relative = directory.lexically_relative(entry.second);
    return !relative.empty() && *relative.begin() != "..";
  });
}

std::string Inputs::resolveImport(const Path &dir, const Import &item) {
  if (item.filePath) {
    fileProbes.insert(dir / item.path);
    auto path = fs::weakly_canonical(dir / item.path);
    if (lockedDirectory(dir) && !lockedSources.contains(path))
      fail(item.span, "file import in a path dependency must resolve to a locked source", "E301");
    if (!outline(path, item.span).package.empty())
      fail(item.span, "named packages require a logical import", "E301");
    return activate(path, item.span).module;
  }
  auto logical = item.path.starts_with("std/") ? item.path.substr(4) : item.path;
  auto leaf = fs::path(logical).filename().string();
  Path directory;
  if (!standardRoot.empty()) directoryProbes.insert(standardRoot / logical);
  if (!standardRoot.empty() && logical != "collections" && fs::is_directory(standardRoot / logical))
    directory = standardRoot / logical;
  if (directory.empty() && !item.path.starts_with("std/")) {
    if (auto dependency = pathDependencies.find(logical); dependency != pathDependencies.end())
      directory = dependency->second;
  }
  if (directory.empty() && !item.path.starts_with("std/")) {
    for (const auto &root : roots) {
      directoryProbes.insert(root / logical);
      if (fs::is_directory(root / logical)) {
        directory = fs::canonical(root / logical);
        break;
      }
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
      if (lockedDirectory(dir) && (standardRoot.empty() || directory.parent_path() != standardRoot) &&
          !lockedSources.contains(path))
        fail(item.span, "package import in a path dependency must resolve to a locked source", "E301");
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

const Inputs::DirectoryNames &Inputs::namesIn(const Path &directory, Span span) {
  auto [entry, inserted] = directoryNames.try_emplace(directory);
  if (inserted) {
    for (const auto &path : siblings(directory)) {
      const auto &symbols = outline(path, span);
      for (const auto &name : symbols.names)
        entry->second[{symbols.package, name}].push_back(path);
    }
  }
  return entry->second;
}

std::string Inputs::scope(const File &file, std::string_view name, Span span) {
  if (name == "main") return file.module;
  Path current(file.span.source->path);
  auto &self = outlines.at(current);
  if (file.anonymous && self.names.contains(name)) return file.module;
  const auto &names = namesIn(current.parent_path(), span);
  auto candidates = names.find({self.package, name});
  if (candidates == names.end()) return file.module;
  if (file.anonymous && candidates->second.size() > 1)
    fail(span, pathsMessage("ambiguous declaration '" + std::string(name) + "'; qualify with a file alias",
                            candidates->second));
  std::string scope = file.module;
  for (const auto &p : candidates->second)
    if (p != current) scope = activate(p, span).module;
  return scope;
}

void Inputs::load(const std::vector<Path> &inputs, const std::vector<Path> &packagePaths, const Path &entry,
                  bool hostedExecutable) {
  bool directory = fs::is_directory(inputs.front());
  if (directory && inputs.size() != 1) fail({}, "use a directory or an explicit list of files", "E301");
  project = fs::weakly_canonical(fs::absolute(inputs.front()));
  if (!directory) {
    project = project.parent_path();
    for (auto ancestor = project; ancestor != ancestor.root_path(); ancestor = ancestor.parent_path()) {
      fileProbes.insert(ancestor / "package.toml");
      if (fs::is_regular_file(ancestor / "package.toml")) {
        project = ancestor;
        break;
      }
    }
  }
  resolveDependencies(false);
  roots = {project, project.parent_path()};
  if (!directory)
    for (const auto &input : inputs)
      roots.push_back(fs::weakly_canonical(fs::absolute(input)).parent_path());
  for (auto &p : packagePaths)
    roots.push_back(fs::weakly_canonical(p));
  std::set<Path> paths;
  std::optional<Path> selected;
  std::optional<std::string> entryPackage;
  if (directory) {
    auto config = entry.empty() && hostedExecutable ? readManifest(project, true).entry : std::optional<Path>{};
    auto relative = entry.empty() ? config.value_or(Path{}) : entry;
    auto selector = relative.string();
    if (auto separator = selector.find("::"); separator != std::string::npos) {
      auto name = selector.substr(0, separator);
      if (name.empty() || selector.substr(separator) != "::main" || identifier(name) != name)
        fail({}, "entry must use the form package::main", "E301");
      entryPackage = std::move(name);
    }
    if (!relative.empty() && !entryPackage) {
      auto path = fs::weakly_canonical(project / relative);
      auto within = path.lexically_relative(project);
      if (relative.is_absolute() || within.empty() || *within.begin() == "..")
        fail({}, "entry must be relative to and inside the project root", "E301");
      selected = path;
      paths.insert(path);
    } else {
      for (fs::recursive_directory_iterator it(project), end; it != end; ++it) {
        if (it->is_directory() &&
            (ignored(it->path()) || std::ranges::any_of(pathDependencies, [&](const auto &dependency) {
              return dependency.second == it->path();
            }))) it.disable_recursion_pending();
        if (it->path().extension() == ".vqr" && it->is_regular_file())
          paths.insert(fs::canonical(it->path()));
      }
    }
  } else {
    for (auto &p : inputs)
      paths.insert(fs::weakly_canonical(fs::absolute(p)));
    if (!entry.empty()) {
      selected = fs::weakly_canonical(entry);
      if (!paths.contains(*selected)) fail({}, "--entry must name an explicitly listed source", "E301");
    }
  }
  if (paths.empty()) fail({}, "no .vqr source files found", "E301");
  if (!selected && (hostedExecutable || entryPackage)) {
    std::vector<Path> candidates, malformed;
    for (auto &path : paths) {
      auto &o = outline(path, {});
      if (entryPackage && o.package != *entryPackage) continue;
      if (o.mainCount == 1 && o.entrySignature)
        candidates.push_back(path);
      else if (o.mainCount)
        malformed.push_back(path);
    }
    if (candidates.empty()) candidates.swap(malformed);
    if (candidates.size() != 1)
      fail({},
           pathsMessage(entryPackage ? (candidates.empty() ? "no main in package " : "multiple main files in package ") +
                                           *entryPackage
                                     : candidates.empty() ? "no main in the selected sources"
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
