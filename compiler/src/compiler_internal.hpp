#pragma once

#include "codegen.hpp"
#include "inputs.hpp"

#include <llvm/BinaryFormat/Dwarf.h>
#include <llvm/Config/llvm-config.h>
#include <llvm/IR/DIBuilder.h>
#include <llvm/IR/Intrinsics.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/Verifier.h>
#include <llvm/Support/raw_ostream.h>

#include <algorithm>
#include <charconv>
#include <deque>
#include <filesystem>
#include <functional>
#include <map>
#include <set>
#include <unordered_map>
#include <utility>

namespace vqr::detail {
using namespace llvm;

enum class Kind {
  Void,
  Never,
  Bool,
  Char,
  Int,
  Float,
  Text,
  View,
  Error,
  Optional,
  Result,
  Tuple,
  Array,
  Slice,
  Pointer,
  Box,
  Allocation,
  AllocationContext,
  RawStorage,
  Function,
  Closure,
  PatternRemainder,
  Class,
  Interface,
  Enum
};
struct Type;
using Types = std::map<std::string, Type *>;
using Constants = std::map<std::string, std::pair<Type *, Constant *>>;

struct Definition {
  const File *file;
  const Decl *decl;
  Type *declaringType = nullptr;
  std::vector<unsigned> receiverPath{};
  bool publicPath = true;
};
struct Variant {
  std::string name;
  APInt tag;
  std::vector<Type *> fields;
  unsigned offset;
};
struct Field {
  const Decl *decl;
  Type *type;
  unsigned index;
};

struct Capture {
  std::string name;
  Type *type;
  bool reference;
  bool writable;
  unsigned index;
};

struct Type {
  Kind kind;
  llvm::Type *ir;
  std::string name;
  std::vector<Type *> parts;
  unsigned bits = 0;
  bool sign = false;
  bool readOnly = false;
  std::uint64_t count = 0;
  Definition def{};
  std::vector<Field> fields;
  std::vector<Variant> variants;
  Constants comptimeArguments;
  std::vector<const Decl *> members;
  std::map<Type *, std::vector<Definition>> conformances;
  bool filling = false;
  bool filled = false;
  bool cAbi = false;
  std::vector<Capture> captures;
  std::vector<Type *> callable;
  Function *callEntry = nullptr;
  bool consuming = false;
  std::vector<Ast> patterns;
};

struct ValueAt {
  Type *type = nullptr;
  Value *value = nullptr;
  Value *address = nullptr;
  bool writable = false;
  int local = -1;
  bool borrowed = false;
};

struct FunctionEntry {
  Definition def;
  Type *result;
  std::vector<Type *> parameters;
  Types substitutions;
  Constants constants;
  Function *ir;
  Type *owner = nullptr;
  bool lowering = false;
  bool lowered = false;
};
struct GlobalEntry {
  Definition def;
  Type *type = nullptr;
  GlobalVariable *ir = nullptr;
  bool resolving = false;
};
struct Local {
  std::string name;
  Type *type;
  Value *slot;
  AllocaInst *order;
  Value *captureFlag;
  bool refCapture;
  std::size_t scope;
  bool writable;
  Constant *constant = nullptr;
  DILocalVariable *debug = nullptr;
  bool debugParameter = false;
};
struct Loop {
  BasicBlock *leave, *next;
  std::size_t depth;
  std::vector<std::vector<bool>> continues, breaks;
};

class Program;
class Body {
public:
  Body(Program &program, const File &file, Function *function, Type *result, Types generics = {},
       bool constant = false, Type *owner = nullptr);
  void build(FunctionEntry &entry);
  ValueAt expression(const Ast &node, Type *expected = nullptr);
  ValueAt coerce(ValueAt value, Type *expected, Span span);
  Value *read(ValueAt value, Span span, bool take = true);
  ValueAt place(const Ast &n);
  void emitReturn(ValueAt value, Span span);
  void setDebug(Span span);
  void bindConstant(const std::string &name, Type *type, Constant *constant, Span span);
  void debugLocal(int id, Span span, unsigned argument = 0);
  void debugValue(int id, bool alive);
  void emitDrop(Type *t, Value *address);
  void dropRemainder(Type *t, Value *address, const Ast &pattern);
  Value *emitCopy(Type *t, Value *address, Span span);
  IRBuilder<> ir;

private:
  Program &p;
  const File &file;
  Function *fn;
  Type *result;
  Types generics;
  bool evaluating = false;
  Type *owner;
  Type *closure = nullptr;
  unsigned unsafeDepth = 0;
  std::vector<Local> locals;
  std::multiset<int> patternBorrows;
  std::vector<std::vector<int>> scopes;
  std::vector<bool> initialized;
  std::vector<Loop> loops;
  bool constructing = false;
  std::set<unsigned> constructed;
  DISubprogram *debug = nullptr;

