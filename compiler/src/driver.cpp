#include "codegen.hpp"
#include "inputs.hpp"
#include "tooling.hpp"

#include <llvm/ADT/StringExtras.h>
#include <llvm/Analysis/TargetTransformInfo.h>
#include <llvm/Bitcode/BitcodeReader.h>
#include <llvm/Bitcode/BitcodeWriter.h>
#include <llvm/Config/llvm-config.h>
#include <llvm/IR/InstIterator.h>
#include <llvm/IR/IntrinsicInst.h>
#include <llvm/IR/Intrinsics.h>
#include <llvm/IR/LegacyPassManager.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/Verifier.h>
#include <llvm/MC/TargetRegistry.h>
#include <llvm/Object/ArchiveWriter.h>
#include <llvm/Passes/PassBuilder.h>
#include <llvm/Support/FileSystem.h>
#include <llvm/Support/JSON.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/Support/Path.h>
#include <llvm/Support/SHA256.h>
#include <llvm/Support/Program.h>
#include <llvm/Support/TargetSelect.h>
#include <llvm/Support/raw_ostream.h>
#include <llvm/TargetParser/Host.h>
#include <llvm/Transforms/IPO/GlobalDCE.h>
#include <llvm/Transforms/Utils/IntegerDivision.h>
#include <llvm/Transforms/Utils/LowerMemIntrinsics.h>

#include <algorithm>
#include <array>
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
  fs::path output, sysroot, linkerScript, entry, overlay, overlayMap, dependencies;
  bool timings = false;
  bool editorHints = false;
  bool editorSymbols = false;
  bool workspaceSymbols = false;
  bool syntaxOnly = false;
  bool run = false;
  std::vector<std::string> programArguments;
  std::string cDriver, archiver;
};

