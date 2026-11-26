#include "compiler_internal.hpp"

#include <charconv>

namespace vqr::detail {

// Parsing an annotation does not lower the lambda body or evaluate a
// capture. Inference and final lowering must agree on this same signature.
struct LambdaAnnotations {
  std::vector<Type *> parameters;
  std::vector<unsigned> moves;
  Type *result;
};

template <typename Resolve>
LambdaAnnotations lambdaAnnotations(const Ast &n, Type *expected, Resolve resolve) {
  if (expected && (expected->cAbi || expected->parts.size() != n->parameters.size() + 1))
    fail(n->span, "lambda signature does not match its context");
  LambdaAnnotations signature{{}, {}, n->type ? resolve(n->type) : expected ? expected->parts.back() : nullptr};
  for (unsigned i = 0; i < n->parameters.size(); ++i) {
    const auto &param = n->parameters[i];
    auto *t = param.type ? resolve(param.type) : expected ? expected->parts[i] : nullptr;
    if (!t) fail(param.span, "lambda parameter requires a type or a function type context");
    if (param.type && scopedCallableInput(t) && parameterMode(param.type) != ParameterMode::Mut)
      fail(param.span, "a scoped callable loan requires an explicit 'mut' parameter", "E230");
    if (expected && t != expected->parts[i])
      fail(param.span, "lambda parameter does not match its context", "E201");
    signature.parameters.push_back(t);
    if (parameterMode(param.type) == ParameterMode::Move ||
        (!param.type && expected &&
         std::ranges::find(expected->moveParameters, i) != expected->moveParameters.end()))
      signature.moves.push_back(i);
  }
  if (expected && expected->moveParameters != signature.moves)
    fail(n->span, "lambda ownership parameter mode does not match its context", "E201");
  return signature;
}

template <typename Resolve>
Type *standaloneLambdaType(Program &p, const Ast &n, Resolve resolve) {
  if (n->kind != NodeKind::Lambda || !n->children.empty() || !n->type ||
      !std::ranges::all_of(n->parameters, [](const auto &param) { return bool(param.type); }))
    return nullptr;
  auto signature = lambdaAnnotations(n, nullptr, resolve);
  // Borrow-return contracts can depend on the expected callable. Do
  // not guess a source or an environment-backed closure type.
  if (p.hasReference(signature.result) || p.checkedViewResult(signature.result)) return nullptr;
  signature.parameters.push_back(signature.result);
  return p.aggregate(Kind::Function, signature.parameters, 0, false, std::move(signature.moves));
}

Type *Body::referenceHint(const Ast &n) {
  if (n->kind == NodeKind::Name) {
    auto id = findLocal(n->text);
    // Do not let inference smuggle a call-only loan into a Value binder.
    if (id >= 0 && locals[id].reference && !scopedCallableInput(locals[id].reference))
      return locals[id].reference;
  }
  return hint(n);
}

Type *Body::hint(const Ast &n) {
  if (!n) return p.voidTy;
  switch (n->kind) {
  case NodeKind::Number: {
    auto number = parseNumber(n->text);
    return number.suffix.empty() ? nullptr : p.named(number.suffix, file, n->span);
  }
  case NodeKind::Boolean:
    return p.boolTy;
  case NodeKind::Text:
    return p.textTy;
  case NodeKind::Character:
    return p.charTy;
  case NodeKind::Name: {
    int local = findLocal(n->text);
    if (local >= 0) return locals[local].type;
    if (owner && findLocal("self") >= 0)
      for (const auto &field : owner->fields)
        if (field.decl->name == n->text) return field.type;
    auto defs = p.resolve(file, n->text, n->span);
    if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Global) return p.global(defs[0]).type;
    if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Function && defs[0].decl->generics.empty() &&
        !defs[0].decl->unsafe && !defs[0].decl->comptime && !hasComptimeParameters(*defs[0].decl))
      return p.signature(p.function(defs[0]));
    return nullptr;
  }
  case NodeKind::Block:
    return n->tail ? hint(n->tail) : nullptr;
  case NodeKind::Move:
    return hint(n->children[0]);
  case NodeKind::Unary: {
    auto *t = hint(n->children[0]);
    if (n->text == "!") return p.boolTy;
    if (!t) return nullptr;
    if (n->text == "*")
      return t->kind == Kind::Pointer || t->kind == Kind::Box || t->kind == Kind::Reference ? t->parts[0]
                                                                                            : nullptr;
    if (n->text == "post!") return t->kind == Kind::Result ? t->parts[0] : nullptr;
    if (n->text == "post?") return t->kind == Kind::Optional ? t->parts[0] : nullptr;
    if (n->text == "ref" || n->text == "ref mut") return p.reference(t, n->text == "ref");
    if (n->text == "raw" && n->children[0]->kind == NodeKind::Name) {
      int i = findLocal(n->children[0]->text);
      if (i >= 0) return p.pointer(t, !locals[i].writable);
    }
    return n->text == "raw" ? nullptr : t;
  }
  case NodeKind::Compare:
    return p.boolTy;
  case NodeKind::Binary: {
    auto operation = std::string_view(n->text);
    if (operation == "&&" || operation == "||" || operation == "==" || operation == "!=")
      return p.boolTy;
    auto *left = hint(n->children[0]);
    if (operation == "catch" || operation == "??")
      return left && left->kind == (operation == "catch" ? Kind::Result : Kind::Optional)
                 ? left->parts[0] : nullptr;
    if (operation == ".." || operation == "..=") return nullptr;
    if (operation.starts_with("<<") || operation == ">>")
      return left && left->kind == Kind::Int ? left : nullptr;
    auto *right = hint(n->children[1]);
    auto arithmetic = [&](Type *type, const Ast &other) -> Type * {
      if (!type) return nullptr;
      if (type->kind == Kind::Int && mathInteger(other)) return type;
      if (type->kind == Kind::Float && other->kind == NodeKind::Number &&
          parseNumber(other->text).floating)
        return type;
      return nullptr;
    };
    if (!left || !right) return left ? arithmetic(left, n->children[1]) : arithmetic(right, n->children[0]);
    if (left == right && left->kind == Kind::Float) return left;
    if (left == right && left->kind == Kind::Int) return left;
    return nullptr;
  }
  case NodeKind::Index:
  case NodeKind::Slice: {
    auto *base = hint(n->children[0]);
    if (!base || (base->kind != Kind::Array && base->kind != Kind::Slice)) return nullptr;
    if (n->kind == NodeKind::Index) return base->parts[0];
    if (base->kind == Kind::Slice) return base;
    auto source = n->children[0];
    if (source->kind == NodeKind::Unary && source->text == "*") {
      auto *pointer = hint(source->children[0]);
      if (pointer && pointer->kind == Kind::Pointer) return p.slice(base->parts[0], pointer->readOnly);
    }
    int i = source->kind == NodeKind::Name ? findLocal(source->text) : -1;
    return p.slice(base->parts[0], i < 0 || !locals[i].writable);
  }
  case NodeKind::Call: {
    auto callee = n->children[0];
    ArrayRef<Ast> typeArguments;
    if (callee->kind == NodeKind::Generic) {
      typeArguments = ArrayRef<Ast>(callee->children).drop_front();
      callee = callee->children.front();
      if (std::ranges::any_of(typeArguments, [](const Ast &argument) {
            return argument->kind != NodeKind::TypeName && argument->kind != NodeKind::TypePrefix &&
                   argument->kind != NodeKind::TypeArray && argument->kind != NodeKind::TypeTuple &&
                   argument->kind != NodeKind::TypeFunction && argument->kind != NodeKind::Name;
          }))
        return nullptr;
    }
    auto canHint = [&](const Decl &definition) {
      return definition.generics.size() == typeArguments.size() && !hasComptimeParameters(definition) &&
             std::ranges::none_of(definition.generics, &Parameter::comptime);
    };
    auto returnType = [&](Definition definition, Type *declaring = nullptr) {
      auto types = declaring ? p.classTypes(declaring) : Types{};
      for (unsigned i = 0; i < typeArguments.size(); ++i)
        types[definition.decl->generics[i].name] = parseType(typeArguments[i]);
      return p.type(definition.decl->type, *definition.file, types,
                    declaring ? declaring->comptimeArguments : Constants{});
    };
    if (callee->kind == NodeKind::Member && callee->names.empty()) {
      auto source = callee->children[0];
      auto *receiver = hint(source);
      if (receiver && receiver->kind == Kind::Optional && callee->text == "unwrap") return receiver->parts[0];
      if (receiver && receiver->kind == Kind::Class) {
        int i = source->kind == NodeKind::Name ? findLocal(source->text) : -1;
        bool writable = i < 0 || locals[i].writable;
        Definition method{};
        for (auto def : p.methods(receiver, callee->text, false)) {
          auto *candidate = def.decl;
          if (candidate->params.size() + 1 != n->children.size() || !canHint(*candidate) || (!candidate->readOnly && !writable) ||
              ((!candidate->visible || !def.publicPath) && owner != receiver))
            continue;
          if (!method.decl || (!candidate->readOnly && method.decl->readOnly))
            method = def;
          else if (candidate->readOnly == method.decl->readOnly)
            return nullptr;
        }
        if (method.decl) {
          auto *actual = method.declaringType ? method.declaringType : receiver;
          return returnType(method, actual);
        }
      }
    }
    auto key = path(callee);
    auto dot = key.find('.');
    if (!key.empty() && findLocal(key.substr(0, dot)) < 0) {
      if (owner && dot == key.npos && findLocal("self") >= 0) {
        auto methods = p.methods(owner, key, false);
        if (!methods.empty()) {
          Type *result = nullptr;
          for (auto def : methods) {
            auto &method = *def.decl;
            if (method.params.size() + 1 != n->children.size() || !canHint(method))
              continue;
            auto *actual = def.declaringType ? def.declaringType : owner;
            auto *type = returnType(def, actual);
            if (result && result != type) return nullptr;
            result = type;
          }
          return result;
        }
      }
      auto ds = p.resolve(file, key, n->span);
      Type *result = nullptr;
      for (auto def : ds) {
        const auto &candidate = *def.decl;
        if (candidate.kind != DeclKind::Function || candidate.params.size() + 1 != n->children.size())
          continue;
        if (!canHint(candidate)) return nullptr;
        auto *type = returnType(def);
        if (result && result != type) return nullptr;
        result = type;
      }
      if (result) return result;
      if (typeArguments.empty() && ds.size() == 1 && ds[0].decl->kind == DeclKind::Class &&
          ds[0].decl->generics.empty())
        return p.nominal(ds[0], {});
      if (typeArguments.empty() && (generics.contains(key) || key == "bool" || key == "char" || key == "usize" || key == "isize" || key == "f32" ||
          key == "f64" ||
          (key.size() > 1 && (key[0] == 'i' || key[0] == 'u') && key[1] >= '0' && key[1] <= '9'))) {
        auto *target = p.named(key, file, n->span, generics);
        if (n->children.size() == 2 && mathInteger(n->children[1])) return target;
      }
    }
    return nullptr;
  }
  default:
    return nullptr;
  }
}

Type *Body::branchHint(const Ast &n) {
  if (!n) return nullptr;
  auto *t = hint(n);
  if (t == p.voidTy || t == p.neverTy) return nullptr;
  return t;
}

ValueAt Body::name(const Ast &n, bool useScopedCallable) {
  int i = findLocal(n->text);
  if (i >= 0) {
    if (n->text == "self" && constructing && constructed.size() < owner->fields.size())
      fail(n->span, "self cannot escape before every field is initialized", "E230");
    const auto &l = locals[i];
    if (scopedCallableInput(l.reference) && (!useScopedCallable || deferredCleanup))
      fail(n->span, "a scoped callable parameter may only be invoked or reborrowed for an immediate call", "E230");
    indexSymbol(n->span, l.declaration);
    auto origin = l.origin;
    if (l.reference || p.hasReference(l.type)) origin.via = i;
    return {l.type, nullptr, l.slot, l.writable, i, true, false, origin, l.noBorrowPayload};
  }
  if (owner)
    for (const auto &f : owner->fields) {
      if (f.decl->name != n->text) continue;
      int self = findLocal("self");
      if (self < 0) break;
      if (constructing && !constructed.contains(f.index))
        fail(n->span, "field is not initialized yet", "E230");
      indexField(n->span, owner, *f.decl);
      Value *receiver = nullptr;
      if (deferredCleanup)
        receiver = ir.CreateLoad(p.storage(locals[self].type), locals[self].slot);
      else
        receiver = fn->getArg(0);
      return {f.type,
              nullptr,
              ir.CreateStructGEP(owner->ir, receiver, f.index),
              !locals[self].type->readOnly && !f.decl->readOnly,
              -1,
              true,
              false,
              {f.type->kind == Kind::Pointer ? -1 : self, -1}};
    }
  auto defs = p.resolve(file, n->text, n->span);
  if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Global) {
    if (auto *hints = p.options.editorHints; hints && hints->workspace && !defs[0].file->standard)
      indexSymbol(n->span, defs[0].decl->nameSpan, false);
    auto &g = p.global(defs[0]);
    return {g.type, nullptr, g.ir, !defs[0].decl->readOnly, -1, true};
  }
  if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Function && defs[0].decl->generics.empty()) {
    if (defs[0].decl->unsafe || defs[0].decl->comptime || hasComptimeParameters(*defs[0].decl))
      fail(n->span, "unsafe and comptime functions require a direct call");
    if (auto *hints = p.options.editorHints; hints && hints->workspace && !defs[0].file->standard &&
        !defs[0].decl->external && !defs[0].decl->exported)
      indexSymbol(n->span, defs[0].decl->nameSpan, false);
    auto &f = p.function(defs[0]);
    p.lower(f);
    return {p.signature(f), f.cBridge ? f.cBridge : f.ir};
  }
  if (defs.size() > 1) fail(n->span, "overloaded name requires a call with arguments");
  fail(n->span, "unknown value '" + n->text + "'");
}

ValueAt Body::numeric(const Ast &n, Type *t) {
  auto number = parseNumber(n->text);
  if (!number.suffix.empty())
    t = p.named(number.suffix, file, n->span);
  else if (!t || (number.floating ? t->kind != Kind::Float : t->kind != Kind::Int))
    t = number.floating ? p.canonical.at("f64") : p.integer(32, true);
  if (!number.floating) {
    if (!fits(number.integer, t->bits, t->sign))
      fail(n->span, "integer literal does not fit '" + t->name + "'", "E211");
    return {t, ConstantInt::get(t->ir, number.integer.sextOrTrunc(t->bits))};
  }
  APFloat f(t->bits == 32 ? APFloat::IEEEsingle() : APFloat::IEEEdouble());
  auto status = f.convertFromString(number.decimal, APFloat::rmNearestTiesToEven);
  if (!status) {
    consumeError(status.takeError());
    fail(n->span, "invalid floating-point literal", "E211");
  }
  return {t, ConstantFP::get(p.context, f)};
}

ValueAt Body::place(const Ast &n) {
  auto v = expression(n);
  if (v.type == p.neverTy) return v;
  if (!v.address) fail(n->span, "expected an assignable storage location");
  if (v.type->kind == Kind::Interface) fail(n->span, "an interface cannot be assigned by value");
  return v;
}

