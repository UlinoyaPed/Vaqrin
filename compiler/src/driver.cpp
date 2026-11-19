#include "codegen.hpp"

#include <llvm/Bitcode/BitcodeWriter.h>
#include <llvm/Config/llvm-config.h>
#include <llvm/IR/LegacyPassManager.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/Verifier.h>
#include <llvm/MC/TargetRegistry.h>
#include <llvm/Passes/PassBuilder.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/Program.h>
#include <llvm/Support/TargetSelect.h>
#include <llvm/Support/raw_ostream.h>
#include <llvm/TargetParser/Host.h>

#include <algorithm>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <set>

namespace vqr {
std::unique_ptr<llvm::TargetMachine> makeTarget(Options &o) {
  llvm::InitializeAllTargetInfos();
  llvm::InitializeAllTargets();
  llvm::InitializeAllTargetMCs();
  llvm::InitializeAllAsmPrinters();
  if (o.triple.empty()) o.triple = llvm::sys::getDefaultTargetTriple();
  o.triple = llvm::Triple::normalize(o.triple);
  llvm::Triple triple(o.triple);
  std::string message;
#if LLVM_VERSION_MAJOR >= 21
  auto *target = llvm::TargetRegistry::lookupTarget(triple, message);
#else
  auto *target = llvm::TargetRegistry::lookupTarget(o.triple, message);
#endif
  if (!target) fail({}, message, "E300");
  llvm::TargetOptions targetOptions;
  if (triple.isThumb() && o.profile == "freestanding") targetOptions.FloatABIType = llvm::FloatABI::Soft;
  auto relocation = o.profile == "hosted" ? llvm::Reloc::PIC_ : llvm::Reloc::Static;
  auto level = o.optimization == 0   ? llvm::CodeGenOptLevel::None
               : o.optimization == 3 ? llvm::CodeGenOptLevel::Aggressive
                                     : llvm::CodeGenOptLevel::Default;
#if LLVM_VERSION_MAJOR >= 21
  auto machine = std::unique_ptr<llvm::TargetMachine>(
      target->createTargetMachine(triple, o.cpu, o.features, targetOptions, relocation, std::nullopt, level));
#else
  auto machine = std::unique_ptr<llvm::TargetMachine>(target->createTargetMachine(
      o.triple, o.cpu, o.features, targetOptions, relocation, std::nullopt, level));
#endif
  if (!machine) fail({}, "unable to configure the LLVM target", "E300");
  return machine;
}
} // namespace vqr

