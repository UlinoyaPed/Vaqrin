#include "codegen.hpp"
#include "inputs.hpp"
#include "tooling.hpp"

#include <llvm/ADT/StringExtras.h>
#include <llvm/Bitcode/BitcodeWriter.h>
#include <llvm/Config/llvm-config.h>
#include <llvm/IR/Intrinsics.h>
#include <llvm/IR/LegacyPassManager.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/Verifier.h>
#include <llvm/MC/TargetRegistry.h>
#include <llvm/Passes/PassBuilder.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/JSON.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/Support/SHA256.h>
#include <llvm/Support/Program.h>
#include <llvm/Support/TargetSelect.h>
#include <llvm/Support/raw_ostream.h>
#include <llvm/TargetParser/Host.h>
#include <llvm/Transforms/IPO/GlobalDCE.h>

#include <algorithm>
#include <chrono>
#include <cstdlib>
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
  fs::path output, sysroot, linkerScript, entry, overlay, dependencies;
  bool timings = false;
  bool editorHints = false;
  bool syntaxOnly = false;
  std::string cDriver, archiver;
};

void help() {
  std::cout << "Vaqrin native compiler\n"
               "Usage: vaqc [build|check] [options] source.vqr [source.vqr ...]\n"
               "       vaqc build [options] package-directory\n"
               "       vaqc fmt [--check | --write] FILE... | vaqc fmt -\n"
               "  --emit=exe|obj|llvm-ir|bc|asm|staticlib|sharedlib|ast\n"
               "  -o PATH                 Output path\n"
               "  -O0|-O1|-O2|-O3         Optimization level\n"
               "  -g                      Emit source locations and function debug information\n"
               "  --target TRIPLE         LLVM target triple\n"
               "  --cpu CPU               Target processor\n"
               "  --features FEATURES     Target feature string\n"
               "  --profile PROFILE       hosted or freestanding\n"
               "  --entry FILE            Select a file-local main\n"
               "  --overlay FILE          Read replacement contents for one .vqr in check mode\n"
               "  --syntax-only          Check syntax without resolving imports or types\n"
               "  --diagnostics=json     Emit a JSON diagnostic with a UTF-16 range\n"
               "  --editor-hints         Report inferred binding types as JSON in check mode\n"
               "  --dependencies FILE     List source inputs and directory probes for one .vqr\n"
               "  --timings              Report build stage durations on stderr\n"
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
    if (a == "--timings") { r.timings = true; continue; }
    if (a == "--syntax-only") { r.syntaxOnly = true; continue; }
    if (a == "--diagnostics=json") continue;
    if (a == "--editor-hints") { r.editorHints = true; continue; }
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
    else if (flag == "--entry")
      r.entry = value();
    else if (flag == "--overlay")
      r.overlay = value();
    else if (flag == "--dependencies")
      r.dependencies = value();
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
  if ((!r.overlay.empty() || !r.dependencies.empty()) &&
      (r.inputs.size() != 1 || r.inputs.front().extension() != ".vqr" || fs::is_directory(r.inputs.front())))
    fail({}, "--overlay and --dependencies require exactly one .vqr input", "E300");
  if (r.syntaxOnly && (!r.options.check || r.editorHints || !r.dependencies.empty() || r.options.emit == "ast"))
    fail({}, "--syntax-only requires check mode and accepts source files and an optional overlay", "E300");
  if (!r.overlay.empty() && !r.options.check) fail({}, "--overlay requires check mode", "E300");
  if (r.editorHints && (!r.options.check || r.options.emit == "ast" || r.inputs.size() != 1 ||
                        r.inputs.front().extension() != ".vqr" || fs::is_directory(r.inputs.front())))
    fail({}, "--editor-hints requires check mode and one .vqr input", "E300");
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

std::optional<std::string> fileDigest(const fs::path &file) {
  auto bytes = llvm::MemoryBuffer::getFile(file.string());
  if (!bytes) return {};
  llvm::SHA256 digest;
  digest.update((*bytes)->getBuffer());
  return llvm::toHex(digest.final(), true);
}

fs::path runtimeCache() {
  if (auto *cache = std::getenv("XDG_CACHE_HOME"); cache && fs::path(cache).is_absolute())
    return fs::path(cache) / "vaqrin/runtime";
  if (auto *home = std::getenv("HOME"); home && fs::path(home).is_absolute())
    return fs::path(home) / ".cache/vaqrin/runtime";
  return {};
}

void compileRuntime(const std::string &driver, std::vector<std::string> args, const fs::path &source,
                    const fs::path &object, bool cacheable, bool timings) {
  const auto started = std::chrono::steady_clock::now();
  const auto report = [&](std::string_view status) {
    if (timings)
      std::cerr << "vaqc: runtime " << source.filename().string() << ' ' << status << ' '
                << std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count()
                << " ms\n";
  };
  auto cache = cacheable ? runtimeCache() : fs::path{};
  auto tool = cache.empty() ? llvm::ErrorOr<std::string>(std::make_error_code(std::errc::no_such_file_or_directory))
                            : llvm::sys::findProgramByName(driver);
  auto identity = tool ? fileDigest(*tool) : std::nullopt;
  std::error_code error;
  if (!identity || (fs::create_directories(cache, error), error)) {
    args.insert(args.end(), {"-c", source.string(), "-o", object.string()});
    execute(driver, args);
    report("uncached");
    return;
  }

  const bool cpp = source.extension() == ".cpp";
  auto rendered = object;
  rendered.replace_extension(cpp ? ".ii" : ".i");
  auto preprocessing = args;
  preprocessing.insert(preprocessing.end(), {"-E", source.string(), "-o", rendered.string()});
  execute(driver, preprocessing);
  auto contents = fileDigest(rendered);
  if (!contents) fail({}, "cannot read preprocessed runtime source", "E302");
  llvm::SHA256 hash;
  const auto add = [&](llvm::StringRef field) {
    hash.update(field);
    hash.update(llvm::StringRef("\0", 1));
  };
  add("vaqrin-runtime-v1");
  add(*tool);
  add(*identity);
  add(*contents);
  for (const auto &arg : args) add(arg);
  for (const char *name : {"PATH", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "COMPILER_PATH",
                           "GCC_EXEC_PREFIX", "SDKROOT", "CLANG_CONFIG_FILE", "SOURCE_DATE_EPOCH"}) {
    add(name);
    if (auto *value = std::getenv(name)) add(value);
  }
  auto entry = cache / llvm::toHex(hash.final(), true);
  const auto cachedObject = entry / "runtime.o";
  const auto cachedDigest = entry / "sha256";
  if (auto stored = fileDigest(cachedObject)) {
    std::ifstream expected(cachedDigest);
    std::string checksum;
    if (expected >> checksum && checksum == *stored &&
        fs::copy_file(cachedObject, object, fs::copy_options::overwrite_existing, error)) {
      report("cache hit");
      return;
    }
  }

  if (cpp) {
    args.push_back("-x");
    args.push_back("c++-cpp-output");
  }
  args.insert(args.end(), {"-c", rendered.string(), "-o", object.string()});
  execute(driver, args);
  report("cache miss");
  auto checksum = fileDigest(object);
  llvm::SmallString<256> temporary;
  if (!checksum || llvm::sys::fs::createUniqueDirectory((cache / "publish").string(), temporary)) return;
  fs::path stage(temporary.str().str());
  if (fs::copy_file(object, stage / "runtime.o", error)) {
    std::ofstream out(stage / "sha256");
    out << *checksum << '\n';
    out.close();
    if (out) {
      // Rename individual files into a shared entry. Readers validate the checksum
      // before reuse, including during a concurrent publication.
      fs::create_directories(entry, error);
      fs::rename(stage / "runtime.o", cachedObject, error);
      if (!error) fs::rename(stage / "sha256", cachedDigest, error);
    }
  }
  fs::remove_all(stage, error);
}

void optimize(llvm::Module &module, llvm::TargetMachine &machine, const Options &options) {
  auto level = options.optimization;
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
  if (options.emit == "exe") passes.addPass(llvm::GlobalDCEPass());
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

std::vector<std::string> targetFlags(const Request &r, const llvm::Triple &target, const std::string &driver) {
  std::vector<std::string> args;
  if (fs::path(driver).filename().string().find("clang") != std::string::npos)
    args.push_back("--target=" + r.options.triple);
  if (!r.options.cpu.empty())
    args.push_back(std::string(target.isX86() ? "-march=" : "-mcpu=") + r.options.cpu);
  if (!r.sysroot.empty()) args.push_back("--sysroot=" + fs::absolute(r.sysroot).string());
  return args;
}

void produce(Request &r, llvm::Module &module, llvm::TargetMachine &machine, const Inputs &inputs) {
  auto &target = machine.getTargetTriple();
  auto output = fs::absolute(r.output.empty() ? defaultOutput(r, inputs.rootName, target) : r.output);
  if (!fs::is_directory(output.parent_path())) fail({}, "output directory does not exist", "E302");
  for (const auto &s : inputs.files)
    if (fs::weakly_canonical(s.span.source->path) == fs::weakly_canonical(output))
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
    auto driver = r.cDriver.empty() ? "clang" : r.cDriver;
    bool usesTasks = std::ranges::any_of(module, [](const llvm::Function &function) {
      return function.getName().starts_with("__vqr_rt_") && !function.hasExternalWeakLinkage() &&
             !function.use_empty();
    });
    auto usesRuntime = [&](llvm::StringRef prefix) {
      return std::ranges::any_of(module, [prefix](const llvm::Function &function) {
        return function.getName().starts_with(prefix) && !function.hasExternalWeakLinkage() &&
               !function.use_empty();
      });
    };
    const bool usesProcessRuntime = usesRuntime("__vqr_process_");
    const bool usesNetRuntime = usesRuntime("__vqr_net_");
    const bool usesTlsRuntime = usesRuntime("__vqr_tls_");
    const bool usesIoRuntime = usesProcessRuntime || usesRuntime("__vqr_io_");
    const bool usesDecimalRuntime = usesRuntime("__vqr_decimal_");
    const bool supportsHostRuntime =
        r.options.profile == "hosted" && target.isOSLinux() && target.isGNUEnvironment() &&
        module.getDataLayout().getPointerSizeInBits() == 64 &&
        (target.getArch() == llvm::Triple::x86_64 || target.getArch() == llvm::Triple::aarch64);
    if (usesProcessRuntime && !supportsHostRuntime)
      fail({}, "process execution requires hosted GNU/Linux x86-64 or AArch64", "E290");
    if (usesNetRuntime && !supportsHostRuntime)
      fail({}, "host networking requires hosted GNU/Linux x86-64 or AArch64", "E290");
    if (usesTlsRuntime && !supportsHostRuntime)
      fail({}, "TLS requires hosted GNU/Linux x86-64 or AArch64", "E290");
    if (usesIoRuntime && !supportsHostRuntime)
      fail({}, "host I/O currently requires hosted GNU/Linux x86-64 or AArch64", "E290");
    if (usesDecimalRuntime && !supportsHostRuntime)
      fail({}, "decimal conversion requires hosted GNU/Linux x86-64 or AArch64", "E290");
    const auto codegen = std::chrono::steady_clock::now();
    emit(module, machine, object, "obj");
    if (r.timings)
      std::cerr << "vaqc: codegen "
                << std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - codegen).count()
                << " ms\n";
    auto runtime = intermediates / "vaqrin-task-runtime.o";
    if (usesTasks) {
      auto source = inputs.taskRuntimeSource();
      if (!fs::is_regular_file(source)) fail({}, "the Vaqrin task runtime source is missing", "E302");
      auto args = targetFlags(r, target, driver);
      args.insert(args.end(), {"-std=c11", "-O2", "-fPIC", "-fvisibility=hidden", "-pthread"});
      compileRuntime(driver, std::move(args), source, runtime, target.isOSLinux(), r.timings);
    }
    auto compileCppRuntime = [&](const fs::path &source, const fs::path &object, bool pthread) {
      if (!fs::is_regular_file(source))
        fail({}, "Vaqrin runtime source is missing: " + source.string(), "E302");
      auto args = targetFlags(r, target, driver);
      args.insert(args.end(), {"-x", "c++", "-std=c++20", "-O2", "-fPIC", "-fvisibility=hidden",
                               "-fno-exceptions", "-fno-rtti"});
      if (pthread) args.push_back("-pthread");
      compileRuntime(driver, std::move(args), source, object, target.isOSLinux(), r.timings);
    };
    auto ioRuntime = intermediates / "vaqrin-io-runtime.o";
    if (usesIoRuntime) compileCppRuntime(inputs.ioRuntimeSource(), ioRuntime, true);
    auto processRuntime = intermediates / "vaqrin-process-runtime.o";
    if (usesProcessRuntime) compileCppRuntime(inputs.processRuntimeSource(), processRuntime, true);
    auto netRuntime = intermediates / "vaqrin-net-runtime.o";
    if (usesNetRuntime) compileCppRuntime(inputs.netRuntimeSource(), netRuntime, false);
    auto tlsRuntime = intermediates / "vaqrin-tls-runtime.o";
    if (usesTlsRuntime) compileCppRuntime(inputs.tlsRuntimeSource(), tlsRuntime, true);
    auto decimalRuntime = intermediates / "vaqrin-decimal-runtime.o";
    if (usesDecimalRuntime) compileCppRuntime(inputs.decimalRuntimeSource(), decimalRuntime, false);
    if (kind == "staticlib") {
      if (!r.linkInputs.empty())
        fail({}, "use --link-input with executable or shared-library emission", "E302");
      std::vector<std::string> args{"rcsD", product.string(), object.string()};
      if (usesTasks) args.push_back(runtime.string());
      if (usesIoRuntime) args.push_back(ioRuntime.string());
      if (usesProcessRuntime) args.push_back(processRuntime.string());
      if (usesNetRuntime) args.push_back(netRuntime.string());
      if (usesTlsRuntime) args.push_back(tlsRuntime.string());
      if (usesDecimalRuntime) args.push_back(decimalRuntime.string());
      execute(r.archiver.empty() ? "llvm-ar" : r.archiver, args);
    } else {
      auto args = targetFlags(r, target, driver);
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
      if (usesTasks) args.push_back(runtime.string());
      if (usesIoRuntime) args.push_back(ioRuntime.string());
      if (usesProcessRuntime) args.push_back(processRuntime.string());
      if (usesNetRuntime) args.push_back(netRuntime.string());
      if (usesTlsRuntime) args.push_back(tlsRuntime.string());
      if (usesDecimalRuntime) args.push_back(decimalRuntime.string());
      for (const auto &input : r.linkInputs)
        args.push_back(fs::absolute(input).string());
      if (usesDecimalRuntime) args.push_back("-lstdc++");
      if (usesTlsRuntime) args.push_back("-ldl");
      if (r.options.profile == "hosted" && target.isOSLinux() &&
          (usesTasks || usesIoRuntime || usesTlsRuntime || module.getFunction("pthread_create") ||
           module.getFunction("pthread_self")))
        args.push_back("-pthread");
      if (r.options.profile == "hosted" && target.isOSLinux() &&
          std::ranges::any_of(module, [](const llvm::Function &function) {
            if (function.hasFnAttribute("vaqrin.libm") && !function.use_empty()) return true;
            auto intrinsic = function.getIntrinsicID();
            return intrinsic == llvm::Intrinsic::floor || intrinsic == llvm::Intrinsic::ceil ||
                   intrinsic == llvm::Intrinsic::trunc || intrinsic == llvm::Intrinsic::roundeven;
          }))
        args.push_back("-lm");
      args.insert(args.end(), {"-o", product.string()});
      execute(driver, args);
    }
  }
  std::error_code error;
  fs::rename(product, output, error);
  if (error) fail({}, "cannot publish output '" + output.string() + "': " + error.message(), "E302");
}
void printEditorHints(const EditorHints &hints) {
  llvm::json::Array bindings;
  for (const auto &binding : hints.bindings) {
    const char *kind = "local";
    switch (binding.kind) {
    case EditorHints::BindingKind::Local: break;
    case EditorHints::BindingKind::Short: kind = "short"; break;
    case EditorHints::BindingKind::Loop: kind = "loop"; break;
    case EditorHints::BindingKind::Pattern: kind = "pattern"; break;
    }
    bindings.emplace_back(llvm::json::Object{{"begin", binding.begin}, {"end", binding.end},
                                            {"type", binding.type}, {"kind", kind}});
  }
  llvm::outs() << llvm::json::Value(std::move(bindings)) << '\n';
}
} // namespace