ValueAt Body::member(const Ast &n) {
  auto base = n->children[0];
  auto tupleIndex = [&](Type *tuple) {
    unsigned index = 0;
    auto end = n->text.data() + n->text.size();
    auto [last, error] = std::from_chars(n->text.data(), end, index);
    if (error != std::errc{} || last != end || index >= tuple->parts.size())
      fail(n->span, "tuple field index is out of range");
    return index;
  };
  if (!n->names.empty()) {
    auto optional = expression(base);
    if (optional.type == p.neverTy) return optional;
    if (optional.type->kind != Kind::Optional) fail(n->span, "?. requires an optional receiver");
    if (!optional.borrowed && p.drops(optional.type))
      fail(n->span, "bind a resource-owning temporary before projecting one of its fields", "E290");
    auto *source = read(optional, n->span, false);
    auto *some = block("optional.some"), *none = block("optional.null"), *end = block("optional.end");
    auto state = initialized;
    ir.CreateCondBr(ir.CreateExtractValue(source, 0), some, none);
    ir.SetInsertPoint(some);
    enter();
    auto inner = payload(optional, 1, optional.type->parts[0]);
    if (inner.type->kind == Kind::Pointer || inner.type->kind == Kind::Box)
      fail(n->span, "optional member access cannot implicitly dereference raw pointers or Box", "E230");
    Type *obj = inner.type;
    p.ensureSized(obj, n->span);
    unsigned index;
    Type *fieldType;
    if (obj->kind == Kind::Tuple) {
      index = tupleIndex(obj);
      fieldType = obj->parts[index];
    } else {
      auto f = std::ranges::find_if(obj->fields, [&](const Field &f) { return f.decl->name == n->text; });
      if (f == obj->fields.end()) fail(n->span, "unknown field '" + n->text + "'");
      if (!f->decl->visible && owner != obj) fail(n->span, "field is private");
      indexField(n->span, obj, *f->decl);
      index = f->index;
      fieldType = f->type;
    }
    auto field = payload(inner, index, fieldType);
    auto *type = p.aggregate(Kind::Optional, {fieldType});
    auto value = wrap(type, read(field, n->span), true);
    leave();
    auto *from = ir.GetInsertBlock();
    ir.CreateBr(end);
    ir.SetInsertPoint(none);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(type->ir, 2);
    phi->addIncoming(value.value, from);
    phi->addIncoming(Constant::getNullValue(type->ir), none);
    merge({state, initialized}, state.size());
    return {type, phi, nullptr, false, -1, false, field.temporaryBorrow, field.origin};
  }
  auto target = base->kind == NodeKind::Generic ? base->children[0] : base;
  auto basePath = path(target);
  if (!basePath.empty() && findLocal(basePath.substr(0, basePath.find('.'))) < 0) {
    auto defs = p.resolve(file, basePath, n->span);
    if (target == base && defs.size() == 1 && defs[0].decl->kind == DeclKind::Error) {
      if (auto *hints = p.options.editorHints; hints && hints->workspace && !defs[0].file->standard)
        indexSymbol(target->span, defs[0].decl->nameSpan, false);
      return {p.errorTy, ir.getInt32(p.errorCode(defs[0], n->text))};
    }
    if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Enum) {
      std::vector<Ast> args;
      if (target != base) args.assign(base->children.begin() + 1, base->children.end());
      auto *t = p.named(basePath, file, n->span, generics, args, visibleConstants(), target->span);
      p.ensureSized(t, n->span);
      auto v = std::ranges::find(t->variants, n->text, &Variant::name);
      if (v == t->variants.end() || !v->fields.empty())
        fail(n->span, "expected an enum member without a payload");
      return {t, ir.CreateInsertValue(Constant::getNullValue(t->ir), ConstantInt::get(p.context, v->tag), 0),
              nullptr, false, -1, false, false, {}, t->scopedBorrow};
    }
    if (target == base && !p.import(file, basePath).empty()) {
      auto full = p.resolve(file, basePath + "." + n->text, n->span);
      if (full.size() == 1 && full[0].decl->kind == DeclKind::Global) {
        if (auto *hints = p.options.editorHints; hints && hints->workspace && !full[0].file->standard)
          indexSymbol(n->span, full[0].decl->nameSpan, false);
        auto &g = p.global(full[0]);
        return {g.type, nullptr, g.ir, !full[0].decl->readOnly, -1, true};
      }
      if (full.size() == 1 && full[0].decl->kind == DeclKind::Function && full[0].decl->generics.empty()) {
        if (full[0].decl->unsafe || full[0].decl->comptime || hasComptimeParameters(*full[0].decl))
          fail(n->span, "function requires a direct call");
        if (auto *hints = p.options.editorHints; hints && hints->workspace && !full[0].file->standard &&
            !full[0].decl->external && !full[0].decl->exported)
          indexSymbol(n->span, full[0].decl->nameSpan, false);
        auto &f = p.function(full[0]);
        p.lower(f);
        return {p.signature(f), f.cBridge ? f.cBridge : f.ir};
      }
      fail(n->span, "unresolved imported symbol '" + basePath + "." + n->text + "'");
    }
  }
  ValueAt v;
  if (constructing && base->kind == NodeKind::Name && base->text == "self") {
    auto *receiver = locals[findLocal("self")].type;
    v = {owner, nullptr, fn->getArg(0), !receiver->readOnly, -1, true};
  } else
    v = expression(base);
  if (v.type == p.neverTy) return v;
  if (v.type->kind == Kind::Box)
    fail(n->span, "Box payload member access requires explicit dereference", "E230");
  if (!v.borrowed && p.drops(v.type))
    fail(n->span, "bind a resource-owning temporary before projecting one of its fields", "E290");
  if (v.type->kind == Kind::Pointer || v.type->kind == Kind::Reference)
    v = dereference(v, n->span, true);
  p.ensureSized(v.type, n->span);
  if (v.type->kind == Kind::Tuple) {
    auto index = tupleIndex(v.type);
    return payload(v, index, v.type->parts[index]);
  }
  if (v.type->kind != Kind::Class) fail(n->span, "member access requires a class instance");
  auto f = std::ranges::find_if(v.type->fields, [&](const Field &f) { return f.decl->name == n->text; });
  if (f == v.type->fields.end()) fail(n->span, "unknown field '" + n->text + "'");
  if (!f->decl->visible && owner != v.type) fail(n->span, "field '" + n->text + "' is private");
  if (constructing && base->kind == NodeKind::Name && base->text == "self" && !constructed.contains(f->index))
    fail(n->span, "field is not initialized yet", "E230");
  indexField(n->span, v.type, *f->decl);
  auto field = payload(v, f->index, f->type);
  field.writable &= !f->decl->readOnly && !v.type->scopedBorrow;
  return field;
}

Value *Body::arrayStorage(ValueAt array, Span span, std::size_t scope) {
  if (!p.hasReference(array.type)) return asPointer(array, span).value;
  checkAccess(array, false, span);
  pinBorrow(array, span, scope);
  if (array.address) return array.address;
  auto *storage = slot(array.type, "borrowed.array");
  ir.CreateStore(read(array, span), storage);
  return storage;
}

ValueAt Body::index(const Ast &n) {
  auto v = expression(n->children[0]);
  if (v.type == p.neverTy) return v;
  requireInit(v, n->span);
  auto *t = v.type;
  if (t->kind == Kind::Box)
    fail(n->span, "indexing a Box payload requires explicit dereference", "E230");
  if (!v.borrowed && p.drops(t))
    fail(n->span, "bind a resource-owning array before indexing or borrowing it", "E290");
  if (t->kind == Kind::Class && t->def.file->standard && t->def.file->package == "coll" &&
      t->def.decl->name == "Vec") {
    auto call = std::make_shared<Node>();
    call->kind = NodeKind::Call;
    call->span = n->span;
    call->children = {nullptr};
    // Vec subscripting uses an asSlice overload. During argument
    // evaluation a reserved Vec is still readable, but cannot produce a
    // mutable slice while its receiver reservation is pending.
    if (v.writable && hasReceiverReservation(v)) v.writable = false;
    v = invoke(call, p.methods(t, "asSlice", false), {}, v, t);
    t = v.type;
  }
  if (t->kind != Kind::Array && t->kind != Kind::Slice) fail(n->span, "indexing requires an array or slice");
  auto *element = t->parts[0];
  auto *zero = ConstantInt::get(p.sizeTy->ir, 0);
  Value *count, *address;
  bool writable;
  if (t->kind == Kind::Array) {
    count = ConstantInt::get(p.sizeTy->ir, t->count);
    if (n->kind == NodeKind::Slice && p.hasReference(t))
      fail(n->span, "reference-bearing arrays cannot expose a raw slice", "E230");
    address = arrayStorage(v, n->span, temporaryScope);
    writable = v.writable;
  } else {
    auto *slice = read(v, n->span);
    count = ir.CreateExtractValue(slice, 1);
    address = ir.CreateExtractValue(slice, 0);
    writable = !t->readOnly;
  }
  auto bound = [&](const Ast &node, bool inclusive) -> Value * {
    auto x = expression(node);
    if (x.type == p.neverTy) return nullptr;
    if (x.type->kind != Kind::Int) fail(node->span, "array index must be an integer");
    auto *raw = read(x, node->span);
    auto *width = ir.getIntNTy(std::max(x.type->bits, p.pointerBits()));
    auto *i = ir.CreateIntCast(raw, width, x.type->sign);
    auto *length = ir.CreateZExtOrTrunc(count, width);
    Value *ok = inclusive ? ir.CreateICmpULE(i, length) : ir.CreateICmpULT(i, length);
    if (x.type->sign) ok = ir.CreateAnd(ok, ir.CreateICmpSGE(raw, ConstantInt::get(raw->getType(), 0)));
    guard(ir, ok);
    return ir.CreateZExtOrTrunc(i, p.sizeTy->ir);
  };
  auto offset = [&](Value *i) -> Value * {
    if (t->kind == Kind::Array) return ir.CreateGEP(t->ir, address, {ir.getInt32(0), i});
    return ir.CreateGEP(p.storage(element), address, i);
  };
  if (n->kind == NodeKind::Index) {
    auto *i = bound(n->children[1], false);
    if (!i) return neverValue();
    Borrow origin;
    if (t->kind == Kind::Array) {
      origin = v.origin;
    } else if (t->readOnly && v.origin.checkedView && v.origin.root >= 0 && !v.origin.raw) {
      // Scoped view inputs also accept ordinary slice descriptors. Their
      // parameter slots do not prove the lifetime of the storage underneath.
      auto isViewInput = [&](int root) {
        const auto &source = locals[root];
        return source.reference && source.origin.checkedView && source.origin.root == root;
      };
      if (!isViewInput(v.origin.root) &&
          (!v.origin.alternatives || std::ranges::none_of(*v.origin.alternatives, isViewInput))) {
        origin = v.origin;
        origin.checkedView = false; // The element is a regular scalar place.
      }
    }
    return {element,
            nullptr,
            offset(i),
            writable,
            t->kind == Kind::Array ? v.local : -1,
            true,
            v.temporaryBorrow,
            std::move(origin)};
  }
  Value *lo = zero, *hi = count;
  if (n->children.size() > 1) {
    auto range = n->children[1];
    if (range->text != "..") fail(range->span, "slice ranges must use '..'");
    // Evaluate both endpoints before making the range check.
    auto low = expression(range->children[0]);
    if (low.type == p.neverTy) return low;
    if (low.type->kind != Kind::Int) fail(range->span, "slice bounds must be integers");
    auto *left = read(low, range->span);
    hi = bound(range->children[1], true);
    if (!hi) return neverValue();
    auto *width = ir.getIntNTy(std::max(low.type->bits, p.pointerBits()));
    auto *wide = ir.CreateIntCast(left, width, low.type->sign);
    Value *valid = ir.CreateICmpULE(wide, ir.CreateZExtOrTrunc(hi, width));
    if (low.type->sign)
      valid = ir.CreateAnd(valid, ir.CreateICmpSGE(left, ConstantInt::get(left->getType(), 0)));
    guard(ir, valid);
    lo = ir.CreateZExtOrTrunc(wide, p.sizeTy->ir);
  }
  auto *slice = p.slice(element, !writable);
  auto *out = ir.CreateInsertValue(Constant::getNullValue(slice->ir), offset(lo), 0);
  auto origin = p.readonlyView(slice) ? viewOrigin(v, t->kind == Kind::Array) : Borrow{};
  return trackView({slice, ir.CreateInsertValue(out, ir.CreateSub(hi, lo), 1), nullptr, false, -1,
                    false, v.temporaryBorrow, origin}, n->span);
}

ValueAt Body::unary(const Ast &n, Type *expected) {
  const auto &op = n->text;
  if (op == "ref" || op == "ref mut") {
    struct Guard {
      unsigned &depth;
      ~Guard() { --depth; }
    } guard{explicitBorrowDepth};
    ++explicitBorrowDepth;
    auto place = expression(n->children[0]);
    if (!live()) return place;
    if (!place.address) fail(n->span, "ref requires an existing storage location", "E230");
    return borrow(place, p.reference(place.type, op == "ref"), n->span, false);
  }
  auto v = expression(n->children[0], op == "!" ? p.boolTy : nullptr);
  if (v.type == p.neverTy) return v;
  if (op == "raw") {
    auto *reference = referenceHint(n->children[0]);
    if (reference && reference->kind == Kind::Reference)
      fail(n->span, "use toRaw() to convert a safe reference", "E230");
    if (!v.address) fail(n->span, "address-of requires an existing storage location");
    return asPointer(v, n->span);
  }
  if (op == "*") {
    return dereference(v, n->span);
  }
  if (op == "post!" || op == "post?") {
    if (deferredCleanup)
      fail(n->span, "deferred cleanup cannot propagate; handle the result or absence locally", "E231");
    const bool optional = op == "post?";
    Type *emptyChannel = nullptr;
    if (optional) {
      if (v.type->kind != Kind::Optional)
        fail(n->span, "postfix '?' requires an Optional operand, found '" + v.type->name + "'", "E201");
      if (!result)
        fail(n->span, "postfix '?' requires an explicit or contextual Optional return type",
             evaluating ? "E220" : "E201");
      emptyChannel = result->kind == Kind::Result ? result->parts[0] : result;
      if (emptyChannel->kind != Kind::Optional) {
        if (result->kind == Kind::Result)
          fail(n->span,
               "postfix '?' requires '?U' or '!?U'; use 'value ?? { return LookupError.NotFound; }' to map "
               "absence to an error",
               "E201");
        fail(n->span, "postfix '?' requires an Optional return channel ('?U' or '!?U')", "E201");
      }
      if (v.borrowed && !p.copyable(v.type->parts[0]))
        fail(n->span, "cannot copy the Optional payload; use 'move(value)?' to consume a movable owner",
             "E230");
    } else if (v.type->kind != Kind::Result || !result || result->kind != Kind::Result) {
      fail(n->span, "error propagation requires a result and a fallible function");
    }
    auto *raw = read(v, n->span, false);
    auto *tag = ir.CreateExtractValue(raw, 0);
    auto *success = block("try.ok"), *failure = block(optional ? "try.null" : "try.error");
    ir.CreateCondBr(optional ? tag : ir.CreateICmpEQ(tag, ir.getInt32(0)), success, failure);
    ir.SetInsertPoint(failure);
    if (optional) {
      auto *empty = Constant::getNullValue(emptyChannel->ir);
      emitReturn(emptyChannel == result ? ValueAt{result, empty} : wrap(result, empty, true), n->span);
    } else {
      emitReturn({p.errorTy, tag}, n->span);
    }
    ir.SetInsertPoint(success);
    auto out = payload(v, 1, v.type->parts[0]);
    return {out.type, read(out, n->span), nullptr, false, -1, false, out.temporaryBorrow, out.origin};
  }
  auto *raw = read(v, n->span);
  if (op == "!") return {p.boolTy, ir.CreateNot(raw)};
  if (v.type->kind == Kind::Float && op == "-") return {v.type, ir.CreateFNeg(raw)};
  if (v.type->kind != Kind::Int) fail(n->span, "integer operand required for '" + op + "'");
  auto *t = v.type;
  if (op == "~") return {t, ir.CreateNot(raw)};
  (void)expected;
  return {t, binaryNumber(ir, "-", ConstantInt::get(t->ir, 0), raw, t->sign, t->sign)};
}