namespace {
using namespace vqr;
namespace fs = std::filesystem;

struct Request {
  Options options;
  std::vector<fs::path> inputs, linkInputs, packagePaths;
  fs::path output, sysroot, linkerScript;
  std::string cDriver, archiver;
};

void help() {
  std::cout << "Vaqrin native compiler\n"
               "Usage: vaqc [build|check] [options] source.vqr [source.vqr ...]\n"
               "       vaqc build [options] package-directory\n"
               "  --emit=exe|obj|llvm-ir|bc|asm|staticlib|sharedlib|ast\n"
               "  -o PATH                 Output path\n"
               "  -O0|-O1|-O2|-O3         Optimization level\n"
               "  -g                      Emit source locations and function debug information\n"
               "  --target TRIPLE         LLVM target triple\n"
               "  --cpu CPU               Target processor\n"
               "  --features FEATURES     Target feature string\n"
               "  --profile PROFILE       hosted or freestanding\n"
               "  --package-path DIR      Search additional local package roots\n"
               "  --cc PROGRAM            C driver for linking\n"
               "  --ar PROGRAM            Archiver for static libraries\n"
               "  --sysroot DIR           Target SDK/sysroot\n"
               "  --link-input FILE       Add a native object or library to the link\n"
               "  --linker-script FILE    Linker script for freestanding executables\n"
               "  --version               Show language and LLVM versions\n";
}

Request arguments(int argc, char **argv) {
  Request r;
  for (int i = 1; i < argc; ++i) {
    std::string_view a = argv[i];
    if (i == 1 && (a == "build" || a == "check")) {
      r.options.check = a == "check";
      continue;
    }
    if (a == "--check") {
      r.options.check = true;
      continue;
    }
    if (a == "-g") {
      r.options.debug = true;
      continue;
    }
    if (a.size() == 3 && a.starts_with("-O") && a[2] >= '0' && a[2] <= '3') {
      r.options.optimization = a[2] - '0';
      continue;
    }
    if (!a.starts_with('-')) {
      r.inputs.emplace_back(a);
      continue;
    }
    auto insertion = a.find('=');
    auto flag = a.substr(0, insertion);
    auto value = [&]() -> std::string {
      if (insertion != a.npos && insertion + 1 < a.size()) return std::string(a.substr(insertion + 1));
      if (insertion == a.npos && i + 1 < argc) return argv[++i];
      fail({}, "missing value for " + std::string(flag), "E300");
    };
    if (flag == "--emit")
      r.options.emit = value();
    else if (flag == "-o")
      r.output = value();
    else if (flag == "--target")
      r.options.triple = value();
    else if (flag == "--cpu")
      r.options.cpu = value();
    else if (flag == "--features")
      r.options.features = value();
    else if (flag == "--profile")
      r.options.profile = value();
    else if (flag == "--package-path")
      r.packagePaths.emplace_back(value());
    else if (flag == "--cc")
      r.cDriver = value();
    else if (flag == "--ar")
      r.archiver = value();
    else if (flag == "--sysroot")
      r.sysroot = value();
    else if (flag == "--link-input")
      r.linkInputs.emplace_back(value());
    else if (flag == "--linker-script")
      r.linkerScript = value();
    else
      fail({}, "unknown option '" + std::string(a) + "'", "E300");
  }
  if (r.inputs.empty()) fail({}, "no input files; see vaqc --help", "E300");
  if (r.options.profile != "hosted" && r.options.profile != "freestanding")
    fail({}, "unknown target profile '" + r.options.profile + "'", "E300");
  const std::set<std::string> emits{"exe", "obj", "llvm-ir", "bc", "asm", "staticlib", "sharedlib", "ast"};
  if (!emits.contains(r.options.emit)) fail({}, "unknown output kind '" + r.options.emit + "'", "E300");
  if (r.options.profile == "freestanding" && r.options.emit == "sharedlib")
    fail({}, "shared libraries require a hosted target", "E300");
  if (r.options.check && !r.output.empty()) fail({}, "check does not write an output file", "E300");
  if (r.options.emit == "ast" && !r.output.empty())
    fail({}, "AST output is written to standard output", "E300");
  return r;
}

class Inputs {
public:
  Inputs(const Request &request, const fs::path &executable) : request(request) {
    for (auto directory : {executable.parent_path(), executable.parent_path().parent_path()}) {
      auto candidate = directory / "share/vaqrin/stdlib";
      if (fs::is_directory(candidate)) {
        standardRoot = fs::weakly_canonical(candidate);
        break;
      }
    }
  }
  void load();
  std::deque<Source> sources;
  std::vector<File> files;
  std::string rootName;

private:
  const Request &request;
  fs::path standardRoot;
  std::map<std::string, std::vector<fs::path>> packages;
  std::map<std::string, std::vector<std::pair<std::string, Span>>> edges;
  std::map<fs::path, std::string> loaded;
  std::vector<fs::path> roots;
  std::vector<std::string> active;
  std::set<std::string> complete;
  std::string add(std::vector<fs::path> paths, Span span);
  void dependencies(const std::string &name);
};

std::vector<fs::path> sourcesIn(const fs::path &directory) {
  std::vector<fs::path> result;
  for (const auto &p : fs::directory_iterator(directory))
    if (p.is_regular_file() && p.path().extension() == ".vqr") result.push_back(p.path());
  std::ranges::sort(result);
  return result;
}

std::string Inputs::add(std::vector<fs::path> paths, Span span) {
  std::string name;
  if (paths.empty()) fail(span, "package has no .vqr files", "E301");
  for (const auto &path : paths) {
    if (path.extension() != ".vqr")
      fail(span, "Vaqrin source files require a .vqr extension: " + path.string(), "E301");
    auto canonical = fs::weakly_canonical(path);
    auto already = loaded.find(canonical);
    if (already != loaded.end()) {
      if (!name.empty() && name != already->second) fail(span, "files name different packages", "E301");
      name = already->second;
      continue;
    }
    std::ifstream f(path, std::ios::binary);
    if (!f) fail(span, "cannot open source file " + path.string(), "E301");
    std::string bytes(std::istreambuf_iterator<char>{f}, {});
    sources.push_back({path.string(), std::move(bytes)});
    auto input = parse(sources.back());
    input.standard = !standardRoot.empty() && canonical.parent_path().parent_path() == standardRoot;
    if (!name.empty() && name != input.package) fail(input.span, "files name different packages", "E301");
    name = input.package;
    for (const auto &i : input.imports)
      edges[name].emplace_back(i.path, i.span);
    loaded.emplace(canonical, name);
    files.push_back(std::move(input));
  }
  if (auto p = packages.find(name); p != packages.end()) {
    std::set<fs::path> expected, actual;
    for (const auto &file : p->second)
      expected.insert(fs::weakly_canonical(file));
    for (const auto &file : paths)
      actual.insert(fs::weakly_canonical(file));
    if (expected != actual) fail(span, "two source groups use the same package name '" + name + "'", "E301");
  } else
    packages.emplace(name, std::move(paths));
  return name;
}

void Inputs::dependencies(const std::string &name) {
  if (complete.contains(name)) return;
  active.push_back(name);
  for (const auto &[path, span] : edges[name]) {
    static const std::set<std::string> standard{"core", "collections", "text", "io"};
    auto logical = path.starts_with("std/") ? path.substr(4) : path;
    std::string dependency;
    if (standard.contains(logical)) {
      auto package = standardRoot / logical;
      if (standardRoot.empty() || !fs::is_directory(package))
        fail(span, "standard package '" + logical + "' is missing beside vaqc", "E301");
      dependency = add(sourcesIn(package), span);
    }
    if (path == "task" || path == "channel") continue;
    for (const auto &root : roots) {
      if (!dependency.empty()) break;
      auto candidate = root / path;
      if (!fs::is_directory(candidate)) continue;
      dependency = add(sourcesIn(candidate), span);
      break;
    }
    if (dependency.empty()) fail(span, "cannot locate local package '" + path + "'", "E301");
    if (dependency != fs::path(path).filename())
      fail(span, "import leaf does not match package declaration '" + dependency + "'", "E301");
    if (std::ranges::find(active, dependency) != active.end()) {
      std::string chain;
      for (const auto &item : active)
        chain += item + " -> ";
      fail(span, "cyclic package dependency: " + chain + dependency, "E301");
    }
    dependencies(dependency);
  }
  active.pop_back();
  complete.insert(name);
}

void Inputs::load() {
  std::vector<fs::path> paths;
  for (const auto &input : request.inputs) {
    bool directory = fs::is_directory(input);
    auto root = directory ? input : input.parent_path();
    if (root.empty()) root = ".";
    roots.push_back(root);
    if (directory) roots.push_back(root.parent_path().empty() ? fs::path(".") : root.parent_path());
    if (directory) {
      auto children = sourcesIn(input);
      paths.insert(paths.end(), children.begin(), children.end());
    } else
      paths.push_back(input);
  }
  roots.insert(roots.end(), request.packagePaths.begin(), request.packagePaths.end());
  std::ranges::sort(paths);
  rootName = add(std::move(paths), {});
  if (request.options.emit != "ast") dependencies(rootName);
}

void execute(const std::string &program, const std::vector<std::string> &args) {
  auto path = llvm::sys::findProgramByName(program);
  if (!path) fail({}, "required tool is unavailable: " + program, "E302");
  std::vector<llvm::StringRef> argv{*path};
  for (const auto &s : args)
    argv.push_back(s);
  std::string message;
  auto result = llvm::sys::ExecuteAndWait(*path, argv, std::nullopt, {}, 0, 0, &message);
  if (result != 0)
    fail({}, program + " failed (" + std::to_string(result) + ")" + (message.empty() ? "" : ": " + message),
         "E302");
}

void optimize(llvm::Module &module, llvm::TargetMachine &machine, unsigned level) {
  llvm::LoopAnalysisManager lam;
  llvm::FunctionAnalysisManager fam;
  llvm::CGSCCAnalysisManager cam;
  llvm::ModuleAnalysisManager mam;
  llvm::PassBuilder pb(&machine);
  pb.registerModuleAnalyses(mam);
  pb.registerCGSCCAnalyses(cam);
  pb.registerFunctionAnalyses(fam);
  pb.registerLoopAnalyses(lam);
  pb.crossRegisterProxies(lam, fam, cam, mam);
  static const llvm::OptimizationLevel levels[]{llvm::OptimizationLevel::O0, llvm::OptimizationLevel::O1,
                                                llvm::OptimizationLevel::O2, llvm::OptimizationLevel::O3};
  auto passes =
      level ? pb.buildPerModuleDefaultPipeline(levels[level]) : pb.buildO0DefaultPipeline(levels[0]);
  passes.run(module, mam);
  if (llvm::verifyModule(module, &llvm::errs()))
    fail({}, "LLVM verification failed after optimization", "E900");
}

void emit(llvm::Module &module, llvm::TargetMachine &machine, const fs::path &path, std::string_view kind) {
  std::error_code ec;
  llvm::raw_fd_ostream stream(path.string(), ec, llvm::sys::fs::OF_None);
  if (ec) fail({}, ec.message(), "E302");
  if (kind == "llvm-ir")
    module.print(stream, nullptr);
  else if (kind == "bc")
    llvm::WriteBitcodeToFile(module, stream);
  else {
    llvm::legacy::PassManager passes;
    auto type = kind == "asm" ? llvm::CodeGenFileType::AssemblyFile : llvm::CodeGenFileType::ObjectFile;
    if (machine.addPassesToEmitFile(passes, stream, nullptr, type))
      fail({}, "target cannot emit " + std::string(kind), "E302");
    passes.run(module);
  }
  stream.close();
  if (stream.has_error()) {
    std::string error = stream.error().message();
    stream.clear_error();
    fail({}, error, "E302");
  }
}

fs::path defaultOutput(const Request &r, const std::string &package, const llvm::Triple &triple) {
  auto emit = r.options.emit;
  if (emit == "exe") {
    if (r.options.profile == "freestanding") return package + ".elf";
    return triple.isOSWindows() ? package + ".exe" : package;
  }
  if (emit == "obj") return package + (triple.isOSWindows() ? ".obj" : ".o");
  if (emit == "staticlib") return triple.isOSWindows() ? package + ".lib" : "lib" + package + ".a";
  if (emit == "sharedlib") {
    if (triple.isOSWindows()) return package + ".dll";
    return "lib" + package + (triple.isOSDarwin() ? ".dylib" : ".so");
  }
  return package + (emit == "llvm-ir" ? ".ll" : emit == "bc" ? ".bc" : ".s");
}

struct Stage {
  fs::path path;
  ~Stage() {
    std::error_code error;
    fs::remove_all(path, error);
  }
};

void produce(Request &r, llvm::Module &module, llvm::TargetMachine &machine, const Inputs &inputs) {
  auto &target = machine.getTargetTriple();
  auto output = fs::absolute(r.output.empty() ? defaultOutput(r, inputs.rootName, target) : r.output);
  if (!fs::is_directory(output.parent_path())) fail({}, "output directory does not exist", "E302");
  for (const auto &s : inputs.sources)
    if (fs::weakly_canonical(s.path) == fs::weakly_canonical(output))
      fail({}, "output would overwrite a source file", "E302");
  auto kind = r.options.emit;
  llvm::SmallString<256> dir;
  if (auto err = llvm::sys::fs::createUniqueDirectory((output.parent_path() / ".vaqc").string(), dir))
    fail({}, err.message(), "E302");
  Stage stage{dir.str().str()};
  auto product = stage.path / output.filename();
  if (kind == "llvm-ir" || kind == "bc" || kind == "obj" || kind == "asm")
    emit(module, machine, product, kind);
  else {
    auto intermediates = stage.path / "objects";
    if (intermediates == product) intermediates = stage.path / "intermediate";
    fs::create_directory(intermediates);
    auto object = intermediates / ("package." + std::string(target.isOSWindows() ? "obj" : "o"));
    emit(module, machine, object, "obj");
    if (kind == "staticlib") {
      if (!r.linkInputs.empty())
        fail({}, "use --link-input with executable or shared-library emission", "E302");
      execute(r.archiver.empty() ? "llvm-ar" : r.archiver, {"rcsD", product.string(), object.string()});
    } else {
      auto driver = r.cDriver.empty() ? "clang" : r.cDriver;
      bool clang = fs::path(driver).filename().string().find("clang") != std::string::npos;
      std::vector<std::string> args;
      if (clang) args.push_back("--target=" + r.options.triple);
      if (!r.options.cpu.empty())
        args.push_back(std::string(target.isX86() ? "-march=" : "-mcpu=") + r.options.cpu);
      if (!r.sysroot.empty()) args.push_back("--sysroot=" + fs::absolute(r.sysroot).string());
      if (kind == "sharedlib") args.push_back(target.isOSDarwin() ? "-dynamiclib" : "-shared");
      if (r.options.profile == "freestanding") {
        if (r.linkerScript.empty())
          fail({}, "freestanding executable needs --linker-script and startup objects", "E302");
        args.push_back("-nostdlib");
        args.push_back("-Wl,-T," + fs::absolute(r.linkerScript).string());
        args.push_back("-Wl,--gc-sections");
        if (target.isThumb()) {
          args.push_back("-mthumb");
          args.push_back("-mfloat-abi=soft");
        }
      }
      args.push_back(object.string());
      for (const auto &input : r.linkInputs)
        args.push_back(fs::absolute(input).string());
      args.insert(args.end(), {"-o", product.string()});
      execute(driver, args);
    }
  }
  std::error_code error;
  fs::rename(product, output, error);
  if (error) fail({}, "cannot publish output '" + output.string() + "': " + error.message(), "E302");
}
} // namespace