int main(int argc, char **argv) {
  if (argc == 2 && (std::string_view(argv[1]) == "--help" || std::string_view(argv[1]) == "-h")) {
    help();
    return 0;
  }
  if (argc == 2 && std::string_view(argv[1]) == "--version") {
    std::cout << "vaqc 0.1.5 (Vaqrin 0.1.5, LLVM " << LLVM_VERSION_STRING << ")\n";
    return 0;
  }
  if (argc >= 2 && std::string_view(argv[1]) == "fmt") return formatCommand(argc, argv);
  const bool jsonDiagnostics = std::any_of(argv + 1, argv + argc, [](const char *arg) {
    return std::string_view(arg) == "--diagnostics=json";
  });
  std::unique_ptr<Inputs> inputs;
  Source syntaxSource;
  EditorHints hints;
  try {
    auto request = arguments(argc, argv);
    if (request.syntaxOnly) {
      for (const auto &path : request.inputs) {
        if (path.extension() != ".vqr" || fs::is_directory(path))
          fail({}, "syntax check requires .vqr source files", "E300");
        syntaxSource = readSource(fs::absolute(path), request.overlay.empty() ? path : request.overlay);
        parse(syntaxSource);
      }
      return 0;
    }
    static int executableAnchor;
    auto executable = llvm::sys::fs::getMainExecutable(argv[0], &executableAnchor);
    auto start = std::chrono::steady_clock::now();
    auto stage = [&](std::string_view label) {
      auto now = std::chrono::steady_clock::now();
      if (request.timings)
        std::cerr << "vaqc: " << label << ' ' << std::chrono::duration<double, std::milli>(now - start).count()
                  << " ms\n";
      start = now;
    };
    auto overlayPath = request.overlay.empty() ? fs::path{} : fs::weakly_canonical(request.inputs.front());
    inputs = std::make_unique<Inputs>(executable, overlayPath, request.overlay);
    inputs->load(request.inputs, request.packagePaths, request.entry, !request.options.check &&
                 request.options.emit == "exe" && request.options.profile == "hosted");
    if (request.options.emit == "ast") {
      for (const auto &f : inputs->files)
        printAst(f);
      return 0;
    }
    stage("inputs");
    auto machine = makeTarget(request.options);
    stage("target");
    if (request.editorHints) {
      hints.source = fs::weakly_canonical(request.inputs.front()).string();
      request.options.editorHints = &hints;
    }
    llvm::LLVMContext context;
    auto module = compile(context, *machine, request.options, *inputs);
    stage("compile");
    if (!request.options.check) {
      optimize(*module, *machine, request.options);
      stage("optimize");
      produce(request, *module, *machine, *inputs);
      stage("emit and link");
    }
    if (!request.dependencies.empty()) {
      llvm::json::Array files, directories;
      for (const auto &path : inputs->dependencyFiles()) files.push_back(path.string());
      for (const auto &path : inputs->dependencyDirectories()) directories.push_back(path.string());
      llvm::json::Object manifest{{"files", std::move(files)}, {"directories", std::move(directories)}};
      const auto destination = fs::weakly_canonical(request.dependencies);
      if (inputs->dependencyFiles().contains(destination) ||
          (!request.output.empty() && fs::weakly_canonical(request.output) == destination) ||
          destination.extension() != ".json")
        fail({}, "--dependencies must name a separate .json file", "E302");
      std::ofstream output(destination);
      if (!output) fail({}, "cannot open dependency manifest", "E302");
      std::string body;
      llvm::raw_string_ostream stream(body);
      stream << llvm::json::Value(std::move(manifest));
      output << body << '\n';
      if (!output) fail({}, "cannot write dependency manifest", "E302");
    }
    if (request.editorHints) printEditorHints(hints);
    return 0;
  } catch (const Diagnostic &error) {
    if (!hints.bindings.empty()) printEditorHints(hints);
    if (jsonDiagnostics) printJsonDiagnostic(error);
    else printDiagnostic(error);
  } catch (const fs::filesystem_error &error) {
    Diagnostic diagnostic({}, "E301", error.what());
    if (jsonDiagnostics) printJsonDiagnostic(diagnostic);
    else printDiagnostic(diagnostic);
  } catch (const std::exception &error) {
    Diagnostic diagnostic({}, "E900", error.what());
    if (jsonDiagnostics) printJsonDiagnostic(diagnostic);
    else printDiagnostic(diagnostic);
  }
  return 1;
}