  bool live() const { return !ir.GetInsertBlock()->getTerminator(); }
  BasicBlock *block(std::string_view name);
  AllocaInst *slot(Type *t, std::string_view name);
  int bind(std::string name, Type *t, bool writable, Value *initial, Span span);
  int findLocal(std::string_view name) const;
  void enter() { scopes.emplace_back(); }
  void leave();
  void clean(std::size_t depth);
  void drop(Type *t, Value *address);
  void dropLocal(int index);
  void requireInit(ValueAt v, Span span);
  void setAlive(int local, bool alive);
  void registerDrop(int local, bool alive);
  void merge(const std::vector<std::vector<bool>> &edges, std::size_t size);
  void statement(const Ast &n);
  ValueAt sequence(const Ast &n, Type *expected, bool scoped);
  ValueAt name(const Ast &n);
  ValueAt numeric(const Ast &n, Type *expected);
  ValueAt member(const Ast &n);
  ValueAt index(const Ast &n);
  ValueAt unary(const Ast &n, Type *expected);
  ValueAt binary(const Ast &n, Type *expected);
  ValueAt compareText(const Ast &node, ValueAt left);
  ValueAt operate(std::string_view op, ValueAt left, ValueAt right, Span span, bool retainWidth = false);
  ValueAt chain(const Ast &n);
  ValueAt conditional(const Ast &n, Type *expected);
  ValueAt fallback(const Ast &n, Type *expected);
  ValueAt loop(const Ast &n);
  ValueAt call(const Ast &n, Type *expected);
  ValueAt atomicIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types);
  ValueAt bitIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types);
  ValueAt threadIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types);
  ValueAt syncIntrinsic(std::string_view op, const Ast &n);
  ValueAt standardIntrinsic(std::string_view key, const Ast &n, const std::vector<Ast> &types);
  ValueAt builtin(std::string_view key, const Ast &n, const std::vector<Ast> &types, Type *expected);
  ValueAt invoke(const Ast &n, const std::vector<Definition> &defs, const std::vector<Ast> &typeArgs,
                 std::optional<ValueAt> receiver = std::nullopt, Type *classType = nullptr,
                 bool delegation = false);
  ValueAt construct(const Ast &n, Type *t, const std::vector<Definition> &defs = {});
  ValueAt allocate(const Ast &n);
  ValueAt lambda(const Ast &n, Type *expected);
  void bindCapture(const Capture &capture, Value *environment, Span span);
  void freeAllocation(Value *address);
  void releaseBox(Value *address, Value *context);
  Value *textView(ValueAt value, Span span);
  void disposeTemporary(ValueAt value, Span span);
  ValueAt match(const Ast &n, Type *expected);
  void checkPattern(const Ast &pattern, ValueAt subject, BasicBlock *failure,
                    std::vector<std::pair<Ast, ValueAt>> &bindings);
  void assertCondition(Value *test, BasicBlock *failure);
  ValueAt castNumber(Type *to, const Ast &arg, Span span);
  Type *parseType(const Ast &n);
  Constants visibleConstants() const;
  Type *hint(const Ast &n);
  Type *branchHint(const Ast &n);
  ValueAt wrap(Type *t, Value *payload, bool present);
  ValueAt payload(ValueAt source, unsigned index, Type *type);
  ValueAt asPointer(ValueAt v, Span span);
  ValueAt voidValue();
  ValueAt neverValue();
};

class Program {
public:
  Program(LLVMContext &context, TargetMachine &target, const Options &options, Inputs &inputs);
  std::unique_ptr<Module> build();
  LLVMContext &context;
  std::unique_ptr<Module> module;
  const Options &options;
  Inputs &inputs;
  const std::deque<File> &files;
  std::size_t indexed = 0;
  void indexFiles();
  std::string root;
  std::deque<Type> types;
  Types canonical;
  std::map<std::string, std::vector<Definition>> definitions;
  std::map<std::string, GlobalEntry> globals;
  std::map<std::string, FunctionEntry> functions;
  std::set<std::string> cSymbols;
  unsigned lambdaCount = 0, matchCount = 0;
  std::map<std::string, Constant *> strings;
  std::map<Type *, Function *> dropFunctions, copyFunctions;
  std::map<std::pair<Type *, Type *>, GlobalVariable *> interfaceTables;
  std::map<std::pair<Type *, Type *>, Function *> allocatorReleaseFunctions;
  Type *allocationContext = nullptr;
  std::set<const Decl *> aliases;
  Type *voidTy, *neverTy, *boolTy, *charTy, *errorTy, *sizeTy, *textTy, *viewTy;
  std::unique_ptr<DIBuilder> debug;
  std::map<Type *, DIType *> debugTypes;
  DIType *debugType(Type *type);
  DICompileUnit *unit = nullptr;

