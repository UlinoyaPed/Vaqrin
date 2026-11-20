#pragma once

#include "syntax.hpp"

#include <deque>
#include <filesystem>
#include <map>
#include <set>

namespace vqr {
class Inputs {
  using Path = std::filesystem::path;

public:
  explicit Inputs(const Path &executable, Path overlayPath = {}, Path overlayContents = {});
  std::set<Path> dependencyFiles() const;
  std::set<Path> dependencyDirectories() const;
  void load(const std::vector<Path> &inputs, const std::vector<Path> &packagePaths, const Path &entry,
            bool hostedExecutable);
  std::string qualifier(const File &file, std::string_view alias, Span span);
  std::string scope(const File &file, std::string_view symbol, Span span);
  bool directoryVisible(const File &a, const File &b) const;
  Path taskRuntimeSource() const { return runtimeRoot / "tasks.c"; }
  Path ioRuntimeSource() const { return runtimeRoot / "io_posix.cpp"; }
  Path processRuntimeSource() const { return runtimeRoot / "process_posix.cpp"; }
  Path decimalRuntimeSource() const { return runtimeRoot / "decimal.cpp"; }

  std::deque<File> files;
  std::string rootName;

private:
  struct Outline {
    Source source;
    std::string package, stem;
    std::set<std::string, std::less<>> names;
    unsigned mainCount = 0;
    bool entrySignature = false, exported = false;
    File *file = nullptr;
  };
  Path project, standardRoot, runtimeRoot, overlayPath, overlayContents;
  std::set<Path> fileProbes, directoryProbes;
  std::vector<Path> roots;
  std::map<Path, Outline> outlines;
  std::map<Path, std::vector<Path>> directories;
  std::set<Path> importing;
  std::map<std::string, Path> identities;
  std::map<std::pair<Path, std::string>, Path> normalizedStems;
  Outline &outline(const Path &path, Span span);
  const std::vector<Path> &siblings(const Path &dir);
  File &activate(const Path &path, Span span);
  std::string resolveImport(const Path &dir, const Import &item);
};
} // namespace vqr