void help() {
  std::cout << "Vaqrin native compiler\n"
               "Usage: vaqc [build|check] [options] source.vqr [source.vqr ...]\n"
               "       vaqc build [options] package-directory\n"
               "       vaqc run [options] file.vqr [-- PROGRAM-ARGUMENTS...]\n"
               "       vaqc lock package-directory\n"
               "       vaqc fmt [--check | --write] FILE... | vaqc fmt -\n"
               "  --emit=exe|obj|llvm-ir|bc|asm|staticlib|sharedlib|ast\n"
               "  -o PATH                 Output path\n"
               "  -O0|-O1|-O2|-O3         Optimization level\n"
               "  -g                      Emit source locations and function debug information\n"
               "  --target TRIPLE         LLVM target triple\n"
               "  --cpu CPU               Target processor\n"
               "  --features FEATURES     Target feature string\n"
               "  --profile PROFILE       hosted or freestanding\n"
               "  --entry PACKAGE::main   Select a named package entry in a directory build\n"
               "  --overlay FILE          Read replacement contents for one .vqr in check mode\n"
               "  --overlay-map FILE      Read a JSON object mapping source paths to replacement files\n"
               "  --syntax-only          Check syntax without imports or types; '-' reads stdin\n"
               "  --diagnostics=json     Emit one JSON diagnostic per line with UTF-16 ranges\n"
               "  --editor-hints         Report inferred binding types as JSON in check mode\n"
               "  --editor-symbols       Report local binding occurrences as JSON in check mode\n"
               "  --editor-workspace-symbols  Report binding and top-level references with paths\n"
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
    if (i == 1 && (a == "build" || a == "check" || a == "run")) {
      r.options.check = a == "check";
      r.run = a == "run";
      continue;
    }
    if (r.run && a == "--") {
      r.programArguments.assign(argv + i + 1, argv + argc);
      break;
    }
    if (a == "--timings") { r.timings = true; continue; }
    if (a == "--syntax-only") { r.syntaxOnly = true; continue; }
    if (a == "--diagnostics=json") continue;
    if (a == "--editor-hints") { r.editorHints = true; continue; }
    if (a == "--editor-symbols") { r.editorSymbols = true; continue; }
    if (a == "--editor-workspace-symbols") { r.workspaceSymbols = true; continue; }
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
    if (!a.starts_with('-') || a == "-") {
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
    else if (flag == "--overlay-map")
      r.overlayMap = value();
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
  if (r.run && (r.inputs.size() != 1 || !fs::is_regular_file(r.inputs.front()) ||
                r.inputs.front().extension() != ".vqr" || r.options.check || r.options.profile != "hosted" ||
                r.options.emit != "exe" || !r.options.triple.empty() || !r.output.empty() || !r.entry.empty()))
    fail({}, "run requires one .vqr source and builds a temporary host executable", "E300");
  const auto stdinInputs = std::ranges::count(r.inputs, fs::path("-"));
  if (stdinInputs && (!r.syntaxOnly || stdinInputs != 1 || !r.overlay.empty()))
    fail({}, "'-' requires --syntax-only and may be specified once without --overlay", "E300");
  if ((!r.overlay.empty() || !r.dependencies.empty()) &&
      (r.inputs.size() != 1 || r.inputs.front().extension() != ".vqr" || fs::is_directory(r.inputs.front())))
    fail({}, "--overlay and --dependencies require exactly one .vqr input", "E300");
  if (r.syntaxOnly && (!r.options.check || r.editorHints || r.editorSymbols || r.workspaceSymbols || !r.dependencies.empty() || r.options.emit == "ast"))
    fail({}, "--syntax-only requires check mode and accepts source files and an optional overlay", "E300");
  if (!r.overlayMap.empty() && (!r.options.check || r.syntaxOnly || !r.overlay.empty()))
    fail({}, "--overlay-map requires semantic check mode and is exclusive with --overlay", "E300");
  if (!r.overlay.empty() && !r.options.check) fail({}, "--overlay requires check mode", "E300");
  if ((r.editorHints || r.editorSymbols) && (!r.options.check || r.options.emit == "ast" || r.inputs.size() != 1 ||
                        r.inputs.front().extension() != ".vqr" || fs::is_directory(r.inputs.front())))
    fail({}, "editor output requires semantic check mode and one .vqr input", "E300");
  if (r.workspaceSymbols && (!r.options.check || r.options.emit == "ast"))
    fail({}, "--editor-workspace-symbols requires semantic check mode", "E300");
  if (static_cast<int>(r.editorHints) + r.editorSymbols + r.workspaceSymbols > 1)
    fail({}, "editor output modes are mutually exclusive", "E300");
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
  if (!identity || (fs::create_directories(cache, error), error) || !artifact::trustedDirectory(cache)) {
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
  if (artifact::trustedDirectory(entry) && artifact::trustedFile(cachedObject) && artifact::trustedFile(cachedDigest)) {
    std::ifstream expected(cachedDigest);
    std::array<char, 65> checksum{};
    if (expected.read(checksum.data(), checksum.size()) && checksum.back() == '\n' &&
        fs::copy_file(cachedObject, object, fs::copy_options::overwrite_existing, error)) {
      if (auto stored = fileDigest(object); stored && *stored == std::string_view(checksum.data(), 64)) {
        report("cache hit");
        return;
      }
      fs::remove(object, error);
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
      // of the staged copy, including during a concurrent publication.
      fs::create_directories(entry, error);
      if (!error && artifact::trustedDirectory(entry)) {
        fs::rename(stage / "runtime.o", cachedObject, error);
        if (!error) fs::rename(stage / "sha256", cachedDigest, error);
      }
    }
  }
  fs::remove_all(stage, error);
}

void lowerLargeArrayStores(llvm::Module &module, llvm::TargetMachine &machine, bool freestanding) {
  // SelectionDAG expands aggregate stores into one operation per element.
  // Keep large copies in bulk form while preserving when the source is read.
  constexpr std::uint64_t minimumBytes = 4096;
  struct Initialization {
    llvm::StoreInst *store;
    llvm::Constant *literal;
    llvm::LoadInst *copy;
  };
  const auto &layout = module.getDataLayout();
  std::vector<Initialization> stores;
  for (auto &function : module) {
    for (auto &block : function) {
      for (auto &instruction : block) {
        auto *store = llvm::dyn_cast<llvm::StoreInst>(&instruction);
        if (!store || !store->isSimple()) continue;
        auto *value = store->getValueOperand();
        auto *array = llvm::dyn_cast<llvm::ArrayType>(value->getType());
        if (!array || !llvm::isa<llvm::IntegerType>(array->getElementType())) continue;
        auto width = array->getElementType()->getIntegerBitWidth();
        if (width != 8 && width != 16 && width != 32 && width != 64) continue;
        if (layout.getTypeStoreSize(array).getFixedValue() < minimumBytes) continue;
        if (llvm::isa<llvm::ConstantAggregateZero, llvm::ConstantDataArray>(value)) {
          stores.push_back({store, llvm::cast<llvm::Constant>(value), nullptr});
        } else if (auto *load = llvm::dyn_cast<llvm::LoadInst>(value);
                   load && load->isSimple() && load->hasOneUse()) {
          auto *source = load->getPointerOperand()->stripPointerCasts();
          auto *destination = store->getPointerOperand()->stripPointerCasts();
          auto *global = llvm::dyn_cast<llvm::GlobalVariable>(source);
          const bool immutable = global && global->isConstant() && global->hasInitializer();
          auto *next = load->getNextNode();
          while (llvm::isa_and_nonnull<llvm::DbgInfoIntrinsic>(next)) next = next->getNextNode();
          const bool localCopy = llvm::isa<llvm::AllocaInst>(source) && llvm::isa<llvm::AllocaInst>(destination) &&
                                 source != destination && next == store;
          if (immutable || localCopy) stores.push_back({store, nullptr, load});
        }
      }
    }
  }
  llvm::DenseMap<llvm::Constant *, llvm::GlobalVariable *> literals;
  for (const auto &[store, literal, copy] : stores) {
    auto *type = store->getValueOperand()->getType();
    auto bytes = layout.getTypeStoreSize(type).getFixedValue();
    llvm::IRBuilder<> builder(store);
    builder.SetCurrentDebugLocation(store->getDebugLoc());
    if (literal && literal->isNullValue()) {
      auto *fill = builder.CreateMemSet(store->getPointerOperand(), builder.getInt8(0), bytes, store->getAlign());
      if (freestanding) {
        llvm::expandMemSetAsLoop(llvm::cast<llvm::MemSetInst>(fill));
        fill->eraseFromParent();
      }
    } else {
      llvm::Value *source;
      llvm::Align alignment;
      if (copy) {
        source = copy->getPointerOperand();
        alignment = copy->getAlign();
      } else {
        auto *&constant = literals[literal];
        if (!constant) {
          constant = new llvm::GlobalVariable(module, type, true, llvm::GlobalValue::PrivateLinkage,
                                              literal, "vaqrin.array.initializer");
          constant->setUnnamedAddr(llvm::GlobalValue::UnnamedAddr::Global);
        }
        source = constant;
        alignment = layout.getABITypeAlign(type);
      }
      auto *transfer = builder.CreateMemCpy(store->getPointerOperand(), store->getAlign(), source, alignment, bytes);
      if (freestanding) {
        auto info = machine.getTargetTransformInfo(*store->getFunction());
        llvm::expandMemCpyAsLoop(llvm::cast<llvm::MemCpyInst>(transfer), info);
        transfer->eraseFromParent();
      }
    }
    store->eraseFromParent();
    if (copy) copy->eraseFromParent();
  }
}

void lowerWindowsWideDivision(llvm::Module &module) {
  llvm::SmallVector<llvm::BinaryOperator *, 16> divisions;
  for (auto &function : module)
    for (auto &instruction : llvm::instructions(function)) {
      if (!instruction.getType()->isIntegerTy(128)) continue;
      switch (instruction.getOpcode()) {
      case llvm::Instruction::SDiv:
      case llvm::Instruction::UDiv:
      case llvm::Instruction::SRem:
      case llvm::Instruction::URem:
        divisions.push_back(llvm::cast<llvm::BinaryOperator>(&instruction));
        break;
      default:
        break;
      }
    }
  // The MSVC CRT has no compiler-rt helpers for 128-bit division and remainder.
  for (auto *operation : divisions) {
    auto opcode = operation->getOpcode();
    if (opcode == llvm::Instruction::SDiv || opcode == llvm::Instruction::UDiv)
      llvm::expandDivision(operation);
    else
      llvm::expandRemainder(operation);
  }
}

void optimize(llvm::Module &module, llvm::TargetMachine &machine, const Options &options) {
  for (auto it = module.begin(); it != module.end();) {
    auto &function = *it++;
    if (function.hasFnAttribute("vaqrin.comptime") && function.use_empty())
      function.eraseFromParent();
  }
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
  lowerLargeArrayStores(module, machine, options.profile == "freestanding");
  if (machine.getTargetTriple().isWindowsMSVCEnvironment()) lowerWindowsWideDivision(module);
  if (llvm::verifyModule(module, &llvm::errs()))
    fail({}, "LLVM verification failed after optimization", "E900");
}

fs::path moduleCacheEntry(const llvm::Module &module, const Options &options, const fs::path &executable) {
  const auto *directory = std::getenv("VAQRIN_MODULE_CACHE");
  if (!directory || !fs::path(directory).is_absolute() || options.optimization == 0) return {};
  auto identity = artifact::executableIdentity(executable);
  if (!identity) return {};

  // Hash the validated input IR, which already includes the resolved sources
  // and their concrete generic instantiations.
  llvm::SmallVector<char, 0> input;
  llvm::raw_svector_ostream bitcode(input);
  llvm::WriteBitcodeToFile(module, bitcode);
  llvm::SHA256 hash;
  auto field = [&](llvm::StringRef value) {
    hash.update(std::to_string(value.size()) + ":");
    hash.update(value);
  };
  for (const auto &value : std::array<std::string, 9>{
           "vaqrin-module-v2", LLVM_VERSION_STRING, *identity, options.triple, options.cpu,
           options.features, options.profile, options.emit, std::to_string(options.optimization)})
    field(value);
  field(bitcode.str());
  return fs::path(directory) / (llvm::toHex(hash.final(), true) + ".bc");
}

void optimizeBuild(std::unique_ptr<llvm::Module> &module, llvm::TargetMachine &machine,
                   const Request &request, const fs::path &executable,
                   std::unique_ptr<llvm::LLVMContext> &cachedContext) {
  constexpr std::size_t maxBytes = 64 * 1024 * 1024;
  const auto path = moduleCacheEntry(*module, request.options, executable);
  if (auto record = artifact::read(path, maxBytes)) {
    auto context = std::make_unique<llvm::LLVMContext>();
    auto parsed = llvm::parseBitcodeFile({artifact::payload(*record), "cached module"}, *context);
    if (!parsed) {
      llvm::consumeError(parsed.takeError());
    } else if (!llvm::verifyModule(**parsed)) {
      (*parsed)->setModuleIdentifier(module->getModuleIdentifier());
      module = std::move(*parsed);
      cachedContext = std::move(context);
      if (request.timings) std::cerr << "vaqc: optimization cache hit\n";
      return;
    }
  }
  optimize(*module, machine, request.options);
  if (path.empty()) return;
  if (request.timings) std::cerr << "vaqc: optimization cache miss\n";
  llvm::SmallVector<char, 0> bytes;
  llvm::raw_svector_ostream output(bytes);
  llvm::WriteBitcodeToFile(*module, output);
  artifact::write(path, output.str(), maxBytes);
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

int runProgram(const fs::path &program, const std::vector<std::string> &arguments) {
  const auto executable = program.string();
  std::vector<llvm::StringRef> args{executable};
  args.insert(args.end(), arguments.begin(), arguments.end());
  std::string error;
  const auto status = llvm::sys::ExecuteAndWait(executable, args, std::nullopt, {}, 0, 0, &error);
  if (status < 0) fail({}, "program terminated: " + error, "E302");
  return status;
}

std::vector<std::string> targetFlags(const Request &r, const llvm::Triple &target, const std::string &driver) {
  std::vector<std::string> args;
  if (fs::path(driver).filename().string().find("clang") != std::string::npos)
    args.push_back("--target=" + r.options.triple);
  if (!r.options.cpu.empty())
    args.push_back(std::string(target.isX86() ? "-march=" : "-mcpu=") + r.options.cpu);
  if (!r.sysroot.empty()) args.push_back("--sysroot=" + fs::absolute(r.sysroot).string());
  return args;
}

void writeStaticLibrary(const fs::path &output, const llvm::Triple &target,
                        const std::vector<std::string> &objects, const std::string &archiver) {
  if (!archiver.empty()) {
    std::vector<std::string> args{"rcsD", output.string()};
    args.insert(args.end(), objects.begin(), objects.end());
    execute(archiver, args);
    return;
  }
  std::vector<llvm::NewArchiveMember> members;
  members.reserve(objects.size());
  for (const auto &object : objects) {
    auto member = llvm::NewArchiveMember::getFile(object, /*Deterministic=*/true);
    if (!member) fail({}, llvm::toString(member.takeError()), "E302");
    member->MemberName = llvm::sys::path::filename(member->MemberName);
    members.push_back(std::move(*member));
  }
  using Archive = llvm::object::Archive;
  const auto format = target.isOSBinFormatMachO() ? Archive::K_DARWIN :
                      target.isOSBinFormatCOFF() ? Archive::K_COFF : Archive::K_GNU;
  if (auto error = llvm::writeArchive(output.string(), members, llvm::SymtabWritingMode::NormalSymtab,
                                      format, /*Deterministic=*/true, /*Thin=*/false))
    fail({}, llvm::toString(std::move(error)), "E302");
}

fs::path buildOutput(const Request &r, const Inputs &inputs, const llvm::Triple &target) {
  return fs::absolute(r.output.empty() ? defaultOutput(r, inputs.rootName, target) : r.output);
}

fs::path dependencyDestination(const Request &r, const Inputs &inputs, const llvm::Triple &target) {
  if (r.dependencies.empty()) return {};
  const auto destination = fs::absolute(r.dependencies);
  const auto resolved = fs::weakly_canonical(destination);
  if (destination.extension() != ".json" || inputs.dependencyFiles().contains(resolved) ||
      (!r.options.check && fs::weakly_canonical(buildOutput(r, inputs, target)) == resolved))
    fail({}, "--dependencies must name a separate .json file", "E302");
  if (!fs::is_directory(destination.parent_path()))
    fail({}, "dependency manifest directory does not exist", "E302");
  return destination;
}

void writeDependencies(const fs::path &destination, const Inputs &inputs) {
  llvm::json::Array files, directories;
  for (const auto &path : inputs.dependencyFiles()) files.push_back(path.string());
  for (const auto &path : inputs.dependencyDirectories()) directories.push_back(path.string());
  llvm::json::Object manifest{{"files", std::move(files)}, {"directories", std::move(directories)}};
  auto temporary = llvm::sys::fs::TempFile::create((destination.parent_path() / ".vaqc-deps-%%%%%%").string());
  if (!temporary) fail({}, llvm::toString(temporary.takeError()), "E302");
  llvm::raw_fd_ostream output(temporary->FD, false);
  output << llvm::json::Value(std::move(manifest)) << '\n';
  output.flush();
  if (output.has_error()) {
    output.clear_error();
    llvm::consumeError(temporary->discard());
    fail({}, "cannot write dependency manifest", "E302");
  }
  if (auto error = temporary->keep(destination.string()))
    fail({}, llvm::toString(std::move(error)), "E302");
}

void produce(Request &r, llvm::Module &module, llvm::TargetMachine &machine, const Inputs &inputs) {
  auto &target = machine.getTargetTriple();
  auto output = buildOutput(r, inputs, target);
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
    const bool usesHttp2Runtime = usesRuntime("__vqr_http2_");
    const bool usesGzipRuntime = usesRuntime("__vqr_gzip_");
    const bool usesTlsRuntime = usesRuntime("__vqr_tls_");
    const bool usesIoRuntime = usesProcessRuntime || usesRuntime("__vqr_io_");
    const bool usesDecimalRuntime = usesRuntime("__vqr_decimal_");
    const bool supportsHostRuntime =
        r.options.profile == "hosted" && target.isOSLinux() && target.isGNUEnvironment() &&
        module.getDataLayout().getPointerSizeInBits() == 64 &&
        (target.getArch() == llvm::Triple::x86_64 || target.getArch() == llvm::Triple::aarch64);
    const bool supportsPosixIo = supportsHostRuntime ||
        (r.options.profile == "hosted" && target.isMacOSX() &&
         (target.getArch() == llvm::Triple::x86_64 || target.getArch() == llvm::Triple::aarch64));
    if (usesRuntime("__vqr_io_reactor_") && !supportsHostRuntime)
      fail({}, "the epoll reactor requires hosted GNU/Linux x86-64 or AArch64", "E290");
    if (usesProcessRuntime && !supportsPosixIo)
      fail({}, "process execution requires hosted GNU/Linux or macOS on x86-64 or AArch64", "E290");
    if (usesNetRuntime && !supportsPosixIo)
      fail({}, "host networking requires hosted GNU/Linux or macOS on x86-64 or AArch64", "E290");
    if (usesHttp2Runtime && !supportsHostRuntime)
      fail({}, "HTTP/2 requires hosted GNU/Linux x86-64 or AArch64", "E290");
    if (usesGzipRuntime && !supportsHostRuntime)
      fail({}, "gRPC gzip requires hosted GNU/Linux x86-64 or AArch64", "E290");
    if (usesTlsRuntime && !supportsPosixIo)
      fail({}, "TLS requires hosted GNU/Linux or macOS on x86-64 or AArch64", "E290");
    if (usesIoRuntime && !supportsPosixIo)
      fail({}, "host I/O requires hosted GNU/Linux or macOS on x86-64 or AArch64", "E290");
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
    if (usesNetRuntime) compileCppRuntime(inputs.netRuntimeSource(), netRuntime, true);
    auto http2Runtime = intermediates / "vaqrin-http2-runtime.o";
    if (usesHttp2Runtime) compileCppRuntime(inputs.http2RuntimeSource(), http2Runtime, false);
    auto gzipRuntime = intermediates / "vaqrin-gzip-runtime.o";
    if (usesGzipRuntime) compileCppRuntime(inputs.gzipRuntimeSource(), gzipRuntime, false);
    auto tlsRuntime = intermediates / "vaqrin-tls-runtime.o";
    if (usesTlsRuntime) compileCppRuntime(inputs.tlsRuntimeSource(), tlsRuntime, true);
    auto decimalRuntime = intermediates / "vaqrin-decimal-runtime.o";
    if (usesDecimalRuntime) compileCppRuntime(inputs.decimalRuntimeSource(), decimalRuntime, false);
    if (kind == "staticlib") {
      if (!r.linkInputs.empty())
        fail({}, "use --link-input with executable or shared-library emission", "E302");
      std::vector<std::string> objects{object.string()};
      if (usesTasks) objects.push_back(runtime.string());
      if (usesIoRuntime) objects.push_back(ioRuntime.string());
      if (usesProcessRuntime) objects.push_back(processRuntime.string());
      if (usesNetRuntime) objects.push_back(netRuntime.string());
      if (usesHttp2Runtime) objects.push_back(http2Runtime.string());
      if (usesGzipRuntime) objects.push_back(gzipRuntime.string());
      if (usesTlsRuntime) objects.push_back(tlsRuntime.string());
      if (usesDecimalRuntime) objects.push_back(decimalRuntime.string());
      writeStaticLibrary(product, target, objects, r.archiver);
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
      if (usesHttp2Runtime) args.push_back(http2Runtime.string());
      if (usesGzipRuntime) args.push_back(gzipRuntime.string());
      if (usesTlsRuntime) args.push_back(tlsRuntime.string());
      if (usesDecimalRuntime) args.push_back(decimalRuntime.string());
      for (const auto &input : r.linkInputs)
        args.push_back(fs::absolute(input).string());
      if (usesDecimalRuntime) args.push_back("-lstdc++");
      if (r.options.profile == "hosted" && target.isWindowsMSVCEnvironment()) {
        // Clang otherwise selects the static CRT even when linking a prebuilt COFF object.
        args.insert(args.end(), {"-nostartfiles", "-lmsvcrt", "-lvcruntime", "-lucrt", "-lkernel32"});
        auto usesProvider = [&](llvm::StringRef name) {
          return std::ranges::any_of(module, [name](const llvm::Function &function) {
            return function.hasFnAttribute(name) && !function.use_empty();
          });
        };
        auto usesSymbol = [&](llvm::StringRef name) {
          const auto *function = module.getFunction(name);
          return function && !function->use_empty();
        };
        if (usesSymbol("printf") || usesSymbol("fprintf"))
          args.push_back("-llegacy_stdio_definitions");
        if (usesProvider("vaqrin.bcrypt")) args.push_back("-lbcrypt");
        if (usesProvider("vaqrin.synchronization")) args.push_back("-lsynchronization");
      }
      if (usesHttp2Runtime) args.push_back("-lnghttp2");
      if (usesGzipRuntime) args.push_back("-lz");
      if (usesTlsRuntime && target.isOSLinux()) args.push_back("-ldl");
      if (r.options.profile == "hosted" && target.isOSLinux() &&
          (usesTasks || usesIoRuntime || usesNetRuntime || usesTlsRuntime || module.getFunction("pthread_create") ||
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
Inputs::Overlays sourceOverlays(const Request &r) {
  Inputs::Overlays result;
  if (!r.overlay.empty()) result.emplace(fs::weakly_canonical(fs::absolute(r.inputs.front())), r.overlay);
  if (r.overlayMap.empty()) return result;
  auto file = llvm::MemoryBuffer::getFile(r.overlayMap.string());
  if (!file) fail({}, "cannot read overlay map " + r.overlayMap.string(), "E301");
  auto parsed = llvm::json::parse((*file)->getBuffer());
  if (!parsed) fail({}, llvm::toString(parsed.takeError()), "E301");
  const auto *entries = parsed->getAsObject();
  if (!entries) fail({}, "overlay map must be a JSON object", "E301");
  for (const auto &[source, value] : *entries) {
    auto replacement = value.getAsString();
    auto path = fs::weakly_canonical(fs::absolute(fs::path(source.str())));
    if (!replacement || replacement->empty() || path.extension() != ".vqr" ||
        fs::is_directory(path) || !fs::is_directory(path.parent_path()) ||
        !result.emplace(path, fs::path(replacement->str())).second)
      fail({}, "invalid or duplicate source in overlay map: " + source.str(), "E301");
  }
  return result;
}

void printEditorSymbols(const EditorHints &hints) {
  llvm::json::Array occurrences;
  for (const auto &[path, begin, end, declarationPath, declaration, definition] : hints.workspaceOccurrences)
    occurrences.emplace_back(llvm::json::Object{{"path", path}, {"begin", begin}, {"end", end},
        {"declarationPath", declarationPath}, {"declaration", declaration}, {"definition", definition}});
  for (const auto &[begin, end, declaration, definition] : hints.occurrences)
    occurrences.emplace_back(llvm::json::Object{{"begin", begin}, {"end", end},
                                                {"declaration", declaration}, {"definition", definition}});
  llvm::outs() << llvm::json::Value(std::move(occurrences)) << '\n';
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
    std::cout << "vaqc " VAQRIN_VERSION " (Vaqrin " VAQRIN_VERSION ", LLVM " << LLVM_VERSION_STRING << ")\n";
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
    static int executableAnchor;
    auto executable = llvm::sys::fs::getMainExecutable(argv[0], &executableAnchor);
    if (argc >= 2 && std::string_view(argv[1]) == "lock") {
      if (argc != 3) fail({}, "usage: vaqc lock package-directory", "E300");
      Inputs(executable).writeLock(argv[2]);
      return 0;
    }
    auto request = arguments(argc, argv);
    if (request.syntaxOnly) {
      bool failed = false;
      for (const auto &path : request.inputs) {
        try {
          if (path == "-") {
            syntaxSource = readStdinSource();
          } else {
            if (path.extension() != ".vqr" || fs::is_directory(path))
              fail({}, "syntax check requires a .vqr source file: " + path.string(), "E300");
            syntaxSource = readSource(fs::absolute(path), request.overlay.empty() ? path : request.overlay);
          }
          parse(syntaxSource);
        } catch (const Diagnostic &diagnostic) {
          if (jsonDiagnostics)
            printJsonDiagnostic(diagnostic);
          else
            printDiagnostic(diagnostic);
          failed = true;
        }
      }
      return failed ? 1 : 0;
    }
    auto start = std::chrono::steady_clock::now();
    auto stage = [&](std::string_view label) {
      auto now = std::chrono::steady_clock::now();
      if (request.timings)
        std::cerr << "vaqc: " << label << ' ' << std::chrono::duration<double, std::milli>(now - start).count()
                  << " ms\n";
      start = now;
    };
    inputs = std::make_unique<Inputs>(executable, sourceOverlays(request));
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
    if (request.editorHints || request.editorSymbols || request.workspaceSymbols) {
      hints.source = fs::weakly_canonical(fs::absolute(request.inputs.front())).string();
      hints.symbols = request.editorSymbols || request.workspaceSymbols;
      hints.workspace = request.workspaceSymbols;
      request.options.editorHints = &hints;
    }
    llvm::LLVMContext context;
    std::unique_ptr<llvm::LLVMContext> cachedContext;
    CompileTimings compileTimings;
    if (request.timings) request.options.compileTimings = &compileTimings;
    auto module = compile(context, *machine, request.options, *inputs);
    if (request.timings) compileTimings.advance(CompileTimings::Other);
    stage("compile");
    if (request.timings) {
      constexpr std::array labels{"other", "types", "instantiation", "ctfe", "lowering"};
      for (std::size_t i = 0; i < labels.size(); ++i)
        std::cerr << "vaqc: compile." << labels[i] << ' '
                  << std::chrono::duration<double, std::milli>(compileTimings.durations[i]).count() << " ms\n";
    }
    const auto dependencies = dependencyDestination(request, *inputs, machine->getTargetTriple());
    Stage execution;
    if (request.run) {
      llvm::SmallString<256> directory;
      if (auto error = llvm::sys::fs::createUniqueDirectory((fs::temp_directory_path() / "vaqc-run").string(), directory))
        fail({}, "cannot create program directory: " + error.message(), "E302");
      execution.path = directory.str().str();
      request.output = execution.path / (machine->getTargetTriple().isOSWindows() ? "program.exe" : "program");
    }
    if (!request.options.check) {
      optimizeBuild(module, *machine, request, executable, cachedContext);
      stage("optimize");
      produce(request, *module, *machine, *inputs);
      stage("emit and link");
    }
    if (!dependencies.empty()) writeDependencies(dependencies, *inputs);
    if (request.run) return runProgram(request.output, request.programArguments);
    if (request.editorHints) printEditorHints(hints);
    if (request.editorSymbols || request.workspaceSymbols) printEditorSymbols(hints);
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