ValueAt Body::operate(std::string_view op, ValueAt a, ValueAt b, Span span) {
  if (a.type == p.neverTy) return a;
  if (b.type == p.neverTy) return b;
  if (a.type == p.boolTy || a.type == p.charTy || a.type == p.errorTy || a.type->kind == Kind::Pointer) {
    if (a.type != b.type || (op != "==" && op != "!="))
      fail(span, "incompatible operands for '" + std::string(op) + "'");
    if (a.type->kind == Kind::Pointer && !a.type->ir->isPointerTy()) {
      auto *left = read(a, span), *right = read(b, span);
      auto *same = ir.CreateAnd(ir.CreateICmpEQ(ir.CreateExtractValue(left, 0), ir.CreateExtractValue(right, 0)),
                                ir.CreateICmpEQ(ir.CreateExtractValue(left, 1), ir.CreateExtractValue(right, 1)));
      return {p.boolTy, op == "==" ? same : ir.CreateNot(same)};
    }
    return {p.boolTy, binaryNumber(ir, op, read(a, span), read(b, span), false, false)};
  }
  if (a.type->kind == Kind::Enum && a.type == b.type &&
      std::ranges::all_of(a.type->variants, [](const auto &v) { return v.fields.empty(); })) {
    if (op != "==" && op != "!=") fail(span, "enum values support only equality comparison");
    return {p.boolTy,
            binaryNumber(ir, op, read(payload(a, 0, p.integer(a.type->bits, a.type->sign)), span),
                         read(payload(b, 0, p.integer(b.type->bits, b.type->sign)), span), false, false)};
  }
  bool shift = op.starts_with("<<") || op == ">>";
  auto *at = a.type, *bt = b.type;
  auto *av = read(a, span), *bv = read(b, span);
  if ((shift && (at->kind != Kind::Int || bt->kind != Kind::Int)) || (!shift && at != bt) ||
      (at->kind != Kind::Int && at->kind != Kind::Float))
    fail(span,
         "incompatible operands '" + at->name + "' and '" + bt->name + "' for '" + std::string(op) + "'");
  auto *r = binaryNumber(ir, op, av, bv, at->sign, bt->sign);
  if (!r) fail(span, "operator '" + std::string(op) + "' is unavailable for '" + at->name + "'");
  bool compare = op == "==" || op == "!=" || op == "<" || op == "<=" || op == ">" || op == ">=";
  return {compare ? p.boolTy : at, r};
}

ValueAt Body::compareText(const Ast &n, ValueAt left) {
  // Keep each owning operand alive until after both operands have been evaluated.
  enter();
  auto view = [&](ValueAt value, const std::string &name) -> Value * {
    if (value.type != p.viewTy && value.type != p.textTy)
      fail(n->span, "text equality requires two text operands");
    if (p.drops(value.type)) {
      int owner = bind(name, value.type, false, read(value, n->span), n->span);
      value = {value.type, nullptr, locals[owner].slot, false, owner, true};
    }
    return textView(value, n->span);
  };
  auto *a = view(left, " <text left>");
  auto right = expression(n->children[1]);
  if (!live()) {
    leave();
    return neverValue();
  }
  auto *b = view(right, " <text right>");
  auto *aLength = ir.CreateExtractValue(a, 1);
  auto *bLength = ir.CreateExtractValue(b, 1);
  auto *aData = ir.CreateExtractValue(a, 0);
  auto *bData = ir.CreateExtractValue(b, 0);
  auto *from = ir.GetInsertBlock();
  auto *header = block("text.compare"), *bytes = block("text.bytes"), *advance = block("text.advance"),
       *end = block("text.compared");
  ir.CreateCondBr(ir.CreateICmpEQ(aLength, bLength), header, end);
  ir.SetInsertPoint(header);
  auto *index = ir.CreatePHI(p.sizeTy->ir, 2);
  index->addIncoming(ConstantInt::get(p.sizeTy->ir, 0), from);
  ir.CreateCondBr(ir.CreateICmpEQ(index, aLength), end, bytes);
  ir.SetInsertPoint(bytes);
  auto *byte = ir.getInt8Ty();
  auto *aByte = ir.CreateLoad(byte, ir.CreateGEP(byte, aData, index));
  auto *bByte = ir.CreateLoad(byte, ir.CreateGEP(byte, bData, index));
  ir.CreateCondBr(ir.CreateICmpEQ(aByte, bByte), advance, end);
  ir.SetInsertPoint(advance);
  index->addIncoming(ir.CreateAdd(index, ConstantInt::get(p.sizeTy->ir, 1)), advance);
  ir.CreateBr(header);
  ir.SetInsertPoint(end);
  auto *same = ir.CreatePHI(ir.getInt1Ty(), 3);
  same->addIncoming(ir.getFalse(), from);
  same->addIncoming(ir.getTrue(), header);
  same->addIncoming(ir.getFalse(), bytes);
  Value *result = n->text == "==" ? static_cast<Value *>(same) : ir.CreateNot(same);
  leave();
  return {p.boolTy, result};
}

ValueAt Body::binary(const Ast &n, Type *expected) {
  auto &op = n->text;
  if (op == "catch" || op == "??") return fallback(n, expected);
  if (op == ".." || op == "..=")
    fail(n->span, "range values are currently supported in for and slicing", "E290");
  if (op == "&&" || op == "||") {
    auto a = expression(n->children[0], p.boolTy);
    if (a.type == p.neverTy) return a;
    auto *v = read(a, n->span);
    auto *from = ir.GetInsertBlock(), *rhs = block("logic.rhs"), *end = block("logic.end");
    auto initial = initialized;
    ir.CreateCondBr(v, op == "&&" ? rhs : end, op == "&&" ? end : rhs);
    ir.SetInsertPoint(rhs);
    auto b = expression(n->children[1], p.boolTy);
    Value *right = nullptr;
    BasicBlock *rightBlock = nullptr;
    if (live()) {
      right = read(b, n->span);
      rightBlock = ir.GetInsertBlock();
      ir.CreateBr(end);
    }
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(p.boolTy->ir, 2);
    phi->addIncoming(ir.getInt1(op == "||"), from);
    if (right) phi->addIncoming(right, rightBlock);
    merge(right ? std::vector{initial, initialized} : std::vector<std::vector<bool>>{initial},
          initial.size());
    return {p.boolTy, phi};
  }
  bool shift = op.starts_with("<<") || op == ">>";
  if (op == "==" || op == "!=") {
    bool leftNull = n->children[0]->kind == NodeKind::Null;
    bool rightNull = n->children[1]->kind == NodeKind::Null;
    if (leftNull != rightNull) {
      auto operand = expression(n->children[leftNull ? 1 : 0]);
      if (!live()) return neverValue();
      if (operand.type->kind != Kind::Optional) fail(n->span, "null comparison requires an optional value");
      auto *tag = read(payload(operand, 0, p.boolTy), n->span, false);
      disposeTemporary(operand, n->span);
      return {p.boolTy, op == "==" ? ir.CreateNot(tag) : tag};
    }
  }
  auto *leftHint = hint(n->children[0]);
  auto *rightHint = hint(n->children[1]);
  auto wants = [&](const Ast &a, Type *t) -> Type * {
    if (t && mathInteger(a) && t->kind == Kind::Int) return t;
    if (t && a->kind == NodeKind::Number && parseNumber(a->text).floating &&
        parseNumber(a->text).suffix.empty() && t->kind == Kind::Float)
      return t;
    return nullptr;
  };
  auto left = expression(n->children[0], wants(n->children[0], shift ? expected : rightHint));
  if (left.type == p.neverTy) return left;
  if (left.type->kind == Kind::Class) {
    auto defs = p.methods(left.type, "operator" + op, false);
    if (defs.empty()) fail(n->span, "class does not implement '" + op + "'");
    auto fake = std::make_shared<Node>(*n);
    fake->children = {nullptr, n->children[1]};
    return invoke(fake, defs, {}, left, left.type);
  }
  if ((left.type == p.textTy || left.type == p.viewTy) && (op == "==" || op == "!="))
    return compareText(n, left);
  left = {left.type, read(left, n->span, false)};
  auto right =
      expression(n->children[1], shift ? nullptr : wants(n->children[1], leftHint ? leftHint : left.type));
  return operate(op, left, right, n->span);
}

ValueAt Body::chain(const Ast &n) {
  auto first = n->children[0];
  Type *context = nullptr;
  if (mathInteger(first)) {
    for (const auto &operand : llvm::ArrayRef(n->children).drop_front()) {
      if (mathInteger(operand)) continue;
      auto *type = hint(operand);
      if (type && type->kind == Kind::Int) context = type;
      break;
    }
  }
  auto left = expression(first, context);
  if (left.type == p.neverTy) return left;
  left = {left.type, read(left, n->span, false)};
  auto *end = block("compare.end");
  std::vector<std::pair<Value *, BasicBlock *>> edges;
  std::vector<std::vector<bool>> states;
  auto size = initialized.size();
  for (std::size_t i = 0; i < n->names.size(); ++i) {
    auto next = n->children[i + 1];
    auto *t = left.type;
    auto right = expression(next, mathInteger(next) && t->kind == Kind::Int ? t : nullptr);
    if (right.type == p.neverTy) break;
    right = {right.type, read(right, n->span, false)};
    auto v = operate(n->names[i], left, right, n->span);
    auto *from = ir.GetInsertBlock();
    states.push_back(initialized);
    if (i + 1 == n->names.size()) {
      edges.emplace_back(v.value, from);
      ir.CreateBr(end);
    } else {
      auto *continuation = block("compare.next");
      edges.emplace_back(ir.getFalse(), from);
      ir.CreateCondBr(v.value, continuation, end);
      ir.SetInsertPoint(continuation);
    }
    left = right;
  }
  ir.SetInsertPoint(end);
  if (edges.empty()) {
    ir.CreateUnreachable();
    return neverValue();
  }
  merge(states, size);
  auto *phi = ir.CreatePHI(p.boolTy->ir, edges.size());
  for (auto [v, b] : edges)
    phi->addIncoming(v, b);
  return {p.boolTy, phi};
}

ValueAt Body::fallback(const Ast &n, Type *expected) {
  (void)expected;
  bool caught = n->text == "catch";
  const auto &handler = n->children[1];
  if (deferredCleanup && caught && handler->kind == NodeKind::Block &&
      handler->children.empty() && !handler->tail)
    fail(handler->span, "deferred cleanup must explicitly handle errors; empty catch discards them", "E231");
  auto v = expression(n->children[0]);
  if (v.type == p.neverTy) return v;
  if (v.type->kind != (caught ? Kind::Result : Kind::Optional))
    fail(n->span, "invalid left operand for '" + n->text + "'");
  auto *data = read(v, n->span, false);
  auto *tag = ir.CreateExtractValue(data, 0);
  auto *ok = block("fallback.value"), *no = block("fallback.other"), *end = block("fallback.end");
  auto *inner = v.type->parts[0];
  auto initial = initialized;
  ir.CreateCondBr(caught ? ir.CreateICmpEQ(tag, ir.getInt32(0)) : tag, ok, no);
  ir.SetInsertPoint(ok);
  auto *value = read(payload(v, 1, inner), n->span);
  auto *good = ir.GetInsertBlock();
  ir.CreateBr(end);
  ir.SetInsertPoint(no);
  enter();
  if (!n->names.empty()) {
    auto id = bind(n->names[0], p.errorTy, false, tag, n->span);
    indexLocal(id, n->bindingSpans[0]);
  }
  auto alt = expression(n->children[1], inner);
  Value *other = live() ? read(alt, n->span) : nullptr;
  leave();
  auto *bad = ir.GetInsertBlock();
  if (other) ir.CreateBr(end);
  ir.SetInsertPoint(end);
  auto *phi = ir.CreatePHI(p.storage(inner), 2);
  phi->addIncoming(value, good);
  if (other) phi->addIncoming(other, bad);
  merge(other ? std::vector{initial, initialized} : std::vector<std::vector<bool>>{initial}, initial.size());
  Borrow origin = v.origin;
  const bool reference = p.hasReference(inner);
  const bool view = p.optionalReadonlyView(v.type);
  bool borrowPayload = (reference || view) && v.hasBorrowPayload();
  if (other) {
    if (reference || view) mergeValueBorrow(origin, borrowPayload, alt, n->span);
    else if (!(v.origin == alt.origin)) origin = {};
  }
  return trackView({inner, phi, nullptr, false, -1, false,
                    v.temporaryBorrow || alt.temporaryBorrow, origin, reference && !borrowPayload}, n->span);
}

ValueAt Body::castNumber(Type *to, const Ast &arg, Span span) {
  if (to->kind == Kind::Int && mathInteger(arg)) return expression(arg, to);
  auto v = expression(arg, to->kind == Kind::Float && arg->kind == NodeKind::Number &&
                                   parseNumber(arg->text).floating && parseNumber(arg->text).suffix.empty()
                               ? to
                               : nullptr);
  if (v.type == p.neverTy || v.type == to) return v;
  auto *from = v.type;
  if (from->kind == Kind::Enum && to->kind == Kind::Int &&
      std::ranges::all_of(from->variants, [](const auto &a) { return a.fields.empty(); })) {
    v = payload(v, 0, p.integer(from->bits, from->sign));
    from = v.type;
  }
  auto *raw = read(v, span);
  if (to->kind == Kind::Float) {
    if (from->kind == Kind::Float) return {to, ir.CreateFPCast(raw, to->ir)};
    if (from->kind == Kind::Int)
      return {to, from->sign ? ir.CreateSIToFP(raw, to->ir) : ir.CreateUIToFP(raw, to->ir)};
  }
  if (to->kind != Kind::Int || from->kind != Kind::Int) fail(span, "invalid explicit conversion");
  auto *converted = ir.CreateIntCast(raw, to->ir, from->sign);
  bool total = to->sign == from->sign ? to->bits >= from->bits : to->sign && to->bits > from->bits;
  if (total) return {to, converted};
  auto *result = p.aggregate(Kind::Result, {to});
  unsigned bits = std::max(to->bits, from->bits) + 1;
  auto *wide = ir.getIntNTy(bits);
  auto *rawWide = ir.CreateIntCast(raw, wide, from->sign);
  auto *back = ir.CreateIntCast(converted, wide, to->sign);
  auto *ok = ir.CreateICmpEQ(rawWide, back);
  if (auto *valid = dyn_cast<ConstantInt>(ok); valid && valid->isZero())
    fail(span, "statically known conversion is out of range", "E211");
  return {result, ir.CreateSelect(ok, wrap(result, converted, true).value,
                                  wrap(result, ir.getInt32(0xfffffffe), false).value)};
}

