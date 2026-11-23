#include <llvm/ADT/SmallString.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/Program.h>
#include <llvm/Support/raw_ostream.h>

#include <cstdlib>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {
namespace fs = std::filesystem;

#ifdef _WIN32
constexpr std::string_view executableSuffix = ".exe";
#else
constexpr std::string_view executableSuffix = "";
#endif

struct Options {
  fs::path source;
  fs::path output;
  std::vector<std::string> targetArgs;
  bool buildOnly = false;
};

struct TemporaryDirectory {
  llvm::SmallString<256> path;
  ~TemporaryDirectory() {
    if (path.empty()) return;
    std::error_code ignored;
    fs::remove_all(path.str().str(), ignored);
  }
};

Options arguments(int argc, char **argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    std::string_view arg(argv[i]);
    if (arg == "--") {
      options.targetArgs.assign(argv + i + 1, argv + argc);
      break;
    }
    if (arg == "--build-only") {
      options.buildOnly = true;
    } else if (arg == "-o" || arg == "--output") {
      if (++i == argc || std::string_view(argv[i]).empty()) throw std::runtime_error("missing output path");
      options.output = argv[i];
    } else if (arg.starts_with('-')) {
      throw std::runtime_error("unknown option " + std::string(arg));
    } else if (options.source.empty()) {
      options.source = arg;
    } else {
      throw std::runtime_error("use '--' before arguments to the debugged program");
    }
  }
  if (options.source.empty()) throw std::runtime_error("expected a .vqr file or package directory");
  if (options.buildOnly && (options.output.empty() || !options.targetArgs.empty()))
    throw std::runtime_error("--build-only requires --output and accepts no target arguments");
  if (!options.output.empty() && fs::weakly_canonical(options.output) == fs::weakly_canonical(options.source))
    throw std::runtime_error("--output must not overwrite the source");
  return options;
}

std::string findProgram(std::string name, const char *variable, const fs::path &sibling = {}) {
  const char *override = std::getenv(variable);
  if ((!override || !*override) && !sibling.empty() && fs::is_regular_file(sibling)) return sibling.string();
  auto path = llvm::sys::findProgramByName(override && *override ? override : name);
  if (path) return *path;
  throw std::runtime_error(name + " not found; set " + variable + " or add it to PATH");
}

int run(const std::vector<std::string> &command) {
  std::vector<llvm::StringRef> argv(command.begin(), command.end());
  std::string message;
  int result = llvm::sys::ExecuteAndWait(command.front(), argv, std::nullopt, {}, 0, 0, &message);
  if (result < 0)
    throw std::runtime_error("could not run " + command.front() + (message.empty() ? "" : ": " + message));
  return result;
}
} // namespace

int main(int argc, char **argv) {
  if (argc == 2 && (std::string_view(argv[1]) == "--help" || std::string_view(argv[1]) == "-h")) {
    llvm::outs() << "usage: vaqdbg [--build-only] [-o OUTPUT] SOURCE [-- PROGRAM_ARGS...]\n"
                    "Build SOURCE with vaqc -O0 -g, then launch LLDB.\n";
    return 0;
  }
  try {
    auto options = arguments(argc, argv);
    static int executableAnchor;
    auto executable = fs::path(llvm::sys::fs::getMainExecutable(argv[0], &executableAnchor));
    auto compilerName = "vaqc" + std::string(executableSuffix);
    auto compiler = findProgram(compilerName, "VAQC", executable.parent_path() / compilerName);
    auto debugger = options.buildOnly ? "" : findProgram("lldb", "VAQRIN_LLDB");

    TemporaryDirectory temporary;
    if (options.output.empty()) {
      if (auto error = llvm::sys::fs::createUniqueDirectory("vaqdbg", temporary.path))
        throw std::runtime_error(error.message());
      options.output = fs::path(temporary.path.str().str()) / ("program" + std::string(executableSuffix));
    } else {
      options.output = fs::absolute(options.output);
      fs::create_directories(options.output.parent_path());
    }
    const auto output = options.output.string();
    int status = run({compiler, "build", "-O0", "-g", fs::absolute(options.source).string(), "-o", output});
    if (status || options.buildOnly) return status;
    std::vector<std::string> command{debugger, "--no-lldbinit", "--", output};
    command.insert(command.end(), options.targetArgs.begin(), options.targetArgs.end());
    return run(command);
  } catch (const std::exception &error) {
    llvm::errs() << "vaqdbg: " << error.what() << '\n';
    return 1;
  }
}
