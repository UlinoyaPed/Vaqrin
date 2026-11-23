#include "compiler_internal.hpp"

namespace vqr::detail {
namespace {

struct GenericSignature {
  Program &program;
  Definition definition;
  const Types &types;
  const Constants &values;
  std::map<std::string, unsigned> variables;

  GenericSignature(Program &program, Definition definition, const Types &types, const Constants &values)
      : program(program), definition(definition), types(types), values(values) {
    unsigned index = 0;
    for (const auto &item : definition.decl->generics) variables.emplace(item.name, index++);
    for (const auto &item : definition.decl->params)
      if (item.comptime) variables.emplace(item.name, index++);
  }

  bool isVariable(const Ast &node) const {
    return (node->kind == NodeKind::Name || node->kind == NodeKind::TypeName) &&
           node->children.empty() && variables.contains(node->text);
  }

  bool dependent(const Ast &node) const {
    return node && (isVariable(node) || dependent(node->type) ||
                    std::ranges::any_of(node->children, [&](const Ast &child) { return dependent(child); }));
  }

  std::string key(const Ast &node) const {
    if (!node) return "-";
    if (isVariable(node)) return "$" + std::to_string(variables.at(node->text));
    if (node->kind == NodeKind::Name) {
      if (auto value = values.find(node->text); value != values.end()) {
        std::string key;
        raw_string_ostream output(key);
        value->second.second->printAsOperand(output, true);
        return key;
      }
    }
    if (node->kind == NodeKind::TypeName &&
        (node->text == "Copyable" || node->text == "Movable" ||
         node->text == "Sendable" || node->text == "Shareable"))
      return node->text;
    if (!dependent(node) && (node->kind == NodeKind::TypeName || node->kind == NodeKind::TypeTuple ||
                             node->kind == NodeKind::TypeFunction || node->kind == NodeKind::TypePrefix ||
                             node->kind == NodeKind::TypeArray))
      return program.type(node, *definition.file, types, values)->name;

    std::string spelling = node->text;
    if (node->kind == NodeKind::TypeFunction && !node->names.empty())
      spelling += " borrow(" + node->names.front() + ')';
    if (node->kind == NodeKind::TypeName) {
      auto defs = program.resolve(*definition.file, spelling, node->span);
      if (defs.size() == 1) spelling = program.key(defs.front());
    }
    std::string result = std::to_string(static_cast<unsigned>(node->kind)) + ":" + spelling + "(";
    for (const auto &child : node->children) {
      result += key(child);
      result += ',';
    }
    return result + ")->" + key(node->type);
  }

  std::multiset<std::string> bounds(const Ast &node) const {
    std::multiset<std::string> result;
    for (auto item = node; item; item = item->tail) result.insert(key(item));
    return result;
  }