ValueAt Body::builtin(std::string_view key, const Ast &n, const std::vector<Ast> &types, Type *expected) {
  auto arity = [&](unsigned count) {
    if (n->children.size() != count + 1) fail(n->span, "wrong number of arguments to " + std::string(key));
  };
  auto unsafe = [&] {
    if (!unsafeDepth) fail(n->span, "call to '" + std::string(key) + "' requires an unsafe block", "E240");
    if (evaluating) fail(n->span, "unsafe built-in is unavailable during compile-time evaluation", "E220");
  };
  auto evaluationType = [&] {
    if (types.size() != 1) fail(n->span, "expected one type argument");
    return parseType(types[0]);
  };
  if (key == "some" || key == "ok") {
    arity(1);
    auto kind = key == "some" ? Kind::Optional : Kind::Result;
    auto *target = expected;
    while (target && target->kind != kind && (target->kind == Kind::Result || target->kind == Kind::Optional))
      target = target->parts[0];
    auto value = expression(n->children[1], target && target->kind == kind ? target->parts[0] : nullptr);
    if (!live()) return neverValue();
    auto *wrappedType = p.aggregate(kind, {value.type});
    if (!p.optionalReadonlyView(wrappedType)) rejectViewEscape(value, n->span);
    auto wrapped = wrap(wrappedType, read(value, n->span), true);
    wrapped.temporaryBorrow = value.temporaryBorrow;
    wrapped.origin = value.origin;
    wrapped.noPayload = value.noPayload;
    return wrapped;
  }
  if (key == "bitCast") {
    arity(1);
    auto *to = evaluationType();
    auto value = expression(n->children[1]);
    if (!live()) return neverValue();
    auto admissible = [&](Type *t) {
      return (t->kind == Kind::Float || t->kind == Kind::Int) && t->bits % 8 == 0 &&
             p.module->getDataLayout().getTypeAllocSize(t->ir).getFixedValue() * 8 == t->bits;
    };
    if (!admissible(to) || !admissible(value.type) || to->bits != value.type->bits)
      fail(n->span, "bitCast requires padding-free integer or float types with equal widths");
    return {to, ir.CreateBitCast(read(value, n->span), to->ir)};
  }
  if (key == "ptrFromInt" || key == "ptrCast" || key == "constCast") {
    arity(1);
    unsafe();
    auto *to = evaluationType();
    if (to->kind != Kind::Pointer || !to->ir->isPointerTy())
      fail(n->span, "intrinsic requires an ordinary pointer type");
    auto v =
        expression(n->children[1], key == "ptrFromInt" && mathInteger(n->children[1]) ? p.sizeTy : nullptr);
    if (!live()) return neverValue();
    auto *raw = read(v, n->span);
    if (key == "ptrFromInt") {
      if (v.type->kind != Kind::Int) fail(n->span, "ptrFromInt requires an integer address");
      auto *addr = ir.CreateIntCast(raw, p.sizeTy->ir, v.type->sign);
      auto *valid = ir.CreateICmpNE(addr, ConstantInt::get(p.sizeTy->ir, 0));
      if (v.type->bits > p.pointerBits())
        valid = ir.CreateAnd(valid, ir.CreateICmpEQ(raw, ir.CreateZExt(addr, raw->getType())));
      if (v.type->sign)
        valid = ir.CreateAnd(valid, ir.CreateICmpSGE(raw, ConstantInt::get(raw->getType(), 0)));
      if (auto *c = dyn_cast<ConstantInt>(valid); c && c->isZero())
        fail(n->span, "invalid nonempty target address");
      guard(ir, valid);
      return {to, ir.CreateIntToPtr(addr, to->ir)};
    }
    if (v.type->kind != Kind::Pointer || !v.type->ir->isPointerTy() || (key == "ptrCast" && v.type->readOnly && !to->readOnly) ||
        (key == "constCast" && v.type->parts != to->parts))
      fail(n->span, "invalid pointer conversion or permission change");
    return {to, raw};
  }
  if (key == "volatileLoad" || key == "volatileStore") {
    bool write = key == "volatileStore";
    arity(write ? 2 : 1);
    unsafe();
    auto address = expression(n->children[1]);
    if (!live()) return neverValue();
    if (address.type->kind != Kind::Pointer || (write && address.type->readOnly))
      fail(n->span, "volatile access requires a suitably qualified pointer");
    auto *t = address.type->parts[0];
    unsigned maxWidth = p.targetTriple().isThumb() ? 32 : 64;
    if ((t->kind != Kind::Int && t->kind != Kind::Float) ||
        !(t->bits == 8 || t->bits == 16 || t->bits == 32 || t->bits == 64) || t->bits > maxWidth)
      fail(n->span, "target does not support volatile access at this width");
    auto *ptr = read(address, n->span);
    if (!write) return {t, ir.CreateLoad(t->ir, ptr, true)};
    auto v = expression(n->children[2], t);
    if (!live()) return neverValue();
    ir.CreateStore(read(v, n->span), ptr, true);
    return voidValue();
  }
  if (key == "truncToInt" || key == "roundToInt" || key == "floorToInt" || key == "ceilToInt") {
    arity(1);
    auto *to = evaluationType();
    auto v = expression(n->children[1]);
    if (!live()) return neverValue();
    if (to->kind != Kind::Int || v.type->kind != Kind::Float)
      fail(n->span, "expected a float and integer target type");
    auto *raw = read(v, n->span);
    auto intr = key == "truncToInt" ? Intrinsic::trunc : key == "floorToInt" ? Intrinsic::floor :
                key == "ceilToInt" ? Intrinsic::ceil : Intrinsic::roundeven;
    auto *rounded =
        ir.CreateCall(Intrinsic::getOrInsertDeclaration(p.module.get(), intr, {v.type->ir}), {raw});
    auto bounds = [&](bool lower) -> Constant * {
      const auto &format = v.type->bits == 32 ? APFloat::IEEEsingle() : APFloat::IEEEdouble();
      APFloat f(format);
      auto n = APInt::getOneBitSet(to->bits + 2, to->sign ? to->bits - 1 : to->bits);
      if (lower) n = to->sign ? -n : APInt(n.getBitWidth(), 0);
      f.convertFromAPInt(n, true, APFloat::rmNearestTiesToEven);
      return ConstantFP::get(p.context, f);
    };
    auto *valid =
        ir.CreateAnd(ir.CreateFCmpOGE(rounded, bounds(true)), ir.CreateFCmpOLT(rounded, bounds(false)));
    auto *safe = ir.CreateSelect(valid, rounded, ConstantFP::get(v.type->ir, 0.0));
    auto *converted = to->sign ? ir.CreateFPToSI(safe, to->ir) : ir.CreateFPToUI(safe, to->ir);
    auto *t = p.aggregate(Kind::Result, {to});
    return {t, ir.CreateSelect(valid, wrap(t, converted, true).value,
                               wrap(t, ir.getInt32(0xfffffffe), false).value)};
  }
  if (key == "panic" || key == "io.println") {
    arity(1);
    if (evaluating) fail(n->span, "host I/O is unavailable during compile-time evaluation", "E220");
    bool panic = key == "panic";
    if (!panic && p.options.profile != "hosted")
      fail(n->span, "io.println requires the hosted profile", "E290");
    auto v = expression(n->children[1]);
    if (!live()) return neverValue();
    if (panic && v.type != p.textTy && v.type != p.viewTy)
      fail(n->span, "panic requires a text message");
    auto *value = read(v, n->span, false);
    Value *success = ir.getTrue();
    if (p.options.profile == "hosted") {
      auto *ptr = PointerType::getUnqual(p.context);
      auto *file = p.standardStream(ir, panic);
      if (v.type == p.textTy || v.type == p.viewTy) {
        auto write = p.module->getOrInsertFunction(
            "fwrite", FunctionType::get(p.sizeTy->ir, {ptr, p.sizeTy->ir, p.sizeTy->ir, ptr}, false));
        auto newline = p.module->getOrInsertFunction(
            "fputc", FunctionType::get(ir.getInt32Ty(), {ir.getInt32Ty(), ptr}, false));
        auto *len = ir.CreateExtractValue(value, 1);
        auto *written = ir.CreateCall(
            write, {ir.CreateExtractValue(value, 0), ConstantInt::get(p.sizeTy->ir, 1), len, file});
        auto *end = ir.CreateCall(newline, {ir.getInt32(10), file});
        success = ir.CreateAnd(ir.CreateICmpEQ(written, len), ir.CreateICmpSGE(end, ir.getInt32(0)));
      } else {
        std::string format;
        if (v.type->kind == Kind::Int && v.type->bits <= 64) {
          format = v.type->sign ? "%lld\n" : "%llu\n";
          value = ir.CreateIntCast(value, ir.getInt64Ty(), v.type->sign);
        } else if (v.type->kind == Kind::Float) {
          format = "%g\n";
          if (v.type->bits == 32) value = ir.CreateFPExt(value, ir.getDoubleTy());
        } else if (v.type == p.boolTy) {
          format = "%s\n";
          value = ir.CreateSelect(value, p.bytes("true"), p.bytes("false"));
        } else
          fail(n->span, "io.println supports text, bool, floats and integers up to 64 bits");
        auto print = p.module->getOrInsertFunction("printf", FunctionType::get(ir.getInt32Ty(), {ptr}, true));
        success = ir.CreateICmpSGE(ir.CreateCall(print, {p.bytes(format), value}), ir.getInt32(0));
      }
      auto flush = p.module->getOrInsertFunction("fflush", FunctionType::get(ir.getInt32Ty(), {ptr}, false));
      success = ir.CreateAnd(success, ir.CreateICmpEQ(ir.CreateCall(flush, {file}), ir.getInt32(0)));
    }
    if (panic) {
      ir.CreateCall(Intrinsic::getOrInsertDeclaration(p.module.get(), Intrinsic::trap));
      ir.CreateUnreachable();
      return neverValue();
    }
    disposeTemporary(v, n->span);
    auto *result = p.aggregate(Kind::Result, {p.voidTy});
    return {result, ir.CreateSelect(success, wrap(result, voidValue().value, true).value,
                                    wrap(result, ir.getInt32(0xfffffffd), false).value)};
  }
  fail(n->span, "unknown built-in '" + std::string(key) + "'");
}