  Type *add(Kind kind, llvm::Type *ir, std::string name, std::vector<Type *> parts = {});
  Type *integer(unsigned bits, bool sign);
  Type *aggregate(Kind kind, std::vector<Type *> parts, std::uint64_t count = 0, bool cAbi = false);
  Type *pointer(Type *t, bool readOnly);
  Type *slice(Type *t, bool readOnly);
  Type *box(Type *t);
  std::pair<Type *, Constant *> boxLayout(Type *element, Type *allocator);
  Function *boxRelease(Type *element, Type *allocator);
  Type *rawStorage(Type *element, Span span);
  Type *type(const Ast &n, const File &file, const Types &generics = {}, const Constants &values = {});
  Type *named(std::string name, const File &file, Span span, const Types &generics = {},
              const std::vector<Ast> &args = {}, const Constants &values = {});
  Type *nominal(Definition def, const std::vector<Type *> &args, bool complete = true,
                const Constants &values = {});
  std::pair<Type *, Constant *> genericValue(const Ast &arg, const File &file, Type *expected,
                                             const Types &types, const Constants &values);
  void fill(Type *t, const Types &substitutions);
  void validatePromotions(Type *type);
  void validateInterfaces(Type *type);
  GlobalVariable *interfaceTable(Type *type, Type *interface, Span span);
  void ensureSized(Type *t, Span span);
  bool copyable(Type *t);
  bool satisfies(Type *t, const Ast &constraint, const File &file, const Types &generics = {},
                 const Constants &values = {});
  bool movable(Type *t);
  bool transferable(Type *t, bool shared);
  bool drops(Type *t);
  bool customCopy(Type *t);
  Function *dropGlue(Type *t);
  void buildDropGlue(Type *t, Function *function);
  Function *copyGlue(Type *t, Span span);
  std::string attribute(const Decl &d, std::string_view key);
  std::vector<Definition> resolve(const File &f, std::string_view name, Span span);
  std::vector<Definition> methods(Type *t, std::string_view name, bool isStatic, bool keepHidden = false);
  std::string import(const File &f, std::string_view name);
  GlobalEntry &global(Definition d);
  std::vector<Type *> parameters(Definition def, const Types &types, Type *owner,
                                 const Constants &values = {});
  FunctionEntry &function(Definition d, Types substitutions = {}, Type *owner = nullptr,
                          Constants values = {});
  void lower(FunctionEntry &entry);
  Type *signature(FunctionEntry &entry);
  std::pair<Type *, Constant *> evaluate(const Ast &node, const File &file, Type *expected,
                                         const Types &generics, const Constants &values = {});
  Constant *bytes(const std::string &text);
  Value *standardStream(IRBuilder<> &builder, bool error);
  std::uint32_t errorCode(Definition def, std::string_view member);
  llvm::Type *storage(Type *t) { return t->ir->isVoidTy() ? StructType::get(context) : t->ir; }
  Triple targetTriple() const { return Triple(module->getTargetTriple()); }
  bool scalarCAbi() const;
  bool extendCNarrowInteger() const;
  bool hostedLinux64() const {
    auto target = targetTriple();
    return options.profile == "hosted" && target.isOSLinux() && target.isGNUEnvironment() &&
           (target.getArch() == Triple::x86_64 || target.getArch() == Triple::aarch64) && pointerBits() == 64;
  }
  bool heapAvailable() const { return options.profile == "hosted" && targetTriple().isOSLinux(); }
  unsigned pointerBits() const { return module->getDataLayout().getPointerSizeInBits(); }
  std::string key(Definition d) const {
    return d.file->module +
           (d.decl->name == "main" && !d.decl->external && !d.decl->exported ? ":" + d.file->identity : "") +
           "." + d.decl->name;
  }
  Types classTypes(Type *t) {
    Types out{{"Self", t}};
    std::size_t i = 0;
    for (const auto &g : t->def.decl->generics)
      if (!g.comptime) out[g.name] = t->parts[i++];
    return out;
  }
};

std::string path(const Ast &node);
std::string typeKey(std::string key, const std::vector<Type *> &parts);
bool hasComptimeParameters(const Decl &declaration);

} // namespace vqr::detail