  int borrowRoot() const {
    const auto &decl = *definition.decl;
    const auto &name = decl.borrowSource;
    if (name.empty()) return -2;
    if (name == "self") return -1;
    for (unsigned i = 0; i < decl.params.size(); ++i)
      if (decl.params[i].name == name) return static_cast<int>(i);
    fail(decl.span, "from must name self or a parameter", "E230");
  }
};

} // namespace

bool Program::matchesGenericInterface(Definition required, const Types &requiredTypes,
                                      const Constants &requiredValues, Definition method,
                                      const Types &methodTypes, const Constants &methodValues) {
  const auto &a = *required.decl;
  const auto &b = *method.decl;
  if (a.generics.size() != b.generics.size() || a.params.size() != b.params.size()) return false;
  GenericSignature left(*this, required, requiredTypes, requiredValues);
  GenericSignature right(*this, method, methodTypes, methodValues);
  for (unsigned i = 0; i < a.generics.size(); ++i) {
    const auto &x = a.generics[i], &y = b.generics[i];
    if (x.comptime != y.comptime) return false;
    if (x.comptime ? left.key(x.type) != right.key(y.type) : left.bounds(x.type) != right.bounds(y.type))
      return false;
  }
  for (unsigned i = 0; i < a.params.size(); ++i) {
    const auto &x = a.params[i], &y = b.params[i];
    if (x.comptime != y.comptime || left.key(x.type) != right.key(y.type)) return false;
  }
  return left.key(a.type) == right.key(b.type) && left.borrowRoot() == right.borrowRoot();
}

Function *Program::genericInterfaceLookup(Type *interface, const Decl &method, const Types &types,
                                          const Constants &values) {
  for (const auto &lookup : genericInterfaceLookups)
    if (lookup.interface == interface && lookup.method == &method &&
        lookup.types == types && lookup.values == values)
      return lookup.function;
  auto *ptr = PointerType::getUnqual(context);
  auto *function = Function::Create(FunctionType::get(ptr, {ptr}, false), GlobalValue::InternalLinkage,
                                    "vqr.interface.generic.lookup", *module);
  function->setDoesNotThrow();
  genericInterfaceLookups.push_back({interface, &method, types, values, function, {}});
  return function;
}

void Program::prepareGenericInterfaces() {
  // A specialized implementation can itself create interface views or invoke
  // other specializations. Close over those requests before emitting lookups.
  bool changed;
  do {
    changed = false;
    for (std::size_t i = 0; i < genericInterfaceLookups.size(); ++i) {
      auto &lookup = genericInterfaceLookups[i];
      const auto &requirements = lookup.interface->def.decl->members;
      auto index = static_cast<std::size_t>(lookup.method - requirements.data());
      for (const auto &[types, table] : interfaceTables) {
        auto [concrete, interface] = types;
        if (interface != lookup.interface || lookup.targets.contains(table)) continue;
        const auto &method = concrete->conformances.at(interface)[index];
        auto *owner = method.declaringType ? method.declaringType : concrete;
        auto substitutions = classTypes(owner);
        Constants values = owner->comptimeArguments;
        for (unsigned j = 0; j < method.decl->generics.size(); ++j) {
          const auto &binder = method.decl->generics[j];
          const auto &required = lookup.method->generics[j];
          if (binder.comptime) values[binder.name] = lookup.values.at(required.name);
          else substitutions[binder.name] = lookup.types.at(required.name);
        }
        for (unsigned j = 0; j < method.decl->params.size(); ++j) {
          const auto &binder = method.decl->params[j];
          if (binder.comptime) values[binder.name] = lookup.values.at(lookup.method->params[j].name);
        }
        auto &entry = function(method, substitutions, owner, values);
        lookup.targets.emplace(table, interfaceThunk(concrete, method, entry));
        changed = true;
        lower(entry);
      }
    }
  } while (changed);
}

void Program::emitGenericInterfaces() {
  for (const auto &lookup : genericInterfaceLookups) {
    auto *function = lookup.function;
    IRBuilder<> builder(BasicBlock::Create(context, "entry", function));
    // Table names are deterministic. LLVM globals themselves carry the
    // runtime identity and must not be merged even if all their slots match.
    std::vector<std::pair<GlobalVariable *, Function *>> targets(lookup.targets.begin(), lookup.targets.end());
    std::ranges::sort(targets, [](const auto &a, const auto &b) {
      return a.first->getName() < b.first->getName();
    });
    for (auto [table, method] : targets) {
      auto *found = BasicBlock::Create(context, "found", function);
      auto *next = BasicBlock::Create(context, "next", function);
      builder.CreateCondBr(builder.CreateICmpEQ(function->getArg(0), table), found, next);
      builder.SetInsertPoint(found);
      builder.CreateRet(method);
      builder.SetInsertPoint(next);
    }
    builder.CreateCall(Intrinsic::getOrInsertDeclaration(module.get(), Intrinsic::trap));
    builder.CreateUnreachable();
  }
}

} // namespace vqr::detail