ValueAt Body::invoke(const Ast &n, const std::vector<Definition> &defs, const std::vector<Ast> &args,
                     std::optional<ValueAt> receiver, Type *classType, bool delegation, DeferRegistration *defer) {
  struct Candidate {
    Definition definition;
    Types substitutions;
    Constants constants;
    std::vector<Type *> parameters;
    std::vector<unsigned> ranks;
  };
  std::vector<Candidate> candidates;
  for (auto def : defs) {
    const auto &d = *def.decl;
    if (d.kind != DeclKind::Function || d.params.size() + 1 != n->children.size()) continue;
    if (classType && (!d.visible || !def.publicPath) && owner != classType) continue;
    if (receiver && !d.readOnly && !receiver->writable) continue;
    if (args.size() > d.generics.size()) continue;
    auto *declaring = def.declaringType ? def.declaringType : classType;
    Types subst = declaring ? p.classTypes(declaring) : Types{};
    Constants constants = declaring ? declaring->comptimeArguments : Constants{};
    std::set<std::string> typeNames, valueNames;
    for (std::size_t i = args.size(); i < d.generics.size(); ++i) {
      const auto &g = d.generics[i];
      (g.comptime ? valueNames : typeNames).insert(g.name);
    }
    bool consistent = true;
    Constants inferredValues;
    struct AliasPattern {
      const Definition &definition;
      const std::vector<Ast> &arguments;
      const AliasPattern *parent;
    };
    auto expandParameter = [](Ast &pattern, const AliasPattern *&alias) {
      while (alias && pattern && pattern->children.empty() &&
             (pattern->kind == NodeKind::TypeName || pattern->kind == NodeKind::Name)) {
        const auto &parameters = alias->definition.decl->generics;
        auto parameter = std::ranges::find(parameters, pattern->text, &Parameter::name);
        if (parameter == parameters.end()) break;
        pattern = alias->arguments[parameter - parameters.begin()];
        alias = alias->parent;
      }
    };
    auto inferValue = [&](Ast pattern, std::pair<Type *, Constant *> value, const AliasPattern *alias) {
      expandParameter(pattern, alias);
      if (alias || !pattern || (pattern->kind != NodeKind::TypeName && pattern->kind != NodeKind::Name) ||
          !pattern->children.empty() || !valueNames.contains(pattern->text))
        return;
      auto [it, inserted] = inferredValues.emplace(pattern->text, value);
      consistent &= inserted || it->second == value;
    };
    auto infer = [&](auto &&recurse, Ast pattern, Type *actual, const AliasPattern *alias = nullptr) -> void {
      if (!actual) return;
      expandParameter(pattern, alias);
      if (pattern->kind == NodeKind::TypeName && !alias && pattern->children.empty() &&
          typeNames.contains(pattern->text)) {
        auto [it, inserted] = subst.emplace(pattern->text, actual);
        consistent &= inserted || it->second == actual;
      } else if (pattern->kind == NodeKind::TypeName && !pattern->children.empty()) {
        const auto &patternFile = *(alias ? alias->definition.file : def.file);
        auto matches = p.resolve(patternFile, pattern->text, pattern->span);
        if (matches.size() != 1) return;
        const auto &match = matches.front();
        if (match.decl->kind == DeclKind::Alias) {
          if (pattern->children.size() != match.decl->generics.size()) return;
          for (auto *outer = alias; outer; outer = outer->parent)
            if (outer->definition.decl == match.decl) return; // Instantiation diagnoses cyclic aliases.
          AliasPattern expansion{match, pattern->children, alias};
          recurse(recurse, match.decl->type, actual, &expansion);
          return;
        }
        if ((actual->kind != Kind::Class && actual->kind != Kind::Enum) || match.decl != actual->def.decl)
          return;
        const auto &parameters = actual->def.decl->generics;
        if (pattern->children.size() != parameters.size()) return;
        std::size_t typeIndex = 0;
        for (std::size_t i = 0; i < parameters.size(); ++i) {
          if (parameters[i].comptime)
            inferValue(pattern->children[i], actual->comptimeArguments.at(parameters[i].name), alias);
          else
            recurse(recurse, pattern->children[i], actual->parts[typeIndex++], alias);
        }
      } else if (pattern->kind == NodeKind::TypeArray && actual->kind == Kind::Array) {
        inferValue(pattern->children[0], {p.sizeTy, ConstantInt::get(p.sizeTy->ir, actual->count)}, alias);
        recurse(recurse, pattern->children[1], actual->parts[0], alias);
      } else if (pattern->kind == NodeKind::TypeTuple && actual->kind == Kind::Tuple &&
                 pattern->children.size() == actual->parts.size()) {
        for (std::size_t i = 0; i < pattern->children.size(); ++i)
          recurse(recurse, pattern->children[i], actual->parts[i], alias);
      } else if (pattern->kind == NodeKind::TypeFunction && actual->kind == Kind::Function &&
                 pattern->children.size() + 1 == actual->parts.size()) {
        for (std::size_t i = 0; i < pattern->children.size(); ++i)
          recurse(recurse, pattern->children[i], actual->parts[i], alias);
        recurse(recurse, pattern->type, actual->parts.back(), alias);
      } else if (pattern->kind == NodeKind::TypePrefix) {
        const auto &op = pattern->text;
        if (parameterMode(pattern) == ParameterMode::Move)
          recurse(recurse, pattern->children[0], actual, alias);
        else if (parameterMode(pattern) == ParameterMode::In ||
                 parameterMode(pattern) == ParameterMode::Mut)
          recurse(recurse, pattern->children[0], actual->kind == Kind::Reference ? actual->parts[0] : actual, alias);
        else if (!actual->parts.empty() && (((op == "*" || op == "*const") && actual->kind == Kind::Pointer) ||
                 ((op == "ref" || op == "ref mut") && actual->kind == Kind::Reference) ||
                 ((op == "[]" || op == "[]const") && actual->kind == Kind::Slice) ||
                 (op == "?" && actual->kind == Kind::Optional) ||
                 (op == "!" && actual->kind == Kind::Result) || (op == "^" && actual->kind == Kind::Box)))
          recurse(recurse, pattern->children[0], actual->parts[0], alias);
      }
    };
    if (args.size() < d.generics.size()) {
      std::vector<std::pair<Ast, Type *>> untyped;
      for (unsigned j = 0; j < d.params.size(); ++j) {
        auto node = n->children[j + 1];
        auto parameter = d.params[j].type;
        auto *actual = referenceHint(node);
        if (!actual)
          actual = standaloneLambdaType(p, node, [&](const Ast &type) { return parseType(type); });
        if (actual) {
          infer(infer, parameter, actual);
        } else if (mathInteger(node)) {
          untyped.emplace_back(parameter, p.integer(32, true));
        } else {
          auto literal = node;
          while (literal->kind == NodeKind::Unary && literal->text == "-") literal = literal->children[0];
          if (literal->kind == NodeKind::Number && parseNumber(literal->text).floating)
            untyped.emplace_back(parameter, p.canonical.at("f64"));
        }
      }
      for (const auto &[parameter, t] : untyped)
        if ((parameter->kind == NodeKind::TypeName && !subst.contains(parameter->text)) ||
            parameterMode(parameter) != ParameterMode::Value)
          infer(infer, parameter, t);
    }
    if (!consistent) continue;
    for (unsigned i = 0; i < d.generics.size(); ++i) {
      const auto &g = d.generics[i];
      if (!g.comptime) {
        if (i < args.size()) subst[g.name] = parseType(args[i]);
        if (!subst.contains(g.name)) {
          consistent = false;
          break;
        }
        continue;
      }
      auto *t = p.type(g.type, *def.file, subst, constants);
      if (t->kind != Kind::Int && t->kind != Kind::Bool && t->kind != Kind::Float && t->kind != Kind::Char)
        fail(g.span, "comptime generic values currently require scalar types", "E290");
      if (i < args.size()) {
        constants[g.name] = p.genericValue(args[i], file, t, generics, visibleConstants());
      } else if (auto it = inferredValues.find(g.name); it != inferredValues.end()) {
        if (it->second.first == t) constants[g.name] = it->second;
        else if (t->kind == Kind::Int && it->second.first->kind == Kind::Int) {
          auto value = cast<ConstantInt>(it->second.second)->getValue();
          value = it->second.first->sign ? value.sext(value.getBitWidth() + 1)
                                          : value.zext(value.getBitWidth() + 1);
          if (!fits(value, t->bits, t->sign)) {
            consistent = false;
            break;
          }
          constants[g.name] = {t, ConstantInt::get(t->ir, value.sextOrTrunc(t->bits))};
        } else {
          consistent = false;
          break;
        }
      } else {
        consistent = false;
        break;
      }
    }
    if (!consistent) continue;
    bool valid = true;
    for (const auto &g : d.generics)
      if (g.type && !g.comptime) valid &= p.satisfies(subst.at(g.name), g.type, *def.file, subst, constants);
    if (!valid) continue;
    Candidate c{def, subst, constants, p.parameters(def, subst, declaring, constants), {}};
    if (receiver) c.ranks.push_back(receiver->writable && d.readOnly);
    unsigned offset = receiver ? 1 : 0;
    for (unsigned i = 0; i < d.params.size(); ++i) {
      auto *t = c.parameters[offset + i];
      auto a = n->children[i + 1];
      auto *from = t->kind == Kind::Reference ? referenceHint(a) : hint(a);
      if (!from)
        from = standaloneLambdaType(p, a, [&](const Ast &type) { return parseType(type); });
      if (t->parameterMode != ParameterMode::Value) {
        if (!from || from == t->parts[0] ||
            (from->kind == Kind::Reference && from->parts == t->parts && (t->readOnly || !from->readOnly)) ||
            (p.scopedViewInput(t) && p.canAdaptViewInput(from, t->parts[0]))) {
          c.ranks.push_back(from == t->parts[0] ? 1 : 2);
          continue;
        }
        valid = false;
        break;
      }
      if (!from && a->kind == NodeKind::Unary && a->text == "raw") {
        // Address-of always produces a pointer, even when the pointee type
        // cannot be inferred before selecting the overload.
        auto *target = t;
        while (target->kind == Kind::Optional || target->kind == Kind::Result)
          target = target->parts[0];
        if (target->kind != Kind::Pointer) {
          valid = false;
          break;
        }
      }
      auto math = mathInteger(a);
      unsigned rank = 0;
      for (;;) {
        if (from == t) break;
        if (math && t->kind == Kind::Int && fits(*math, t->bits, t->sign)) break;
        if (a->kind == NodeKind::Null && t->kind == Kind::Optional) break;
        if (t->kind == Kind::Reference) {
          if (!from && a->kind != NodeKind::Null && !math) break;
          if (from && from->kind == Kind::Reference && t->parts == from->parts &&
              (t->readOnly || !from->readOnly)) {
            rank += t->readOnly && !from->readOnly;
            break;
          }
          valid = false;
          break;
        }
        if (!from && !math && a->kind != NodeKind::Null) break;
        if (from && (((t->kind == Kind::Pointer || t->kind == Kind::Slice) && from->kind == t->kind &&
                      t->readOnly && t->parts == from->parts) ||
                     (t == p.viewTy && from == p.textTy))) {
          rank += 1;
          break;
        }
        if (from && from->kind == Kind::Pointer && t->kind == Kind::Pointer &&
            from->parts[0]->kind == Kind::Class && t->parts[0]->kind == Kind::Interface &&
            (t->readOnly || !from->readOnly)) {
          p.ensureSized(from->parts[0], a->span);
          if (from->parts[0]->conformances.contains(t->parts[0])) {
            rank += 1;
            break;
          }
        }
        if (t->kind == Kind::Optional || t->kind == Kind::Result) {
          rank += 2;
          t = t->parts[0];
          continue;
        }
        valid = false;
        break;
      }
      c.ranks.push_back(rank);
    }
    if (valid) candidates.push_back(std::move(c));
  }
  Candidate *best = nullptr;
  auto better = [&](const Candidate &a, const Candidate &b) {
    bool strict = false;
    for (std::size_t i = 0; i < a.ranks.size(); ++i) {
      if (a.ranks[i] > b.ranks[i]) return false;
      strict |= a.ranks[i] < b.ranks[i];
    }
    if (strict) return true;
    return receiver && a.definition.receiverPath.empty() && !b.definition.receiverPath.empty() &&
           a.definition.decl->readOnly == b.definition.decl->readOnly &&
           std::equal(a.parameters.begin() + 1, a.parameters.end(), b.parameters.begin() + 1,
                      b.parameters.end());
  };
  for (auto &a : candidates)
    if (std::ranges::none_of(candidates, [&](const auto &b) { return better(b, a); })) {
      if (best) fail(n->span, "ambiguous overload", "E202");
      best = &a;
    }
  if (!best) {
    std::string message = "no accessible overload matches the argument types and receiver";
    for (const auto &definition : defs) {
      const auto &candidate = *definition.decl;
      message += "\n  candidate " + candidate.name + "(";
      for (unsigned i = 0; i < candidate.params.size(); ++i) {
        if (i) message += ", ";
        message += candidate.params[i].name;
        if (const auto &type = candidate.params[i].type; type && !type->text.empty())
          message += ": " + type->text;
      }
      message += ")";
    }
    fail(n->span, message, "E202");
  }
  auto &d = *best->definition.decl;
  if (auto *hints = p.options.editorHints; hints && hints->workspace && !classType &&
      !best->definition.file->standard && !d.external && !d.exported && d.name != "main") {
    auto callee = n->children.front();
    if (callee->kind == NodeKind::Generic) callee = callee->children.front();
    if (callee->kind == NodeKind::Name || callee->kind == NodeKind::Member)
      indexSymbol(callee->span, d.nameSpan, false);
  }
  unsigned offset = receiver.has_value();
  for (unsigned i = 0; i < d.params.size(); ++i) {
    const auto &param = d.params[i];
    auto *t = best->parameters[i + offset];
    // Diagnose an illegal scoped alias use before instantiating a Value
    // parameter with the callback's concrete environment type.
    validateScopedCallArgument(n->children[i + 1], t);
    if (!param.comptime) continue;
    if (t->kind != Kind::Int && t->kind != Kind::Float && t->kind != Kind::Char && t->kind != Kind::Bool)
      fail(param.span, "comptime value parameters currently require scalar types", "E290");
    best->constants[param.name] = p.evaluate(n->children[i + 1], file, t, generics, visibleConstants());
  }
  auto *declaring = best->definition.declaringType ? best->definition.declaringType : classType;
  bool dynamic = declaring && declaring->kind == Kind::Interface;
  auto *entry = dynamic ? nullptr : &p.function(best->definition, best->substitutions, declaring, best->constants);
  if (delegation && entry && entry->lowering) fail(n->span, "cyclic constructor delegation");
  Type *returnType = entry ? entry->result :
      p.type(d.type, *best->definition.file, best->substitutions, best->constants);
  if (defer && (returnType != p.voidTy || d.comptime))
    fail(n->span, "defer requires a runtime void call; handle errors within the deferred function", "E231");
  int borrowFrom = p.borrowParameter(best->definition, best->parameters, returnType, receiver.has_value());
  const bool viewResult = borrowFrom >= 0 && p.checkedViewResult(returnType);
  Borrow returnOrigin{};
  bool returnTemporary = false;
  if (dynamic) {
    auto containsSelf = [&](auto &&self, const Ast &node) -> bool {
      if (!node) return false;
      if ((node->kind == NodeKind::Name || node->kind == NodeKind::TypeName) && node->text == "Self") return true;
      return self(self, node->type) ||
             std::ranges::any_of(node->children, [&](const auto &child) { return self(self, child); });
    };
    if (d.comptime || containsSelf(containsSelf, d.type) ||
        std::ranges::any_of(d.params, [&](const auto &param) { return containsSelf(containsSelf, param.type); }))
      fail(n->span, "this method requires the concrete interface implementation");
  }
  if ((d.external || d.unsafe) && !unsafeDepth) fail(n->span, "this call requires an unsafe block", "E240");
  if (evaluating && d.external)
    fail(n->span, "external calls are unavailable during compile-time evaluation", "E220");
  std::vector<Value *> values;
  Value *callee = entry ? entry->ir : nullptr;
  const bool scopedReceiver = receiver && receiver->type->kind == Kind::Class && receiver->type->scopedBorrow;
  if (receiver) {
    checkAccess(*receiver, !d.readOnly, n->span);
    if (borrowFrom == 0) {
      returnOrigin = receiver->origin;
      if (viewResult && returnOrigin.raw)
        fail(n->span, "checked readonly view requires a receiver with a proven owner", "E230");
      if (returnOrigin.root < 0) returnOrigin.root = receiver->local;
      if (returnOrigin.root >= 0 && locals[returnOrigin.root].refCapture &&
          !locals[returnOrigin.root].reference && !(viewResult && locals[returnOrigin.root].name == "self"))
        returnOrigin = {};
      returnOrigin.raw = false;
      returnTemporary = receiver->temporaryBorrow;
    }
    Value *address;
    if (scopedReceiver) {
      // Pass the physical slot for this synchronous const call only. Do not
      // construct a source-level raw pointer to a reference-bearing record.
      if (defer || receiver->local < 0 || receiver->address != locals[receiver->local].slot)
        fail(n->span, "a scoped borrow method requires a whole bound receiver and a direct call", "E230");
      requireInit(*receiver, n->span);
      address = receiver->address;
    } else {
      address = asPointer(*receiver, n->span).value;
    }
    if (dynamic) {
      auto *table = ir.CreateExtractValue(address, 1);
      if (!d.generics.empty() || hasComptimeParameters(d)) {
        if (evaluating) fail(n->span, "generic dispatch through an interface needs a runtime receiver", "E220");
        auto *lookup = p.genericInterfaceLookup(declaring, d, best->substitutions, best->constants);
        callee = ir.CreateCall(lookup, {table});
      } else {
        const auto &methods = declaring->def.decl->members;
        auto slot = static_cast<unsigned>(&d - methods.data());
        auto *pointer = PointerType::getUnqual(p.context);
        callee = ir.CreateLoad(pointer, ir.CreateConstGEP1_32(pointer, table, slot));
      }
      address = ir.CreateExtractValue(address, 0);
    }
    auto *type = receiver->type;
    for (auto field : best->definition.receiverPath) {
      address = ir.CreateStructGEP(type->ir, address, field);
      type = type->fields[field].type;
    }
    values.push_back(address);
  }
  std::vector<int> transfers;
  bool temporaryBorrow = receiver && receiver->temporaryBorrow;
  std::vector<int> deferredLoans;
  int receiverReservation = -1;
  enter();
  if (receiver && !defer) {
    // The receiver has already been evaluated. Keep its storage alive while
    // arguments are evaluated, but allow nested reads until the call starts.
    auto loan = *receiver;
    if (scopedReceiver && !locals[loan.local].reference) loan.origin = {loan.local};
    else if (loan.origin.root < 0) loan.origin.root = loan.local;
    if (loan.origin.root >= 0) {
      loan.type = p.reference(loan.type, true);
      loan.temporaryBorrow = false; // The reservation ends within this full expression.
      receiverReservation = pinBorrow(loan, n->span, scopes.size() - 1);
      locals[receiverReservation].receiverReservation = true;
    }
  }
  if (defer && receiver) {
    // An argument could replace a Box (or another owning receiver) after
    // evaluating its address. Reserve the receiver before evaluating arguments,
    // and give the loan the defer scope only after registration succeeds.
    auto loan = borrow(*receiver, p.reference(receiver->type, d.readOnly), n->span, false);
    validateDeferredBorrow(loan, n->span, defer->scope);
    deferredLoans.push_back(pinBorrow(loan, n->span, scopes.size() - 1));
  }
  for (unsigned i = 1; i < n->children.size(); ++i) {
    if (d.params[i - 1].comptime) continue;
    auto *t = best->parameters[i - 1 + offset];
    if (defer && scopedCallableInput(t))
      fail(n->children[i]->span, "a scoped callable parameter cannot be saved for a deferred call", "E230");
    auto value = argument(n->children[i], t, parameterMode(d.params[i - 1].type) == ParameterMode::Move);
    temporaryBorrow |= value.temporaryBorrow;
    if (!live()) {
      leave();
      return neverValue();
    }
    if (defer && p.scopedViewInput(t))
      fail(n->children[i]->span, "scoped view input cannot be saved for deferred calls", "E230");
    if (!p.scopedViewInput(t)) rejectViewEscape(value, n->children[i]->span);
    if (defer) validateDeferredBorrow(value, n->children[i]->span, defer->scope);
    auto *raw = read(value, n->span);
    if (borrowFrom == static_cast<int>(i - 1 + offset)) {
      returnOrigin = value.origin;
      returnTemporary = value.temporaryBorrow;
    }
    if (p.hasReference(t)) {
      const bool write = !p.copyable(t);
      checkAccess(value, write, n->span);
      if (write) invalidateOwnedAddresses(value);
      auto loan = bind(" <loan " + std::to_string(i) + ">", t, false, raw, n->span);
      locals[loan].origin = value.origin;
      if (defer && value.hasBorrowPayload()) deferredLoans.push_back(loan);
    }
    values.push_back(raw);
    if (p.drops(t)) {
      auto temp = bind(" <argument " + std::to_string(i) + ">", t, true, raw, n->span);
      transfers.push_back(temp);
    }
  }
  if (receiver) {
    // Activate the real receiver access. Any loans held by the arguments
    // still participate in this check; only our own reservation is released.
    if (receiverReservation >= 0) initialized[receiverReservation] = false;
    if (!defer) checkAccess(*receiver, !d.readOnly, n->span);
    if (!d.readOnly) invalidateOwnedAddresses(*receiver);
  }
  for (auto i : transfers)
    setAlive(i, false);
  if (entry) p.lower(*entry);
  if (d.comptime && !evaluating) {
    std::vector<Constant *> constants;
    for (auto *v : values) {
      auto *constant = dyn_cast<Constant>(v);
      if (!constant) fail(n->span, "comptime function arguments must be constant", "E220");
      constants.push_back(constant);
    }
    auto *value = interpret(*entry->ir, constants, n->span);
    leave();
    return returnType == p.voidTy ? voidValue() : ValueAt{returnType, value};
  }
  CallInst *called;
  if (dynamic) {
    std::vector<llvm::Type *> parameters;
    for (auto *value : values) parameters.push_back(value->getType());
    called = ir.CreateCall(FunctionType::get(returnType->ir, parameters, false), callee, values);
    called->setDoesNotThrow();
  } else {
    called = ir.CreateCall(entry->ir, values);
    called->setAttributes(entry->ir->getAttributes());
  }
  if (defer) {
    for (auto i : deferredLoans) retainDeferredLocal(i, defer->scope);
    registerDefer(called, n->span, *defer);
    leave();
    return voidValue();
  }
  if (returnType == p.neverTy) ir.CreateUnreachable();
  leave();
  if (returnType == p.voidTy) return voidValue();
  returnOrigin.checkedView = viewResult;
  return trackView({returnType, called, nullptr, false, -1, false,
                    (p.hasReference(returnType) || viewResult) ? returnTemporary || returnOrigin.root < 0
                                                              : temporaryBorrow && carriesBorrow(returnType),
                    returnOrigin}, n->span);
}

