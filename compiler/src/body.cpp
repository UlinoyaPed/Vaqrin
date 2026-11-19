#include "compiler_internal.hpp"

namespace vqr::detail {

Body::Body(Program &p, const File &f, Function *fn, Type *result, Types generics, bool constant, Type *owner)
    : ir(p.context), p(p), file(f), fn(fn), result(result), generics(std::move(generics)),
      evaluating(constant), owner(owner) {
  ir.SetInsertPoint(block("entry"));
  enter();
}

BasicBlock *Body::block(std::string_view name) {
  return BasicBlock::Create(p.context, StringRef(name), fn);
}

AllocaInst *Body::slot(Type *t, std::string_view name) {
  IRBuilder<> entry(&fn->getEntryBlock(), fn->getEntryBlock().getFirstInsertionPt());
  return entry.CreateAlloca(p.storage(t), nullptr, StringRef(name));
}

int Body::findLocal(std::string_view name) const {
  for (auto s = scopes.rbegin(); s != scopes.rend(); ++s)
    for (auto i : *s)
      if (locals[i].name == name) return i;
  return -1;
}

int Body::bind(std::string name, Type *t, bool writable, Value *initial, Span span) {
  bool reference = name.starts_with('&');
  if (reference) {
    name.erase(0, 1);
    if (t->kind != Kind::Pointer || !initial)
      fail(span, "dereference binding requires an initialized pointer");
    writable = !t->readOnly;
    t = t->parts[0];
  }
  if (name == "_") name = " <discarded parameter " + std::to_string(locals.size()) + ">";
  if (std::ranges::any_of(scopes.back(), [&](int i) { return locals[i].name == name; }))
    fail(span, "duplicate local '" + name + "'");
  int i = static_cast<int>(locals.size());
  auto *address = reference ? initial : slot(t, name);
  auto *order = !reference && p.drops(t) ? slot(p.integer(32, false), "drop.order") : nullptr;
  if (order) ir.CreateStore(ir.getInt32(0), order);
  locals.push_back({std::move(name), t, address, order, nullptr, reference, scopes.size() - 1, writable});
  scopes.back().push_back(i);
  initialized.resize(locals.size(), false);
  if (!reference && initial && t != p.voidTy) ir.CreateStore(initial, address);
  setAlive(i, initial != nullptr);
  return i;
}

void Body::requireInit(ValueAt v, Span span) {
  if (v.local >= 0 && !initialized[v.local])
    fail(span, "local '" + locals[v.local].name + "' is not initialized on every incoming path", "E230");
}

void Body::setAlive(int i, bool alive) {
  initialized[i] = alive;
  registerDrop(i, alive);
  if (locals[i].captureFlag) ir.CreateStore(ir.getInt1(alive), locals[i].captureFlag);
  if (locals[i].debug) debugValue(i, alive);
}

void Body::registerDrop(int i, bool alive) {
  const auto &local = locals[i];
  if (!local.order) return;
  Value *rank = ir.getInt32(0);
  if (!alive) rank = ir.CreateLoad(ir.getInt32Ty(), local.order);
  Value *highest = ir.getInt32(0);
  for (auto j : scopes[local.scope]) {
    auto *other = locals[j].order;
    if (j == i || !other) continue;
    auto *n = ir.CreateLoad(ir.getInt32Ty(), other);
    if (alive)
      highest = ir.CreateSelect(ir.CreateICmpUGT(n, highest), n, highest);
    else {
      auto *younger = ir.CreateAnd(ir.CreateICmpNE(rank, ir.getInt32(0)), ir.CreateICmpUGT(n, rank));
      ir.CreateStore(ir.CreateSub(n, ir.CreateZExt(younger, ir.getInt32Ty())), other);
    }
  }
  ir.CreateStore(alive ? ir.CreateAdd(highest, ir.getInt32(1)) : ir.getInt32(0), local.order);
}

void Body::merge(const std::vector<std::vector<bool>> &states, std::size_t size) {
  initialized.assign(locals.size(), false);
  for (std::size_t i = 0; i < size; ++i) {
    initialized[i] =
        !states.empty() && std::ranges::all_of(states, [&](const auto &s) { return i < s.size() && s[i]; });
  }
}

void Body::freeAllocation(Value *address) {
  auto *ptr = PointerType::getUnqual(p.context);
  auto free = p.module->getOrInsertFunction("free", FunctionType::get(ir.getVoidTy(), {ptr}, false));
  ir.CreateCall(free, {address});
}

void Body::drop(Type *t, Value *address) {
  p.ensureSized(t, {});
  if (p.drops(t)) ir.CreateCall(p.dropGlue(t), {address});
}

void Body::emitDrop(Type *t, Value *address) {
  if (t->kind == Kind::Text) {
    auto *data = ir.CreateLoad(t->ir, address);
    auto *owner = ir.CreateExtractValue(data, 2);
    auto *release = block("text.release"), *last = block("text.last"), *end = block("text.done");
    ir.CreateCondBr(ir.CreateIsNotNull(owner), release, end);
    ir.SetInsertPoint(release);
    auto *one = ConstantInt::get(p.sizeTy->ir, 1);
    auto *previous = ir.CreateAtomicRMW(AtomicRMWInst::Sub, owner, one,
                                        p.module->getDataLayout().getABITypeAlign(p.sizeTy->ir),
                                        AtomicOrdering::AcquireRelease);
    ir.CreateCondBr(ir.CreateICmpEQ(previous, one), last, end);
    ir.SetInsertPoint(last);
    freeAllocation(owner);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
  } else if (t->kind == Kind::Box || t->kind == Kind::Allocation) {
    auto *pointer = ir.CreateLoad(t->ir, address);
    if (t->kind == Kind::Box) drop(t->parts[0], pointer);
    freeAllocation(pointer);
  } else if (t->kind == Kind::PatternRemainder) {
    auto *subject = ir.CreateLoad(PointerType::getUnqual(p.context), ir.CreateStructGEP(t->ir, address, 0));
    auto *pattern = ir.CreateLoad(ir.getInt32Ty(), ir.CreateStructGEP(t->ir, address, 1));
    auto *end = block("drop.match.end");
    auto *dispatch = ir.CreateSwitch(pattern, end, t->patterns.size());
    for (unsigned i = 0; i < t->patterns.size(); ++i) {
      auto *selected = block("drop.match");
      dispatch->addCase(ir.getInt32(i), selected);
      ir.SetInsertPoint(selected);
      dropRemainder(t->parts[0], subject, t->patterns[i]);
      ir.CreateBr(end);
    }
    ir.SetInsertPoint(end);
  } else if (t->kind == Kind::Closure) {
    for (auto c = t->captures.rbegin(); c != t->captures.rend(); ++c) {
      if (c->reference || !p.drops(c->type)) continue;
      auto *dispose = block("drop.capture"), *next = block("drop.capture.next");
      auto *flag = ir.CreateStructGEP(t->ir, address, c->index + 1);
      ir.CreateCondBr(ir.CreateLoad(p.boolTy->ir, flag), dispose, next);
      ir.SetInsertPoint(dispose);
      drop(c->type, ir.CreateStructGEP(t->ir, address, c->index));
      ir.CreateBr(next);
      ir.SetInsertPoint(next);
    }
  } else if (t->kind == Kind::Class) {
    auto methods = p.methods(t, "deinit", false);
    if (!methods.empty()) {
      auto &f = p.function(methods.front(), p.classTypes(t), t);
      p.lower(f);
      ir.CreateCall(f.ir, {address});
    }
    for (auto f = t->fields.rbegin(); f != t->fields.rend(); ++f)
      if (p.drops(f->type)) drop(f->type, ir.CreateStructGEP(t->ir, address, f->index));
  } else if (t->kind == Kind::Tuple) {
    for (unsigned i = t->parts.size(); i-- > 0;)
      if (p.drops(t->parts[i])) drop(t->parts[i], ir.CreateStructGEP(t->ir, address, i));
  } else if (t->kind == Kind::Optional || t->kind == Kind::Result) {
    auto *active = block("drop.payload"), *end = block("drop.done");
    auto *tagTy = t->kind == Kind::Optional ? p.boolTy->ir : p.errorTy->ir;
    auto *tag = ir.CreateLoad(tagTy, ir.CreateStructGEP(t->ir, address, 0));
    ir.CreateCondBr(t->kind == Kind::Optional ? tag : ir.CreateICmpEQ(tag, ir.getInt32(0)), active, end);
    ir.SetInsertPoint(active);
    drop(t->parts[0], ir.CreateStructGEP(t->ir, address, 1));
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
  } else if (t->kind == Kind::Enum) {
    auto *end = block("drop.enum.done");
    auto *tag = ir.CreateLoad(ir.getIntNTy(t->bits), ir.CreateStructGEP(t->ir, address, 0));
    auto *dispatch = ir.CreateSwitch(tag, end, t->variants.size());
    for (const auto &v : t->variants) {
      if (!std::ranges::any_of(v.fields, [&](auto *f) { return p.drops(f); })) continue;
      auto *active = block("drop.variant");
      dispatch->addCase(ConstantInt::get(p.context, v.tag), active);
      ir.SetInsertPoint(active);
      for (unsigned i = v.fields.size(); i-- > 0;)
        if (p.drops(v.fields[i])) drop(v.fields[i], ir.CreateStructGEP(t->ir, address, v.offset + i));
      ir.CreateBr(end);
    }
    ir.SetInsertPoint(end);
  } else if (t->kind == Kind::Array && t->count) {
    auto *start = ir.GetInsertBlock();
    auto *body = block("drop.array"), *end = block("drop.array.done");
    ir.CreateBr(body);
    ir.SetInsertPoint(body);
    auto *index = ir.CreatePHI(p.sizeTy->ir, 2);
    index->addIncoming(ConstantInt::get(p.sizeTy->ir, t->count), start);
    auto *next = ir.CreateSub(index, ConstantInt::get(p.sizeTy->ir, 1));
    drop(t->parts[0], ir.CreateInBoundsGEP(t->ir, address, {ir.getInt32(0), next}));
    index->addIncoming(next, ir.GetInsertBlock());
    ir.CreateCondBr(ir.CreateICmpEQ(next, ConstantInt::get(p.sizeTy->ir, 0)), end, body);
    ir.SetInsertPoint(end);
  }
}

void Body::dropLocal(int i) {
  const auto &l = locals[i];
  if (!l.order && !l.captureFlag) return;
  auto *run = block("drop.local"), *end = block("drop.next");
  auto *active = l.order ? ir.CreateICmpNE(ir.CreateLoad(ir.getInt32Ty(), l.order), ir.getInt32(0))
                         : ir.CreateLoad(p.boolTy->ir, l.captureFlag);
  ir.CreateCondBr(active, run, end);
  ir.SetInsertPoint(run);
  registerDrop(i, false);
  if (l.captureFlag) ir.CreateStore(ir.getFalse(), l.captureFlag);
  drop(l.type, l.slot);
  ir.CreateBr(end);
  ir.SetInsertPoint(end);
}

void Body::clean(std::size_t depth) {
  for (std::size_t s = scopes.size(); s-- > depth;) {
    auto &scope = scopes[s];
    std::vector<int> managed;
    for (int i : scope)
      if (locals[i].order) managed.push_back(i);
    if (managed.empty()) continue;
    if (managed.size() == 1) {
      dropLocal(managed.front());
      continue;
    }
    auto *choose = block("drop.scope"), *done = block("drop.scope.done");
    ir.CreateBr(choose);
    ir.SetInsertPoint(choose);
    Value *highest = ir.getInt32(0), *selected = ir.getInt32(0);
    for (unsigned i = 0; i < managed.size(); ++i) {
      auto *rank = ir.CreateLoad(ir.getInt32Ty(), locals[managed[i]].order);
      auto *newest = ir.CreateICmpUGT(rank, highest);
      highest = ir.CreateSelect(newest, rank, highest);
      selected = ir.CreateSelect(newest, ir.getInt32(i + 1), selected);
    }
    auto *dispatch = ir.CreateSwitch(selected, done, managed.size());
    for (unsigned i = 0; i < managed.size(); ++i) {
      auto *next = block("drop.newest");
      dispatch->addCase(ir.getInt32(i + 1), next);
      ir.SetInsertPoint(next);
      const auto &local = locals[managed[i]];
      ir.CreateStore(ir.getInt32(0), local.order);
      drop(local.type, local.slot);
      ir.CreateBr(choose);
    }
    ir.SetInsertPoint(done);
  }
}

void Body::leave() {
  if (live()) clean(scopes.size() - 1);
  scopes.pop_back();
}

ValueAt Body::voidValue() {
  return {p.voidTy, Constant::getNullValue(p.storage(p.voidTy))};
}
ValueAt Body::neverValue() {
  return {p.neverTy};
}

Value *Body::emitCopy(Type *t, Value *address, Span span) {
  if (t->kind == Kind::Text) {
    auto *value = ir.CreateLoad(t->ir, address);
    auto *owner = ir.CreateExtractValue(value, 2);
    auto *retain = block("text.retain"), *end = block("text.done");
    ir.CreateCondBr(ir.CreateIsNotNull(owner), retain, end);
    ir.SetInsertPoint(retain);
    ir.CreateAtomicRMW(AtomicRMWInst::Add, owner, ConstantInt::get(p.sizeTy->ir, 1),
                       p.module->getDataLayout().getABITypeAlign(p.sizeTy->ir), AtomicOrdering::Monotonic);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    return value;
  }
  auto copy = [&](Type *field, Value *source) -> Value * {
    if (p.customCopy(field)) return ir.CreateCall(p.copyGlue(field, span), {source});
    return ir.CreateLoad(p.storage(field), source);
  };
  auto component = [&](Type *field, unsigned i) {
    return copy(field, ir.CreateStructGEP(t->ir, address, i));
  };
  Value *out = Constant::getNullValue(t->ir);
  if (t->kind == Kind::Class) {
    if (p.attribute(*t->def.decl, "copy") == "enable") {
      auto methods = p.methods(t, "copy", false);
      if (!methods.empty()) {
        auto &f = p.function(methods.front(), p.classTypes(t), t);
        p.lower(f);
        return ir.CreateCall(f.ir, {address});
      }
    }
    for (const auto &f : t->fields)
      out = ir.CreateInsertValue(out, component(f.type, f.index), f.index);
  } else if (t->kind == Kind::Closure) {
    for (const auto &c : t->captures) {
      auto *field = c.reference ? p.pointer(c.type, !c.writable) : c.type;
      out = ir.CreateInsertValue(out, component(field, c.index), c.index);
      out = ir.CreateInsertValue(out, ir.getTrue(), c.index + 1);
    }
  } else if (t->kind == Kind::Tuple) {
    for (unsigned i = 0; i < t->parts.size(); ++i)
      out = ir.CreateInsertValue(out, component(t->parts[i], i), i);
  } else if (t->kind == Kind::Array && t->count) {
    auto *target = slot(t, "copy.array");
    auto *start = ir.GetInsertBlock(), *loop = block("copy.array.loop"), *end = block("copy.array.end");
    ir.CreateBr(loop);
    ir.SetInsertPoint(loop);
    auto *i = ir.CreatePHI(p.sizeTy->ir, 2);
    i->addIncoming(ConstantInt::get(p.sizeTy->ir, 0), start);
    auto *value = copy(t->parts[0], ir.CreateGEP(t->ir, address, {ir.getInt32(0), i}));
    ir.CreateStore(value, ir.CreateGEP(t->ir, target, {ir.getInt32(0), i}));
    auto *next = ir.CreateAdd(i, ConstantInt::get(p.sizeTy->ir, 1));
    i->addIncoming(next, ir.GetInsertBlock());
    ir.CreateCondBr(ir.CreateICmpULT(next, ConstantInt::get(p.sizeTy->ir, t->count)), loop, end);
    ir.SetInsertPoint(end);
    return ir.CreateLoad(t->ir, target);
  } else if (t->kind == Kind::Optional || t->kind == Kind::Result) {
    auto *start = ir.GetInsertBlock(), *active = block("copy.present"), *end = block("copy.end");
    auto *tag = component(t->kind == Kind::Optional ? p.boolTy : p.errorTy, 0);
    out = ir.CreateInsertValue(out, tag, 0);
    ir.CreateCondBr(t->kind == Kind::Optional ? tag : ir.CreateICmpEQ(tag, ir.getInt32(0)), active, end);
    ir.SetInsertPoint(active);
    auto *ready = ir.CreateInsertValue(out, component(t->parts[0], 1), 1);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(t->ir, 2);
    phi->addIncoming(out, start);
    phi->addIncoming(ready, active);
    return phi;
  } else if (t->kind == Kind::Enum) {
    auto *start = ir.GetInsertBlock(), *end = block("copy.enum.end");
    auto *tag = component(p.integer(t->bits, t->sign), 0);
    out = ir.CreateInsertValue(out, tag, 0);
    auto *dispatch = ir.CreateSwitch(tag, end, t->variants.size());
    std::vector<std::pair<Value *, BasicBlock *>> values;
    for (const auto &v : t->variants) {
      if (v.fields.empty()) continue;
      auto *active = block("copy.variant");
      dispatch->addCase(ConstantInt::get(p.context, v.tag), active);
      ir.SetInsertPoint(active);
      auto *ready = out;
      for (unsigned i = 0; i < v.fields.size(); ++i)
        ready = ir.CreateInsertValue(ready, component(v.fields[i], v.offset + i), v.offset + i);
      values.emplace_back(ready, ir.GetInsertBlock());
      ir.CreateBr(end);
    }
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(t->ir, values.size() + 1);
    phi->addIncoming(out, start);
    for (auto [value, from] : values)
      phi->addIncoming(value, from);
    return phi;
  }
  return out;
}

Value *Body::read(ValueAt v, Span span, bool take) {
  if (v.type == p.voidTy) return voidValue().value;
  if (v.value) return v.value;
  requireInit(v, span);
  if (v.local >= 0 && v.address == locals[v.local].slot && locals[v.local].constant)
    return locals[v.local].constant;
  if (!v.address) fail(span, "expression has no value");
  if (take && v.borrowed) {
    if (!p.copyable(v.type)) fail(span, "cannot copy value of type '" + v.type->name + "'", "E230");
    if (p.customCopy(v.type)) return ir.CreateCall(p.copyGlue(v.type, span), {v.address});
  }
  return ir.CreateLoad(p.storage(v.type), v.address);
}

ValueAt Body::wrap(Type *t, Value *v, bool present) {
  Value *out = Constant::getNullValue(t->ir);
  if (t->kind == Kind::Optional)
    out = ir.CreateInsertValue(out, ir.getInt1(present), 0);
  else if (!present)
    out = ir.CreateInsertValue(out, v, 0);
  if (present) out = ir.CreateInsertValue(out, v, 1);
  return {t, out};
}

ValueAt Body::coerce(ValueAt v, Type *t, Span span) {
  if (!t || v.type == t || v.type == p.neverTy) return v;
  if (t->kind == Kind::Pointer && v.type->kind == Kind::Pointer && t->readOnly && t->parts == v.type->parts)
    return {t, read(v, span)};
  if (t->kind == Kind::Slice && v.type->kind == Kind::Slice && t->readOnly && t->parts == v.type->parts)
    return {t, read(v, span)};
  if (t == p.viewTy && v.type == p.textTy) return {t, textView(v, span)};
  if (t->kind == Kind::Result && v.type == p.errorTy) return wrap(t, read(v, span), false);
  if (t->kind == Kind::Result || t->kind == Kind::Optional) {
    auto inner = coerce(v, t->parts[0], span);
    return wrap(t, read(inner, span), true);
  }
  fail(span, "expected '" + t->name + "', found '" + v.type->name + "'", "E201");
}

Value *Body::textView(ValueAt value, Span span) {
  auto *raw = read(value, span, false);
  if (value.type == p.viewTy) return raw;
  if (!value.borrowed && p.heapAvailable()) {
    auto *constant = dyn_cast<Constant>(raw);
    auto *storage = constant ? constant->getAggregateElement(2u) : nullptr;
    if (!storage || !storage->isNullValue())
      fail(span, "bind the owning string before borrowing a text view", "E230");
  }
  auto *view = ir.CreateInsertValue(Constant::getNullValue(p.viewTy->ir), ir.CreateExtractValue(raw, 0), 0);
  return ir.CreateInsertValue(view, ir.CreateExtractValue(raw, 1), 1);
}

void Body::disposeTemporary(ValueAt value, Span span) {
  if (!value.borrowed && p.drops(value.type)) drop(value.type, asPointer(value, span).value);
}

ValueAt Body::payload(ValueAt source, unsigned i, Type *t) {
  if (source.address) {
    Value *address;
    if (source.type->kind == Kind::Array)
      address = ir.CreateInBoundsGEP(source.type->ir, source.address, {ir.getInt32(0), ir.getInt32(i)});
    else
      address = ir.CreateStructGEP(source.type->ir, source.address, i);
    return {t, nullptr, address, source.writable, source.local, source.borrowed};
  }
  return {t, ir.CreateExtractValue(source.value, i)};
}

ValueAt Body::asPointer(ValueAt v, Span span) {
  requireInit(v, span);
  if (v.address) return {p.pointer(v.type, !v.writable), v.address};
  auto *t = slot(v.type, "temporary");
  ir.CreateStore(read(v, span), t);
  return {p.pointer(v.type, true), t};
}

void Body::emitReturn(ValueAt value, Span span) {
  if (value.type == p.neverTy) return;
  if (result == p.neverTy) fail(span, "never function cannot return normally");
  value = coerce(value, result, span);
  auto *returned = read(value, span);
  clean(0);
  if (constructing && result->kind == Kind::Result) {
    auto *rollback = block("init.rollback"), *done = block("init.done");
    ir.CreateCondBr(ir.CreateICmpNE(ir.CreateExtractValue(returned, 0), ir.getInt32(0)), rollback, done);
    ir.SetInsertPoint(rollback);
    for (auto f = owner->fields.rbegin(); f != owner->fields.rend(); ++f)
      if (constructed.contains(f->index) && p.drops(f->type))
        drop(f->type, ir.CreateStructGEP(owner->ir, fn->getArg(0), f->index));
    ir.CreateBr(done);
    ir.SetInsertPoint(done);
  }
  if (result == p.voidTy)
    ir.CreateRetVoid();
  else
    ir.CreateRet(returned);
}

void Body::setDebug(Span span) {
  if (!debug || !span.source) return;
  const auto &s = span.source->bytes;
  auto end = s.begin() + static_cast<std::ptrdiff_t>(span.begin);
  unsigned line = static_cast<unsigned>(std::count(s.begin(), end, '\n')) + 1;
  auto pos = span.begin ? s.rfind('\n', span.begin - 1) : s.npos;
  auto start = pos == s.npos ? 0 : pos + 1;
  unsigned column = 1;
  for (auto i = start; i < span.begin; ++i)
    if ((static_cast<unsigned char>(s[i]) & 0xc0) != 0x80) ++column;
  ir.SetCurrentDebugLocation(DILocation::get(p.context, line, column, debug));
}

DIType *Program::debugType(Type *t) {
  if (auto known = debugTypes.find(t); known != debugTypes.end()) return known->second;
  DIType *out = nullptr;
  switch (t->kind) {
  case Kind::Void:
  case Kind::Never:
    break;
  case Kind::Bool:
    out = debug->createBasicType(t->name, 1, dwarf::DW_ATE_boolean);
    break;
  case Kind::Char:
    out = debug->createBasicType(t->name, 32, dwarf::DW_ATE_UTF);
    break;
  case Kind::Int:
    out = debug->createBasicType(t->name, t->bits, t->sign ? dwarf::DW_ATE_signed : dwarf::DW_ATE_unsigned);
    break;
  case Kind::Float:
    out = debug->createBasicType(t->name, t->bits, dwarf::DW_ATE_float);
    break;
  case Kind::Pointer: {
    auto *pointee = t->parts[0];
    auto *description =
        pointee->kind == Kind::Pointer ? debug->createUnspecifiedType(pointee->name) : debugType(pointee);
    out = debug->createPointerType(description, pointerBits());
    break;
  }
  default:
    out = debug->createUnspecifiedType(t->name);
    break;
  }
  debugTypes.emplace(t, out);
  return out;
}

void Body::debugValue(int id, bool alive) {
  auto &l = locals[id];
  if (!l.debug || !ir.getCurrentDebugLocation()) return;
  auto *location = ir.getCurrentDebugLocation().get();
  if (alive && l.debugParameter)
    p.debug->insertDbgValueIntrinsic(l.slot, l.debug, p.debug->createExpression({dwarf::DW_OP_deref}),
                                     location, ir.GetInsertBlock());
  else if (alive)
    p.debug->insertDeclare(l.slot, l.debug, p.debug->createExpression(), location, ir.GetInsertBlock());
  else
    p.debug->insertDbgValueIntrinsic(PoisonValue::get(p.storage(l.type)), l.debug,
                                     p.debug->createExpression(), location, ir.GetInsertBlock());
}

void Body::debugLocal(int id, Span span, unsigned argument) {
  if (!debug) return;
  auto previous = ir.getCurrentDebugLocation();
  setDebug(span);
  auto &local = locals[id];
  auto line = ir.getCurrentDebugLocation().getLine();
  auto *type = p.debugType(local.type);
  if (argument) {
    local.debug = p.debug->createParameterVariable(debug, local.name, argument, debug->getFile(), line, type);
    local.debugParameter = true;
    auto before = fn->getEntryBlock().getFirstInsertionPt();
    if (!local.refCapture)
      p.debug->insertDbgValueIntrinsic(fn->getArg(argument - 1), local.debug, p.debug->createExpression(),
                                       ir.getCurrentDebugLocation().get(), before);
  } else
    local.debug = p.debug->createAutoVariable(debug, local.name, debug->getFile(), line, type);
  if (initialized[id]) debugValue(id, true);
  ir.SetCurrentDebugLocation(previous);
}

void Body::build(FunctionEntry &entry) {
  const auto &d = *entry.def.decl;
  if (p.debug) {
    std::filesystem::path path(d.span.source->path);
    auto *src = p.debug->createFile(path.filename().string(), path.parent_path().string());
    unsigned line =
        std::count(d.span.source->bytes.begin(),
                   d.span.source->bytes.begin() + static_cast<std::ptrdiff_t>(d.span.begin), '\n') +
        1;
    SmallVector<Metadata *> types{p.debugType(result)};
    for (auto *param : entry.parameters)
      types.push_back(p.debugType(param));
    debug = p.debug->createFunction(src, d.name, fn->getName(), src, line,
                                    p.debug->createSubroutineType(p.debug->getOrCreateTypeArray(types)), line,
                                    DINode::FlagZero, DISubprogram::SPFlagDefinition);
    fn->setSubprogram(debug);
  }
  if (owner)
    for (const auto &[name, value] : owner->comptimeArguments)
      bindConstant(name, value.first, value.second, d.span);
  unsigned index = 0, runtime = 0;
  if (owner && !d.associated) {
    auto *arg = fn->getArg(runtime++);
    ++index;
    arg->setName("self");
    debugLocal(bind("self", entry.parameters[0], false, arg, d.span), d.span, runtime);
  }
  for (const auto &param : d.params) {
    auto *t = entry.parameters[index++];
    if (param.comptime) {
      bindConstant(param.name, t, entry.constants.at(param.name).second, param.span);
    } else {
      auto *arg = fn->getArg(runtime++);
      arg->setName(param.name);
      debugLocal(bind(param.name, t, true, arg, param.span), param.span, runtime);
    }
  }
  for (const auto &g : d.generics)
    if (g.comptime) {
      auto [type, value] = entry.constants.at(g.name);
      bindConstant(g.name, type, value, g.span);
    }
  if (owner && d.name == "init") {
    constructing = true;
    std::map<std::string, Ast> values;
    for (const auto &[key, n] : d.initializers)
      if (!values.emplace(key, n).second) fail(n->span, "duplicate field initializer");
    if (auto delegate = values.find("self"); delegate != values.end()) {
      if (values.size() != 1) fail(d.span, "constructor delegation cannot be mixed with field initializers");
      auto call = std::make_shared<Node>(*delegate->second);
      call->kind = NodeKind::Call;
      call->children.insert(call->children.begin(), nullptr);
      auto completed = invoke(call, p.methods(owner, "init", false), {},
                              ValueAt{owner, nullptr, fn->getArg(0), true}, owner, true);
      if (live() && completed.type->kind == Kind::Result) {
        if (result->kind != Kind::Result) fail(d.span, "delegation to a fallible constructor requires !void");
        auto *status = ir.CreateExtractValue(completed.value, 0);
        auto *ok = block("delegate.ok"), *bad = block("delegate.error");
        ir.CreateCondBr(ir.CreateICmpEQ(status, ir.getInt32(0)), ok, bad);
        ir.SetInsertPoint(bad);
        emitReturn({p.errorTy, status}, d.span);
        ir.SetInsertPoint(ok);
      }
      if (live())
        for (const auto &field : owner->fields)
          constructed.insert(field.index);
      values.erase(delegate);
    }
    for (const auto &field : owner->fields) {
      if (!live() || constructed.contains(field.index)) continue;
      Ast initializer = field.decl->body;
      if (auto i = values.find(field.decl->name); i != values.end()) {
        if (i->second->children.size() != 1) fail(i->second->span, "field initializer expects one value");
        initializer = i->second->children[0];
        values.erase(i);
      }
      if (!initializer) fail(d.span, "missing initializer for field '" + field.decl->name + "'");
      auto val = expression(initializer, field.type);
      if (val.type == p.neverTy) break;
      ir.CreateStore(read(val, initializer->span), ir.CreateStructGEP(owner->ir, fn->getArg(0), field.index));
      constructed.insert(field.index);
    }
    if (!values.empty()) fail(d.span, "unknown constructor field '" + values.begin()->first + "'");
  }
  if (live()) {
    auto v = sequence(d.body, result, false);
    if (live()) emitReturn(v, d.body->span);
  }
}

void Body::bindConstant(const std::string &name, Type *type, Constant *constant, Span span) {
  auto id = bind(name, type, false, constant, span);
  locals[id].constant = constant;
}

Constants Body::visibleConstants() const {
  Constants constants;
  std::set<std::string> seen;
  for (auto s = scopes.rbegin(); s != scopes.rend(); ++s)
    for (int i : *s) {
      const auto &local = locals[i];
      if (seen.insert(local.name).second && local.constant)
        constants.emplace(local.name, std::pair{local.type, local.constant});
    }
  return constants;
}

Type *Body::parseType(const Ast &n) {
  return p.type(n, file, generics, visibleConstants());
}

ValueAt Body::conditional(const Ast &n, Type *expected) {
  auto c = expression(n->children[0], p.boolTy);
  if (c.type == p.neverTy) return c;
  auto yes = n->children[1], no = n->children[2];
  if (!no && expected && expected != p.voidTy &&
      !(expected->kind == Kind::Result && expected->parts[0] == p.voidTy))
    fail(n->span, "if used as a value requires else");
  auto *t = no ? expected : p.voidTy;
  if (!t && no) {
    auto *a = branchHint(yes), *b = branchHint(no);
    if (!a || !b || a == b) t = a ? a : b;
  }
  auto *aBlock = block("if.then"), *bBlock = block("if.else"), *end = block("if.end");
  ir.CreateCondBr(read(c, n->span), aBlock, bBlock);
  const auto initial = initialized;
  std::vector<std::vector<bool>> states;
  std::vector<std::pair<Value *, BasicBlock *>> edges;
  auto arm = [&](BasicBlock *target, const Ast &body) {
    ir.SetInsertPoint(target);
    merge({initial}, initial.size());
    enter();
    auto v = body ? expression(body, t == p.voidTy ? nullptr : t) : voidValue();
    if (!live()) {
      leave();
      return;
    }
    if (t == p.voidTy) {
      if (v.type->kind == Kind::Result) fail(body->span, "unused error result", "E231");
      if (p.drops(v.type) && !v.borrowed) drop(v.type, asPointer(v, body->span).value);
      v = voidValue();
    }
    if (!t) t = v.type;
    v = coerce(v, t, body ? body->span : n->span);
    auto *value = read(v, body ? body->span : n->span);
    leave();
    states.push_back(initialized);
    edges.emplace_back(value, ir.GetInsertBlock());
    ir.CreateBr(end);
  };
  arm(aBlock, yes);
  arm(bBlock, no);
  ir.SetInsertPoint(end);
  if (edges.empty()) {
    ir.CreateUnreachable();
    return neverValue();
  }
  merge(states, initial.size());
  if (t == p.voidTy) return voidValue();
  auto *phi = ir.CreatePHI(p.storage(t), edges.size());
  for (auto [value, from] : edges)
    phi->addIncoming(value, from);
  return {t, phi};
}

void Body::statement(const Ast &n) {
  setDebug(n->span);
  if (n->kind == NodeKind::Binding) {
    auto *t = n->type ? parseType(n->type) : nullptr;
    bool writable = n->text != "const" && n->text != "comptime";
    ValueAt v{t};
    if (!n->children.empty()) {
      if (n->text == "comptime") {
        auto [ct, c] = p.evaluate(n->children[0], file, t, generics, visibleConstants());
        bindConstant(n->names[0], ct, c, n->span);
        return;
      } else
        v = expression(n->children[0], t);
      if (v.type == p.neverTy) return;
    }
    p.ensureSized(v.type, n->span);
    if (n->names.size() == 1) {
      if (n->names[0] == "_") {
        if (v.type->kind == Kind::Result) fail(n->span, "unused error result", "E231");
        if (p.drops(v.type) && !v.borrowed) drop(v.type, asPointer(v, n->span).value);
        return;
      }
      auto id =
          bind(n->names[0], v.type, writable, (v.value || v.address) ? read(v, n->span) : nullptr, n->span);
      debugLocal(id, n->span);
      return;
    }
    if (v.type->kind != Kind::Tuple || v.type->parts.size() != n->names.size())
      fail(n->span, "destructuring requires a tuple of matching arity");
    for (unsigned i = 0; i < n->names.size(); ++i) {
      auto element = payload(v, i, v.type->parts[i]);
      if (n->names[i] == "_") {
        if (!v.borrowed && p.drops(element.type)) drop(element.type, asPointer(element, n->span).value);
      } else
        bind(n->names[i], element.type, writable, read(element, n->span), n->span);
    }
    return;
  }
  if (n->kind == NodeKind::Assign) {
    auto l = place(n->children[0]);
    if (l.type == p.neverTy) return;
    if (!l.writable) fail(n->span, "assignment requires a writable location");
    bool whole = l.local >= 0 && !locals[l.local].refCapture && locals[l.local].slot == l.address;
    if (!whole) requireInit(l, n->span);
    const auto &op = n->text;
    ValueAt value;
    if (op == "=") {
      auto rhs = n->children[1];
      bool moved = rhs->kind == NodeKind::Move;
      auto source = moved ? rhs->children[0] : rhs;
      if (whole && source->kind == NodeKind::Name && findLocal(source->text) == l.local) {
        requireInit(l, n->span);
        if (!(moved ? p.movable(l.type) : p.copyable(l.type)))
          fail(n->span, "value lacks required ownership capability");
        return;
      }
      if (!p.movable(l.type) && !p.copyable(l.type) && (!whole || initialized[l.local]))
        fail(n->span, "cannot replace an immovable and noncopyable live value", "E230");
      value = expression(rhs, l.type);
    } else {
      auto *old = read(l, n->span, false);
      std::string bin = op == "++" ? "+" : op == "--" ? "-" : op.substr(0, op.size() - 1);
      bool shift = bin.starts_with("<<") || bin == ">>";
      ValueAt rhs;
      if (n->children.size() == 1) {
        if (l.type->kind != Kind::Int) fail(n->span, "update requires an integer");
        rhs = {l.type, ConstantInt::get(l.type->ir, 1)};
      } else
        rhs = expression(n->children[1], shift ? nullptr : l.type);
      value = operate(bin, {l.type, old}, rhs, n->span, true);
    }
    if (value.type == p.neverTy) return;
    auto *r = read(value, n->span);
    if (whole)
      dropLocal(l.local);
    else
      drop(l.type, l.address);
    if (l.type != p.voidTy) ir.CreateStore(r, l.address);
    if (whole) setAlive(l.local, true);
    return;
  }
  Type *expected = nullptr;
  if ((n->kind == NodeKind::If && !n->children[2]) || n->kind == NodeKind::Switch) expected = p.voidTy;
  auto v = expression(n, expected);
  if (v.type->kind == Kind::Result) fail(n->span, "unused error result; use ! or catch", "E231");
  if (live()) {
    if (p.drops(v.type) && !v.borrowed)
      drop(v.type, asPointer(v, n->span).value);
    else if (v.address)
      requireInit(v, n->span);
  }
}

ValueAt Body::sequence(const Ast &n, Type *expected, bool scoped) {
  if (scoped) enter();
  for (const auto &s : n->children) {
    if (!live()) break;
    statement(s);
  }
  ValueAt v = neverValue();
  if (live()) {
    v = n->tail ? expression(n->tail, expected) : coerce(voidValue(), expected, n->span);
    if (live() && scoped && v.address) v = {v.type, read(v, n->span)};
  }
  if (scoped) leave();
  return v;
}

ValueAt Body::loop(const Ast &n) {
  enter();
  const bool each = n->kind == NodeKind::ForIn;
  Ast condition, step, body;
  ValueAt start, last, array, iterator, nextItem;
  Ast nextCall;
  bool closed = false, enumerate = false;
  Type *element = nullptr;
  AllocaInst *indexSlot = nullptr;
  Value *arrayPtr = nullptr, *length = nullptr;
  if (each) {
    body = n->children[1];
    auto range = n->children[0];
    if (range->kind == NodeKind::Binary && (range->text == ".." || range->text == "..=")) {
      if (n->names.size() != 1) fail(n->span, "range loop binds one variable");
      closed = range->text == "..=";
      auto *t = hint(range->children[1]);
      start = expression(range->children[0],
                         mathInteger(range->children[0]) && t && t->kind == Kind::Int ? t : nullptr);
      if (start.type != p.neverTy) {
        start = {start.type, read(start, n->span)};
        last = expression(range->children[1], start.type);
        if (last.type != p.neverTy) last = {last.type, read(last, n->span)};
      }
      if (!live()) {
        leave();
        return neverValue();
      }
      if (start.type->kind != Kind::Int) fail(n->span, "range bounds must have the same integer type");
      element = start.type;
    } else {
      if (range->kind == NodeKind::Call && path(range->children[0]) == "enumerate" &&
          range->children.size() == 2 && findLocal("enumerate") < 0 &&
          p.resolve(file, "enumerate", n->span).empty()) {
        enumerate = true;
        range = range->children[1];
      }
      array = expression(range);
      if (!live()) {
        leave();
        return neverValue();
      }
      if (array.type->kind == Kind::Class) {
        auto call = std::make_shared<Node>();
        call->kind = NodeKind::Call;
        call->span = n->span;
        call->children = {nullptr};
        auto methods = p.methods(array.type, "iter", false);
        auto source = array;
        if (!methods.empty()) {
          if (!array.address && p.drops(array.type)) {
            int backing = bind(" <iterable>", array.type, true, read(array, n->span), n->span);
            array = {array.type, nullptr, locals[backing].slot, true, backing, true};
          }
          source = invoke(call, methods, {}, array, array.type);
        }
        if (source.type->kind != Kind::Class || p.methods(source.type, "next", false).empty())
          fail(n->span, "iterator requires a next() method returning ?Item");
        int i = bind(" <iterator>", source.type, true, read(source, n->span), n->span);
        iterator = {source.type, nullptr, locals[i].slot, true, i, true};
        nextCall = call;
        if (enumerate) {
          if (n->names.size() != 2) fail(n->span, "enumerate binds an index and an item");
          start = {p.sizeTy, ConstantInt::get(p.sizeTy->ir, 0)};
        }
      } else {
        if (array.type->kind != Kind::Array && array.type->kind != Kind::Slice)
          fail(n->span, "iteration requires a range, array, slice or iterator");
        if (n->names.size() != (enumerate ? 2u : 1u))
          fail(n->span, "iteration binding count does not match item");
        if (!array.borrowed && p.drops(array.type)) {
          int i = bind(" <iterable>", array.type, false, read(array, n->span), n->span);
          array = {array.type, nullptr, locals[i].slot, false, i, true};
        }
        element = array.type->parts[0];
        start = {p.sizeTy, ConstantInt::get(p.sizeTy->ir, 0)};
        if (array.type->kind == Kind::Array) {
          arrayPtr = asPointer(array, n->span).value;
          length = ConstantInt::get(p.sizeTy->ir, array.type->count);
        } else {
          auto *pair = read(array, n->span);
          arrayPtr = ir.CreateExtractValue(pair, 0);
          length = ir.CreateExtractValue(pair, 1);
        }
        last = {p.sizeTy, length};
      }
    }
    if (!iterator.type || enumerate) {
      indexSlot = slot(start.type, "iteration.index");
      ir.CreateStore(start.value, indexSlot);
    }
  } else {
    if (n->children[0]) statement(n->children[0]);
    if (!live()) {
      leave();
      return neverValue();
    }
    condition = n->children[1];
    step = n->children[2];
    body = n->children[3];
  }
  auto *header = block("loop.test"), *iteration = block("loop.body"), *next = block("loop.next"),
       *end = block("loop.end");
  ir.CreateBr(header);
  ir.SetInsertPoint(header);
  auto initial = initialized;
  std::vector<std::vector<bool>> exits;
  Value *counter = nullptr;
  if (iterator.type) {
    nextItem = invoke(nextCall, p.methods(iterator.type, "next", false), {}, iterator, iterator.type);
    if (nextItem.type->kind != Kind::Optional) fail(n->span, "iterator next() must return ?Item");
    element = nextItem.type->parts[0];
    ir.CreateCondBr(ir.CreateExtractValue(read(nextItem, n->span), 0), iteration, end);
    exits.push_back(initialized);
  } else if (each) {
    counter = ir.CreateLoad(start.type->ir, indexSlot);
    auto *test =
        binaryNumber(ir, closed ? "<=" : "<", counter, last.value, start.type->sign, start.type->sign);
    ir.CreateCondBr(test, iteration, end);
    exits.push_back(initialized);
  } else if (condition) {
    auto v = expression(condition, p.boolTy);
    if (live()) {
      ir.CreateCondBr(read(v, n->span), iteration, end);
      exits.push_back(initialized);
    } else {
      for (auto *unused : {iteration, next, end})
        unused->eraseFromParent();
      leave();
      return neverValue();
    }
  } else
    ir.CreateBr(iteration);
  ir.SetInsertPoint(iteration);
  loops.push_back({end, next, scopes.size(), {}, {}});
  enter();
  if (iterator.type) {
    auto item = payload(nextItem, 1, element);
    auto bindItem = [&](const std::string &name, ValueAt value) {
      if (name != "_")
        bind(name, value.type, true, read(value, n->span), n->span);
      else if (p.drops(value.type))
        drop(value.type, asPointer(value, n->span).value);
    };
    if (enumerate) {
      counter = ir.CreateLoad(start.type->ir, indexSlot);
      bindItem(n->names[0], {p.sizeTy, counter});
      bindItem(n->names[1], item);
    } else if (n->names.size() == 1)
      bindItem(n->names[0], item);
    else {
      if (element->kind != Kind::Tuple || n->names.size() != element->parts.size())
        fail(n->span, "iteration bindings require a matching tuple item");
      for (unsigned i = 0; i < n->names.size(); ++i)
        bindItem(n->names[i], payload(item, i, element->parts[i]));
    }
  } else if (each) {
    Value *item = counter;
    if (arrayPtr) {
      auto *addr = array.type->kind == Kind::Array
                       ? ir.CreateGEP(array.type->ir, arrayPtr, {ir.getInt32(0), counter})
                       : ir.CreateGEP(p.storage(element), arrayPtr, counter);
      item = read({element, nullptr, addr, false, -1, true}, n->span);
    }
    if (enumerate && n->names[0] != "_") bind(n->names[0], p.sizeTy, true, counter, n->span);
    if (n->names.back() != "_")
      bind(n->names.back(), element, true, item, n->span);
    else if (p.drops(element))
      drop(element, asPointer({element, item}, n->span).value);
  }
  auto v = sequence(body, nullptr, false);
  if (live()) {
    if (v.type->kind == Kind::Result) fail(body->span, "unused error result", "E231");
    if (p.drops(v.type) && !v.borrowed) drop(v.type, asPointer(v, body->span).value);
  }
  leave();
  auto &current = loops.back();
  std::vector<std::vector<bool>> backEdges;
  if (live()) {
    current.continues.push_back(initialized); // Continue paths carry state to the update.
    ir.CreateBr(next);
  }
  backEdges = std::move(current.continues);
  auto breaks = std::move(current.breaks);
  loops.pop_back();
  exits.insert(exits.end(), breaks.begin(), breaks.end());
  ir.SetInsertPoint(next);
  if (backEdges.empty())
    ir.CreateUnreachable();
  else {
    merge(backEdges, initial.size());
    if (each && (!iterator.type || enumerate)) {
      if (closed) {
        auto *increment = block("loop.increment");
        ir.CreateCondBr(ir.CreateICmpEQ(counter, last.value), end, increment);
        exits.push_back(initialized);
        ir.SetInsertPoint(increment);
      }
      auto *one = ConstantInt::get(start.type->ir, 1);
      auto *updated = enumerate ? binaryNumber(ir, "+", counter, one, false, false)
                                : ir.CreateAdd(counter, one);
      ir.CreateStore(updated, indexSlot);
    } else if (step)
      statement(step);
    if (live()) {
      for (std::size_t i = 0; i < initial.size(); ++i)
        if (initial[i] && !initialized[i])
          fail(n->span, "loop back edge may reach an uninitialized local '" + locals[i].name + "'", "E230");
      ir.CreateBr(header);
    }
  }
  ir.SetInsertPoint(end);
  if (exits.empty()) {
    ir.CreateUnreachable();
    leave();
    return neverValue();
  }
  merge(exits, initial.size());
  leave();
  return voidValue();
}

ValueAt Body::expression(const Ast &n, Type *expected) {
  setDebug(n->span);
  Type *literal = expected;
  while (literal && (literal->kind == Kind::Result || literal->kind == Kind::Optional))
    literal = literal->parts[0];
  if (auto number = mathInteger(n)) {
    auto *t = literal && literal->kind == Kind::Int ? literal : p.integer(32, true);
    if (!fits(*number, t->bits, t->sign))
      fail(n->span, "integer constant does not fit '" + t->name + "'", "E211");
    return coerce({t, ConstantInt::get(t->ir, number->sextOrTrunc(t->bits))}, expected, n->span);
  }
  ValueAt v;
  switch (n->kind) {
  case NodeKind::Name:
    v = name(n);
    break;
  case NodeKind::Number:
    v = numeric(n, literal);
    break;
  case NodeKind::Boolean:
    v = {p.boolTy, ir.getInt1(n->text == "true")};
    break;
  case NodeKind::Character: {
    std::size_t i = 0;
    v = {p.charTy, ir.getInt32(scalar(n->text, i, n->span))};
    break;
  }
  case NodeKind::Text: {
    auto *t = literal == p.viewTy ? p.viewTy : p.textTy;
    SmallVector<Constant *> fields{p.bytes(n->text), ConstantInt::get(p.sizeTy->ir, n->text.size())};
    if (t == p.textTy) fields.push_back(ConstantPointerNull::get(PointerType::getUnqual(p.context)));
    v = {t, ConstantStruct::get(cast<StructType>(t->ir), fields)};
    break;
  }
  case NodeKind::Null: {
    std::function<ValueAt(Type *)> missing = [&](Type *t) -> ValueAt {
      if (!t) fail(n->span, "null requires an expected optional type");
      if (t->kind == Kind::Optional) return {t, Constant::getNullValue(t->ir)};
      if (t->kind == Kind::Result) return wrap(t, missing(t->parts[0]).value, true);
      fail(n->span, "null requires an expected optional type");
    };
    return missing(expected);
  }
  case NodeKind::Block:
    return sequence(n, expected, true);
  case NodeKind::Unsafe: {
    ++unsafeDepth;
    v = sequence(n->children[0], expected, true);
    --unsafeDepth;
    return v;
  }
  case NodeKind::Move: {
    auto src = n->children[0];
    int i = src->kind == NodeKind::Name ? findLocal(src->text) : -1;
    if (i < 0) fail(n->span, "move requires a complete local or parameter", "E230");
    auto l = name(src);
    requireInit(l, n->span);
    if (patternBorrows.contains(i) || !l.writable || locals[i].refCapture || !p.movable(l.type))
      fail(n->span, "source cannot be moved", "E230");
    if (locals[i].captureFlag) closure->consuming = true;
    v = {l.type, read(l, n->span, false)};
    setAlive(i, false);
    break;
  }
  case NodeKind::Tuple:
  case NodeKind::Array: {
    bool array = n->kind == NodeKind::Array;
    Type *t = literal && literal->kind == Kind::Tuple && !array ? literal : nullptr;
    Type *element = nullptr;
    if (array) {
      element = parseType(n->type->children[1]);
      p.ensureSized(element, n->span);
      t = n->type->children[0] ? parseType(n->type) : p.aggregate(Kind::Array, {element}, n->children.size());
      if (t->count != n->children.size()) fail(n->span, "array initializer count does not match its length");
    }
    if (!array && t && t->parts.size() != n->children.size()) fail(n->span, "tuple arity mismatch");
    std::vector<Type *> types;
    std::vector<int> temporaries;
    enter();
    for (unsigned i = 0; i < n->children.size(); ++i) {
      auto item = expression(n->children[i], array ? element : t ? t->parts[i] : nullptr);
      if (!live()) {
        leave();
        return neverValue();
      }
      types.push_back(item.type);
      temporaries.push_back(bind("", item.type, true, read(item, n->span), n->span));
      locals[temporaries.back()].name = " <element " + std::to_string(i) + ">";
    }
    if (!t) t = p.aggregate(Kind::Tuple, types);
    Value *out = Constant::getNullValue(t->ir);
    for (unsigned i = 0; i < temporaries.size(); ++i) {
      auto j = temporaries[i];
      out = ir.CreateInsertValue(out, ir.CreateLoad(p.storage(types[i]), locals[j].slot), i);
      setAlive(j, false);
    }
    leave();
    v = {t, out};
    break;
  }
  case NodeKind::Fields: {
    auto target = n->children[0];
    std::vector<Ast> args;
    if (target->kind == NodeKind::Generic) {
      args.assign(target->children.begin() + 1, target->children.end());
      target = target->children[0];
    }
    auto *t = p.named(path(target), file, target->span, generics, args, visibleConstants());
    v = construct(n, t);
    break;
  }
  case NodeKind::New:
    v = allocate(n);
    break;
  case NodeKind::Member:
    v = member(n);
    break;
  case NodeKind::Index:
  case NodeKind::Slice:
    v = index(n);
    break;
  case NodeKind::Unary:
    v = unary(n, literal);
    break;
  case NodeKind::Binary:
    v = binary(n, literal);
    break;
  case NodeKind::Compare:
    v = chain(n);
    break;
  case NodeKind::If:
    v = conditional(n, expected);
    break;
  case NodeKind::Switch:
    v = match(n, expected);
    break;
  case NodeKind::Lambda:
    v = lambda(n, literal);
    break;
  case NodeKind::Call:
    v = call(n, expected);
    break;
  case NodeKind::For:
  case NodeKind::ForIn:
    v = loop(n);
    break;
  case NodeKind::Return: {
    if (!result) fail(n->span, "return is unavailable in this constant initializer");
    auto out = n->children.empty() ? voidValue() : expression(n->children[0], result);
    if (live()) emitReturn(out, n->span);
    return neverValue();
  }
  case NodeKind::Break:
  case NodeKind::Continue: {
    if (loops.empty()) fail(n->span, "loop control used outside a loop");
    auto &loop = loops.back();
    clean(loop.depth);
    if (n->kind == NodeKind::Break) {
      loop.breaks.push_back(initialized);
      ir.CreateBr(loop.leave);
    } else {
      loop.continues.push_back(initialized);
      ir.CreateBr(loop.next);
    }
    return neverValue();
  }
  default:
    fail(n->span, "unsupported expression", "E290");
  }
  return coerce(v, expected, n->span);
}

} // namespace vqr::detail
