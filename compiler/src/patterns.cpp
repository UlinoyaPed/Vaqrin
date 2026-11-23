#include "compiler_internal.hpp"

namespace vqr::detail {

struct Constructor {
  std::string tag;
  std::vector<Type *> fields;
};

bool wildcard(const Ast &p) {
  return !p || p->kind == NodeKind::PatternAny || p->kind == NodeKind::PatternName ||
         p->kind == NodeKind::PatternRef || p->kind == NodeKind::PatternMutRef ||
         p->kind == NodeKind::PatternIndirect;
}

std::string patternKey(const Ast &p) {
  if (wildcard(p)) return {};
  if (p->kind == NodeKind::PatternCase) {
    auto dot = p->text.rfind('.');
    return dot == p->text.npos ? p->text : p->text.substr(dot + 1);
  }
  if (p->kind == NodeKind::PatternTuple) return "tuple";
  auto v = p->children[0];
  if (auto number = mathInteger(v)) {
    SmallString<32> str;
    number->toString(str, 10, true);
    return "int:" + str.str().str();
  }
  if (v->kind == NodeKind::Number) {
    auto number = parseNumber(v->text);
    if (!number.floating) {
      SmallString<32> str;
      number.integer.toString(str, 10, true);
      return "int:" + str.str().str();
    }
  }
  return std::to_string(static_cast<int>(v->kind)) + ":" + v->text;
}

void Body::dropRemainder(Type *t, Value *address, const Ast &pattern) {
  if (!p.drops(t) || pattern->kind == NodeKind::PatternName) return;
  auto component = [&](Type *field, unsigned index, const Ast &subpattern) {
    if (p.drops(field)) dropRemainder(field, ir.CreateStructGEP(t->ir, address, index), subpattern);
  };
  if (pattern->kind == NodeKind::PatternTuple) {
    for (unsigned i = t->parts.size(); i-- > 0;)
      component(t->parts[i], i, pattern->children[i]);
    return;
  }
  if (pattern->kind == NodeKind::PatternCase) {
    auto tag = patternKey(pattern);
    if (t->kind == Kind::Optional) {
      if (tag == "some") component(t->parts[0], 1, pattern->children[0]);
      return;
    }
    if (t->kind == Kind::Enum) {
      auto v = std::ranges::find(t->variants, tag, &Variant::name);
      if (v == t->variants.end()) return;
      for (unsigned i = v->fields.size(); i-- > 0;)
        component(v->fields[i], v->offset + i, pattern->children[i]);
      return;
    }
  }
  drop(t, address);
}

std::vector<Constructor> constructors(Type *t) {
  if (t->kind == Kind::Optional) return {{"null", {}}, {"some", t->parts}};
  if (t->kind == Kind::Tuple) return {{"tuple", t->parts}};
  if (t->kind == Kind::Enum) {
    std::vector<Constructor> out;
    for (const auto &v : t->variants)
      out.push_back({v.name, v.fields});
    return out;
  }
  if (t->kind == Kind::Bool) {
    auto key = std::to_string(static_cast<int>(NodeKind::Boolean)) + ":";
    return {{key + "true", {}}, {key + "false", {}}};
  }
  return {};
}

using PatternRow = std::vector<Ast>;
using PatternMatrix = std::vector<PatternRow>;

bool covered(const PatternMatrix &rows, const PatternRow &query, const std::vector<Type *> &types,
             unsigned &budget) {
  if (!budget--) return false;
  if (query.empty()) return !rows.empty();
  auto domain = constructors(types[0]);
  auto key = [&](const Ast &pattern) {
    return types[0]->kind == Kind::Error && pattern && pattern->kind == NodeKind::PatternCase
               ? pattern->text
               : patternKey(pattern);
  };
  bool any = wildcard(query[0]);
  if (domain.empty()) {
    if (any)
      domain.push_back({"", {}});
    else
      domain.push_back({key(query[0]), {}});
  }
  bool relevant = false;
  for (const auto &c : domain) {
    if (!any && key(query[0]) != c.tag) continue;
    relevant = true;
    auto specialize = [&](const PatternRow &row) {
      PatternRow out;
      if (wildcard(row[0]))
        out.resize(c.fields.size());
      else if (row[0]->kind != NodeKind::PatternValue)
        out = row[0]->children;
      if (out.size() != c.fields.size()) out.resize(c.fields.size());
      out.insert(out.end(), row.begin() + 1, row.end());
      return out;
    };
    PatternMatrix selected;
    for (const auto &row : rows)
      if (wildcard(row[0]) || (!c.tag.empty() && key(row[0]) == c.tag)) selected.push_back(specialize(row));
    auto t = c.fields;
    t.insert(t.end(), types.begin() + 1, types.end());
    if (!covered(selected, specialize(query), t, budget)) return false;
  }
  return relevant;
}

void Body::assertCondition(Value *test, BasicBlock *failure) {
  auto *pass = block("pattern.pass");
  ir.CreateCondBr(test, pass, failure);
  ir.SetInsertPoint(pass);
}

void Body::checkPattern(const Ast &pat, ValueAt subject, BasicBlock *failure,
                        std::vector<std::pair<Ast, ValueAt>> &bindings) {
  if (pat->kind == NodeKind::PatternAny) return;
  const bool borrowsSlot = pat->kind == NodeKind::PatternRef || pat->kind == NodeKind::PatternMutRef;
  if (pat->kind == NodeKind::PatternName || borrowsSlot || pat->kind == NodeKind::PatternIndirect) {
    if (std::ranges::any_of(bindings, [&](auto &b) { return b.first->text == pat->text; }))
      fail(pat->span, "duplicate pattern binding");
    if (pat->kind != NodeKind::PatternName) {
      if (pat->kind == NodeKind::PatternIndirect) {
        subject = dereference(subject, pat->span);
      } else if (p.hasReference(subject.type)) {
        fail(pat->span, "use '&' to borrow through a reference payload", "E230");
      }
      if (pat->kind == NodeKind::PatternMutRef && !subject.writable)
        fail(pat->span, "ref mut pattern requires writable storage", "E230");
      auto *reference = p.reference(subject.type, !subject.writable || pat->kind == NodeKind::PatternRef);
      auto origin = subject.origin;
      if (borrowsSlot && subject.local >= 0 &&
          (origin.root < 0 || (origin.raw && !locals[subject.local].reference))) {
        if (!reference->readOnly && origin.raw) locals[subject.local].origin = {};
        origin = {subject.local, -1};
      }
      origin.raw = false;
      subject.origin = origin;
      checkAccess(subject, !reference->readOnly, pat->span);
      subject = {reference, subject.address, nullptr, false, -1, false, subject.temporaryBorrow, origin};
    }
    bindings.emplace_back(pat, subject);
    return;
  }
  auto *t = subject.type;
  if (pat->kind == NodeKind::PatternValue) {
    auto left = ValueAt{t, read(subject, pat->span, false)};
    auto [type, constant] = p.evaluate(pat->children[0], file, t, generics, visibleConstants());
    assertCondition(operate("==", left, {type, constant}, pat->span).value, failure);
    return;
  }
  auto tag = patternKey(pat);
  if (pat->kind == NodeKind::PatternTuple) {
    if (t->kind != Kind::Tuple || pat->children.size() != t->parts.size())
      fail(pat->span, "tuple pattern has the wrong type or arity");
    for (unsigned i = 0; i < t->parts.size(); ++i)
      checkPattern(pat->children[i], payload(subject, i, t->parts[i]), failure, bindings);
    return;
  }
  if (t->kind == Kind::Optional && (tag == "some" || tag == "null")) {
    if (pat->children.size() != (tag == "some" ? 1u : 0u)) fail(pat->span, "optional pattern arity mismatch");
    auto *present = read(payload(subject, 0, p.boolTy), pat->span, false);
    assertCondition(tag == "some" ? present : ir.CreateNot(present), failure);
    if (tag == "some") checkPattern(pat->children[0], payload(subject, 1, t->parts[0]), failure, bindings);
    return;
  }
  auto dot = pat->text.rfind('.');
  if (dot != std::string::npos) {
    auto ds = p.resolve(file, pat->text.substr(0, dot), pat->span);
    if (ds.size() == 1 && ds[0].decl->kind == DeclKind::Error && t == p.errorTy && pat->children.empty()) {
      assertCondition(ir.CreateICmpEQ(read(subject, pat->span, false), ir.getInt32(p.errorCode(ds[0], tag))),
                      failure);
      return;
    }
    if (t->kind == Kind::Enum && ds.size() == 1 && ds[0].decl == t->def.decl) {
      auto variant = std::ranges::find(t->variants, tag, &Variant::name);
      if (variant == t->variants.end() || variant->fields.size() != pat->children.size())
        fail(pat->span, "enum pattern member or payload arity mismatch");
      auto actual = payload(subject, 0, p.integer(t->bits, t->sign));
      assertCondition(
          ir.CreateICmpEQ(read(actual, pat->span, false), ConstantInt::get(p.context, variant->tag)),
          failure);
      for (unsigned i = 0; i < variant->fields.size(); ++i)
        checkPattern(pat->children[i], payload(subject, variant->offset + i, variant->fields[i]), failure,
                     bindings);
      return;
    }
  }
  fail(pat->span, "pattern does not match subject type '" + t->name + "'");
}

ValueAt Body::match(const Ast &n, Type *expected) {
  auto subject = expression(n->children[0]);
  if (!live()) return neverValue();
  p.ensureSized(subject.type, n->span);
  requireInit(subject, n->span);
  Type *ownerType = nullptr;
  AllocaInst *selection = nullptr;
  if (!subject.borrowed) {
    auto *address = slot(subject.type, "matched.value");
    ir.CreateStore(read(subject, n->span), address);
    subject.address = address;
    subject.value = nullptr;
    subject.writable = true;
  }
  if (!subject.borrowed && p.drops(subject.type)) {
    auto *layout = StructType::get(p.context, {PointerType::getUnqual(p.context), ir.getInt32Ty()});
    ownerType = p.add(Kind::PatternRemainder, layout,
                      "<pattern remainder " + std::to_string(p.matchCount++) + ">", {subject.type});
    for (std::size_t i = 1; i < n->children.size(); ++i)
      for (const auto &pat : n->children[i]->children)
        ownerType->patterns.push_back(pat);
    selection = slot(p.integer(32, false), "pattern.selected");
  }
  auto initial = initialized;
  auto *end = block("switch.end");
  auto *resultType = expected;
  if (!resultType)
    for (std::size_t i = 1; i < n->children.size(); ++i)
      if (auto *hint = branchHint(n->children[i]->tail)) {
        resultType = hint;
        break;
      }
  PatternMatrix used;
  std::vector<std::vector<bool>> states;
  std::vector<std::pair<Value *, BasicBlock *>> edges;
  bool temporaryBorrow = false;
  Borrow origin{};
  bool borrowPayload = false;
  for (std::size_t i = 1; i < n->children.size(); ++i) {
    auto arm = n->children[i];
    auto *entry = block("switch.arm");
    using Binding = std::tuple<std::string, Type *, NodeKind, Borrow>;
    std::vector<Binding> signature;
    std::vector<AllocaInst *> slots;
    std::vector<Span> declarations;
    bool first = true;
    for (const auto &pattern : arm->children) {
      unsigned budget = 100000;
      if (covered(used, {pattern}, {subject.type}, budget))
        fail(pattern->span, "pattern is already covered", "E250");
      used.push_back({pattern});
      auto *next = block("switch.test");
      std::vector<std::pair<Ast, ValueAt>> bindings;
      checkPattern(pattern, subject, next, bindings);
      std::ranges::sort(bindings, {}, [](const auto &b) { return b.first->text; });
      std::vector<Binding> current;
      for (auto &[pat, v] : bindings) {
        current.emplace_back(pat->text, v.type, pat->kind, v.origin);
        hintBinding(pat->span, v.type, EditorHints::BindingKind::Pattern);
      }
      if (first) {
        signature = current;
        for (auto &[pat, v] : bindings)
          slots.push_back(slot(v.type, pat->text + ".pattern"));
        first = false;
      } else if (current != signature)
        fail(pattern->span, "alternative patterns must bind the same names, types and permissions");
      if (declarations.empty())
        for (const auto &[pattern, _] : bindings) declarations.push_back(pattern->span);
      for (unsigned i = 0; i < bindings.size(); ++i) {
        indexSymbol(bindings[i].first->span, declarations[i], true);
        ir.CreateStore(read(bindings[i].second, bindings[i].first->span), slots[i]);
      }
      if (selection) ir.CreateStore(ir.getInt32(used.size() - 1), selection);
      ir.CreateBr(entry);
      ir.SetInsertPoint(next);
    }
    auto *next = ir.GetInsertBlock();
    ir.SetInsertPoint(entry);
    merge({initial}, initial.size());
    enter();
    int armOwner = -1;
    if (ownerType) {
      auto *owner = ir.CreateInsertValue(Constant::getNullValue(ownerType->ir), subject.address, 0);
      owner = ir.CreateInsertValue(owner, ir.CreateLoad(ir.getInt32Ty(), selection), 1);
      armOwner = bind(" <match remainder>", ownerType, true, owner, arm->span);
    }
    for (unsigned j = 0; j < signature.size(); ++j) {
      auto &[key, t, mode, borrowed] = signature[j];
      auto id = bind(key, t, true, ir.CreateLoad(p.storage(t), slots[j]), arm->span);
      locals[id].declaration = declarations[j];
      if (!subject.borrowed && (mode == NodeKind::PatternRef || mode == NodeKind::PatternMutRef)) {
        if (armOwner < 0)
          armOwner = bind(" <match storage>", p.voidTy, false, voidValue().value, arm->span);
        borrowed.root = armOwner;
        borrowed.via = -1;
        borrowed.alternatives.reset();
      }
      if (t->kind == Kind::Reference || p.hasReference(t))
        recordBorrow(id, {t, nullptr, nullptr, false, -1, false, false, borrowed}, arm->span);
    }
    auto borrow = patternBorrows.end();
    if (subject.local >= 0 && std::ranges::any_of(signature, [](const auto &item) {
          return std::get<2>(item) != NodeKind::PatternName;
        }))
      borrow = patternBorrows.insert(subject.local);
    auto v = expression(arm->tail, resultType == p.voidTy ? nullptr : resultType);
    if (borrow != patternBorrows.end()) patternBorrows.erase(borrow);
    if (live()) {
      if (!resultType) resultType = v.type;
      if (resultType == p.voidTy) {
        if (v.type->kind == Kind::Result) fail(arm->span, "unused error result", "E231");
        if (p.drops(v.type) && !v.borrowed) drop(v.type, asPointer(v, arm->span).value);
        v = voidValue();
      }
      v = coerce(v, resultType, arm->span);
      temporaryBorrow |= v.temporaryBorrow;
      mergeValueBorrow(origin, borrowPayload, v, arm->span);
      auto *value = read(v, arm->span);
      leave();
      states.push_back(initialized);
      edges.emplace_back(value, ir.GetInsertBlock());
      ir.CreateBr(end);
    } else
      leave();
    ir.SetInsertPoint(next);
    merge({initial}, initial.size());
  }
  unsigned budget = 100000;
  bool exhaustive = covered(used, {nullptr}, {subject.type}, budget);
  if (ownerType && p.dropFunctions.contains(ownerType))
    p.buildDropGlue(ownerType, p.dropFunctions.at(ownerType));
  if (exhaustive)
    ir.CreateUnreachable();
  else {
    if (expected != p.voidTy) fail(n->span, "switch used as a value must be exhaustive", "E250");
    if (ownerType) drop(subject.type, subject.address);
    states.push_back(initialized);
    edges.emplace_back(voidValue().value, ir.GetInsertBlock());
    ir.CreateBr(end);
  }
  ir.SetInsertPoint(end);
  if (edges.empty()) {
    ir.CreateUnreachable();
    return neverValue();
  }
  merge(states, initial.size());
  if (!resultType || resultType == p.voidTy) return voidValue();
  auto *phi = ir.CreatePHI(resultType->ir, edges.size());
  for (auto [v, b] : edges)
    phi->addIncoming(v, b);
  return {resultType, phi, nullptr, false, -1, false, temporaryBorrow, origin,
          p.hasReference(resultType) && !borrowPayload};
}

} // namespace vqr::detail