ValueAt Body::construct(const Ast &n, Type *t, const std::vector<Definition> &defs) {
  p.ensureSized(t, n->span);
  if (t->kind != Kind::Class) fail(n->span, "expected a class type");
  if (!p.movable(t))
    fail(n->span, "immovable construction currently requires new for a fixed address", "E290");
  if (n->kind == NodeKind::Call && !defs.empty()) {
    auto *address = slot(t, "object.init");
    auto result = invoke(n, defs, {}, ValueAt{t, nullptr, address, true}, t);
    if (!live()) return neverValue();
    if (result.type == p.voidTy) return {t, ir.CreateLoad(t->ir, address)};
    auto *error = ir.CreateExtractValue(result.value, 0);
    auto *out = p.aggregate(Kind::Result, {t});
    // The load is performed only on successful construction.
    auto *success = block("object.ready"), *failure = block("object.failed"), *end = block("object.end");
    ir.CreateCondBr(ir.CreateICmpEQ(error, ir.getInt32(0)), success, failure);
    ir.SetInsertPoint(success);
    auto ok = wrap(out, ir.CreateLoad(t->ir, address), true);
    ir.CreateBr(end);
    ir.SetInsertPoint(failure);
    auto bad = wrap(out, error, false);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(out->ir, 2);
    phi->addIncoming(ok.value, success);
    phi->addIncoming(bad.value, failure);
    return {out, phi};
  }
  if (n->kind == NodeKind::Call && t->fields.empty() && n->children.size() == 1)
    // With no declared init and no fields to initialize, T() is the empty
    // initializer. Existing init overloads above keep their access checks.
    return {t, Constant::getNullValue(t->ir)};
  if (n->kind != NodeKind::Fields)
    fail(n->span, "class has no matching constructor; declare an init taking these arguments");
  if (n->names.empty())
    fail(n->span, "empty brace construction uses T(), not T{}");
  if (!p.methods(t, "init", false).empty())
    fail(n->span, "class declares init and cannot use field construction");
  std::map<std::string, std::size_t> explicitValues;
  for (std::size_t i = 0; i < n->names.size(); ++i)
    if (!explicitValues.emplace(n->names[i], i).second)
      fail(n->span, "field initialized twice");
  Value *out = Constant::getNullValue(t->ir);
  enter();
  std::vector<int> transfers;
  Borrow origin{};
  for (const auto &f : t->fields) {
    if (!f.decl->visible && owner != t) fail(n->span, "field '" + f.decl->name + "' is private");
    auto value = explicitValues.find(f.decl->name);
    Ast initializer = f.decl->body;
    if (value != explicitValues.end()) {
      indexField(n->bindingSpans[value->second], t, *f.decl);
      initializer = n->children[value->second + 1];
      explicitValues.erase(value);
    }
    if (!initializer) fail(n->span, "missing initializer for '" + f.decl->name + "'");
    auto v = f.type->kind == Kind::Reference
                 ? borrowExpression(initializer, f.type)
                 : expression(initializer, f.type);
    if (!live()) {
      leave();
      return neverValue();
    }
    rejectViewEscape(v, initializer->span);
    auto *data = read(v, n->span);
    if (f.type->kind == Kind::Reference) {
      holdBorrow(origin, v, data, initializer->span);
    }
    if (p.drops(f.type))
      transfers.push_back(bind(" <field " + f.decl->name + ">", f.type, true, data, n->span));
    out = ir.CreateInsertValue(out, data, f.index);
  }
  if (!explicitValues.empty()) fail(n->span, "unknown field '" + explicitValues.begin()->first + "'");
  for (auto i : transfers)
    setAlive(i, false);
  leave();
  return {t, out, nullptr, false, -1, false, false, origin};
}

void Body::bindCapture(const Capture &capture, Value *environment, Span span) {
  auto *field = ir.CreateStructGEP(closure->ir, environment, capture.index);
  Value *address = field;
  Value *flag = nullptr;
  if (capture.reference)
    address = ir.CreateLoad(p.pointer(capture.type, !capture.writable)->ir, field);
  else
    flag = ir.CreateStructGEP(closure->ir, environment, capture.index + 1);
  int i = static_cast<int>(locals.size());
  locals.push_back({capture.name, capture.type, address, nullptr, flag, capture.reference, scopes.size() - 1,
                    capture.writable});
  scopes.back().push_back(i);
  locals.back().declaration = capture.declaration;
  locals.back().reference = capture.safeAlias;
  if (capture.scoped) locals.back().origin.root = i;
  initialized.push_back(true);
  (void)span;
}

ValueAt Body::lambda(const Ast &n, Type *expected) {
  if (expected && expected->kind != Kind::Function) expected = nullptr;
  const bool hasCaptures = !n->children.empty();
  auto annotations = lambdaAnnotations(n, expected, [&](const Ast &type) { return parseType(type); });
  auto &signature = annotations.parameters;
  auto &moveParameters = annotations.moves;
  auto *result = annotations.result;
  std::vector<llvm::Type *> irArgs;
  if (hasCaptures) irArgs.push_back(PointerType::getUnqual(p.context));
  for (unsigned i = 0; i < signature.size(); ++i) {
    p.ensureSized(signature[i], n->parameters[i].span);
    const auto &param = n->parameters[i];
    auto mode = param.type ? parameterMode(param.type) : signature[i]->parameterMode;
    if (std::ranges::find(moveParameters, i) != moveParameters.end()) mode = ParameterMode::Move;
    p.verifyValueParameter(signature[i], mode, param.span);
    irArgs.push_back(p.storage(signature[i]));
  }
  auto name = "vqr.lambda." + std::to_string(p.lambdaCount++);
  Type *t = nullptr;
  std::vector<Capture> captures;
  std::vector<llvm::Type *> fields;
  std::set<std::string> names;
  if (hasCaptures) {
    for (const auto &item : n->children) {
      auto original = item->kind == NodeKind::Name ? item : item->children[0];
      int i = findLocal(original->text);
      if (i < 0) fail(item->span, "capture requires a local binding");
      if (!names.insert(original->text).second) fail(item->span, "duplicate lambda capture");
      auto &local = locals[i];
      if (scopedCallableInput(local.reference))
        fail(item->span, "a scoped callable parameter cannot be captured", "E230");
      indexSymbol(original->span, local.declaration);
      const bool reference = item->kind == NodeKind::Unary;
      if (reference && item->text != "ref" && item->text != "ref mut")
        fail(item->span, "legacy closure capture; use 'ref name' or 'ref mut name'", "E110");
      const bool mutableCapture = reference && item->text == "ref mut";
      const bool referenceValue = p.hasReference(local.type);
      Type *safeAlias = nullptr;
      if (reference) {
        if (local.type->kind == Kind::Interface || referenceValue || carriesBorrow(local.type))
          fail(item->span, "capture requires a place without untracked or nested borrows", "E230");
        safeAlias = p.reference(local.type, !mutableCapture);
      }
      const bool scoped = reference || (referenceValue && item->kind == NodeKind::Name);
      if ((local.reference && item->kind == NodeKind::Move) ||
          (referenceValue && (!scoped || !p.copyable(local.type) || p.drops(local.type))))
        fail(item->span, "scoped closure captures require a tracked borrow", "E230");
      if (item->kind == NodeKind::Name && !p.copyable(local.type))
        fail(item->span, "copy capture requires a Copyable value; use move(name) or ref name", "E230");
      if (!reference && !p.movable(local.type))
        fail(item->span, "immovable values require a reference capture", "E290");
      const bool writable = reference ? mutableCapture : local.writable && !scoped;
      captures.push_back({original->text, local.type, reference, writable,
                          static_cast<unsigned>(fields.size()), local.declaration, safeAlias, scoped});
      fields.push_back(p.storage(reference ? p.pointer(local.type, !writable) : local.type));
      fields.push_back(p.boolTy->ir);
    }
    t = p.add(Kind::Closure, StructType::get(p.context, fields), name);
    t->captures = captures;
    t->scopedBorrow = std::ranges::any_of(captures, &Capture::scoped);
    if (t->scopedBorrow) {
      // Owned captures may coexist with loans, but untracked pointers and
      // views cannot establish an independent lifetime just by being copied.
      std::set<Type *> visited;
      auto containsBorrow = [&](auto &&self, Type *type) -> bool {
        if (p.hasReference(type) || type->kind == Kind::Pointer || type->kind == Kind::View ||
            type->kind == Kind::Slice || type->kind == Kind::Interface ||
            type->kind == Kind::RawStorage || type->kind == Kind::Allocation)
          return true;
        if (!visited.insert(type).second) return false;
        auto part = [&](Type *component) { return self(self, component); };
        if (type->kind == Kind::Closure)
          return std::ranges::any_of(type->captures, [&](const Capture &capture) {
            return capture.reference || part(capture.type);
          });
        if (type->kind == Kind::Class || type->kind == Kind::Enum) {
          p.ensureSized(type, n->span);
          if (type->kind == Kind::Class)
            return std::ranges::any_of(type->fields, [&](const Field &field) { return part(field.type); });
          return std::ranges::any_of(type->variants, [&](const Variant &variant) {
            return std::ranges::any_of(variant.fields, part);
          });
        }
        if (type->kind == Kind::Box || type->kind == Kind::Optional || type->kind == Kind::Result ||
            type->kind == Kind::Array || type->kind == Kind::Tuple)
          return std::ranges::any_of(type->parts, part);
        return false;
      };
      for (const auto &capture : captures)
        if (!capture.scoped && containsBorrow(containsBorrow, capture.type))
          fail(n->span, "owned captures in a scoped closure cannot carry untracked borrows", "E230");
    }
  }
  for (const auto &param : n->parameters)
    if (!names.insert(param.name).second)
      fail(param.span, "parameter conflicts with another parameter or capture");
  if (result) p.ensureSized(result, n->span);
  auto *function = Function::Create(FunctionType::get(result ? result->ir : p.voidTy->ir, irArgs, false),
                                    GlobalValue::InternalLinkage, name, *p.module);
  function->setDoesNotThrow();
  Body body(p, file, function, result, generics, evaluating);
  body.functionName = functionName.empty() ? "lambda" : functionName + " (lambda)";
  body.closure = t;
  if (hasCaptures)
    for (const auto &c : captures)
      body.bindCapture(c, function->getArg(0), n->span);
  std::vector<int> parameterLocals;
  unsigned arg = hasCaptures ? 1 : 0;
  for (unsigned i = 0; i < n->parameters.size(); ++i) {
    const auto &param = n->parameters[i];
    auto *value = function->getArg(arg++);
    value->setName(param.name);
    auto id = body.bind(param.name, signature[i], true, value, param.span);
    body.indexLocal(id, param.span);
    parameterLocals.push_back(id);
    if (p.hasReference(signature[i])) body.locals[id].origin.root = id;
    body.locals[id].origin.checkedView = p.scopedViewInput(signature[i]);
  }
  auto source = result ? p.functionBorrow(signature, result, expected ? expected->count : 0, n->span) : 0;
  body.checkedViewReturn = source && p.checkedViewResult(result);
  if (source) body.returnRoot = parameterLocals[source - 1];
  auto returned = body.expression(n->tail, result);
  if (!result) {
    result = returned.type == p.neverTy ? p.voidTy : returned.type;
    if (result != p.voidTy) {
      auto *typed = Function::Create(FunctionType::get(result->ir, irArgs, false),
                                     GlobalValue::InternalLinkage, name + ".typed", *p.module);
      typed->setDoesNotThrow();
      for (unsigned i = 0; i < function->arg_size(); ++i)
        function->getArg(i)->replaceAllUsesWith(typed->getArg(i));
      typed->splice(typed->begin(), function);
      body.fn = typed;
      function->eraseFromParent();
      function = typed;
    }
    body.result = result;
    source = p.functionBorrow(signature, result, 0, n->span);
    if (source) body.returnRoot = parameterLocals[source - 1];
  }
  if (body.live()) body.emitReturn(returned, n->span);
  signature.push_back(result);
  if (!hasCaptures) return {p.aggregate(Kind::Function, signature, source, false, std::move(moveParameters)), function};
  t->count = source;
  t->callable = signature;
  t->moveParameters = std::move(moveParameters);
  t->callEntry = function;
  Value *environment = Constant::getNullValue(t->ir);
  enter();
  std::vector<int> transfers;
  std::vector<CapturedLoan> capturedLoans;
  for (unsigned i = 0; i < captures.size(); ++i) {
    const auto &capture = captures[i];
    const auto &item = n->children[i];
    auto v = capture.reference
                 ? borrow(this->name(item->children.front()), capture.safeAlias, item->span, false)
                 : expression(item);
    if (!live()) {
      leave();
      return neverValue();
    }
    rejectViewEscape(v, n->children[i]->span);
    auto *stored = read(v, n->span);
    if (capture.scoped) holdCaptureBorrow(capturedLoans, v, item->span);
    if (p.drops(v.type))
      transfers.push_back(bind(" <capture " + std::to_string(i) + ">", v.type, true, stored, n->span));
    environment = ir.CreateInsertValue(environment, stored, captures[i].index);
    environment = ir.CreateInsertValue(environment, ir.getTrue(), captures[i].index + 1);
  }
  for (int i : transfers)
    setAlive(i, false);
  leave();
  return {t, environment, nullptr, false, -1, false, false, captureBorrow(std::move(capturedLoans))};
}

