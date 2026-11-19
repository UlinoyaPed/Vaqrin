#include "compiler_internal.hpp"

namespace vqr::detail {

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
    if (n->text == "*") return t->kind == Kind::Pointer ? t->parts[0] : nullptr;
    if (n->text == "post!") return t->kind == Kind::Result ? t->parts[0] : nullptr;
    if (n->text == "&" && n->children[0]->kind == NodeKind::Name) {
      int i = findLocal(n->children[0]->text);
      if (i >= 0) return p.pointer(t, !locals[i].writable);
    }
    return n->text == "&" ? nullptr : t;
  }
  case NodeKind::Index:
  case NodeKind::Slice: {
    auto *base = hint(n->children[0]);
    if (!base || (base->kind != Kind::Array && base->kind != Kind::Slice)) return nullptr;
    if (n->kind == NodeKind::Index) return base->parts[0];
    if (base->kind == Kind::Slice) return base;
    auto source = n->children[0];
    int i = source->kind == NodeKind::Name ? findLocal(source->text) : -1;
    return p.slice(base->parts[0], i < 0 || !locals[i].writable);
  }
  case NodeKind::Call: {
    auto callee = n->children[0];
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
          if (candidate->params.size() + 1 != n->children.size() || !candidate->generics.empty() ||
              hasComptimeParameters(*candidate) || (!candidate->readOnly && !writable) ||
              ((!candidate->visible || !def.publicPath) && owner != receiver))
            continue;
          if (!method.decl || (!candidate->readOnly && method.decl->readOnly))
            method = def;
          else if (candidate->readOnly == method.decl->readOnly)
            return nullptr;
        }
        if (method.decl) {
          auto *actual = method.declaringType ? method.declaringType : receiver;
          return p.type(method.decl->type, *method.file, p.classTypes(actual), actual->comptimeArguments);
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
            if (method.params.size() + 1 != n->children.size() || !method.generics.empty() ||
                hasComptimeParameters(method))
              continue;
            auto *actual = def.declaringType ? def.declaringType : owner;
            auto *type = p.type(method.type, *def.file, p.classTypes(actual), actual->comptimeArguments);
            if (result && result != type) return nullptr;
            result = type;
          }
          return result;
        }
      }
      auto ds = p.resolve(file, key, n->span);
      if (ds.size() == 1 && ds[0].decl->kind == DeclKind::Function && ds[0].decl->generics.empty() &&
          !hasComptimeParameters(*ds[0].decl))
        return p.function(ds[0]).result;
      if (ds.size() == 1 && ds[0].decl->kind == DeclKind::Class && ds[0].decl->generics.empty())
        return p.nominal(ds[0], {});
      if (generics.contains(key) || key == "bool" || key == "char" || key == "usize" || key == "isize" || key == "f32" ||
          key == "f64" ||
          (key.size() > 1 && (key[0] == 'i' || key[0] == 'u') && key[1] >= '0' && key[1] <= '9')) {
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

ValueAt Body::name(const Ast &n) {
  int i = findLocal(n->text);
  if (i >= 0) {
    if (n->text == "self" && constructing && constructed.size() < owner->fields.size())
      fail(n->span, "self cannot escape before every field is initialized", "E230");
    const auto &l = locals[i];
    return {l.type, nullptr, l.slot, l.writable, i, true};
  }
  if (owner)
    for (const auto &f : owner->fields) {
      if (f.decl->name != n->text) continue;
      int self = findLocal("self");
      if (self < 0) break;
      if (constructing && !constructed.contains(f.index))
        fail(n->span, "field is not initialized yet", "E230");
      return {f.type,
              nullptr,
              ir.CreateStructGEP(owner->ir, fn->getArg(0), f.index),
              !locals[self].type->readOnly && !f.decl->readOnly,
              -1,
              true};
    }
  auto defs = p.resolve(file, n->text, n->span);
  if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Global) {
    auto &g = p.global(defs[0]);
    return {g.type, nullptr, g.ir, !defs[0].decl->readOnly, -1, true};
  }
  if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Function && defs[0].decl->generics.empty()) {
    if (defs[0].decl->unsafe || defs[0].decl->comptime || hasComptimeParameters(*defs[0].decl))
      fail(n->span, "unsafe and comptime functions require a direct call");
    auto &f = p.function(defs[0]);
    p.lower(f);
    return {p.signature(f), f.ir};
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
    Type *obj = inner.type->kind == Kind::Pointer ? inner.type->parts[0] : inner.type;
    p.ensureSized(obj, n->span);
    auto f = std::ranges::find_if(obj->fields, [&](const Field &f) { return f.decl->name == n->text; });
    if (f == obj->fields.end()) fail(n->span, "unknown field '" + n->text + "'");
    if (!f->decl->visible && owner != obj) fail(n->span, "field is private");
    if (inner.type->kind == Kind::Pointer)
      inner = {obj, nullptr, read(inner, n->span), !inner.type->readOnly, -1, true};
    auto field = payload(inner, f->index, f->type);
    auto *type = p.aggregate(Kind::Optional, {f->type});
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
    return {type, phi};
  }
  auto basePath = path(base);
  if (!basePath.empty() && findLocal(basePath.substr(0, basePath.find('.'))) < 0) {
    auto defs = p.resolve(file, basePath, n->span);
    if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Error)
      return {p.errorTy, ir.getInt32(p.errorCode(defs[0], n->text))};
    if (defs.size() == 1 && defs[0].decl->kind == DeclKind::Enum) {
      auto *t = p.nominal(defs[0], {});
      auto v = std::ranges::find(t->variants, n->text, &Variant::name);
      if (v == t->variants.end() || !v->fields.empty())
        fail(n->span, "expected an enum member without a payload");
      return {t, ir.CreateInsertValue(Constant::getNullValue(t->ir), ConstantInt::get(p.context, v->tag), 0)};
    }
    if (!p.import(file, basePath).empty()) {
      auto full = p.resolve(file, basePath + "." + n->text, n->span);
      if (full.size() == 1 && full[0].decl->kind == DeclKind::Global) {
        auto &g = p.global(full[0]);
        return {g.type, nullptr, g.ir, !full[0].decl->readOnly, -1, true};
      }
      if (full.size() == 1 && full[0].decl->kind == DeclKind::Function && full[0].decl->generics.empty()) {
        if (full[0].decl->unsafe || full[0].decl->comptime || hasComptimeParameters(*full[0].decl))
          fail(n->span, "function requires a direct call");
        auto &f = p.function(full[0]);
        p.lower(f);
        return {p.signature(f), f.ir};
      }
      fail(n->span, "unresolved imported symbol '" + basePath + "." + n->text + "'");
    }
  }
  ValueAt v;
  if (constructing && base->kind == NodeKind::Name && base->text == "self")
    v = {locals[findLocal("self")].type, fn->getArg(0)};
  else
    v = expression(base);
  if (v.type == p.neverTy) return v;
  if (!v.borrowed && p.drops(v.type))
    fail(n->span, "bind a resource-owning temporary before projecting one of its fields", "E290");
  if (v.type->kind == Kind::Pointer)
    v = {v.type->parts[0], nullptr, read(v, n->span), !v.type->readOnly, -1, true};
  p.ensureSized(v.type, n->span);
  if (v.type->kind != Kind::Class) fail(n->span, "member access requires a class instance");
  auto f = std::ranges::find_if(v.type->fields, [&](const Field &f) { return f.decl->name == n->text; });
  if (f == v.type->fields.end()) fail(n->span, "unknown field '" + n->text + "'");
  if (!f->decl->visible && owner != v.type) fail(n->span, "field '" + n->text + "' is private");
  if (constructing && base->kind == NodeKind::Name && base->text == "self" && !constructed.contains(f->index))
    fail(n->span, "field is not initialized yet", "E230");
  auto field = payload(v, f->index, f->type);
  field.writable &= !f->decl->readOnly;
  return field;
}

