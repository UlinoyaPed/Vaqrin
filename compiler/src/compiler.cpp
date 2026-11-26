#include "compiler_internal.hpp"

#include <array>

namespace vqr::detail {

std::string path(const Ast &n) {
  if (n->kind == NodeKind::Name || n->kind == NodeKind::TypeName) return n->text;
  if (n->kind == NodeKind::Member && n->names.empty()) {
    auto base = path(n->children[0]);
    if (!base.empty()) return base + "." + n->text;
  }
  return {};
}

std::string typeKey(std::string key, const std::vector<Type *> &parts) {
  key += '<';
  for (auto *p : parts)
    key += p->name + ",";
  return key + '>';
}

Program::Program(LLVMContext &c, TargetMachine &target, const Options &o, Inputs &sources)
    : context(c), module(std::make_unique<Module>("Vaqrin", c)), options(o), inputs(sources), files(sources.files), root(sources.rootName) {
#if LLVM_VERSION_MAJOR >= 21
  module->setTargetTriple(target.getTargetTriple());
#else
  module->setTargetTriple(target.getTargetTriple().str());
#endif
  module->setDataLayout(target.createDataLayout());
  module->setSourceFileName(root);
  module->addModuleFlag(Module::Warning, "Vaqrin language version", MDString::get(c, "0.1.5"));
  voidTy = add(Kind::Void, llvm::Type::getVoidTy(c), "void");
  neverTy = add(Kind::Never, voidTy->ir, "never");
  boolTy = add(Kind::Bool, llvm::Type::getInt1Ty(c), "bool");
  charTy = add(Kind::Char, llvm::Type::getInt32Ty(c), "char");
  errorTy = add(Kind::Error, llvm::Type::getInt32Ty(c), "ErrorId");
  sizeTy = integer(pointerBits(), false);
  auto *ptr = PointerType::getUnqual(c);
  textTy = add(Kind::Text, StructType::get(c, {ptr, sizeTy->ir, ptr}), "string");
  viewTy = add(Kind::View, StructType::get(c, {ptr, sizeTy->ir}), "str");
  add(Kind::Float, llvm::Type::getFloatTy(c), "f32")->bits = 32;
  add(Kind::Float, llvm::Type::getDoubleTy(c), "f64")->bits = 64;
  if (options.debug) {
    module->addModuleFlag(Module::Warning, "Debug Info Version", DEBUG_METADATA_VERSION);
    if (target.getTargetTriple().isWindowsMSVCEnvironment())
      module->addModuleFlag(Module::Warning, "CodeView", 1);
    else
      module->addModuleFlag(Module::Warning, "Dwarf Version", 4);
    debug = std::make_unique<DIBuilder>(*module);
    auto *di =
        debug->createFile(std::filesystem::path(files.front().span.source->path).filename().string(), ".");
    unit = debug->createCompileUnit(0xb001, di, "vaqc 0.1.5", options.optimization != 0, "", 0);
  }
  indexFiles();
}

void Program::indexFiles() {
  while (indexed < files.size()) {
    const auto &source = files[indexed++];
    for (const auto &d : source.declarations) {
      auto &group = definitions[key({&source, &d})];
      if (d.name == "main" && !d.external && !d.exported && (d.kind != DeclKind::Function || !group.empty() || d.visible ||
          d.unsafe || d.comptime || !d.params.empty() || !d.generics.empty()))
        fail(d.span, "main must be a unique file-private function with no parameters");
      if (!group.empty() && (d.kind != DeclKind::Function || group.front().decl->kind != DeclKind::Function))
        fail(d.span, "duplicate declaration '" + d.name + "'");
      group.push_back({&source, &d});
      if (auto *hints = options.editorHints; hints && hints->workspace && !source.standard &&
          !d.external && !d.exported && d.name != "main" &&
          (d.kind == DeclKind::Function || d.kind == DeclKind::Global || d.kind == DeclKind::Class ||
           d.kind == DeclKind::Interface || d.kind == DeclKind::Alias || d.kind == DeclKind::Enum ||
           d.kind == DeclKind::Error))
        hints->index(d.nameSpan, d.nameSpan, true);
    }
  }
}

Type *Program::add(Kind kind, llvm::Type *ir, std::string name, std::vector<Type *> parts) {
  Type t{};
  t.kind = kind;
  t.ir = ir;
  t.name = std::move(name);
  t.parts = std::move(parts);
  types.push_back(std::move(t));
  auto *out = &types.back();
  canonical.emplace(out->name, out);
  return out;
}

Type *Program::integer(unsigned bits, bool sign) {
  auto key = std::string(sign ? "i" : "u") + std::to_string(bits);
  if (auto i = canonical.find(key); i != canonical.end()) return i->second;
  auto *t = add(Kind::Int, IntegerType::get(context, bits), key);
  t->bits = bits;
  t->sign = sign;
  return t;
}

Type *Program::pointer(Type *t, bool readOnly) {
  auto key = std::string(readOnly ? "*const " : "*") + t->name;
  if (auto i = canonical.find(key); i != canonical.end()) return i->second;
  auto *address = PointerType::getUnqual(context);
  llvm::Type *representation = address;
  if (t->kind == Kind::Interface) representation = StructType::get(context, {address, address});
  auto *p = add(Kind::Pointer, representation, key, {t});
  p->readOnly = readOnly;
  return p;
}

Type *Program::reference(Type *t, bool readOnly, ParameterMode mode) {
  auto key = std::string(mode == ParameterMode::In ? "in " : mode == ParameterMode::Mut ? "mut "
                         : readOnly ? "ref " : "ref mut ") + t->name;
  if (auto i = canonical.find(key); i != canonical.end()) return i->second;
  auto *ref = add(Kind::Reference, pointer(t, readOnly)->ir, key, {t});
  ref->readOnly = readOnly;
  ref->parameterMode = mode;
  return ref;
}

bool Program::hasReference(Type *t) {
  if (t->kind == Kind::Reference || t->scopedBorrow) return true;
  if (t->kind != Kind::Optional && t->kind != Kind::Result && t->kind != Kind::Array &&
      t->kind != Kind::Tuple)
    return false;
  return std::ranges::any_of(t->parts, [&](auto *part) { return hasReference(part); });
}

static bool returnBorrowSource(Type *t) {
  return t->kind == Kind::Reference && !scopedCallableInput(t) &&
         (t->parameterMode == ParameterMode::In || t->parameterMode == ParameterMode::Mut);
}

static bool mutableReferenceResult(Type *t) {
  if (t->kind == Kind::Reference) return !t->readOnly;
  if (t->kind != Kind::Optional && t->kind != Kind::Result && t->kind != Kind::Array &&
      t->kind != Kind::Tuple)
    return false;
  return std::ranges::any_of(t->parts, mutableReferenceResult);
}

bool Program::readonlyView(Type *t) const {
  if (!t) return false;
  if (t == viewTy) return true;
  if (t->kind != Kind::Slice || !t->readOnly) return false;
  auto kind = t->parts[0]->kind;
  return kind == Kind::Int || kind == Kind::Bool || kind == Kind::Char || kind == Kind::Float;
}

bool Program::optionalReadonlyView(Type *t) const {
  // One nullable layer preserves the same owner contract as its readonly payload.
  return t && t->kind == Kind::Optional && readonlyView(t->parts[0]);
}

bool Program::checkedViewResult(Type *t) const {
  return readonlyView(t) || optionalReadonlyView(t);
}

bool Program::scopedViewInput(Type *t) const {
  return t->kind == Kind::Reference && t->parameterMode == ParameterMode::In && readonlyView(t->parts[0]);
}

bool Program::canAdaptViewInput(Type *from, Type *target) const {
  if (!from) return false;
  if (from->kind == Kind::Reference) from = from->parts[0];
  return (target == viewTy && from == textTy) ||
         (target->kind == Kind::Slice && target->readOnly && from->kind == Kind::Slice &&
          !from->readOnly && from->parts == target->parts);
}

bool Program::viewContractSource(Type *parameter) const {
  if (parameter->kind != Kind::Reference || parameter->parameterMode != ParameterMode::In) return false;
  auto *source = parameter->parts[0];
  return source == textTy || source->kind == Kind::Array || source->kind == Kind::Class;
}

int Program::borrowParameter(Definition def, const std::vector<Type *> &params, Type *result, bool receiver) {
  const auto &source = def.decl->borrowSource;
  const bool view = !source.empty() && checkedViewResult(result);
  if (view && (def.decl->external || def.decl->comptime))
    fail(def.decl->span, "checked view source requires a runtime Vaqrin function", "E230");
  if (!hasReference(result) && !view) {
    if (!source.empty()) fail(def.decl->span, "borrow source requires a reference or checked readonly view result", "E230");
    return -1;
  }
  if (receiver && source == "self") {
    if (view && !def.decl->readOnly)
      fail(def.decl->span, "a checked view from self requires a const method", "E230");
    if (def.decl->readOnly && mutableReferenceResult(result))
      fail(def.decl->span, "mutable reference result requires a mutable source", "E230");
    return 0;
  }
  int found = -1;
  for (unsigned i = 0; i < def.decl->params.size(); ++i) {
    auto *param = params[i + receiver];
    if (view ? !viewContractSource(param) : !returnBorrowSource(param)) continue;
    if (!source.empty() && def.decl->params[i].name != source) continue;
    if (found >= 0)
      fail(def.decl->span, "reference result requires an explicit borrow source", "E230");
    found = i + receiver;
  }
  if (found < 0)
    fail(def.decl->span, "reference result requires a valid borrow source ('in', 'mut', or 'from self')", "E230");
  if (params[found]->readOnly && mutableReferenceResult(result))
    fail(def.decl->span, "mutable reference result requires a mutable source", "E230");
  return found;
}

std::size_t Program::functionBorrow(ArrayRef<Type *> parameters, Type *result, std::size_t explicitSource,
                                    Span span) {
  if (!hasReference(result)) {
    if (!explicitSource) return 0;
    if (!checkedViewResult(result) || explicitSource > parameters.size() ||
        !viewContractSource(parameters[explicitSource - 1]))
      fail(span, "checked readonly view requires a named 'in' source with owning storage", "E230");
    return explicitSource;
  }
  if (explicitSource != 0) {
    if (explicitSource > parameters.size() || !returnBorrowSource(parameters[explicitSource - 1]))
      fail(span, "borrow source must identify an 'in' or 'mut' parameter", "E230");
    if (parameters[explicitSource - 1]->readOnly && mutableReferenceResult(result))
      fail(span, "mutable reference result requires a mutable source", "E230");
    return explicitSource;
  }
  std::size_t source = 0;
  for (std::size_t i = 0; i < parameters.size(); ++i) {
    if (!returnBorrowSource(parameters[i])) continue;
    if (source) fail(span, "reference result requires 'from name' to identify its source", "E230");
    source = i + 1;
  }
  if (!source) fail(span, "reference result requires an 'in' or 'mut' parameter", "E230");
  if (parameters[source - 1]->readOnly && mutableReferenceResult(result))
    fail(span, "mutable reference result requires a mutable source", "E230");
  return source;
}

Type *Program::box(Type *t) {
  if (hasReference(t)) fail({}, "references cannot be stored in Box allocations", "E230");
  auto key = '^' + t->name;
  if (auto i = canonical.find(key); i != canonical.end()) return i->second;
  auto *address = PointerType::getUnqual(context);
  if (!allocationContext)
    allocationContext = add(Kind::AllocationContext, StructType::get(context, {address, address, address}),
                            "<allocation context>");
  return add(Kind::Box, StructType::get(context, {address, allocationContext->ir}), key, {t});
}

std::pair<Type *, Constant *> Program::boxLayout(Type *element, Type *allocator) {
  auto allocation = methods(allocator, "alloc", false).front();
  auto *type = this->type(allocation.decl->params.front().type, *allocation.file);
  ensureSized(type, allocation.decl->span);
  const auto &layout = module->getDataLayout();
  auto size = std::max<std::uint64_t>(1, layout.getTypeAllocSize(storage(element)).getFixedValue());
  auto alignment = layout.getABITypeAlign(storage(element)).value();
  return {type, ConstantStruct::get(cast<StructType>(type->ir),
                                   {ConstantInt::get(sizeTy->ir, size), ConstantInt::get(sizeTy->ir, alignment)})};
}

Function *Program::boxRelease(Type *element, Type *allocator) {
  auto key = std::pair{element, allocator};
  if (auto cached = allocatorReleaseFunctions.find(key); cached != allocatorReleaseFunctions.end())
    return cached->second;
  auto *address = PointerType::getUnqual(context);
  auto *fn = Function::Create(FunctionType::get(voidTy->ir, {address, address, address}, false),
                              GlobalValue::InternalLinkage, "vqr.box.release", *module);
  fn->setDoesNotThrow();
  allocatorReleaseFunctions.emplace(key, fn);
  auto [layoutType, layout] = boxLayout(element, allocator);
  auto method = methods(allocator, "free", false).front();
  auto index = static_cast<unsigned>(method.decl - allocator->def.decl->members.data());
  IRBuilder<> builder(BasicBlock::Create(context, "entry", fn));
  auto *function = builder.CreateLoad(address, builder.CreateConstGEP1_32(address, fn->getArg(1), index));
  auto *signature = FunctionType::get(voidTy->ir, {address, address, layoutType->ir}, false);
  builder.CreateCall(signature, function, {fn->getArg(0), fn->getArg(2), layout})->setDoesNotThrow();
  builder.CreateRetVoid();
  return fn;
}

Type *Program::slice(Type *t, bool readOnly) {
  auto key = std::string(readOnly ? "[]const " : "[]") + t->name;
  if (auto i = canonical.find(key); i != canonical.end()) return i->second;
  auto *s =
      add(Kind::Slice, StructType::get(context, {PointerType::getUnqual(context), sizeTy->ir}), key, {t});
  s->readOnly = readOnly;
  return s;
}

Type *Program::rawStorage(Type *element, Span span) {
  if (hasReference(element)) fail(span, "raw storage cannot contain safe references", "E230");
  ensureSized(element, span);
  if (element == neverTy) fail(span, "never has no storage");
  auto key = "<raw storage " + element->name + ">";
  if (auto item = canonical.find(key); item != canonical.end()) return item->second;
  std::array layout{storage(element)};
  return add(Kind::RawStorage, StructType::get(context, layout), key, {element});
}

Type *Program::aggregate(Kind kind, std::vector<Type *> parts, std::uint64_t count, bool cAbi,
                         std::vector<unsigned> moveParameters) {
  std::string key;
  switch (kind) {
  case Kind::Array:
    key = '[' + std::to_string(count) + ']' + parts[0]->name;
    break;
  case Kind::Optional:
    key = '?' + parts[0]->name;
    break;
  case Kind::Result:
    key = '!' + parts[0]->name;
    break;
  case Kind::Function:
    key = typeKey(cAbi ? "extern C func" : "func", parts);
    if (count != 0) key += " borrow(" + std::to_string(count) + ')';
    for (auto index : moveParameters) key += " move(" + std::to_string(index) + ')';
    break;
  default:
    key = typeKey("tuple", parts);
    break;
  }
  if (auto i = canonical.find(key); i != canonical.end()) return i->second;
  llvm::Type *ir;
  if (kind == Kind::Array)
    ir = ArrayType::get(storage(parts[0]), count);
  else if (kind == Kind::Optional)
    ir = StructType::get(context, {boolTy->ir, storage(parts[0])});
  else if (kind == Kind::Result)
    ir = StructType::get(context, {errorTy->ir, storage(parts[0])});
  else if (kind == Kind::Function)
    ir = PointerType::getUnqual(context);
  else {
    std::vector<llvm::Type *> fields;
    for (auto *p : parts)
      fields.push_back(storage(p));
    ir = StructType::get(context, fields);
  }
  auto *out = add(kind, ir, key, std::move(parts));
  out->count = count;
  out->cAbi = cAbi;
  out->moveParameters = std::move(moveParameters);
  return out;
}

std::string Program::import(const File &file, std::string_view name) {
  return inputs.qualifier(file, name);
}

std::vector<Definition> Program::resolve(const File &f, std::string_view name, Span span) {
  auto dot = name.find('.');
  auto symbol = dot == name.npos ? name : name.substr(dot + 1);
  if (symbol == "main" && dot != name.npos) return {};
  auto package = dot == name.npos ? inputs.scope(f, name, span) : inputs.qualifier(f, name.substr(0, dot));
  indexFiles();
  if (package.empty()) return {};
  auto id = package + (symbol == "main" ? ":" + f.identity : "") + "." + std::string(symbol);
  auto i = definitions.find(id);
  if (i == definitions.end()) return {};
  auto out = i->second;
  if (package != f.module) {
    std::erase_if(out, [&](auto d) { return !d.decl->visible; });
    if (out.empty()) fail(span, "declaration '" + std::string(name) + "' is private");
  }
  return out;
}

bool Program::scalarCAbi() const {
  auto triple = targetTriple();
  return triple.isX86() || triple.isARM() || triple.isThumb() || triple.isAArch64();
}

bool Program::extendCNarrowInteger() const {
  auto triple = targetTriple();
  return (triple.isX86() && !triple.isOSWindows()) || triple.isARM() || triple.isThumb() ||
         (triple.isAArch64() && triple.isOSDarwin());
}

std::pair<Type *, Constant *> Program::genericValue(const Ast &arg, const File &file, Type *expected,
                                                    const Types &types, const Constants &values) {
  if (expected->kind != Kind::Int && expected->kind != Kind::Bool && expected->kind != Kind::Float &&
      expected->kind != Kind::Char)
    fail(arg->span, "comptime generic parameters require scalar types", "E290");
  auto expression = arg;
  if (arg->kind == NodeKind::TypeName && arg->children.empty()) {
    expression = std::make_shared<Node>(*arg);
    expression->kind = NodeKind::Name;
  }
  return evaluate(expression, file, expected, types, values);
}

Type *Program::named(std::string name, const File &file, Span span, const Types &generics,
                     const std::vector<Ast> &args, const Constants &values, Span occurrence) {
  if (auto i = generics.find(name); i != generics.end() && args.empty()) return i->second;
  auto defs = resolve(file, name, span);
  if (!defs.empty()) {
    auto d = defs.front();
    const auto kind = d.decl->kind;
    if (kind != DeclKind::Alias && kind != DeclKind::Class && kind != DeclKind::Enum &&
        kind != DeclKind::Interface)
      fail(span, "'" + name + "' does not name a type");
    if (auto *hints = options.editorHints; hints && hints->workspace && !file.standard && !d.file->standard)
      hints->index(occurrence, d.decl->nameSpan, false);
    if (args.size() != d.decl->generics.size()) fail(span, "wrong number of type arguments for " + name);
    Types substitutions;
    Constants constants;
    std::vector<Type *> typeArgs;
    for (std::size_t i = 0; i < args.size(); ++i) {
      const auto &parameter = d.decl->generics[i];
      if (parameter.comptime) {
        auto *expected = type(parameter.type, *d.file, substitutions, constants);
        constants.emplace(parameter.name, genericValue(args[i], file, expected, generics, values));
      } else {
        auto *arg = type(args[i], file, generics, values);
          substitutions.emplace(parameter.name, arg);
        typeArgs.push_back(arg);
      }
    }
    if (kind == DeclKind::Alias || kind == DeclKind::Interface)
      for (const auto &parameter : d.decl->generics)
        if (!parameter.comptime && parameter.type &&
            !satisfies(substitutions.at(parameter.name), parameter.type, *d.file, substitutions, constants))
          fail(span, "type arguments do not satisfy declared constraints");
    if (kind != DeclKind::Alias) return nominal(d, typeArgs, false, constants);
    if (!aliases.insert(d.decl).second) fail(span, "cyclic type alias '" + name + "'");
    auto *t = type(d.decl->type, *d.file, substitutions, constants);
    aliases.erase(d.decl);
    return t;
  }
  if (name == "__vqr_RawStorage") {
    if (!file.standard) fail(span, "raw storage type is private to the Vaqrin standard library");
    if (args.size() != 1) fail(span, "raw storage requires one element type");
    return rawStorage(type(args[0], file, generics, values), span);
  }
  if (name == "Box" && args.size() == 1) return box(type(args[0], file, generics, values));
  if (!args.empty()) fail(span, "unsupported generic type '" + name + "'", "E290");
  if (name == "usize") return sizeTy;
  if (name == "isize") return integer(pointerBits(), true);
  if (name == "c_int") return integer(32, true);
  if (name == "c_char") {
    auto triple = targetTriple();
    if (!scalarCAbi()) fail(span, "this target has no C scalar type profile", "E290");
    bool arm = triple.isARM() || triple.isThumb() || triple.isAArch64();
    return integer(8, !arm || triple.isOSDarwin() || triple.isOSWindows());
  }
  if (auto i = canonical.find(name); i != canonical.end()) return i->second;
  if (name.size() > 1 && (name[0] == 'i' || name[0] == 'u') && name[1] >= '0' && name[1] <= '9') {
    unsigned width = 0;
    auto result = std::from_chars(name.data() + 1, name.data() + name.size(), width);
    if (result.ec == std::errc{} && result.ptr == name.data() + name.size() && width >= 1 && width <= 128)
      return integer(width, name[0] == 'i');
    fail(span, "supported integer widths are 1 through 128");
  }
  fail(span, "unknown type '" + name + "'");
}

Type *Program::type(const Ast &n, const File &file, const Types &generics, const Constants &values) {
  CompileTimings::Scope timer(options.compileTimings, CompileTimings::Types);
  if (n->kind == NodeKind::TypeName || n->kind == NodeKind::Name)
    return named(n->text, file, n->span, generics, n->children, values,
                 n->bindingSpans.empty() ? n->span : n->bindingSpans.front());
  if (n->kind == NodeKind::TypeArray) {
    if (!n->children[0]) fail(n->span, "inferred array length only applies to constructors");
    auto [integer, c] = evaluate(n->children[0], file, nullptr, generics, values);
    if (integer->kind != Kind::Int) fail(n->span, "array length must be an integer");
    auto len = cast<ConstantInt>(c)->getValue();
    if ((integer->sign && len.isNegative()) || len.getActiveBits() > pointerBits() - 1)
      fail(n->span, "array length exceeds the target object size");
    auto *element = type(n->children[1], file, generics, values);
    ensureSized(element, n->span);
    if (hasReference(element) && (!copyable(element) || drops(element)))
      fail(n->span, "arrays of references require copyable, non-owning elements", "E230");
    auto size = module->getDataLayout().getTypeAllocSize(storage(element)).getFixedValue();
    auto limit = (std::uint64_t{1} << (pointerBits() - 1)) - 1;
    if (size && len.getZExtValue() > limit / size)
      fail(n->span, "array layout exceeds the target object size");
    return aggregate(Kind::Array, {element}, len.getZExtValue());
  }
  if (n->kind == NodeKind::TypeTuple || n->kind == NodeKind::TypeFunction) {
    std::vector<Type *> parts;
    std::vector<unsigned> moveParameters;
    for (const auto &a : n->children) {
      auto mode = parameterMode(a);
      if (mode == ParameterMode::Move) moveParameters.push_back(parts.size());
      auto *part = type(a, file, generics, values);
      if (scopedCallableInput(part) && mode != ParameterMode::Mut)
        fail(a->span, "a scoped callable loan requires an explicit 'mut' parameter", "E230");
      parts.push_back(part);
      if (n->kind == NodeKind::TypeFunction && mode == ParameterMode::Value)
        pendingValueParameters.emplace_back(part, a->span);
    }
    if (n->type) {
      parts.push_back(type(n->type, file, generics, values));
      const bool c = n->text == "C";
      if (c && (!moveParameters.empty() || !n->names.empty() || std::ranges::any_of(parts, [&](auto *part) {
            return hasReference(part) || (part->kind == Kind::Pointer && !part->ir->isPointerTy());
          })))
        fail(n->span, "references and interface views cannot use the C ABI");
      std::size_t source = 0;
      if (!n->names.empty()) {
        const auto &text = n->names.front();
        auto end = text.data() + text.size();
        auto parsed = std::from_chars(text.data(), end, source);
        if (parsed.ec != std::errc{} || parsed.ptr != end || source == 0)
          fail(n->bindingSpans.front(), "borrow source must be a 1-based parameter index", "E230");
      }
      source = functionBorrow(ArrayRef<Type *>(parts).drop_back(), parts.back(), source, n->span);
      return aggregate(Kind::Function, parts, source, c, std::move(moveParameters));
    }
    for (auto *t : parts)
      ensureSized(t, n->span);
    return aggregate(Kind::Tuple, parts);
  }
  if (n->kind == NodeKind::TypePrefix) {
    auto *inner = type(n->children[0], file, generics, values);
    auto mode = parameterMode(n);
    if (mode == ParameterMode::Move) {
      ensureSized(inner, n->span);
      if (!movable(inner) || copyable(inner) || hasReference(inner))
        fail(n->span, "move parameter requires an owning, movable, non-Copyable type", "E230");
      return inner;
    }
    // Mutating a scoped callable may only borrow its entire environment
    // for immediate invocation/reborrowing. It is not a storable reference.
    if (mode == ParameterMode::Mut && inner->kind == Kind::Closure && inner->scopedBorrow)
      return reference(inner, false, mode);
    // A call-scoped read-only borrow of a copyable reference aggregate
    // preserves its existing provenance; pointers and exclusive borrows do not.
    if (n->text != "?" && n->text != "!" && hasReference(inner) &&
        !(mode == ParameterMode::In && inner->kind != Kind::Reference && copyable(inner) && !drops(inner)))
      fail(n->span, "indirect storage of a reference-bearing type is not supported", "E230");
    if (n->text == "*" || n->text == "*const") return pointer(inner, n->text == "*const");
    if (n->text == "ref" || n->text == "ref mut" ||
        mode == ParameterMode::In || mode == ParameterMode::Mut) {
      if (inner == voidTy || inner == neverTy) fail(n->span, "reference requires an object type");
      return reference(inner, n->text != "ref mut" && mode != ParameterMode::Mut, mode);
    }
    if (n->text == "^") return box(inner);
    if (n->text == "[]" || n->text == "[]const") return slice(inner, n->text == "[]const");
    if (n->text == "?" || n->text == "!") {
      ensureSized(inner, n->span);
      return aggregate(n->text == "?" ? Kind::Optional : Kind::Result, {inner});
    }
    fail(n->span, "unsupported type constructor '" + n->text + "'", "E290");
  }
  fail(n->span, "expected a type argument");
}

std::string Program::attribute(const Decl &d, std::string_view key) {
  for (const auto &a : d.attributes)
    if (a.name == key) return a.value;
  return {};
}

Type *Program::nominal(Definition d, const std::vector<Type *> &args, bool complete,
                        const Constants &values) {
  CompileTimings::Scope timer(options.compileTimings, CompileTimings::Types);
  if (std::ranges::any_of(args, [&](auto *argument) { return hasReference(argument); }))
    fail(d.decl->span, "nominal types cannot store reference type arguments", "E230");
  if (args.size() + values.size() != d.decl->generics.size())
    fail(d.decl->span, "wrong number of type arguments for " + d.decl->name);
  auto id = args.empty() ? key(d) : typeKey(key(d), args);
  for (const auto &[name, value] : values) {
    std::string operand;
    raw_string_ostream output(operand);
    value.second->printAsOperand(output, false);
    id += "." + name + "=" + value.first->name + ":" + operand;
  }
  Type *t;
  if (auto i = canonical.find(id); i != canonical.end())
    t = i->second;
  else {
    auto kind = d.decl->kind == DeclKind::Enum ? Kind::Enum :
                d.decl->kind == DeclKind::Interface ? Kind::Interface : Kind::Class;
    auto *layout = kind == Kind::Interface ? voidTy->ir : StructType::create(context, id);
    t = add(kind, layout, id, args);
    t->def = d;
    t->scopedBorrow = (kind == Kind::Class || kind == Kind::Enum) &&
                      attribute(*d.decl, "borrowed") == "scoped";
    t->comptimeArguments = values;
  }
  if (complete) ensureSized(t, d.decl->span);
  return t;
}

void Program::ensureSized(Type *t, Span span) {
  if (t->kind == Kind::Interface) fail(span, "an interface has no by-value representation");
  if ((t->kind != Kind::Class && t->kind != Kind::Enum) || t->filled) return;
  if (t->filling) fail(span, "recursive by-value layout of '" + t->name + "'");
  t->filling = true;
  ++layoutDepth;
  fill(t, classTypes(t));
  t->filling = false;
  t->filled = true;
  if (!t->def.decl->generics.empty()) layoutConstraints.push_back(t);
  if (t->kind == Kind::Class) {
    validatePromotions(t);
    validateInterfaces(t);
  }
  --layoutDepth;
  if (layoutDepth || checkingLayoutConstraints) return;
  checkingLayoutConstraints = true;
  while (!layoutConstraints.empty()) {
    auto *instance = layoutConstraints.back();
    layoutConstraints.pop_back();
    const auto substitutions = classTypes(instance);
    const auto &definition = instance->def;
    for (const auto &g : definition.decl->generics)
      if (!g.comptime && g.type &&
          !satisfies(substitutions.at(g.name), g.type, *definition.file, substitutions,
                     instance->comptimeArguments))
        fail(g.span, "type argument '" + substitutions.at(g.name)->name +
                         "' does not satisfy " + g.type->text);
  }
  checkingLayoutConstraints = false;
}

static bool containsLayoutMember(const std::vector<Decl> &members) {
  return std::ranges::any_of(members, [](const Decl &member) {
    if (member.kind != DeclKind::Conditional) return member.kind != DeclKind::Function;
    return containsLayoutMember(member.members) || containsLayoutMember(member.otherwise);
  });
}

void Program::fill(Type *t, const Types &substitutions) {
  const auto &d = *t->def.decl;
  std::set<std::string> names;
  std::vector<llvm::Type *> layout;
  auto collect = [&](auto &&self, const std::vector<Decl> &members) -> void {
    for (const auto &member : members) {
      if (member.kind == DeclKind::Conditional) {
        // Conditional methods do not affect the class's storage. Resolve them
        // only when needed, after pointer-backed recursive layout can finish.
        if (!t->scopedBorrow && !containsLayoutMember(member.members) &&
            !containsLayoutMember(member.otherwise)) {
          t->members.push_back(&member);
          continue;
        }
        auto [_, condition] = evaluate(member.body, *t->def.file, boolTy, substitutions,
                                        t->comptimeArguments);
        self(self, cast<ConstantInt>(condition)->isOne() ? member.members : member.otherwise);
      } else {
        t->members.push_back(&member);
      }
    }
  };
  collect(collect, d.members);
  bool referenceField = false;
  auto validateBorrowField = [&](Type *field, Span span) {
    if (field->kind == Kind::Reference && field->readOnly)
      referenceField = true;
    else if (field->kind != Kind::Int && field->kind != Kind::Bool &&
             field->kind != Kind::Char && field->kind != Kind::Float)
      fail(span, "scoped borrow fields require a read-only reference or scalar", "E230");
  };
  if (t->kind == Kind::Class) {
    t->cAbi = attribute(d, "repr") == "C";
    if (t->scopedBorrow && std::ranges::any_of(d.attributes, [](const auto &a) { return a.name != "borrowed"; }))
      fail(d.span, "a scoped borrow record cannot declare other class attributes", "E230");
    for (const auto *member : t->members) {
      const auto &m = *member;
      if (t->scopedBorrow && m.kind == DeclKind::Function) {
        if ((!m.associated && !m.readOnly) || m.name == "init" || m.name == "deinit" ||
            m.name == "copy" || m.name == "clone")
          fail(m.span, "a scoped borrow record can only declare const methods and static factories", "E230");
      } else if (t->scopedBorrow && (m.kind != DeclKind::Field || m.embed)) {
        fail(m.span, "a scoped borrow record can only declare fields, const methods and static factories", "E230");
      }
      if (m.kind != DeclKind::Field) continue;
      if (!names.insert(m.name).second) fail(m.span, "duplicate field '" + m.name + "'");
      auto *field = m.type ? type(m.type, *t->def.file, substitutions, t->comptimeArguments)
                           : evaluate(m.body, *t->def.file, nullptr, substitutions, t->comptimeArguments).first;
      ensureSized(field, m.span);
      if (t->scopedBorrow) {
        if (hasReference(field) && m.body)
          fail(m.span, "scoped reference fields require an explicit initializer", "E230");
        validateBorrowField(field, m.span);
      } else if (hasReference(field)) {
        fail(m.span, "reference fields require #borrowed: scoped", "E230");
      }
      if (auto *hints = options.editorHints; hints && hints->workspace && !t->def.file->standard)
        hints->index(m.nameSpan, m.nameSpan, true);
      t->fields.push_back({&m, field, static_cast<unsigned>(layout.size())});
      layout.push_back(storage(field));
    }
    auto copy = attribute(d, "copy");
    if (copy == "enable" && !methods(t, "deinit", false).empty() && methods(t, "copy", false).empty())
      fail(d.span, "copy-enabled class with deinit requires a copy method");
  } else {
    auto repr = attribute(d, "repr");
    auto *tag = repr.empty() ? integer(32, false) : named(repr, *t->def.file, d.span);
    if (tag->kind != Kind::Int) fail(d.span, "enum representation must be an integer");
    t->bits = tag->bits;
    t->sign = tag->sign;
    layout.push_back(tag->ir);
    APInt next(tag->bits + 2, 0);
    for (const auto &m : d.members) {
      if (!names.insert(m.name).second) fail(m.span, "duplicate enum member '" + m.name + "'");
      if (m.body) {
        auto [k, c] = evaluate(m.body, *t->def.file, nullptr, substitutions, t->comptimeArguments);
        if (k->kind != Kind::Int) fail(m.span, "enum tag must be an integer");
        auto v = cast<ConstantInt>(c)->getValue();
        next = k->sign ? v.sext(v.getBitWidth() + 1) : v.zext(v.getBitWidth() + 1);
      }
      if (!fits(next, tag->bits, tag->sign)) fail(m.span, "enum tag out of range");
      auto value = next.sextOrTrunc(tag->bits);
      if (std::ranges::any_of(t->variants, [&](const auto &v) { return v.tag == value; }))
        fail(m.span, "duplicate enum tag");
      Variant v{m.name, value, {}, static_cast<unsigned>(layout.size())};
      for (const auto &a : m.params) {
        auto *field = type(a.type, *t->def.file, substitutions, t->comptimeArguments);
        ensureSized(field, a.span);
        if (t->scopedBorrow) validateBorrowField(field, a.span);
        else if (hasReference(field)) fail(a.span, "reference fields require #borrowed: scoped", "E230");
        v.fields.push_back(field);
        layout.push_back(storage(field));
      }
      t->variants.push_back(std::move(v));
      next = next.sext(std::max(tag->bits + 2, next.getBitWidth())) + 1;
    }
  }
  if (t->scopedBorrow && !referenceField)
    fail(d.span, "a scoped borrow type requires a read-only reference field", "E230");
  cast<StructType>(t->ir)->setBody(layout);
}

std::vector<const Decl *> Program::resolvedMembers(Type *t, std::string_view name,
                                                    std::optional<bool> associated) {
  auto matches = [&](const Decl &member) {
    return name.empty() || (member.kind == DeclKind::Function && member.name == name &&
                            (!associated || member.associated == *associated));
  };
  auto mayMatch = [&](auto &&self, const Decl &member) -> bool {
    if (member.kind != DeclKind::Conditional) return matches(member);
    auto search = [&](const Decl &branch) { return self(self, branch); };
    return std::ranges::any_of(member.members, search) || std::ranges::any_of(member.otherwise, search);
  };
  std::vector<const Decl *> out;
  auto collect = [&](auto &&self, const Decl &member) -> void {
    if (member.kind != DeclKind::Conditional) {
      if (matches(member)) out.push_back(&member);
      return;
    }
    if (!mayMatch(mayMatch, member)) return;
    // Compile-time evaluation may itself look up members. Reject an actual
    // dependency cycle rather than recursing in the compiler.
    using Active = std::pair<Type *, const Decl *>;
    static thread_local std::vector<Active> active;
    Active key{t, &member};
    if (std::ranges::find(active, key) != active.end())
      fail(member.span, "cycle in compile-time method selection", "E220");
    active.push_back(key);
    struct PopActive {
      std::vector<Active> &entries;
      ~PopActive() { entries.pop_back(); }
    } guard{active};
    auto [_, condition] =
        evaluate(member.body, *t->def.file, boolTy, classTypes(t), t->comptimeArguments);
    for (const auto &child : cast<ConstantInt>(condition)->isOne() ? member.members : member.otherwise)
      self(self, child);
  };
  for (const auto *member : t->members) collect(collect, *member);
  return out;
}

std::vector<Definition> Program::methods(Type *t, std::string_view name, bool isStatic, bool keepHidden) {
  std::vector<Definition> out;
  if (t->kind == Kind::Interface && !isStatic) {
    for (const auto &method : t->def.decl->members)
      if (method.name == name) out.push_back({t->def.file, &method});
    return out;
  }
  if (t->kind != Kind::Class) return out;
  if (!t->filling) ensureSized(t, t->def.decl->span);
  for (const auto *member : resolvedMembers(t, name, isStatic))
    if (member->kind == DeclKind::Function && member->name == name && member->associated == isStatic)
      out.push_back({t->def.file, member});
  if (isStatic || name == "init" || name == "deinit" || name == "copy" || name == "clone")
    return out;
  for (const auto &field : t->fields) {
    if (!field.decl->embed) continue;
    for (auto method : methods(field.type, name, false)) {
      if (!method.decl->visible || !method.publicPath) continue;
      if (!method.declaringType) method.declaringType = field.type;
      if (!keepHidden && method.decl->generics.empty() && !hasComptimeParameters(*method.decl)) {
        auto hidden = std::ranges::any_of(out, [&](const auto &local) {
          if (!local.receiverPath.empty() || local.decl->readOnly != method.decl->readOnly ||
              !local.decl->generics.empty() || hasComptimeParameters(*local.decl) ||
              local.decl->params.size() != method.decl->params.size())
            return false;
          auto *actual = method.declaringType;
          return parameters(local, classTypes(t), nullptr, t->comptimeArguments) ==
                 parameters(method, classTypes(actual), nullptr, actual->comptimeArguments);
        });
        if (hidden) continue;
      }
      method.receiverPath.insert(method.receiverPath.begin(), field.index);
      method.publicPath = field.decl->visible;
      out.push_back(std::move(method));
    }
  }
  return out;
}

void Program::validatePromotions(Type *type) {
  if (std::ranges::none_of(type->fields, [](const auto &field) { return field.decl->embed; })) return;
  auto substitutions = classTypes(type);
  for (const auto *own : resolvedMembers(type)) {
    if (own->kind != DeclKind::Function || own->associated || !own->generics.empty() ||
        hasComptimeParameters(*own))
      continue;
    auto params = parameters({type->def.file, own}, substitutions, nullptr, type->comptimeArguments);
    for (const auto &method : methods(type, own->name, false, true)) {
      const auto &member = *method.decl;
      if (!method.declaringType || member.readOnly != own->readOnly || !member.generics.empty() ||
          hasComptimeParameters(member) || member.params.size() != own->params.size())
        continue;
      auto *nested = method.declaringType;
      auto nestedTypes = classTypes(nested);
      if (params != parameters(method, nestedTypes, nullptr, nested->comptimeArguments)) continue;
      auto *local = this->type(own->type, *type->def.file, substitutions, type->comptimeArguments);
      auto *inherited = this->type(member.type, *method.file, nestedTypes, nested->comptimeArguments);
      if (local != inherited) fail(own->span, "method result conflicts with an embedded method");
    }
  }
}

void Program::validateInterfaces(Type *type) {
  const auto &declaration = *type->def.decl;
  auto substitutions = classTypes(type);
  for (const auto &attribute : declaration.attributes) {
    if (attribute.name != "implements") continue;
    for (const auto &syntax : attribute.types) {
      auto *interface = this->type(syntax, *type->def.file, substitutions, type->comptimeArguments);
      if (interface->kind != Kind::Interface) fail(syntax->span, "#implements requires an interface");
      auto [entry, inserted] = type->conformances.try_emplace(interface);
      if (!inserted) fail(syntax->span, "duplicate interface in #implements");
      auto expectedTypes = classTypes(interface);
      expectedTypes["Self"] = type;
      for (const auto &requirement : interface->def.decl->members) {
        bool generic = !requirement.generics.empty() || hasComptimeParameters(requirement);
        Definition wanted{interface->def.file, &requirement};
        std::vector<Type *> expected;
        Type *result = nullptr;
        if (!generic) {
          expected = parameters(wanted, expectedTypes, nullptr, interface->comptimeArguments);
          result = this->type(requirement.type, *wanted.file, expectedTypes, interface->comptimeArguments);
        }
        std::vector<Definition> matches;
        for (auto found : methods(type, requirement.name, false)) {
          const auto &method = *found.decl;
          if (!method.visible || !found.publicPath || method.unsafe != requirement.unsafe ||
              method.readOnly != requirement.readOnly || method.params.size() != requirement.params.size() ||
              method.comptime != requirement.comptime)
            continue;
          if (!std::ranges::equal(method.params, requirement.params, {},
                                 [](const auto &param) { return parameterMode(param.type); },
                                 [](const auto &param) { return parameterMode(param.type); }))
            continue;
          auto *owner = found.declaringType ? found.declaringType : type;
          auto methodTypes = classTypes(owner);
          if (generic) {
            if (matchesGenericInterface(wanted, expectedTypes, interface->comptimeArguments,
                                        found, methodTypes, owner->comptimeArguments))
              matches.push_back(std::move(found));
            continue;
          }
          if (!method.generics.empty() || hasComptimeParameters(method)) continue;
          auto actual = parameters(found, methodTypes, nullptr, owner->comptimeArguments);
          if (actual != expected) continue;
          if (this->type(method.type, *found.file, methodTypes, owner->comptimeArguments) != result) continue;
          if (hasReference(result) || checkedViewResult(result)) {
            actual.insert(actual.begin(), pointer(owner, method.readOnly));
            auto expectedReceiver = expected;
            expectedReceiver.insert(expectedReceiver.begin(), pointer(interface, requirement.readOnly));
            if (borrowParameter(found, actual, result, true) !=
                borrowParameter(wanted, expectedReceiver, result, true))
              continue;
          }
          matches.push_back(std::move(found));
        }
        auto own = std::ranges::find_if(matches, [](const auto &method) { return method.receiverPath.empty(); });
        if (own != matches.end() && matches.size() > 1)
          std::erase_if(matches, [](const auto &method) { return !method.receiverPath.empty(); });
        if (matches.size() != 1)
          fail(syntax->span, "'" + type->name + "' must have one public implementation of " +
                                 interface->name + "." + requirement.name);
        entry->second.push_back(std::move(matches.front()));
      }
    }
  }
}

GlobalVariable *Program::interfaceTable(Type *type, Type *interface, Span span) {
  ensureSized(type, span);
  auto conformance = type->conformances.find(interface);
  if (conformance == type->conformances.end())
    fail(span, "'" + type->name + "' does not implement '" + interface->name + "'");
  auto id = std::pair{type, interface};
  if (auto cached = interfaceTables.find(id); cached != interfaceTables.end()) return cached->second;
  const auto &methods = conformance->second;
  auto *layout = ArrayType::get(PointerType::getUnqual(context), methods.size());
  auto *table = new GlobalVariable(*module, layout, true, GlobalValue::PrivateLinkage,
                                   Constant::getNullValue(layout), "vqr.interface");
  interfaceTables.emplace(id, table);
  std::vector<Constant *> functions;
  for (const auto &method : methods) {
    if (!method.decl->generics.empty() || hasComptimeParameters(*method.decl)) {
      functions.push_back(ConstantPointerNull::get(PointerType::getUnqual(context)));
      continue;
    }
    auto *owner = method.declaringType ? method.declaringType : type;
    auto &entry = function(method, classTypes(owner), owner);
    functions.push_back(interfaceThunk(type, method, entry));
    lower(entry);
  }
  table->setInitializer(ConstantArray::get(layout, functions));
  return table;
}

Function *Program::interfaceThunk(Type *type, const Definition &method, FunctionEntry &entry) {
  if (method.receiverPath.empty()) return entry.ir;
  auto *thunk = Function::Create(entry.ir->getFunctionType(), GlobalValue::InternalLinkage,
                                 "vqr.interface.receiver", *module);
  thunk->setDoesNotThrow();
  IRBuilder<> builder(BasicBlock::Create(context, "entry", thunk));
  Type *current = type;
  Value *address = thunk->getArg(0);
  for (auto field : method.receiverPath) {
    address = builder.CreateStructGEP(current->ir, address, field);
    current = current->fields[field].type;
  }
  std::vector<Value *> parameters{address};
  for (unsigned i = 1; i < thunk->arg_size(); ++i) parameters.push_back(thunk->getArg(i));
  auto *call = builder.CreateCall(entry.ir, parameters);
  if (entry.result == neverTy) builder.CreateUnreachable();
  else if (entry.result == voidTy) builder.CreateRetVoid();
  else builder.CreateRet(call);
  return thunk;
}

bool Program::satisfies(Type *t, const Ast &constraint, const File &file, const Types &generics,
                        const Constants &values) {
  ensureSized(t, constraint->span);
  for (auto item = constraint; item; item = item->tail) {
    const auto &name = item->text;
    if (name == "Copyable" || name == "Movable" || name == "Sendable" || name == "Shareable") {
      auto result = name == "Copyable" ? copyable(t) : name == "Movable" ? movable(t)
                                                                  : transferable(t, name == "Shareable");
      if (!result) return false;
    } else {
      auto *interface = type(item, file, generics, values);
      if (interface->kind != Kind::Interface)
        fail(item->span, "a generic constraint must be an interface or a built-in trait");
      if (!t->conformances.contains(interface)) return false;
    }
  }
  return true;
}

void Program::verifyValueParameter(Type *t, ParameterMode mode, Span span) {
  if (mode != ParameterMode::Value) return;
  if (t->kind != Kind::Interface) ensureSized(t, span);
  if (!copyable(t))
    fail(span, "Value parameter requires a Copyable type, but '" + t->name +
                   "' is not Copyable; use in, mut, or move as appropriate", "E230");
}

bool Program::copyable(Type *t) {
  if (t->kind == Kind::Reference) return t->readOnly;
  if (t->kind == Kind::Interface) return false;
  if (t->kind == Kind::Box || t->kind == Kind::Allocation || t->kind == Kind::RawStorage) return false;
  if (t->kind == Kind::Closure)
    return std::ranges::all_of(t->captures, [&](const auto &c) {
      return c.reference ? !c.writable : copyable(c.type);
    });
  if (t->kind == Kind::Class) {
    auto mode = attribute(*t->def.decl, "copy");
    if (mode == "disabled") return false;
    if (mode == "enable" && !methods(t, "copy", false).empty()) return true;
    if (!methods(t, "deinit", false).empty()) return false;
    return std::ranges::all_of(t->fields, [&](const auto &f) { return copyable(f.type); });
  }
  if (t->kind == Kind::Enum)
    return std::ranges::all_of(t->variants, [&](const auto &v) {
      return std::ranges::all_of(v.fields, [&](auto *f) { return copyable(f); });
    });
  if (t->kind == Kind::Optional || t->kind == Kind::Result || t->kind == Kind::Array ||
      t->kind == Kind::Tuple)
    return std::ranges::all_of(t->parts, [&](auto *f) { return copyable(f); });
  return true;
}

bool Program::movable(Type *t) {
  if (t->kind == Kind::Interface) return false;
  if (t->kind == Kind::RawStorage) return movable(t->parts[0]);
  if (t->kind == Kind::Class)
    return attribute(*t->def.decl, "move") != "disabled" &&
           std::ranges::all_of(t->fields, [&](const auto &f) { return movable(f.type); });
  if (t->kind == Kind::Enum)
    return std::ranges::all_of(t->variants, [&](const auto &v) {
      return std::ranges::all_of(v.fields, [&](auto *f) { return movable(f); });
    });
  if (t->kind == Kind::Optional || t->kind == Kind::Result || t->kind == Kind::Array ||
      t->kind == Kind::Tuple)
    return std::ranges::all_of(t->parts, [&](auto *f) { return movable(f); });
  return true;
}

bool Program::transferable(Type *t, bool shared) {
  std::set<std::pair<Type *, bool>> inspecting;
  auto trait = [&](auto &&self, Type *type, bool shared) -> bool {
    if (!inspecting.emplace(type, shared).second) return true;
    auto check = [&](Type *inner) { return self(self, inner, shared); };
    ensureSized(type, {});
    bool allowed = false;
    switch (type->kind) {
    case Kind::Void:
    case Kind::Bool:
    case Kind::Char:
    case Kind::Int:
    case Kind::Float:
    case Kind::Text:
    case Kind::Error:
    case Kind::Function:
      allowed = true;
      break;
    case Kind::Array:
    case Kind::Tuple:
    case Kind::Optional:
    case Kind::Result:
      allowed = std::ranges::all_of(type->parts, check);
      break;
    case Kind::Class: {
      const auto &file = *type->def.file;
      auto name = type->def.decl->name;
      if (file.standard && file.package == "cell" && name == "OnceLock")
        allowed = check(type->parts[0]);
      else if (file.standard && file.package == "thread" && name == "Thread")
        allowed = !shared && check(type->parts[0]);
      else if (file.standard && file.package == "channel" && name == "Receiver")
        allowed = !shared && check(type->parts[0]);
      else if (file.standard && file.package == "task" && name == "Task")
        allowed = !shared && check(type->parts[0]);
      else if (file.standard && file.package == "sync" && name == "Mutex")
        allowed = self(self, type->parts[0], false);
      else if (file.standard && file.package == "mem" && (name == "Shared" || name == "Weak"))
        allowed = self(self, type->parts[0], true);
      else if (shared && file.standard && file.package == "cell")
        allowed = false;
      else if (file.standard && file.package == "coll" && name == "Vec")
        allowed = false;
      else if (file.standard && file.package == "coll" && name == "FixedVec")
        allowed = check(type->parts[0]);
      // The private queue uses the default allocator.
      else if (file.standard && file.package == "channel" && name == "Queue")
        allowed = check(type->parts[0]);
      // The logging queue owns a deep, default-allocator copy of each record.
      else if (file.standard && file.package == "log" && name == "QueuedRecord")
        allowed = !shared;
      // These private gRPC adapters transfer only default-allocator buffers
      // and share handlers under the stable-address registration contract.
      else if (file.standard && file.package == "grpc" &&
               (name == "ConcurrentInput" || name == "ConcurrentOutput" ||
                name == "ConcurrentStreamInput" || name == "StreamCommand" || name == "StreamReply"))
        allowed = !shared;
      // Select registrations borrow stable storage protected by the mailbox mutex.
      else if (file.standard && file.package == "channel" && name == "Mailbox")
        allowed = !shared && check(type->parts[0]);
      else
        allowed = std::ranges::all_of(type->fields, [&](const auto &field) { return check(field.type); });
      break;
    }
    case Kind::Enum:
      allowed = std::ranges::all_of(type->variants, [&](const auto &variant) {
        return std::ranges::all_of(variant.fields, check);
      });
      break;
    case Kind::Closure:
      allowed = std::ranges::all_of(type->captures, [&](const auto &capture) {
        return !capture.reference && (!shared || !capture.writable) && check(capture.type);
      });
      break;
    default:
      break;
    }
    inspecting.erase({type, shared});
    return allowed;
  };
  return trait(trait, t, shared);
}

bool Program::drops(Type *t) {
  if (t->kind == Kind::Text) return heapAvailable();
  if (t->kind == Kind::Box || t->kind == Kind::Allocation || t->kind == Kind::PatternRemainder) return true;
  if (t->kind == Kind::Closure)
    return std::ranges::any_of(t->captures, [&](const auto &c) { return !c.reference && drops(c.type); });
  if (t->kind == Kind::Class)
    return !methods(t, "deinit", false).empty() ||
           std::ranges::any_of(t->fields, [&](const auto &f) { return drops(f.type); });
  if (t->kind == Kind::Enum)
    return std::ranges::any_of(t->variants, [&](const auto &v) {
      return std::ranges::any_of(v.fields, [&](auto *f) { return drops(f); });
    });
  if (t->kind == Kind::Optional || t->kind == Kind::Result || t->kind == Kind::Array ||
      t->kind == Kind::Tuple)
    return std::ranges::any_of(t->parts, [&](auto *f) { return drops(f); });
  return false;
}

bool Program::customCopy(Type *t) {
  if (t->kind == Kind::Text) return heapAvailable();
  if (t->kind == Kind::Closure)
    return std::ranges::any_of(t->captures,
                               [&](const auto &c) { return !c.reference && customCopy(c.type); });
  if (t->kind == Kind::Class)
    return (attribute(*t->def.decl, "copy") == "enable" && !methods(t, "copy", false).empty()) ||
           std::ranges::any_of(t->fields, [&](const auto &f) { return customCopy(f.type); });
  if (t->kind == Kind::Enum)
    return std::ranges::any_of(t->variants, [&](const auto &v) {
      return std::ranges::any_of(v.fields, [&](auto *f) { return customCopy(f); });
    });
  if (t->kind == Kind::Optional || t->kind == Kind::Result || t->kind == Kind::Array ||
      t->kind == Kind::Tuple)
    return std::ranges::any_of(t->parts, [&](auto *f) { return customCopy(f); });
  return false;
}

Function *Program::dropGlue(Type *t) {
  if (auto i = dropFunctions.find(t); i != dropFunctions.end()) return i->second;
  auto *signature = FunctionType::get(voidTy->ir, {PointerType::getUnqual(context)}, false);
  auto *f = Function::Create(signature, GlobalValue::InternalLinkage, "vqr.drop." + t->name, *module);
  f->setDoesNotThrow();
  dropFunctions.emplace(t, f);
  if (t->kind != Kind::PatternRemainder) buildDropGlue(t, f);
  return f;
}

void Program::buildDropGlue(Type *t, Function *function) {
  Body body(*this, files.front(), function, voidTy);
  body.emitDrop(t, function->getArg(0));
  body.ir.CreateRetVoid();
}

Function *Program::copyGlue(Type *t, Span span) {
  if (auto i = copyFunctions.find(t); i != copyFunctions.end()) return i->second;
  auto *signature = FunctionType::get(t->ir, {PointerType::getUnqual(context)}, false);
  auto *f = Function::Create(signature, GlobalValue::InternalLinkage, "vqr.copy." + t->name, *module);
  f->setDoesNotThrow();
  copyFunctions.emplace(t, f);
  Body body(*this, files.front(), f, t);
  body.ir.CreateRet(body.emitCopy(t, f->getArg(0), span));
  return f;
}

Constant *Program::bytes(const std::string &text) {
  if (auto i = strings.find(text); i != strings.end()) return i->second;
  auto *data = ConstantDataArray::getString(context, text, true);
  auto *global =
      new GlobalVariable(*module, data->getType(), true, GlobalValue::PrivateLinkage, data, "text");
  global->setUnnamedAddr(GlobalValue::UnnamedAddr::Global);
  return strings.emplace(text, global).first->second;
}

Value *Program::standardStream(IRBuilder<> &builder, bool error) {
  auto *ptr = PointerType::getUnqual(context);
  auto target = targetTriple();
  if (target.isWindowsMSVCEnvironment()) {
    auto iob =
        module->getOrInsertFunction("__acrt_iob_func", FunctionType::get(ptr, {builder.getInt32Ty()}, false));
    return builder.CreateCall(iob, {builder.getInt32(error ? 2 : 1)});
  }
  auto symbol = target.isOSDarwin() ? (error ? "__stderrp" : "__stdoutp") : (error ? "stderr" : "stdout");
  auto *stream = module->getGlobalVariable(symbol);
  if (!stream)
    stream = new GlobalVariable(*module, ptr, false, GlobalValue::ExternalLinkage, nullptr, symbol);
  return builder.CreateLoad(ptr, stream);
}

std::uint32_t Program::errorCode(Definition def, std::string_view member) {
  if (def.file->standard && def.file->package == "mem" && def.decl->name == "AllocationError" &&
      member == "OutOfMemory")
    return 0xfffffffc;
  if (def.file->standard && def.file->package == "io" && def.decl->name == "IoError" &&
      member == "WriteFailed")
    return 0xfffffffd;
  if (def.file->standard && def.file->package == "convert" && def.decl->name == "NumericError" &&
      member == "OutOfRange")
    return 0xfffffffe;
  if (!std::ranges::any_of(def.decl->members, [&](const auto &m) { return m.name == member; }))
    fail(def.decl->span, "unknown error member '" + std::string(member) + "'");
  std::uint32_t n = 1;
  for (const auto &[_, ds] : definitions)
    for (auto d : ds) {
      if (d.decl->kind != DeclKind::Error) continue;
      for (const auto &m : d.decl->members) {
        if (d.decl == def.decl && m.name == member) return n;
        ++n;
      }
    }
  return 0;
}

std::pair<Type *, Constant *> Program::globalInitializer(Definition def, Type *expected) {
  const auto &node = def.decl->body;
  const auto evaluateSource = [&] { return evaluate(node, *def.file, expected, {}); };
  // Inferred-length arrays of explicitly typed integer literals depend only
  // on the source text and compiler version. Leave general CTFE on the normal path.
  if (!def.file->standard || !def.decl->readOnly || expected || options.editorHints ||
      node->kind != NodeKind::Array || node->children.size() < 1024 || node->type->children[0])
    return evaluateSource();
  const auto &elementSyntax = node->type->children[1];
  if (elementSyntax->kind != NodeKind::TypeName ||
      (elementSyntax->text != "u32" && elementSyntax->text != "u64") ||
      !std::ranges::all_of(node->children, [&](const Ast &child) {
        return child->kind == NodeKind::Number && child->text.ends_with(elementSyntax->text);
      }))
    return evaluateSource();

  const auto &cache = inputs.standardCache();
  const auto path = cache.tableEntry(*node->span.source, def.decl->nameSpan.begin);
  if (path.empty()) return evaluateSource();
  auto *element = type(elementSyntax, *def.file);
  const auto count = node->children.size();
  const auto size = count * (element->bits / 8);
  if (auto bytes = cache.loadTable(path, size)) {
    auto *array = aggregate(Kind::Array, {element}, count);
    return {array, ConstantDataArray::getRaw(*bytes, count, element->ir)};
  }
  auto result = evaluateSource();
  if (auto *data = dyn_cast<ConstantDataArray>(result.second)) {
    auto bytes = data->getRawDataValues();
    if (bytes.size() == size) cache.storeTable(path, bytes);
  }
  return result;
}

GlobalEntry &Program::global(Definition d) {
  auto id = key(d);
  auto &g = globals[id];
  if (g.resolving) fail(d.decl->span, "cyclic initializer for '" + id + "'", "E220");
  if (g.ir) return g;
  g.resolving = true;
  g.def = d;
  if (!d.decl->body) fail(d.decl->span, "package binding requires a compile-time initializer");
  auto *expected = d.decl->type ? type(d.decl->type, *d.file) : nullptr;
  auto [t, c] = globalInitializer(d, expected);
  if (hasReference(t)) fail(d.decl->span, "references cannot be stored at package scope", "E230");
  ensureSized(t, d.decl->span);
  if (t->kind == Kind::Text) {
    auto *storage = c->getAggregateElement(2u);
    if (!d.decl->readOnly || !storage || !storage->isNullValue())
      fail(d.decl->span, "package string bindings require static immutable storage");
  } else if (drops(t) || t->kind == Kind::Void) {
    fail(d.decl->span, "type cannot be stored in a package binding");
  }
  g.type = t;
  g.ir = new GlobalVariable(*module, storage(t), d.decl->readOnly,
                            d.decl->visible && options.emit != "exe" ? GlobalValue::ExternalLinkage : GlobalValue::InternalLinkage, c,
                            "vqr." + id);
  g.resolving = false;
  return g;
}

bool hasComptimeParameters(const Decl &d) {
  return std::ranges::any_of(d.params, [](const auto &p) { return p.comptime; });
}

std::vector<Type *> Program::parameters(Definition d, const Types &types, Type *owner,
                                        const Constants &values) {
  std::vector<Type *> params;
  if (owner && !d.decl->associated) params.push_back(pointer(owner, d.decl->readOnly));
  for (const auto &param : d.decl->params) {
    auto mode = parameterMode(param.type);
    if ((param.comptime || d.decl->external || d.decl->exported) && mode != ParameterMode::Value)
      fail(param.span, "parameter modes require a native runtime parameter", "E230");
    auto *t = type(param.type, *d.file, types, values);
    if (scopedCallableInput(t) && mode != ParameterMode::Mut)
      fail(param.span, "a scoped callable loan requires an explicit 'mut' parameter", "E230");
    ensureSized(t, param.span);
    params.push_back(t);
  }
  return params;
}

FunctionEntry &Program::function(Definition d, Types substitutions, Type *owner, Constants values) {
  CompileTimings::Scope timer(options.compileTimings, CompileTimings::Instantiation);
  auto &decl = *d.decl;
  if (owner) values.insert(owner->comptimeArguments.begin(), owner->comptimeArguments.end());
  auto params = parameters(d, substitutions, owner, values);
  const unsigned receiverOffset = owner && !decl.associated;
  for (unsigned i = 0; i < decl.params.size(); ++i)
    verifyValueParameter(params[receiverOffset + i], parameterMode(decl.params[i].type), decl.params[i].span);
  std::string id = owner ? owner->name + "." + decl.name : key(d);
  std::vector<Type *> genericTypes;
  for (const auto &g : decl.generics)
    if (!g.comptime) genericTypes.push_back(substitutions.at(g.name));
  auto overloadParams = params;
  for (auto *&param : overloadParams) {
    if (param->parameterMode != ParameterMode::Value)
      param = param->parts[0];
  }
  auto instance = typeKey(typeKey(id, genericTypes), overloadParams);
  for (const auto &[name, value] : values) {
    std::string operand;
    raw_string_ostream output(operand);
    value.second->printAsOperand(output, false);
    instance += '.' + name + '=' + operand;
  }
  auto *ret = type(decl.type, *d.file, substitutions, values);
  ensureSized(ret, decl.span);
  borrowParameter(d, params, ret, owner && !decl.associated);
  if (!movable(ret) || std::ranges::any_of(params, [&](auto *t) { return !movable(t); }))
    fail(decl.span, "direct by-value parameters and returns of immovable types are not yet supported",
         "E290");
  if (auto f = functions.find(instance); f != functions.end()) {
    if (f->second.def.decl != d.decl) fail(decl.span, "duplicate overload '" + id + "'");
    return f->second;
  }
  if (decl.external || decl.exported) {
    if (!scalarCAbi()) fail(decl.span, "this target has no C ABI profile", "E290");
    if (hasComptimeParameters(decl) || !decl.generics.empty())
      fail(decl.span, "C functions require a fixed runtime signature");
    if (!cSymbols.insert(decl.name).second) fail(decl.span, "duplicate C symbol '" + decl.name + "'");
    if (d.file->standard && decl.external && decl.name.starts_with("__vqr_io_") && !hostedLinux64()) {
      if (decl.name.starts_with("__vqr_io_reactor_"))
        fail(decl.span, "the epoll reactor requires hosted GNU/Linux x86-64 or AArch64", "E290");
      if (!hostedMac64())
        fail(decl.span, "host I/O requires hosted GNU/Linux or macOS on x86-64 or AArch64", "E290");
    }
    if (d.file->standard && decl.external && !hostedLinux64()) {
      if (decl.name.starts_with("__vqr_net_resolve_"))
        fail(decl.span, "asynchronous name resolution requires hosted GNU/Linux x86-64 or AArch64", "E290");
      if (!hostedMac64() && decl.name.starts_with("__vqr_process_"))
        fail(decl.span, "process execution requires hosted GNU/Linux or macOS on x86-64 or AArch64", "E290");
    }
    validateCAbi(ret, params, decl.span);
  }
  if (owner && decl.name == "deinit" && (ret != voidTy || !decl.params.empty() || decl.readOnly))
    fail(decl.span, "deinit must be a mutable instance method with no arguments returning void");
  if (owner && decl.name == "copy" && (ret != owner || !decl.params.empty() || !decl.readOnly))
    fail(decl.span, "copy must be a const instance method with no arguments returning Self");
  if (owner && decl.name == "init" &&
      (decl.readOnly || decl.associated ||
       (ret != voidTy && !(ret->kind == Kind::Result && ret->parts[0] == voidTy))))
    fail(decl.span, "init must return void or !void");
  std::vector<llvm::Type *> args;
  unsigned offset = owner && !decl.associated;
  if (offset) args.push_back(storage(params[0]));
  for (unsigned i = 0; i < decl.params.size(); ++i)
    if (!decl.params[i].comptime) args.push_back(storage(params[offset + i]));
  auto *signature = FunctionType::get(ret->ir, args, false);
  bool c = decl.external || decl.exported;
  bool publiclyLinked = c || (options.emit != "exe" && decl.visible && !owner && (!d.file->standard || d.file->package == root));
  bool aggregateC = c && hasAggregateCAbi(ret, params);
  auto linkage = publiclyLinked && !aggregateC ? GlobalValue::ExternalLinkage : GlobalValue::InternalLinkage;
  auto *fn = Function::Create(signature, linkage, c && !aggregateC ? decl.name : "vqr." + instance, *module);
  auto *bridge = aggregateC ? bridgeC(fn, ret, params, d) : nullptr;
  if (decl.comptime && fn->hasLocalLinkage()) fn->addFnAttr("vaqrin.comptime");
  if (decl.external && d.file->standard && d.file->package == "math_ffi")
    fn->addFnAttr("vaqrin.libm");
  fn->setDoesNotThrow();
  if (ret == neverTy) fn->setDoesNotReturn();
  if (c && !aggregateC && extendCNarrowInteger()) {
    if (ret->kind == Kind::Int && ret->bits < 32)
      fn->addRetAttr(ret->sign ? llvm::Attribute::SExt : llvm::Attribute::ZExt);
    for (unsigned i = 0; i < params.size(); ++i)
      if (params[i]->kind == Kind::Int && params[i]->bits < 32)
        fn->addParamAttr(i, params[i]->sign ? llvm::Attribute::SExt : llvm::Attribute::ZExt);
  }
  auto &entry = functions
      .emplace(instance, FunctionEntry{d, ret, params, std::move(substitutions), std::move(values), fn, owner})
      .first->second;
  entry.cBridge = bridge;
  return entry;
}

Type *Program::signature(FunctionEntry &entry) {
  if (hasComptimeParameters(*entry.def.decl))
    fail(entry.def.decl->span, "comptime parameters require a direct call");
  auto types = entry.parameters;
  types.push_back(entry.result);
  auto source = borrowParameter(entry.def, entry.parameters, entry.result, false);
  std::vector<unsigned> moveParameters;
  for (unsigned i = 0; i < entry.def.decl->params.size(); ++i)
    if (parameterMode(entry.def.decl->params[i].type) == ParameterMode::Move) moveParameters.push_back(i);
  return aggregate(Kind::Function, types, source + 1, entry.def.decl->external || entry.def.decl->exported,
                   std::move(moveParameters));
}

void Program::lower(FunctionEntry &entry) {
  if (entry.lowered || entry.lowering) return;
  CompileTimings::Scope timer(options.compileTimings, CompileTimings::Lowering);
  entry.lowering = true;
  if (!entry.def.decl->external)
    Body(*this, *entry.def.file, entry.ir, entry.result, entry.substitutions, entry.def.decl->comptime,
         entry.owner)
        .build(entry);
  entry.lowered = true;
  entry.lowering = false;
}

std::pair<Type *, Constant *> Program::evaluate(const Ast &node, const File &file, Type *expected,
                                                const Types &generics, const Constants &values) {
  CompileTimings::Scope timer(options.compileTimings, CompileTimings::Ctfe);
  if (auto n = mathInteger(node, true)) {
    auto *integer = expected && expected->kind == Kind::Int ? expected : this->integer(32, true);
    if ((!expected || expected->kind == Kind::Int) && fits(*n, integer->bits, integer->sign))
      return {integer, ConstantInt::get(integer->ir, n->sextOrTrunc(integer->bits))};
  }
  auto *f = Function::Create(FunctionType::get(llvm::Type::getVoidTy(context), false),
                             GlobalValue::InternalLinkage, "vqr.consteval.probe", *module);
  Body probe(*this, file, f, nullptr, generics, true);
  for (const auto &[name, value] : values)
    probe.bindConstant(name, value.first, value.second, node->span);
  auto v = probe.expression(node, expected);
  if (v.type == neverTy || v.type == voidTy) fail(node->span, "constant requires a value", "E220");
  auto *value = probe.read(v, node->span);
  auto *eval = Function::Create(FunctionType::get(value->getType(), false), GlobalValue::InternalLinkage,
                                "vqr.consteval", *module);
  probe.ir.CreateRet(value);
  eval->splice(eval->begin(), f);
  f->eraseFromParent();
  if (verifyFunction(*eval, &errs())) fail(node->span, "invalid compile-time function IR", "E900");
  auto *result = interpret(*eval, {}, node->span);
  eval->eraseFromParent();
  return {v.type, result};
}

namespace {

// This declaration-only pass does not construct dummy LLVM types.
// Deferred denotes an unsupported proof obligation, not a Copyable fact.
enum class GenericCopyProof { Proven, Missing, Deferred };
using GenericCopyFacts = std::map<std::string, GenericCopyProof>;
using ActiveCopyTypes = std::set<const Decl *>;

bool requiresGenericCopy(const Parameter &generic) {
  if (generic.comptime) return false;
  for (auto constraint = generic.type; constraint; constraint = constraint->tail)
    if (constraint->kind == NodeKind::TypeName && constraint->text == "Copyable" &&
        constraint->children.empty())
      return true;
  return false;
}

void addGenericCopyFacts(GenericCopyFacts &facts, const Decl &decl) {
  for (const auto &generic : decl.generics) {
    if (generic.comptime) {
      facts.erase(generic.name);
      continue;
    }
    // A method's generic parameter shadows the enclosing class parameter.
    facts.insert_or_assign(generic.name, requiresGenericCopy(generic)
        ? GenericCopyProof::Proven : GenericCopyProof::Missing);
  }
}

GenericCopyProof combineCopyProof(GenericCopyProof left, GenericCopyProof right) {
  if (left == GenericCopyProof::Missing || right == GenericCopyProof::Missing)
    return GenericCopyProof::Missing;
  if (left == GenericCopyProof::Deferred || right == GenericCopyProof::Deferred)
    return GenericCopyProof::Deferred;
  return GenericCopyProof::Proven;
}

GenericCopyProof proveGenericCopy(Program &program, const File &file, const Ast &type,
                                  const GenericCopyFacts &facts, ActiveCopyTypes &active);

// Resolve the actual standard-library query before using it as a fact.
// Copyable is not established by a spelling match or a user look-alike.
std::optional<std::string> genericCopyQuery(Program &program, const File &file,
                                             const Ast &condition, const GenericCopyFacts &facts) {
  if (!condition || condition->kind != NodeKind::Call || condition->children.size() != 1)
    return {};
  const auto &callee = condition->children.front();
  if (callee->kind != NodeKind::Generic || callee->children.size() != 2) return {};
  const auto &argument = callee->children[1];
  auto target = path(callee->children.front());
  if ((target != "isCopyable" && !target.ends_with(".isCopyable")) ||
      argument->kind != NodeKind::TypeName || !argument->children.empty() ||
      argument->text == "Self" || !facts.contains(argument->text))
    return {};
  auto definitions = program.resolve(file, target, callee->span);
  if (definitions.size() == 1 && definitions.front().file->standard &&
      definitions.front().file->package == "meta" &&
      definitions.front().decl->name == "isCopyable" && definitions.front().decl->comptime)
    return argument->text;
  return {};
}

std::optional<bool> knownGenericCopyCondition(Program &program, const File &file,
                                              const Ast &condition, const GenericCopyFacts &facts) {
  if (!condition) return {};
  if (condition->kind == NodeKind::Boolean) return condition->text == "true";
  if (auto query = genericCopyQuery(program, file, condition, facts);
      query && facts.at(*query) == GenericCopyProof::Proven)
    return true;
  // Missing is absence of a universal proof, NOT a negative trait fact.
  if (condition->kind == NodeKind::Unary && condition->text == "!" &&
      condition->children.size() == 1) {
    auto inner = knownGenericCopyCondition(program, file, condition->children.front(), facts);
    if (inner) return !*inner;
    return {};
  }
  if (condition->kind == NodeKind::Binary && condition->children.size() == 2 &&
      (condition->text == "&&" || condition->text == "||")) {
    auto left = knownGenericCopyCondition(program, file, condition->children[0], facts);
    const bool isAnd = condition->text == "&&";
    if (left && *left != isAnd) return !isAnd;
    auto right = knownGenericCopyCondition(program, file, condition->children[1], facts);
    if (right && *right != isAnd) return !isAnd;
    if (left && right) return isAnd;
  }
  return {};
}

void refineGenericCopyFacts(Program &program, const File &file, const Ast &condition,
                            bool selected, GenericCopyFacts &facts) {
  if (!condition) return;
  if (condition->kind == NodeKind::Unary && condition->text == "!" &&
      condition->children.size() == 1) {
    refineGenericCopyFacts(program, file, condition->children[0], !selected, facts);
    return;
  }
  if (condition->kind == NodeKind::Binary && condition->children.size() == 2 &&
      ((selected && condition->text == "&&") || (!selected && condition->text == "||"))) {
    for (const auto &operand : condition->children)
      refineGenericCopyFacts(program, file, operand, selected, facts);
    return;
  }
  if (selected)
    if (auto query = genericCopyQuery(program, file, condition, facts))
      facts[*query] = GenericCopyProof::Proven;
}

bool affectsCopyLayout(const Decl &member) {
  if (member.kind == DeclKind::Field) return true;
  if (member.kind == DeclKind::Function)
    return !member.associated && (member.name == "copy" || member.name == "deinit");
  if (member.kind == DeclKind::Conditional)
    return std::ranges::any_of(member.members, affectsCopyLayout) ||
           std::ranges::any_of(member.otherwise, affectsCopyLayout);
  return false;
}

GenericCopyProof proveNominalCopy(Program &program, const File &file, const Decl &decl,
                                 const GenericCopyFacts &facts, ActiveCopyTypes &active) {
  using Proof = GenericCopyProof;
  if (decl.kind == DeclKind::Interface) return Proof::Missing;
  const auto mode = program.attribute(decl, "copy");
  if (decl.kind == DeclKind::Class && mode == "disabled") return Proof::Missing;

  struct LayoutPath {
    GenericCopyFacts facts;
    std::vector<const Decl *> fields;
    bool hasDeinit = false;
    bool hasCopy = false;
    bool validCopy = false;
  };
  // Analyze selected storage and special methods together. A destructor or
  // custom copy can override the answer from fields in its selected branch.
  // Bound the number of alternatives during migration; never turn exhaustion
  // into a successful proof.
  constexpr std::size_t maxPaths = 64;
  std::vector<LayoutPath> paths{{facts, {}, false, false, false}};
  auto collect = [&](auto &&self, const std::vector<Decl> &members,
                     std::vector<LayoutPath> &current) -> bool {
    for (const auto &member : members) {
      if (member.kind == DeclKind::Conditional) {
        if (!affectsCopyLayout(member)) continue;
        std::vector<LayoutPath> next;
        for (const auto &state : current) {
          auto known = knownGenericCopyCondition(program, file, member.body, state.facts);
          for (bool selected : {true, false}) {
            if (known && *known != selected) continue;
            auto branch = state;
            refineGenericCopyFacts(program, file, member.body, selected, branch.facts);
            std::vector<LayoutPath> alternatives{std::move(branch)};
            if (!self(self, selected ? member.members : member.otherwise, alternatives))
              return false;
            if (alternatives.size() > maxPaths - next.size()) return false;
            for (auto &alternative : alternatives) next.push_back(std::move(alternative));
          }
        }
        current = std::move(next);
      } else {
        for (auto &state : current) {
          if (member.kind == DeclKind::Field || member.kind == DeclKind::Variant) {
            state.fields.push_back(&member);
          } else if (member.kind == DeclKind::Function && !member.associated) {
            if (member.name == "deinit") state.hasDeinit = true;
            if (member.name == "copy") {
              state.hasCopy = true;
              state.validCopy |= member.readOnly && member.params.empty() &&
                                 member.generics.empty() && member.type &&
                                 member.type->kind == NodeKind::TypeName &&
                                 member.type->text == "Self" && member.type->children.empty();
            }
          }
        }
      }
    }
    return true;
  };
  if (!collect(collect, decl.members, paths)) return Proof::Deferred;

  auto classify = [&](const LayoutPath &state) {
    if (decl.kind == DeclKind::Class) {
      if (mode == "enable" && state.validCopy) return Proof::Proven;
      if (mode == "enable" && state.hasCopy) return Proof::Deferred;
      if (state.hasDeinit) return Proof::Missing;
    }
    auto result = Proof::Proven;
    for (const auto *member : state.fields) {
      if (member->kind == DeclKind::Field)
        result = combineCopyProof(result, proveGenericCopy(program, file, member->type, state.facts, active));
      else
        for (const auto &parameter : member->params)
          result = combineCopyProof(result, proveGenericCopy(program, file, parameter.type, state.facts, active));
    }
    return result;
  };
  // All possible layouts must prove Copyable. Disagreement is a deferred
  // obligation (unknown conditions can be correlated); all missing is a
  // definite missing guarantee for this declaration.
  auto result = classify(paths.front());
  for (std::size_t i = 1; i < paths.size(); ++i)
    if (classify(paths[i]) != result) return Proof::Deferred;
  return result;
}

GenericCopyProof proveGenericCopy(Program &program, const File &file, const Ast &type,
                                  const GenericCopyFacts &facts, ActiveCopyTypes &active) {
  using Proof = GenericCopyProof;
  if (!type) return Proof::Deferred;
  if (type->kind == NodeKind::TypePrefix) {
    const auto &prefix = type->text;
    if (prefix == "?" || prefix == "!")
      return proveGenericCopy(program, file, type->children.front(), facts, active);
    if (prefix == "^" || prefix == "ref mut") return Proof::Missing;
    if (prefix == "*" || prefix == "*const" || prefix == "[]" ||
        prefix == "[]const" || prefix == "ref") return Proof::Proven;
    return Proof::Deferred;
  }
  if (type->kind == NodeKind::TypeArray)
    return proveGenericCopy(program, file, type->children[1], facts, active);
  if (type->kind == NodeKind::TypeTuple) {
    auto result = Proof::Proven;
    for (const auto &part : type->children)
      result = combineCopyProof(result, proveGenericCopy(program, file, part, facts, active));
    return result;
  }
  if (type->kind == NodeKind::TypeFunction) return Proof::Proven;
  if (type->kind != NodeKind::TypeName && type->kind != NodeKind::Name) return Proof::Deferred;
  if (type->children.empty()) {
    if (auto fact = facts.find(type->text); fact != facts.end()) return fact->second;
    if (type->text == "Self") return Proof::Deferred;
  }
  auto definitions = program.resolve(file, type->text, type->span);
  if (definitions.empty()) {
    const auto &name = type->text;
    if ((name == "Box" || name == "__vqr_RawStorage") && type->children.size() == 1)
      return Proof::Missing;
    if (!type->children.empty()) return Proof::Deferred;
    if (name == "bool" || name == "char" || name == "str" || name == "string" ||
        name == "ErrorId" || name == "usize" || name == "isize" || name == "c_int" ||
        name == "c_char" || name == "f32" || name == "f64" || name == "void" || name == "never")
      return Proof::Proven;
    if (name.size() > 1 && (name[0] == 'i' || name[0] == 'u') &&
        std::ranges::all_of(std::string_view(name).substr(1), [](char ch) {
          return ch >= '0' && ch <= '9';
        }))
      return Proof::Proven;
    return Proof::Deferred;
  }
  const auto def = definitions.front();
  const auto &decl = *def.decl;
  if (decl.kind != DeclKind::Alias && decl.kind != DeclKind::Class &&
      decl.kind != DeclKind::Enum && decl.kind != DeclKind::Interface)
    return Proof::Deferred;
  if (decl.generics.size() != type->children.size()) return Proof::Deferred;
  if (active.contains(&decl)) {
    if (decl.kind == DeclKind::Alias)
      fail(type->span, "cyclic type alias '" + decl.name + "'", "E230");
    return Proof::Deferred;
  }
  // Compute actual arguments in the caller's scope before entering the
  // declaration scope. Never infer an actual's proof from a callee bound.
  // In particular, Alias<Alias<T>> is finite and is not an alias cycle.
  GenericCopyFacts substituted;
  for (std::size_t i = 0; i < decl.generics.size(); ++i)
    if (!decl.generics[i].comptime)
      substituted.emplace(decl.generics[i].name,
          proveGenericCopy(program, file, type->children[i], facts, active));
  active.insert(&decl);
  struct RemoveActive {
    ActiveCopyTypes &active;
    const Decl *decl;
    ~RemoveActive() { active.erase(decl); }
  } cleanup{active, &decl};
  if (decl.kind == DeclKind::Alias)
    return proveGenericCopy(program, *def.file, decl.type, substituted, active);
  return proveNominalCopy(program, *def.file, decl, substituted, active);
}

void auditGenericValueType(Program &program, const File &file, const Ast &type,
                           const GenericCopyFacts &facts) {
  if (!type || facts.empty()) return;
  ActiveCopyTypes active;
  if (proveGenericCopy(program, file, type, facts, active) == GenericCopyProof::Missing)
    fail(type->span, "generic Value parameter requires a declared Copyable proof; "
                     "use in, mut, or move as appropriate", "E230");
}

void auditNestedGenericCopyTypes(Program &program, const File &file, const Ast &type,
                                 const GenericCopyFacts &facts) {
  if (!type || facts.empty()) return;
  if (type->kind == NodeKind::TypeFunction) {
    for (const auto &parameter : type->children) {
      if (parameterMode(parameter) == ParameterMode::Value)
        auditGenericValueType(program, file, parameter, facts);
      auditNestedGenericCopyTypes(program, file, parameter, facts);
    }
    auditNestedGenericCopyTypes(program, file, type->type, facts);
    return;
  }
  if (type->kind == NodeKind::TypeArray) {
    auditNestedGenericCopyTypes(program, file, type->children[1], facts);
    return;
  }
  if (type->kind == NodeKind::TypeName && !type->children.empty()) {
    auto definitions = program.resolve(file, type->text, type->span);
    const Decl *decl = definitions.empty() ? nullptr : definitions.front().decl;
    if (decl && decl->generics.size() == type->children.size()) {
      for (std::size_t i = 0; i < decl->generics.size(); ++i) {
        const auto &generic = decl->generics[i];
        if (generic.comptime) continue;
        const auto &argument = type->children[i];
        if (requiresGenericCopy(generic)) {
          ActiveCopyTypes active;
          if (proveGenericCopy(program, file, argument, facts, active) == GenericCopyProof::Missing)
            fail(argument->span, "generic type argument requires a declared Copyable proof for '" +
                                 generic.name + "'", "E230");
        }
        auditNestedGenericCopyTypes(program, file, argument, facts);
      }
      return;
    }
  }
  if (type->kind == NodeKind::TypePrefix || type->kind == NodeKind::TypeTuple ||
      type->kind == NodeKind::TypeName)
    for (const auto &part : type->children)
      auditNestedGenericCopyTypes(program, file, part, facts);
}


// Visit the type syntax that occurs inside uninstantiated bodies, including
// explicit Lambda parameters, local bindings, allocations and array lengths.
// A runtime if is lowered on both paths; it is not a declaration-level
// comptime member selection and does not establish additional trait facts.
void auditGenericCopyExpression(Program &program, const File &file, const Ast &node,
                                 const GenericCopyFacts &facts) {
  if (!node || facts.empty()) return;
  if (node->kind == NodeKind::TypeName || node->kind == NodeKind::TypePrefix ||
      node->kind == NodeKind::TypeTuple || node->kind == NodeKind::TypeFunction ||
      node->kind == NodeKind::TypeArray) {
    auditNestedGenericCopyTypes(program, file, node, facts);
    if (node->kind == NodeKind::TypeArray)
      auditGenericCopyExpression(program, file, node->children.front(), facts);
    return;
  }
  auditGenericCopyExpression(program, file, node->type, facts);
  for (const auto &parameter : node->parameters) {
    if (node->kind == NodeKind::Lambda && parameter.type &&
        parameterMode(parameter.type) == ParameterMode::Value)
      auditGenericValueType(program, file, parameter.type, facts);
    auditGenericCopyExpression(program, file, parameter.type, facts);
  }
  for (const auto &child : node->children)
    auditGenericCopyExpression(program, file, child, facts);
  auditGenericCopyExpression(program, file, node->tail, facts);
}

void auditGenericCopyDeclaration(Program &program, const File &file, const Decl &decl,
                                 GenericCopyFacts facts = {}, const Decl *owner = nullptr) {
  addGenericCopyFacts(facts, decl);
  if (decl.kind == DeclKind::Class || decl.kind == DeclKind::Enum ||
      decl.kind == DeclKind::Interface)
    owner = &decl;
  auto refreshSelf = [&](GenericCopyFacts &scope) {
    if (!owner || owner->generics.empty()) return;
    scope.erase("Self");
    ActiveCopyTypes active{owner};
    scope["Self"] = proveNominalCopy(program, file, *owner, scope, active);
  };
  if (owner == &decl) refreshSelf(facts);
  if (decl.kind == DeclKind::Conditional) {
    auditGenericCopyExpression(program, file, decl.body, facts);
    auto known = knownGenericCopyCondition(program, file, decl.body, facts);
    if (!known || *known) {
      auto yes = facts;
      refineGenericCopyFacts(program, file, decl.body, true, yes);
      refreshSelf(yes);
      for (const auto &member : decl.members)
        auditGenericCopyDeclaration(program, file, member, yes, owner);
    }
    if (!known || !*known) {
      auto no = facts;
      refineGenericCopyFacts(program, file, decl.body, false, no);
      refreshSelf(no);
      for (const auto &member : decl.otherwise)
        auditGenericCopyDeclaration(program, file, member, no, owner);
    }
    return;
  }
  for (const auto &parameter : decl.params) {
    if (decl.kind == DeclKind::Function && parameterMode(parameter.type) == ParameterMode::Value)
      auditGenericValueType(program, file, parameter.type, facts);
    auditNestedGenericCopyTypes(program, file, parameter.type, facts);
  }
  auditNestedGenericCopyTypes(program, file, decl.type, facts);
  for (const auto &attribute : decl.attributes)
    for (const auto &type : attribute.types)
      auditNestedGenericCopyTypes(program, file, type, facts);
  for (const auto &[name, value] : decl.initializers)
    auditGenericCopyExpression(program, file, value, facts);
  auditGenericCopyExpression(program, file, decl.body, facts);
  for (const auto &member : decl.members)
    auditGenericCopyDeclaration(program, file, member, facts, owner);
}

} // namespace

std::unique_ptr<Module> Program::build() {
  std::size_t checked = 0;
  do {
    while (checked < files.size()) {
      indexFiles();
      const auto &file = files[checked++];
      if (file.standard && file.package == "net" && !hostedLinux64() && !hostedMac64())
        fail(file.span, "standard package 'net' requires hosted GNU/Linux or macOS on x86-64 or AArch64", "E290");
      if (options.auditGenericCopy)
        for (const auto &decl : file.declarations) auditGenericCopyDeclaration(*this, file, decl);
      if (file.standard && file.package != root) continue;
      for (const auto &d : file.declarations) {
        std::set<std::string> seen;
        for (const auto &a : d.attributes) {
          if (!seen.insert(a.name).second) fail(a.span, "duplicate attribute '" + a.name + "'");
          if ((a.name == "borrowed" && a.value == "scoped" &&
               (d.kind == DeclKind::Class || d.kind == DeclKind::Enum)) ||
              (a.name == "repr" &&
               (d.kind == DeclKind::Enum || (d.kind == DeclKind::Class && a.value == "C"))) ||
              (d.kind == DeclKind::Class &&
               ((a.name == "copy" && (a.value == "enable" || a.value == "disabled")) ||
                (a.name == "move" && a.value == "disabled") || a.name == "implements" || a.name == "derive")))
            continue;
          fail(a.span, "invalid attribute '" + a.name + "' for declaration");
        }
        Definition def{&file, &d};
        if (d.name == "main") {
          if (d.external || d.exported) {
            if (!options.check && options.emit == "exe" && options.profile == "hosted")
              fail(d.span, "C symbol main conflicts with the generated host entry");
          } else {
            auto *result = type(d.type, file);
            if (result->kind == Kind::Result) result = result->parts[0];
            if (result != voidTy && result != integer(32, true))
              fail(d.span, "main must return void, !void, i32 or !i32");
            if (!file.explicitInput && !file.selected) continue;
          }
        }
        if (d.kind == DeclKind::Global) global(def);
        if ((d.kind == DeclKind::Enum || d.kind == DeclKind::Class) && d.generics.empty()) {
          auto *t = nominal(def, {});
          for (const auto *member : resolvedMembers(t))
            if (member->kind == DeclKind::Function && member->generics.empty() &&
                !hasComptimeParameters(*member))
              function({&file, member}, classTypes(t), t);
        }
        if (d.kind == DeclKind::Function && d.generics.empty() && !hasComptimeParameters(d)) function(def);
        if (d.kind == DeclKind::Alias && d.generics.empty()) named(d.name, file, d.span);
      }
      for (auto &[_, entry] : functions) lower(entry);
    }
    prepareGenericInterfaces();
    // A function pointer is fixed-size even when its parameter names an
    // enclosing record. Do not force that record's layout while resolving
    // its fields or an alias to the function pointer.
    while (!pendingValueParameters.empty()) {
      auto [parameter, span] = pendingValueParameters.front();
      pendingValueParameters.pop_front();
      verifyValueParameter(parameter, ParameterMode::Value, span);
    }
  } while (checked < files.size());
  emitGenericInterfaces();
  if (!options.check && options.emit == "exe" && options.profile == "hosted") {
    auto selected = std::ranges::find(files, true, &File::selected);
    if (selected == files.end()) fail({}, "no entry selected");
    auto def = resolve(*selected, "main", selected->span).at(0);
    if (module->getNamedValue("main")) fail(def.decl->span, "duplicate C entry symbol main");
    auto &entry = function(def);
    bool fallible = entry.result->kind == Kind::Result;
    bool returnsStatus = (fallible ? entry.result->parts[0] : entry.result) == integer(32, true);
    IRBuilder<> b(context);
    auto *main = Function::Create(FunctionType::get(b.getInt32Ty(), false), GlobalValue::ExternalLinkage,
                                  "main", *module);
    b.SetInsertPoint(BasicBlock::Create(context, "entry", main));
    auto *value = b.CreateCall(entry.ir);
    if (fallible) {
      auto *error = b.CreateExtractValue(value, 0);
      auto *ok = BasicBlock::Create(context, "ok", main);
      auto *bad = BasicBlock::Create(context, "error", main);
      b.CreateCondBr(b.CreateICmpEQ(error, b.getInt32(0)), ok, bad);
      b.SetInsertPoint(bad);
      auto *ptr = PointerType::getUnqual(context);
      Value *message = bytes("unknown error");
      for (const auto &[_, group] : definitions)
        for (auto def : group) {
          if (def.decl->kind != DeclKind::Error) continue;
          for (const auto &member : def.decl->members) {
            auto code = errorCode(def, member.name);
            message = b.CreateSelect(b.CreateICmpEQ(error, b.getInt32(code)),
                                     bytes(key(def) + "." + member.name), message);
          }
        }
      for (auto [code, label] : std::array{std::pair{0xfffffffeu, "NumericError.OutOfRange"},
                                           std::pair{0xfffffffdu, "IoError.WriteFailed"},
                                           std::pair{0xfffffffcu, "AllocationError.OutOfMemory"}})
        message = b.CreateSelect(b.CreateICmpEQ(error, b.getInt32(code)), bytes(label), message);
      auto report =
          module->getOrInsertFunction("fprintf", FunctionType::get(b.getInt32Ty(), {ptr, ptr}, true));
      b.CreateCall(report,
                   {standardStream(b, true), bytes("Vaqrin: unhandled error %s (%u)\n"), message, error});
      b.CreateRet(b.getInt32(1));
      b.SetInsertPoint(ok);
    }
    b.CreateRet(returnsStatus ? (fallible ? b.CreateExtractValue(value, 1) : value) : b.getInt32(0));
  }
  if (debug) debug->finalize();
  if (verifyModule(*module, &errs())) fail({}, "LLVM module verification failed", "E900");
  return std::move(module);
}

} // namespace vqr::detail

namespace vqr {

std::unique_ptr<llvm::Module> compile(llvm::LLVMContext &context, llvm::TargetMachine &target,
                                      const Options &options, Inputs &inputs) {
  return detail::Program(context, target, options, inputs).build();
}

} // namespace vqr