ValueAt Body::allocate(const Ast &n) {
  if (evaluating) fail(n->span, "heap allocation is unavailable during compile-time evaluation", "E220");
  if (!n->tail && !p.heapAvailable()) fail(n->span, "target has no default Box allocator", "E290");
  auto *t = parseType(n->type);
  p.ensureSized(t, n->span);
  if (t == p.neverTy) fail(n->span, "never cannot be allocated");
  auto *box = p.box(t);
  auto *resultType = p.aggregate(Kind::Result, {box});
  auto *ptr = PointerType::getUnqual(p.context);
  Value *context = Constant::getNullValue(p.allocationContext->ir);
  Value *storage = nullptr;
  Value *error = ir.getInt32(0xfffffffc);
  if (n->tail) {
    // A custom Box stores the allocator address, not an owning or
    // lifetime-tracked loan. The caller must guarantee the allocator
    // stays stable and valid until the Box's storage is released.
    if (!unsafeDepth) fail(n->tail->span, "custom Box allocator requires unsafe", "E240");
    auto placed = expression(n->tail);
    if (!live()) return neverValue();
    if (placed.type->kind == Kind::Box)
      fail(n->tail->span, "using a boxed allocator requires explicit dereference", "E230");
    if (placed.type->kind == Kind::Pointer)
      fail(n->tail->span, "using a raw pointer allocator requires explicit dereference", "E230");
    auto *owner = placed.type;
    auto allocatorInterface = [](Type *candidate) {
      return candidate->kind == Kind::Interface && candidate->def.file->standard &&
             candidate->def.file->package == "mem" && candidate->def.decl->name == "Allocator";
    };
    Type *allocator = nullptr;
    if (allocatorInterface(owner)) {
      allocator = owner;
    } else if (owner->kind == Kind::Class) {
      p.ensureSized(owner, n->span);
      for (const auto &[interface, _] : owner->conformances)
        if (allocatorInterface(interface)) {
          allocator = interface;
          break;
        }
    }
    if (!allocator) fail(n->tail->span, "new requires an allocator implementing mem.Allocator");
    if (!placed.address) fail(n->tail->span, "allocator must be a live storage location");
    placed = asPointer(placed, n->tail->span);
    auto view = coerce(placed, p.pointer(allocator, false), n->tail->span);
    auto *address = read(view, n->tail->span);
    auto [layoutType, layout] = p.boxLayout(t, allocator);
    enter();
    std::string argument = " <allocation layout>";
    bindConstant(argument, layoutType, layout, n->span);
    auto value = std::make_shared<Node>();
    value->kind = NodeKind::Name;
    value->span = n->span;
    value->text = argument;
    auto call = std::make_shared<Node>();
    call->kind = NodeKind::Call;
    call->span = n->span;
    call->children = {nullptr, value};
    auto allocation = invoke(call, p.methods(allocator, "alloc", false), {},
                             ValueAt{allocator, nullptr, address, true, -1, true}, allocator);
    leave();
    auto *result = read(allocation, n->span);
    auto *status = ir.CreateExtractValue(result, 0);
    auto *optional = ir.CreateExtractValue(result, 1);
    auto *ok = ir.CreateIsNull(status);
    auto *some = ir.CreateSelect(ok, ir.CreateExtractValue(optional, 0), ir.getFalse());
    storage = ir.CreateSelect(some, ir.CreateExtractValue(optional, 1), ConstantPointerNull::get(ptr));
    error = ir.CreateSelect(ok, error, status);
    context = ir.CreateInsertValue(context, ir.CreateExtractValue(address, 0), 0);
    context = ir.CreateInsertValue(context, ir.CreateExtractValue(address, 1), 1);
    context = ir.CreateInsertValue(context, p.boxRelease(t, allocator), 2);
  } else {
    const auto &layout = p.module->getDataLayout();
    auto align = std::max<std::uint64_t>(p.pointerBits() / 8, layout.getABITypeAlign(p.storage(t)).value());
    auto size = layout.getTypeAllocSize(p.storage(t)).getFixedValue();
    size = std::max(align, ((size + align - 1) / align) * align);
    auto sizeConstant = [&](std::uint64_t value) { return ConstantInt::get(p.sizeTy->ir, value); };
    storage = allocateAligned(sizeConstant(align), sizeConstant(size));
  }
  auto *rawBox = ir.CreateInsertValue(PoisonValue::get(box->ir), storage, 0);
  rawBox = ir.CreateInsertValue(rawBox, context, 1);
  auto *initialBlock = ir.GetInsertBlock(), *allocated = block("heap.allocated"), *end = block("heap.end");
  auto failure = wrap(resultType, error, false);
  auto initial = initialized;
  ir.CreateCondBr(ir.CreateIsNotNull(storage), allocated, end);
  ir.SetInsertPoint(allocated);
  enter();
  auto *allocationType = p.canonical.contains("<allocation>") ? p.canonical.at("<allocation>")
                                                              : p.add(Kind::Allocation, box->ir, "<allocation>");
  auto raw = bind(" <allocation>", allocationType, true, rawBox, n->span);
  ValueAt constructor = voidValue();
  if (t->kind == Kind::Class) {
    auto methods = p.methods(t, "init", false);
    auto call = std::make_shared<Node>(*n);
    call->kind = NodeKind::Call;
    call->children.insert(call->children.begin(), nullptr);
    constructor = invoke(call, methods, {}, ValueAt{t, nullptr, storage, true}, t);
  } else {
    if (n->children.size() != 1) fail(n->span, "new with a non-class type takes one initializer");
    auto value = expression(n->children[0], t);
    rejectViewEscape(value, n->span);
    if (live() && t != p.voidTy) ir.CreateStore(read(value, n->span), storage);
  }
  std::vector<std::pair<Value *, BasicBlock *>> edges{{failure.value, initialBlock}};
  std::vector<std::vector<bool>> states{initial};
  if (live()) {
    if (constructor.type->kind == Kind::Result) {
      auto *err = ir.CreateExtractValue(constructor.value, 0);
      auto *made = block("heap.initialized"), *failed = block("heap.failed");
      ir.CreateCondBr(ir.CreateICmpEQ(err, ir.getInt32(0)), made, failed);
      ir.SetInsertPoint(failed);
      dropLocal(raw);
      auto returned = wrap(resultType, err, false);
      edges.emplace_back(returned.value, ir.GetInsertBlock());
      ir.CreateBr(end);
      ir.SetInsertPoint(made);
    }
    setAlive(raw, false);
    states.push_back(initialized);
    leave();
    auto success = wrap(resultType, rawBox, true);
    edges.emplace_back(success.value, ir.GetInsertBlock());
    ir.CreateBr(end);
  } else
    leave();
  ir.SetInsertPoint(end);
  merge(states, initial.size());
  auto *phi = ir.CreatePHI(resultType->ir, edges.size());
  for (auto [value, block] : edges)
    phi->addIncoming(value, block);
  return {resultType, phi};
}