ValueAt Body::index(const Ast &n) {
  auto v = expression(n->children[0]);
  if (v.type == p.neverTy) return v;
  requireInit(v, n->span);
  auto *t = v.type;
  if (!v.borrowed && p.drops(t))
    fail(n->span, "bind a resource-owning array before indexing or borrowing it", "E290");
  if (t->kind == Kind::Class && t->def.file->standard && t->def.file->package == "coll" &&
      t->def.decl->name == "Vec") {
    auto call = std::make_shared<Node>();
    call->kind = NodeKind::Call;
    call->span = n->span;
    call->children = {nullptr};
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
    address = asPointer(v, n->span).value;
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
    return {element, nullptr, offset(i), writable, t->kind == Kind::Array ? v.local : -1, true};
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
  return {slice, ir.CreateInsertValue(out, ir.CreateSub(hi, lo), 1)};
}

ValueAt Body::unary(const Ast &n, Type *expected) {
  const auto &op = n->text;
  auto v = expression(n->children[0], op == "!" ? p.boolTy : nullptr);
  if (v.type == p.neverTy) return v;
  if (op == "&") {
    if (!v.address) fail(n->span, "address-of requires an existing storage location");
    return asPointer(v, n->span);
  }
  if (op == "*") {
    if (v.type->kind != Kind::Pointer) fail(n->span, "dereference requires a pointer");
    return {v.type->parts[0], nullptr, read(v, n->span), !v.type->readOnly, -1, true};
  }
  if (op == "post!") {
    if (v.type->kind != Kind::Result || !result || result->kind != Kind::Result)
      fail(n->span, "error propagation requires a result and a fallible function");
    auto *raw = read(v, n->span, false);
    auto *error = ir.CreateExtractValue(raw, 0);
    auto *success = block("try.ok"), *failure = block("try.error");
    ir.CreateCondBr(ir.CreateICmpEQ(error, ir.getInt32(0)), success, failure);
    ir.SetInsertPoint(failure);
    emitReturn({p.errorTy, error}, n->span);
    ir.SetInsertPoint(success);
    auto out = payload(v, 1, v.type->parts[0]);
    return {out.type, read(out, n->span)};
  }
  auto *raw = read(v, n->span);
  if (op == "!") return {p.boolTy, ir.CreateNot(raw)};
  if (v.type->kind == Kind::Float && op == "-") return {v.type, ir.CreateFNeg(raw)};
  if (v.type->kind != Kind::Int) fail(n->span, "integer operand required for '" + op + "'");
  auto *t = v.type->bits < 32 ? p.integer(32, true) : v.type;
  if (v.type != t) raw = ir.CreateIntCast(raw, t->ir, v.type->sign);
  if (op == "~") return {t, ir.CreateNot(raw)};
  (void)expected;
  return {t, binaryNumber(ir, "-", ConstantInt::get(t->ir, 0), raw, t->sign, t->sign)};
}

ValueAt Body::operate(std::string_view op, ValueAt a, ValueAt b, Span span, bool retainWidth) {
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
  bool special = op == "+%" || op == "-%" || op == "*%" || op == "+|" || op == "-|" || op == "*|" ||
                 op == "<<%" || op == "<<|";
  auto *at = a.type, *bt = b.type;
  auto *av = read(a, span), *bv = read(b, span);
  if (at->kind == Kind::Int && bt->kind == Kind::Int && !retainWidth && !special) {
    if (at->bits < 32) {
      av = ir.CreateIntCast(av, ir.getInt32Ty(), at->sign);
      at = p.integer(32, true);
    }
    if (!shift && bt->bits < 32) {
      bv = ir.CreateIntCast(bv, ir.getInt32Ty(), bt->sign);
      bt = p.integer(32, true);
    }
  }
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
  auto *leftHint = hint(n->children[0]);
  auto *rightHint = hint(n->children[1]);
  if (leftHint && leftHint->kind == Kind::Int && leftHint->bits < 32 && !op.ends_with('|') && op != "+%" &&
      op != "-%" && op != "*%" && op != "<<%")
    leftHint = p.integer(32, true);
  if (rightHint && rightHint->kind == Kind::Int && rightHint->bits < 32 && !op.ends_with('|') && op != "+%" &&
      op != "-%" && op != "*%" && op != "<<%")
    rightHint = p.integer(32, true);
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
  auto *context = hint(n->children[1]);
  if (context && context->kind == Kind::Int && context->bits < 32) context = p.integer(32, true);
  auto left =
      expression(first, mathInteger(first) && context && context->kind == Kind::Int ? context : nullptr);
  if (left.type == p.neverTy) return left;
  left = {left.type, read(left, n->span, false)};
  auto *end = block("compare.end");
  std::vector<std::pair<Value *, BasicBlock *>> edges;
  std::vector<std::vector<bool>> states;
  auto size = initialized.size();
  for (std::size_t i = 0; i < n->names.size(); ++i) {
    auto next = n->children[i + 1];
    auto *t = left.type;
    if (t->kind == Kind::Int && t->bits < 32) t = p.integer(32, true);
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
  if (!n->names.empty()) bind(n->names[0], p.errorTy, false, tag, n->span);
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
  return {inner, phi};
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
    return wrap(p.aggregate(kind, {value.type}), read(value, n->span), true);
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
  if (key == "truncToInt" || key == "roundToInt") {
    arity(1);
    auto *to = evaluationType();
    auto v = expression(n->children[1]);
    if (!live()) return neverValue();
    if (to->kind != Kind::Int || v.type->kind != Kind::Float)
      fail(n->span, "expected a float and integer target type");
    auto *raw = read(v, n->span);
    auto intr = key == "truncToInt" ? Intrinsic::trunc : Intrinsic::roundeven;
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
                     std::optional<ValueAt> receiver, Type *classType, bool delegation) {
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
    auto inferValue = [&](const Ast &pattern, std::pair<Type *, Constant *> value) -> void {
      if (!pattern || (pattern->kind != NodeKind::TypeName && pattern->kind != NodeKind::Name) ||
          !pattern->children.empty() || !valueNames.contains(pattern->text))
        return;
      auto [it, inserted] = inferredValues.emplace(pattern->text, value);
      consistent &= inserted || it->second == value;
    };
    auto infer = [&](auto &&recurse, const Ast &pattern, Type *actual) -> void {
      if (!actual) return;
      if (pattern->kind == NodeKind::TypeName && typeNames.contains(pattern->text)) {
        auto [it, inserted] = subst.emplace(pattern->text, actual);
        consistent &= inserted || it->second == actual;
      } else if (pattern->kind == NodeKind::TypeName && !pattern->children.empty() &&
                 (actual->kind == Kind::Class || actual->kind == Kind::Enum)) {
        auto matches = p.resolve(*def.file, pattern->text, pattern->span);
        if (matches.size() != 1 || matches.front().decl != actual->def.decl) return;
        const auto &parameters = actual->def.decl->generics;
        if (pattern->children.size() != parameters.size()) return;
        std::size_t typeIndex = 0;
        for (std::size_t i = 0; i < parameters.size(); ++i) {
          if (parameters[i].comptime)
            inferValue(pattern->children[i], actual->comptimeArguments.at(parameters[i].name));
          else
            recurse(recurse, pattern->children[i], actual->parts[typeIndex++]);
        }
      } else if (pattern->kind == NodeKind::TypeArray && actual->kind == Kind::Array) {
        inferValue(pattern->children[0], {p.sizeTy, ConstantInt::get(p.sizeTy->ir, actual->count)});
        recurse(recurse, pattern->children[1], actual->parts[0]);
      } else if (pattern->kind == NodeKind::TypeFunction && actual->kind == Kind::Function &&
                 pattern->children.size() + 1 == actual->parts.size()) {
        for (std::size_t i = 0; i < pattern->children.size(); ++i)
          recurse(recurse, pattern->children[i], actual->parts[i]);
        recurse(recurse, pattern->type, actual->parts.back());
      } else if (pattern->kind == NodeKind::TypePrefix && !actual->parts.empty()) {
        const auto &op = pattern->text;
        if (((op == "*" || op == "*const") && actual->kind == Kind::Pointer) ||
            ((op == "[]" || op == "[]const") && actual->kind == Kind::Slice) ||
            (op == "?" && actual->kind == Kind::Optional) || (op == "!" && actual->kind == Kind::Result) ||
            (op == "^" && actual->kind == Kind::Box))
          recurse(recurse, pattern->children[0], actual->parts[0]);
      }
    };
    if (args.size() < d.generics.size()) {
      std::vector<std::pair<Ast, Type *>> untyped;
      for (unsigned j = 0; j < d.params.size(); ++j) {
        auto node = n->children[j + 1];
        auto parameter = d.params[j].type;
        if (auto *t = hint(node)) {
          infer(infer, parameter, t);
        } else if (mathInteger(node)) {
          untyped.emplace_back(parameter, p.integer(32, true));
        } else if (node->kind == NodeKind::Number && parseNumber(node->text).floating) {
          untyped.emplace_back(parameter, p.canonical.at("f64"));
        }
      }
      for (const auto &[parameter, t] : untyped)
        if (parameter->kind == NodeKind::TypeName && !subst.contains(parameter->text))
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
      auto *from = hint(a);
      auto math = mathInteger(a);
      unsigned rank = 0;
      for (;;) {
        if (from == t) break;
        if (math && t->kind == Kind::Int && fits(*math, t->bits, t->sign)) break;
        if (a->kind == NodeKind::Null && t->kind == Kind::Optional) break;
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
  if (!best) fail(n->span, "no accessible overload matches the argument types and receiver", "E202");
  auto &d = *best->definition.decl;
  unsigned offset = receiver.has_value();
  for (unsigned i = 0; i < d.params.size(); ++i) {
    const auto &param = d.params[i];
    if (!param.comptime) continue;
    auto *t = best->parameters[i + offset];
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
  if (receiver) {
    auto *address = asPointer(*receiver, n->span).value;
    if (dynamic) {
      auto *table = ir.CreateExtractValue(address, 1);
      const auto &methods = declaring->def.decl->members;
      auto slot = static_cast<unsigned>(&d - methods.data());
      auto *pointer = PointerType::getUnqual(p.context);
      callee = ir.CreateLoad(pointer, ir.CreateConstGEP1_32(pointer, table, slot));
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
  enter();
  for (unsigned i = 1; i < n->children.size(); ++i) {
    if (d.params[i - 1].comptime) continue;
    auto *t = best->parameters[i - 1 + offset];
    auto value = expression(n->children[i], t);
    if (!live()) {
      leave();
      return neverValue();
    }
    auto *raw = read(value, n->span);
    values.push_back(raw);
    if (p.drops(t)) {
      auto temp = bind(" <argument " + std::to_string(i) + ">", t, true, raw, n->span);
      transfers.push_back(temp);
    }
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
  } else {
    called = ir.CreateCall(entry->ir, values);
    called->setAttributes(entry->ir->getAttributes());
  }
  if (returnType == p.neverTy) ir.CreateUnreachable();
  leave();
  return returnType == p.voidTy ? voidValue() : ValueAt{returnType, called};
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
  if (n->kind != NodeKind::Fields)
    fail(n->span, "class has no matching constructor; use a field initializer");
  if (!p.methods(t, "init", false).empty())
    fail(n->span, "class declares init and cannot use field construction");
  std::map<std::string, Ast> explicitValues;
  for (std::size_t i = 0; i < n->names.size(); ++i)
    if (!explicitValues.emplace(n->names[i], n->children[i + 1]).second)
      fail(n->span, "field initialized twice");
  Value *out = Constant::getNullValue(t->ir);
  enter();
  std::vector<int> transfers;
  for (const auto &f : t->fields) {
    if (!f.decl->visible && owner != t) fail(n->span, "field '" + f.decl->name + "' is private");
    auto value = explicitValues.find(f.decl->name);
    auto initializer = value == explicitValues.end() ? f.decl->body : value->second;
    if (!initializer) fail(n->span, "missing initializer for '" + f.decl->name + "'");
    if (value != explicitValues.end()) explicitValues.erase(value);
    auto v = expression(initializer, f.type);
    if (!live()) {
      leave();
      return neverValue();
    }
    auto *data = read(v, n->span);
    if (p.drops(f.type))
      transfers.push_back(bind(" <field " + f.decl->name + ">", f.type, true, data, n->span));
    out = ir.CreateInsertValue(out, data, f.index);
  }
  if (!explicitValues.empty()) fail(n->span, "unknown field '" + explicitValues.begin()->first + "'");
  for (auto i : transfers)
    setAlive(i, false);
  leave();
  return {t, out};
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
  initialized.push_back(true);
  (void)span;
}

ValueAt Body::lambda(const Ast &n, Type *expected) {
  if (expected && expected->kind != Kind::Function) expected = nullptr;
  if (expected && (expected->cAbi || expected->parts.size() != n->parameters.size() + 1))
    fail(n->span, "lambda signature does not match its context");
  bool hasCaptures = !n->children.empty();
  std::vector<Type *> signature;
  auto *result = n->type ? parseType(n->type) : expected ? expected->parts.back() : nullptr;
  std::vector<llvm::Type *> irArgs;
  if (hasCaptures) irArgs.push_back(PointerType::getUnqual(p.context));
  for (unsigned i = 0; i < n->parameters.size(); ++i) {
    const auto &param = n->parameters[i];
    auto *t = param.type ? parseType(param.type) : expected ? expected->parts[i] : nullptr;
    if (!t) fail(param.span, "lambda parameter requires a type or a function type context");
    p.ensureSized(t, param.span);
    signature.push_back(t);
    irArgs.push_back(p.storage(t));
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
      bool reference = item->kind == NodeKind::Unary;
      if (!reference && !p.movable(local.type))
        fail(item->span, "immovable values require a reference capture", "E290");
      captures.push_back(
          {original->text, local.type, reference, local.writable, static_cast<unsigned>(fields.size())});
      fields.push_back(p.storage(reference ? p.pointer(local.type, !local.writable) : local.type));
      fields.push_back(p.boolTy->ir);
    }
    t = p.add(Kind::Closure, StructType::get(p.context, fields), name);
    t->captures = captures;
  }
  for (const auto &param : n->parameters)
    if (!names.insert(param.name).second)
      fail(param.span, "parameter conflicts with another parameter or capture");
  if (result) p.ensureSized(result, n->span);
  auto *function = Function::Create(FunctionType::get(result ? result->ir : p.voidTy->ir, irArgs, false),
                                    GlobalValue::InternalLinkage, name, *p.module);
  function->setDoesNotThrow();
  Body body(p, file, function, result, generics, evaluating);
  body.closure = t;
  if (hasCaptures)
    for (const auto &c : captures)
      body.bindCapture(c, function->getArg(0), n->span);
  unsigned arg = hasCaptures ? 1 : 0;
  for (unsigned i = 0; i < n->parameters.size(); ++i) {
    const auto &param = n->parameters[i];
    auto *value = function->getArg(arg++);
    value->setName(param.name);
    body.bind(param.name, signature[i], true, value, param.span);
  }
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
  }
  if (body.live()) body.emitReturn(returned, n->span);
  signature.push_back(result);
  if (!hasCaptures) return {p.aggregate(Kind::Function, signature), function};
  t->callable = signature;
  t->callEntry = function;
  Value *environment = Constant::getNullValue(t->ir);
  enter();
  std::vector<int> transfers;
  for (unsigned i = 0; i < captures.size(); ++i) {
    auto v = expression(n->children[i]);
    if (!live()) {
      leave();
      return neverValue();
    }
    auto *stored = read(v, n->span);
    if (p.drops(v.type))
      transfers.push_back(bind(" <capture " + std::to_string(i) + ">", v.type, true, stored, n->span));
    environment = ir.CreateInsertValue(environment, stored, captures[i].index);
    environment = ir.CreateInsertValue(environment, ir.getTrue(), captures[i].index + 1);
  }
  for (int i : transfers)
    setAlive(i, false);
  leave();
  return {t, environment};
}

ValueAt Body::allocate(const Ast &n) {
  if (n->tail) fail(n->span, "custom allocators are not yet supported", "E290");
  if (evaluating)
    fail(n->span, "heap allocation during compile-time evaluation is not yet supported", "E290");
  if (!p.heapAvailable()) fail(n->span, "target has no default Box allocator", "E290");
  auto *t = parseType(n->type);
  p.ensureSized(t, n->span);
  auto *box = p.box(t);
  auto *resultType = p.aggregate(Kind::Result, {box});
  auto *ptr = PointerType::getUnqual(p.context);
  auto &layout = p.module->getDataLayout();
  auto align = std::max<std::uint64_t>(p.pointerBits() / 8, layout.getABITypeAlign(p.storage(t)).value());
  auto size = layout.getTypeAllocSize(p.storage(t)).getFixedValue();
  size = std::max(align, ((size + align - 1) / align) * align);
  auto sizeConstant = [&](std::uint64_t value) { return ConstantInt::get(p.sizeTy->ir, value); };
  auto allocator = p.module->getOrInsertFunction("aligned_alloc",
                                                 FunctionType::get(ptr, {p.sizeTy->ir, p.sizeTy->ir}, false));
  auto *storage = ir.CreateCall(allocator, {sizeConstant(align), sizeConstant(size)});
  auto *initialBlock = ir.GetInsertBlock(), *allocated = block("heap.allocated"), *end = block("heap.end");
  auto failure = wrap(resultType, ir.getInt32(0xfffffffc), false);
  auto initial = initialized;
  ir.CreateCondBr(ir.CreateIsNotNull(storage), allocated, end);
  ir.SetInsertPoint(allocated);
  enter();
  auto *allocationType = p.canonical.contains("<allocation>") ? p.canonical.at("<allocation>")
                                                              : p.add(Kind::Allocation, ptr, "<allocation>");
  auto raw = bind(" <allocation>", allocationType, true, storage, n->span);
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
    auto success = wrap(resultType, storage, true);
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

ValueAt Body::call(const Ast &n, Type *expected) {
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
        return invoke(n, ms, types, ValueAt{owner, nullptr, read(v, n->span), !v.type->readOnly, -1, true},
                      owner);
      }
    }
    auto ds = p.resolve(file, key, n->span);
    if (!ds.empty()) {
      auto *d = ds.front().decl;
      if (d->kind == DeclKind::Function) {
        if (ds.front().file->standard && ds.front().file->package == "io" && d->name == "println" &&
            types.empty())
          return builtin("io.println", n, types, expected);
        return invoke(n, ds, types);
      }
      if (d->kind == DeclKind::Class || d->kind == DeclKind::Alias) {
        auto *t = p.named(key, file, n->span, generics, types, visibleConstants());
        if (t->kind == Kind::Class) return construct(n, t, p.methods(t, "init", false));
        if (n->children.size() != 2) fail(n->span, "conversion requires one value");
        return castNumber(t, n->children[1], n->span);
      }
    }
    const std::set<std::string_view> intrinsics{"some",       "ok",         "bitCast",      "ptrFromInt",
                                                "ptrCast",    "constCast",  "volatileLoad", "volatileStore",
                                                "truncToInt", "roundToInt", "panic"};
    if (dot == key.npos && intrinsics.contains(key)) return builtin(key, n, types, expected);
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
    if (key == "Box.intoRaw" && findLocal("Box") < 0 && p.resolve(file, "Box", n->span).empty()) {
      if (n->children.size() != 2) fail(n->span, "Box.intoRaw takes one Box value");
      auto box = expression(n->children[1]);
      if (!live()) return neverValue();
      if (box.type->kind != Kind::Box) fail(n->span, "Box.intoRaw takes one Box value");
      return {p.pointer(box.type->parts[0], false), read(box, n->span)};
    }
    if (base->kind == NodeKind::Generic && path(base->children[0]) == "Box" && callee->text == "fromRaw" &&
        findLocal("Box") < 0 && p.resolve(file, "Box", n->span).empty()) {
      if (!unsafeDepth) fail(n->span, "Box.fromRaw requires an unsafe block", "E240");
      if (base->children.size() != 2 || n->children.size() != 2)
        fail(n->span, "expected Box<T>.fromRaw(pointer)");
      auto *element = parseType(base->children[1]);
      p.ensureSized(element, n->span);
      auto ptr = expression(n->children[1], p.pointer(element, false));
      if (!live()) return neverValue();
      return {p.box(element), read(ptr, n->span)};
    }
    auto target = base->kind == NodeKind::Generic ? base->children[0] : base;
    auto basePath = path(target);
    auto ds = basePath.empty() || findLocal(basePath.substr(0, basePath.find('.'))) >= 0
                  ? std::vector<Definition>{}
                  : p.resolve(file, basePath, n->span);
    if (ds.size() == 1 && (ds[0].decl->kind == DeclKind::Class || ds[0].decl->kind == DeclKind::Enum)) {
      std::vector<Ast> args;
      if (target != base) args.assign(base->children.begin() + 1, base->children.end());
      auto *t = p.named(basePath, file, n->span, generics, args, visibleConstants());
      p.ensureSized(t, n->span);
      if (t->kind == Kind::Class) return invoke(n, p.methods(t, callee->text, true), types, std::nullopt, t);
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
      for (unsigned i = 0; i < variant->fields.size(); ++i) {
        auto v = expression(n->children[i + 1], variant->fields[i]);
        if (!live()) {
          leave();
          return neverValue();
        }
        auto *raw = read(v, n->span);
        if (p.drops(v.type))
          transfers.push_back(bind(" <payload " + std::to_string(i) + ">", v.type, true, raw, n->span));
        value = ir.CreateInsertValue(value, raw, variant->offset + i);
      }
      for (auto i : transfers)
        setAlive(i, false);
      leave();
      return {t, value};
    }
    auto v = expression(base);
    if (!live()) return neverValue();
    auto member = callee->text;
    bool guarded = !callee->names.empty();
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
      if (v.type->kind == Kind::Box && member == "asPtr" && n->children.size() == 1) {
        if (!v.borrowed) fail(n->span, "bind the Box before taking a pointer to its contents", "E290");
        return {p.pointer(v.type->parts[0], !v.writable), read(v, n->span, false)};
      }
      if (v.type->kind == Kind::Optional && member == "unwrap" && n->children.size() == 1) {
        guard(ir, ir.CreateExtractValue(read(v, n->span, false), 0));
        auto out = payload(v, 1, v.type->parts[0]);
        return {out.type, read(out, n->span)};
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
        return {p.slice(p.integer(8, false), true), textView(v, n->span)};
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
      if (v.type->kind == Kind::Pointer)
        v = {v.type->parts[0], nullptr, read(v, n->span), !v.type->readOnly, -1, true};
      if (v.type->kind != Kind::Class && v.type->kind != Kind::Interface)
        fail(n->span, "unknown method '" + member + "'");
      if (v.type->kind == Kind::Class) p.ensureSized(v.type, n->span);
      requireInit(v, n->span);
      if (member == "init" || member == "deinit")
        fail(n->span, "lifecycle method cannot be called explicitly");
      if (!v.address) {
        auto *temp = slot(v.type, "receiver.temp");
        ir.CreateStore(read(v, n->span), temp);
        enter();
        auto local = bind(" <receiver>", v.type, true, ir.CreateLoad(v.type->ir, temp), n->span);
        auto r = invoke(n, p.methods(v.type, member, false), types,
                        ValueAt{v.type, nullptr, locals[local].slot, true, local, true}, v.type);
        leave();
        return r;
      }
      return invoke(n, p.methods(v.type, member, false), types, v, v.type);
    };
    auto result = invokeMember();
    if (!guarded) return result;
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
    return {optional, phi};
  }
  auto target = expression(callee);
  if (!live()) return neverValue();
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
  std::vector<Value *> values;
  std::vector<llvm::Type *> irTypes;
  enter();
  if (closureCall) {
    requireInit(target, n->span);
    Value *env = target.address;
    if (!env) {
      auto i = bind(" <closure>", target.type, true, read(target, n->span), n->span);
      env = locals[i].slot;
    } else if (!target.writable && std::ranges::any_of(target.type->captures, [](const auto &c) {
                 return c.writable && !c.reference;
               }))
      fail(n->span, "closure with writable owned captures requires a writable receiver");
    values.push_back(env);
    irTypes.push_back(env->getType());
  }
  std::vector<int> drops;
  for (std::size_t i = 0; i + 1 < parts.size(); ++i) {
    auto v = expression(n->children[i + 1], parts[i]);
    if (!live()) {
      leave();
      return neverValue();
    }
    auto *value = read(v, n->span);
    if (p.drops(v.type))
      drops.push_back(bind(" <argument " + std::to_string(i) + ">", v.type, true, value, n->span));
    values.push_back(value);
    irTypes.push_back(p.storage(parts[i]));
  }
  for (auto i : drops)
    setAlive(i, false);
  auto *ret = parts.back();
  auto *f = ir.CreateCall(FunctionType::get(ret->ir, irTypes, false), address, values);
  if (target.type->cAbi && p.extendCNarrowInteger()) {
    if (ret->kind == Kind::Int && ret->bits < 32)
      f->addRetAttr(ret->sign ? llvm::Attribute::SExt : llvm::Attribute::ZExt);
    for (unsigned i = 0; i + 1 < parts.size(); ++i)
      if (parts[i]->kind == Kind::Int && parts[i]->bits < 32)
        f->addParamAttr(i, parts[i]->sign ? llvm::Attribute::SExt : llvm::Attribute::ZExt);
  }
  leave();
  if (ret == p.neverTy) {
    ir.CreateUnreachable();
    return neverValue();
  }
  return ret == p.voidTy ? voidValue() : ValueAt{ret, f};
}

} // namespace vqr::detail
