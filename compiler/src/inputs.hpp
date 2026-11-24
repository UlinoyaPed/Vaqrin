#pragma once

#include "syntax_cache.hpp"

#include <deque>
#include <filesystem>
#include <map>
#include <set>

namespace vqr {
class Inputs {
  using Path = std::filesystem::path;

public:
  using Overlays = std::map<Path, Path>;
  explicit Inputs(const Path &executable, Overlays overlays = {});
  const SyntaxCache &standardCache() const { return cache; }
  std::set<Path> dependencyFiles() const;
  std::set<Path> dependencyDirectories() const;
  void loadStandard(std::string package, Span span);
  void writeLock(const Path &directory);
  void load(const std::vector<Path> &inputs, const std::vector<Path> &packagePaths, const Path &entry,
            bool hostedExecutable);
  std::string qualifier(const File &file, std::string_view alias) const;
  std::string scope(const File &file, std::string_view symbol, Span span);
  Path taskRuntimeSource() const { return runtimeRoot / "tasks.c"; }
  Path ioRuntimeSource() const { return runtimeRoot / "io_posix.cpp"; }
  Path processRuntimeSource() const { return runtimeRoot / "process_posix.cpp"; }
  Path netRuntimeSource() const { return runtimeRoot / "net_posix.cpp"; }
  Path http2RuntimeSource() const { return runtimeRoot / "http2_nghttp2.cpp"; }
  Path gzipRuntimeSource() const { return runtimeRoot / "gzip_zlib.cpp"; }
  Path tlsRuntimeSource() const { return runtimeRoot / "tls_openssl.cpp"; }
  Path decimalRuntimeSource() const { return runtimeRoot / "decimal.cpp"; }

  std::deque<File> files;
  std::string rootName;

private:
  struct Outline {
    Source source;
    std::string package;
    std::set<std::string, std::less<>> names;
    unsigned mainCount = 0;
    bool entrySignature = false, exported = false;
    std::vector<Token> tokens;
    Path cacheEntry;
    std::optional<File> cached;
    File *file = nullptr;
  };
  Path project, standardRoot, runtimeRoot;
  SyntaxCache cache;
  Overlays overlays;
  std::set<Path> fileProbes, directoryProbes;
  std::vector<Path> roots;
  std::map<std::string, Path, std::less<>> pathDependencies;
  std::set<Path> lockedSources;
  std::map<Path, std::string> remoteDependencies;
  std::string dependencyIdentity(const Path &path) const;
  void resolveDependencies(bool writeLock);
  bool lockedDirectory(const Path &directory) const;
  std::map<Path, Outline> outlines;
  std::map<Path, std::vector<Path>> directories;
  using DirectoryNames = std::map<std::pair<std::string_view, std::string_view>, std::vector<Path>>;
  std::map<Path, DirectoryNames> directoryNames;
  const DirectoryNames &namesIn(const Path &directory, Span span);
  std::set<Path> importing;
  std::map<std::string, Path> identities;
  Outline &outline(const Path &path, Span span);
  const std::vector<Path> &siblings(const Path &dir);
  File &activate(const Path &path, Span span);
  std::string resolveImport(const Path &dir, const Import &item);
};
} // namespace vqr