ValueAt Body::call(const Ast &n, Type *expected, DeferRegistration *defer) {
  auto callee = n->children[0];
  std::vector<Ast> types;
  if (callee->kind == NodeKind::Generic) {
    types.assign(callee->children.begin() + 1, callee->children.end());
    callee = callee->children[0];
  }
  auto key = path(callee);
  auto dot = key.find('.');
  auto first = key.substr(0, dot);
  if (!key.empty() && findLocal(first) < 0) {
    if (owner && dot == key.npos && findLocal("self") >= 0) {
      auto ms = p.methods(owner, key, false);
      if (!ms.empty()) {
        auto self = std::make_shared<Node>();
        self->kind = NodeKind::Name;
        self->text = "self";
        self->span = n->span;
        auto v = name(self);
        // The hidden ABI pointer is not an unproven source-language pointer.
        // A checked method called on this receiver borrows the enclosing self.
        auto origin = v.origin;
        origin.raw = false;
        return invoke(n, ms, types,
                      ValueAt{owner, nullptr, read(v, n->span), !v.type->readOnly, -1, true, false, origin},
                      owner, false, defer);
      }
    }
    auto ds = p.resolve(file, key, n->span);
    if (!ds.empty()) {
      auto *d = ds.front().decl;
      if (d->kind == DeclKind::Function) {
        if (ds.front().file->standard && ds.front().file->package == "io" && d->name == "println" &&
            types.empty()) {
          if (defer) fail(n->span, "defer requires a void call; wrap the I/O and handle its result", "E231");
          return builtin("io.println", n, types, expected);
        }
        return invoke(n, ds, types, std::nullopt, nullptr, false, defer);
      }
      if (d->kind == DeclKind::Class || d->kind == DeclKind::Alias) {
        auto *t = p.named(key, file, n->span, generics, types, visibleConstants(), callee->span);
        if (t->kind == Kind::Class) return construct(n, t, p.methods(t, "init", false));
        if (n->children.size() != 2) fail(n->span, "conversion requires one value");
        return castNumber(t, n->children[1], n->span);
      }
    }
    const std::set<std::string_view> intrinsics{"some",       "ok",         "bitCast",      "ptrFromInt",
                                                "ptrCast",    "constCast",  "volatileLoad", "volatileStore",
                                                "truncToInt", "roundToInt", "panic"};
    if (dot == key.npos && intrinsics.contains(key)) {
      if (defer) fail(n->span, "intrinsics cannot be deferred directly", "E290");
      return builtin(key, n, types, expected);
    }
    if (dot == key.npos && key.starts_with("__vqr_")) {
      if (!file.standard) fail(n->span, "intrinsic is private to the Vaqrin standard library");
      if (key == "__vqr_println") return builtin("io.println", n, types, expected);
      return standardIntrinsic(key, n, types);
    }
    if (dot == key.npos &&
        (key == "usize" || key == "isize" || key == "f32" || key == "f64" ||
         (key.size() > 1 && (key[0] == 'i' || key[0] == 'u') && key[1] >= '0' && key[1] <= '9') ||
         generics.contains(key))) {
      if (n->children.size() != 2 || !types.empty()) fail(n->span, "conversion requires one value");
      return castNumber(p.named(key, file, n->span, generics), n->children[1], n->span);
    }
  }
  if (callee->kind == NodeKind::Member) {
    auto base = callee->children[0];
    if ((key == "Box.intoRaw" || key == "Box.intoRawParts") && findLocal("Box") < 0 &&
        p.resolve(file, "Box", n->span).empty()) {
      if (n->children.size() != 2) fail(n->span, "Box raw conversion takes one Box value");
      auto box = expression(n->children[1]);
      if (!live()) return neverValue();
      if (box.type->kind != Kind::Box) fail(n->span, "Box raw conversion takes one Box value");
      auto *value = read(box, n->span);
      auto *pointer = p.pointer(box.type->parts[0], false);
      auto *address = ir.CreateExtractValue(value, 0);
      auto *context = ir.CreateExtractValue(value, 1);
      if (key == "Box.intoRaw") {
        guard(ir, ir.CreateIsNull(ir.CreateExtractValue(context, 2)));
        return {pointer, address};
      }
      auto *parts = p.aggregate(Kind::Tuple, {pointer, p.allocationContext});
      auto *out = ir.CreateInsertValue(PoisonValue::get(parts->ir), address, 0);
      return {parts, ir.CreateInsertValue(out, context, 1)};
    }
    if (base->kind == NodeKind::Generic && path(base->children[0]) == "Box" &&
        (callee->text == "fromRaw" || callee->text == "fromRawParts") &&
        findLocal("Box") < 0 && p.resolve(file, "Box", n->span).empty()) {
      bool withContext = callee->text == "fromRawParts";
      if (!unsafeDepth) fail(n->span, "Box reconstruction requires an unsafe block", "E240");
      if (base->children.size() != 2 || n->children.size() != (withContext ? 3u : 2u))
        fail(n->span, "invalid Box reconstruction arguments");
      if (!withContext && !p.heapAvailable()) fail(n->span, "target has no default Box allocator", "E290");
      auto *element = parseType(base->children[1]);
      p.ensureSized(element, n->span);
      auto *box = p.box(element);
      auto input = expression(n->children[1], p.pointer(element, false));
      if (!live()) return neverValue();
      auto *address = read(input, n->span);
      Value *context = Constant::getNullValue(p.allocationContext->ir);
      if (withContext) {
        auto owner = expression(n->children[2], p.allocationContext);
        if (!live()) return neverValue();
        context = read(owner, n->span);
      }
      auto *value = ir.CreateInsertValue(PoisonValue::get(box->ir), address, 0);
      return {box, ir.CreateInsertValue(value, context, 1)};
    }
    auto target = base->kind == NodeKind::Generic ? base->children[0] : base;
    auto basePath = path(target);
    if (target == base && findLocal(basePath) < 0 && generics.contains(basePath)) {
      auto *type = generics.at(basePath);
      p.ensureSized(type, n->span);
      if (type->kind == Kind::Class)
        return invoke(n, p.methods(type, callee->text, true), types, std::nullopt, type, false, defer);
    }
    auto ds = basePath.empty() || findLocal(basePath.substr(0, basePath.find('.'))) >= 0
                  ? std::vector<Definition>{}
                  : p.resolve(file, basePath, n->span);
    if (ds.size() == 1 && (ds[0].decl->kind == DeclKind::Class || ds[0].decl->kind == DeclKind::Enum ||
                           ds[0].decl->kind == DeclKind::Alias)) {
      std::vector<Ast> args;
      if (target != base) args.assign(base->children.begin() + 1, base->children.end());
      auto *t = p.named(basePath, file, n->span, generics, args, visibleConstants(), target->span);
      p.ensureSized(t, n->span);
      if (t->kind == Kind::Class) return invoke(n, p.methods(t, callee->text, true), types, std::nullopt, t, false, defer);
      if (t->kind != Kind::Enum) fail(n->span, "associated functions require a class or enum type");
      if (callee->text == "fromInt" && n->children.size() == 2 &&
          std::ranges::all_of(t->variants, [](const auto &v) { return v.fields.empty(); })) {
        auto x = expression(n->children[1]);
        if (!live()) return neverValue();
        if (x.type->kind != Kind::Int) fail(n->span, "fromInt requires an integer");
        auto *raw = read(x, n->span);
        auto *wide = ir.getIntNTy(std::max(t->bits, x.type->bits) + 1);
        raw = ir.CreateIntCast(raw, wide, x.type->sign);
        auto *optional = p.aggregate(Kind::Optional, {t});
        Value *out = Constant::getNullValue(optional->ir);
        for (const auto &v : t->variants) {
          auto *tag = ConstantInt::get(p.context, v.tag);
          auto *extended = ir.CreateIntCast(tag, wide, t->sign);
          auto *item = ir.CreateInsertValue(Constant::getNullValue(t->ir), tag, 0);
          out = ir.CreateSelect(ir.CreateICmpEQ(raw, extended), wrap(optional, item, true).value, out);
        }
        return {optional, out};
      }
      auto variant = std::ranges::find(t->variants, callee->text, &Variant::name);
      if (variant == t->variants.end() || variant->fields.size() + 1 != n->children.size())
        fail(n->span, "enum member or payload arity mismatch");
      enter();
      Value *value =
          ir.CreateInsertValue(Constant::getNullValue(t->ir), ConstantInt::get(p.context, variant->tag), 0);
      std::vector<int> transfers;
      Borrow origin{};
      for (unsigned i = 0; i < variant->fields.size(); ++i) {
        const auto &syntax = n->children[i + 1];
        auto *field = variant->fields[i];
        auto v = field->kind == Kind::Reference
                     ? borrowExpression(syntax, field)
                     : expression(syntax, field);
        if (!live()) {
          leave();
          return neverValue();
        }
        rejectViewEscape(v, syntax->span);
        auto *raw = read(v, n->span);
        if (field->kind == Kind::Reference) holdBorrow(origin, v, raw, syntax->span);
        if (p.drops(v.type))
          transfers.push_back(bind(" <payload " + std::to_string(i) + ">", v.type, true, raw, n->span));
        value = ir.CreateInsertValue(value, raw, variant->offset + i);
      }
      for (auto i : transfers)
        setAlive(i, false);
      leave();
      return {t, value, nullptr, false, -1, false, false, origin, t->scopedBorrow && origin.root < 0};
    }
    if (callee->text == "toRaw" && callee->names.empty() && n->children.size() == 1) {
      auto *reference = referenceHint(base);
      if (reference && reference->kind == Kind::Reference) {
        auto borrowed = expression(base, reference);
        if (!live()) return neverValue();
        return asPointer(dereference(borrowed, n->span), n->span);
      }
    }
    auto v = expression(base);
    if (!live()) return neverValue();
    auto member = callee->text;
    bool guarded = !callee->names.empty();
    if (defer && guarded) fail(n->span, "optional method calls cannot be deferred directly", "E290");
    BasicBlock *none = nullptr, *end = nullptr;
    std::vector<bool> initial;
    if (guarded) {
      if (v.type->kind != Kind::Optional) fail(n->span, "?. requires an optional receiver");
      auto *some = block("optional.call");
      none = block("optional.skip");
      end = block("optional.end");
      initial = initialized;
      auto *tag = ir.CreateExtractValue(read(v, n->span, false), 0);
      ir.CreateCondBr(tag, some, none);
      ir.SetInsertPoint(some);
      v = payload(v, 1, v.type->parts[0]);
    }
    auto invokeMember = [&]() -> ValueAt {
      if (v.type->kind == Kind::Box && member == "intoInner" && n->children.size() == 1) {
        auto *inner = v.type->parts[0];
        if (!p.movable(inner)) fail(n->span, "Box.intoInner requires a movable payload", "E230");
        if (v.borrowed && (v.local < 0 || locals[v.local].refCapture || !v.writable ||
                           locals[v.local].slot != v.address || locals[v.local].type != v.type))
          fail(n->span, "Box.intoInner requires a complete owning Box", "E230");
        checkAccess(v, true, n->span);
        auto *box = read(v, n->span, false);
        auto *address = ir.CreateExtractValue(box, 0);
        auto *value = inner == p.voidTy ? nullptr : ir.CreateLoad(p.storage(inner), address);
        if (v.borrowed) {
          // This is an ownership transfer just like move(capture). Mark
          // the closure consuming before clearing the captured Box's slot.
          if (locals[v.local].captureFlag) closure->consuming = true;
          invalidateOwnedAddresses(v);
          setAlive(v.local, false);
        }
        releaseBox(address, ir.CreateExtractValue(box, 1));
        return value ? ValueAt{inner, value} : voidValue();
      }
      if (v.type->kind == Kind::Box && member == "asPtr" && n->children.size() == 1) {
        if (!v.borrowed) fail(n->span, "bind the Box before taking a pointer to its contents", "E290");
        return asPointer(dereference(v, n->span), n->span);
      }
      if (v.type->kind == Kind::Optional && member == "unwrap" && n->children.size() == 1) {
        checkUnwrap(ir.CreateExtractValue(read(v, n->span, false), 0), callee->span);
        auto out = payload(v, 1, v.type->parts[0]);
        return {out.type, read(out, n->span), nullptr, false, -1, false, out.temporaryBorrow, out.origin};
      }
      if ((v.type == p.textTy || v.type == p.viewTy || v.type->kind == Kind::Slice ||
           v.type->kind == Kind::Array) &&
          (member == "len" || member == "byteLen") && n->children.size() == 1) {
        if ((member == "byteLen") != (v.type == p.textTy || v.type == p.viewTy))
          fail(n->span, "method is unavailable for this receiver");
        Value *length = v.type->kind == Kind::Array ? ConstantInt::get(p.sizeTy->ir, v.type->count)
                                                    : ir.CreateExtractValue(read(v, n->span, false), 1);
        disposeTemporary(v, n->span);
        return {p.sizeTy, length};
      }
      if ((v.type == p.viewTy || v.type == p.textTy) && member == "asBytes" && n->children.size() == 1)
        return trackView({p.slice(p.integer(8, false), true), textView(v, n->span), nullptr, false, -1, false,
                          v.temporaryBorrow, viewOrigin(v, v.type == p.textTy)}, n->span);
      if (v.type->kind == Kind::Pointer && member == "offset" && n->children.size() == 2) {
        if (!unsafeDepth) fail(n->span, "pointer offset requires an unsafe block", "E240");
        if (!v.type->ir->isPointerTy()) fail(n->span, "interface views do not support pointer arithmetic");
        auto *address = read(v, n->span);
        auto amount = expression(n->children[1],
                                 mathInteger(n->children[1]) ? p.integer(p.pointerBits(), true) : nullptr);
        if (!live()) return neverValue();
        if (amount.type->kind != Kind::Int || amount.type->bits > p.pointerBits())
          fail(n->span, "pointer offset requires an address-width integer");
        auto *index =
            ir.CreateIntCast(read(amount, n->span), p.integer(p.pointerBits(), true)->ir, amount.type->sign);
        return {v.type, ir.CreateGEP(p.storage(v.type->parts[0]), address, index)};
      }
      if (guarded && (v.type->kind == Kind::Pointer || v.type->kind == Kind::Box))
        fail(callee->span, "optional member access cannot implicitly dereference raw pointers or Box", "E230");
      if (v.type->kind == Kind::Box)
        fail(callee->span, "Box payload method calls require explicit dereference", "E230");
      if (v.type->kind == Kind::Pointer || v.type->kind == Kind::Reference)
        v = dereference(v, n->span, true);
      if (v.type->kind != Kind::Class && v.type->kind != Kind::Interface)
        fail(n->span, "unknown method '" + member + "'");
      if (v.type->kind == Kind::Class) p.ensureSized(v.type, n->span);
      requireInit(v, n->span);
      if (member == "init" || member == "deinit")
        fail(n->span, "lifecycle method cannot be called explicitly");
      if (!v.address) {
        auto local = bind(" <receiver " + std::to_string(locals.size()) + ">", v.type, true,
                          read(v, n->span), n->span, temporaryScope);
        auto r = invoke(n, p.methods(v.type, member, false), types,
                        ValueAt{v.type, nullptr, locals[local].slot, true, local, true, true}, v.type, false, defer);
        r.temporaryBorrow = carriesBorrow(r.type);
        return r;
      }
      return invoke(n, p.methods(v.type, member, false), types, v, v.type, false, defer);
    };
    auto result = invokeMember();
    if (!guarded) return result;
    rejectViewEscape(result, n->span);
    if (!live()) fail(n->span, "optional method call needs a normally completing invocation", "E290");
    auto *optional = p.aggregate(Kind::Optional, {result.type});
    auto some = wrap(optional, read(result, n->span), true);
    auto *from = ir.GetInsertBlock();
    ir.CreateBr(end);
    ir.SetInsertPoint(none);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(optional->ir, 2);
    phi->addIncoming(some.value, from);
    phi->addIncoming(Constant::getNullValue(optional->ir), none);
    merge({initial, initialized}, initial.size());
    return {optional, phi, nullptr, false, -1, false, result.temporaryBorrow, result.origin};
  }
  const int calleeLocal = callee->kind == NodeKind::Name ? findLocal(callee->text) : -1;
  const bool scopedParameter = calleeLocal >= 0 && scopedCallableInput(locals[calleeLocal].reference);
  if (defer && scopedParameter)
    fail(callee->span, "a scoped callable parameter cannot be invoked through defer", "E230");
  auto target = scopedParameter ? name(callee, true) : expression(callee);
  if (!live()) return neverValue();
  if (target.type->kind == Kind::Box)
    fail(callee->span, "calling a boxed function requires explicit dereference", "E230");
  bool closureCall = target.type->kind == Kind::Closure;
  if ((!closureCall && target.type->kind != Kind::Function) || !types.empty())
    fail(n->span, "expression is not callable");
  if (target.type->cAbi && !unsafeDepth)
    fail(n->span, "external function pointer call requires unsafe", "E240");
  if (closureCall && target.type->consuming && target.borrowed)
    fail(n->span, "this closure must be consumed with move", "E230");
  Value *address = closureCall ? target.type->callEntry : read(target, n->span);
  auto &parts = closureCall ? target.type->callable : target.type->parts;
  if (parts.size() != n->children.size()) fail(n->span, "wrong argument count");
  if (defer && parts.back() != p.voidTy)
    fail(n->span, "defer requires a runtime void call; handle errors within the deferred function", "E231");
  if (defer && target.type->cAbi)
    fail(n->span, "deferred indirect calls currently require a Vaqrin function value", "E290");
  std::vector<Value *> values;
  std::vector<llvm::Type *> irTypes;
  std::vector<int> drops, deferredLoans;
  int deferredEnvironment = -1;
  enter();
  if (closureCall) {
    requireInit(target, n->span);
    const bool writableEnvironment = std::ranges::any_of(target.type->captures, [](const auto &c) {
      return c.writable && !c.reference;
    });
    const bool exclusiveInvocation = writableEnvironment ||
        std::ranges::any_of(target.type->captures, [](const auto &c) { return c.reference && c.writable; });
    // The payload origin describes the referents captured by the closure.
    // Invoking it accesses those borrows but writes writable captures in the
    // environment itself, not through the independent read-only referents.
    checkAccess(target, false, n->span);
    if (defer) validateDeferredBorrow(target, callee->span, defer->scope, true);
    Value *env = target.address;
    if (!env) {
      auto i = bind(" <closure>", target.type, true, read(target, n->span), n->span);
      recordBorrow(i, target, n->span);
      env = locals[i].slot;
      if (defer) deferredEnvironment = i;
    } else {
      if (!target.writable && writableEnvironment)
        fail(n->span, "closure with writable owned captures requires a writable receiver");
      auto environment = target;
      if (target.local >= 0 && !locals[target.local].reference
          && (locals[target.local].slot == env || p.hasReference(locals[target.local].type)))
        environment.origin = {target.local};
      checkAccess(environment, exclusiveInvocation, n->span);
      if (defer) {
        // The closure's own storage must stay live even when the callable
        // also carries shared borrows of an independent source.
        auto loan = borrow(environment, p.reference(target.type, !exclusiveInvocation), callee->span, false);
        validateDeferredBorrow(loan, callee->span, defer->scope);
        deferredLoans.push_back(pinBorrow(loan, callee->span, scopes.size() - 1));
      }
    }
    values.push_back(env);
    irTypes.push_back(env->getType());
  }
  Borrow returnOrigin{};
  bool returnTemporary = false;
  bool temporaryBorrow = target.temporaryBorrow;
  for (std::size_t i = 0; i + 1 < parts.size(); ++i) {
    bool moves = std::ranges::find(target.type->moveParameters, i) != target.type->moveParameters.end();
    if (defer && scopedCallableInput(parts[i]))
      fail(n->children[i + 1]->span, "a scoped callable parameter cannot be saved for a deferred call", "E230");
    auto v = argument(n->children[i + 1], parts[i], moves);
    if (!live()) {
      leave();
      return neverValue();
    }
    if (defer && p.scopedViewInput(parts[i]))
      fail(n->children[i + 1]->span, "scoped view input cannot be saved for deferred calls", "E230");
    if (!p.scopedViewInput(parts[i])) rejectViewEscape(v, n->children[i + 1]->span);
    if (defer) validateDeferredBorrow(v, n->children[i + 1]->span, defer->scope);
    auto *value = read(v, n->span);
    temporaryBorrow |= v.temporaryBorrow;
    if (target.type->count == i + 1) {
      returnOrigin = v.origin;
      returnTemporary = v.temporaryBorrow;
    }
    if (p.hasReference(parts[i])) {
      const bool write = !p.copyable(parts[i]);
      checkAccess(v, write, n->span);
      if (write) invalidateOwnedAddresses(v);
      auto loan = bind(" <loan " + std::to_string(i) + ">", parts[i], false, value, n->span);
      locals[loan].origin = v.origin;
      if (defer && v.hasBorrowPayload()) deferredLoans.push_back(loan);
    }
    if (p.drops(v.type))
      drops.push_back(bind(" <argument " + std::to_string(i) + ">", v.type, true, value, n->span));
    values.push_back(value);
    irTypes.push_back(p.storage(parts[i]));
  }
  for (auto i : drops)
    setAlive(i, false);
  auto *ret = parts.back();
  if (target.type->cAbi) {
    auto params = ArrayRef<Type *>(parts).drop_back();
    p.validateCAbi(ret, params, n->span);
    if (p.hasAggregateCAbi(ret, params)) {
      auto *value = p.callIndirectC(ir, address, ret, params, values, n->span);
      leave();
      return ret == p.voidTy ? voidValue() : ValueAt{ret, value};
    }
  }
  auto *f = ir.CreateCall(FunctionType::get(ret->ir, irTypes, false), address, values);
  if (target.type->cAbi && p.extendCNarrowInteger()) {
    if (ret->kind == Kind::Int && ret->bits < 32)
      f->addRetAttr(ret->sign ? llvm::Attribute::SExt : llvm::Attribute::ZExt);
    for (unsigned i = 0; i + 1 < parts.size(); ++i)
      if (parts[i]->kind == Kind::Int && parts[i]->bits < 32)
        f->addParamAttr(i, parts[i]->sign ? llvm::Attribute::SExt : llvm::Attribute::ZExt);
  }
  if (defer) {
    if (deferredEnvironment >= 0) retainDeferredLocal(deferredEnvironment, defer->scope);
    for (auto i : deferredLoans) retainDeferredLocal(i, defer->scope);
    registerDefer(f, n->span, *defer);
    leave();
    return voidValue();
  }
  leave();
  if (ret == p.neverTy) {
    ir.CreateUnreachable();
    return neverValue();
  }
  if (ret == p.voidTy) return voidValue();
  const bool viewResult = target.type->count && p.checkedViewResult(ret);
  returnOrigin.checkedView = viewResult;
  return trackView({ret, f, nullptr, false, -1, false,
                    (p.hasReference(ret) || viewResult) ? returnTemporary || returnOrigin.root < 0
                                                       : temporaryBorrow && carriesBorrow(ret),
                    returnOrigin}, n->span);
}

} // namespace vqr::detail