int main(int argc, char **argv) {
  if (argc == 2 && (std::string_view(argv[1]) == "--help" || std::string_view(argv[1]) == "-h")) {
    help();
    return 0;
  }
  if (argc == 2 && std::string_view(argv[1]) == "--version") {
    std::cout << "vaqc 0.1.2 (Vaqrin 0.1.2, LLVM " << LLVM_VERSION_STRING << ")\n";
    return 0;
  }
  std::unique_ptr<Inputs> inputs;
  try {
    auto request = arguments(argc, argv);
    static int executableAnchor;
    auto executable = llvm::sys::fs::getMainExecutable(argv[0], &executableAnchor);
    inputs = std::make_unique<Inputs>(request, executable);
    inputs->load();
    if (request.options.emit == "ast") {
      for (const auto &f : inputs->files)
        printAst(f);
      return 0;
    }
    auto machine = makeTarget(request.options);
    llvm::LLVMContext context;
    auto module = compile(context, *machine, request.options, inputs->files, inputs->rootName);
    if (!request.options.check) {
      optimize(*module, *machine, request.options.optimization);
      produce(request, *module, *machine, *inputs);
    }
    return 0;
  } catch (const Diagnostic &error) {
    printDiagnostic(error);
  } catch (const fs::filesystem_error &error) {
    std::cerr << "vaqc: error[E301]: " << error.what() << '\n';
  } catch (const std::exception &error) {
    std::cerr << "vaqc: error[E900]: " << error.what() << '\n';
  }
  return 1;
}
