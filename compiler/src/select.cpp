#include "compiler_internal.hpp"

namespace vqr::detail {
namespace {

struct SelectArmInfo {
  Ast syntax;
  int ticket = -1;
  int moved = -1;
  Value *deadline = nullptr;
  BasicBlock *probe = nullptr;
  BasicBlock *body = nullptr;
};

} // namespace

ValueAt Body::selection(const Ast &n, Type *expected) {
  if (!p.hostedLinux64()) fail(n->span, "select requires hosted GNU/Linux x86-64 or AArch64", "E290");
  if (evaluating) fail(n->span, "select is unavailable during compile-time evaluation", "E220");
  p.inputs.loadStandard("channel", n->span);
  p.indexFiles();

  auto declaration = [&](StringRef name) {
    const auto &definitions = p.definitions.at(("channel." + name).str());
    return definitions.front();
  };
  auto call = [&](FunctionEntry &function, ArrayRef<Value *> args) -> Value * {
    p.lower(function);
    return ir.CreateCall(function.ir, args);
  };
  auto helper = [&](StringRef name, ArrayRef<Value *> args = {}) -> Value * {
    return call(p.function(declaration(name)), args);
  };
  auto method = [&](Type *owner, StringRef name) -> FunctionEntry & {
    auto methods = p.methods(owner, name.str(), false, true);
    if (methods.size() != 1) fail(n->span, "invalid standard channel selection method", "E900");
    return p.function(methods.front(), p.classTypes(owner), owner);
  };
  auto *sizeTy = cast<IntegerType>(p.sizeTy->ir);
  auto size = [&](std::uint64_t value) { return ConstantInt::get(sizeTy, value); };
  auto *choiceType = p.nominal(declaration("Choice"), {});
  auto *choiceInit = slot(choiceType, "select.choice.init");
  enter();
  call(method(choiceType, "init"), {choiceInit});
  int state = bind(" <select choice>", choiceType, true, ir.CreateLoad(choiceType->ir, choiceInit), n->span);
  auto *choice = locals[state].slot;
  auto &timer = p.function(declaration("choiceTimer"));
  p.lower(timer);
  auto *deadlineTy = timer.parameters[2];
  Value *earliest = Constant::getNullValue(deadlineTy->ir);
  Value *clockStart = nullptr;
  if (std::ranges::any_of(n->children, [](const auto &arm) { return arm->text == "after"; }))
    clockStart = helper("choiceClock", {choice});

  std::vector<SelectArmInfo> arms;
  std::optional<unsigned> fallback;
  for (unsigned i = 0; i < n->children.size(); ++i) {
    auto arm = n->children[i];
    auto &info = arms.emplace_back(SelectArmInfo{arm});
    if (arm->text == "else") {
      fallback = i;
      continue;
    }
    if (arm->text == "after") {
      auto &function = p.function(declaration("choiceDeadline"));
      auto delay = expression(arm->children[0], function.parameters[2]);
      if (!live()) { leave(); return neverValue(); }
      info.deadline = call(function, {choice, clockStart, read(delay, arm->span)});
      earliest = helper("choiceEarlier", {earliest, info.deadline});
      continue;
    }
    auto endpoint = expression(arm->children[0]);
    if (!live()) { leave(); return neverValue(); }
    if (endpoint.type->kind == Kind::Pointer || endpoint.type->kind == Kind::Box)
      endpoint = dereference(endpoint, arm->span);
    auto *type = endpoint.type;
    const bool send = arm->text == "send";
    auto *required = declaration(send ? "Sender" : "Receiver").decl;
    if (type->kind != Kind::Class || type->def.decl != required)
      fail(arm->span, send ? "select send requires a Channel Sender" : "select recv requires a Channel Receiver");
    if (!send && endpoint.address && !endpoint.writable)
      fail(arm->span, "select recv requires a mutable receiver", "E230");
    if (!endpoint.address) {
      auto n = bind(" <select endpoint " + std::to_string(i) + ">", type, true,
                    read(endpoint, arm->span), arm->span);
      endpoint = {type, nullptr, locals[n].slot, true, n, true};
    }
    checkAccess(endpoint, !send, arm->span);
    auto *address = asPointer(endpoint, arm->span).value;
    SmallVector<Value *> parameters{address, choice, size(i)};
    if (send) {
      auto *message = type->parts[0];
      auto input = arm->children[1];
      Value *source;
      if (input->kind == NodeKind::Move) {
        auto operand = input->children[0];
        int id = operand->kind == NodeKind::Name ? findLocal(operand->text) : -1;
        if (id < 0) fail(input->span, "select send move requires a complete local or parameter", "E230");
        auto local = name(operand);
        requireInit(local, input->span);
        checkAccess(local, true, input->span);
        if (local.type != message || !local.writable || locals[id].refCapture ||
            locals[id].reference || patternBorrows.contains(id) || !p.movable(message))
          fail(input->span, "select send source cannot be moved", "E230");
        info.moved = id;
        source = local.address;
      } else {
        auto literal = input;
        if (literal->kind == NodeKind::Unary && literal->text == "-") literal = literal->children[0];
        bool scalar = message->kind == Kind::Int || message->kind == Kind::Float ||
                      message->kind == Kind::Bool || message->kind == Kind::Char;
        bool allowed = literal->kind == NodeKind::Number || literal->kind == NodeKind::Boolean ||
                       literal->kind == NodeKind::Character ||
                       (input == literal && input->kind == NodeKind::Name && findLocal(input->text) >= 0);
        if (!scalar || !allowed)
          fail(input->span, "select send needs a scalar literal, a scalar local, or move(local)", "E230");
        auto value = expression(input, message);
        if (!live()) { leave(); return neverValue(); }
        source = slot(message, "select.scalar");
        ir.CreateStore(read(value, input->span), source);
      }
      parameters.push_back(source);
    }
    auto &make = method(type, "selectTicket");
    auto *ticket = call(make, parameters);
    info.ticket = bind(" <select ticket " + std::to_string(i) + ">", make.result, true, ticket, arm->span);
  }

  for (const auto &arm : arms) {
    if (arm.moved < 0) continue;
    auto operand = arm.syntax->children[1]->children[0];
    auto local = name(operand);
    requireInit(local, operand->span);
    checkAccess(local, true, operand->span);
  }

  auto ticketCall = [&](const SelectArmInfo &arm, StringRef name) {
    const auto &local = locals[arm.ticket];
    auto &entry = method(local.type, name);
    return ValueAt{entry.result, call(entry, {local.slot})};
  };
  auto &observe = method(choiceType, "observeCancellation");
  auto &decided = method(choiceType, "decided");
  auto &choiceFallback = method(choiceType, "choose");
  p.lower(choiceFallback);
  auto &decision = method(choiceType, "decision");
  call(observe, {choice});
  for (const auto &arm : arms)
    if (arm.ticket >= 0) ticketCall(arm, "link");

  const auto count = arms.size();
  auto *start = helper("choiceStart", {size(count)});
  auto *scan = block("select.scan"), *next = block("select.next"), *poll = block("select.poll");
  auto *again = block("select.again"), *idle = block("select.idle"), *chosen = block("select.chosen");
  auto *finished = block("select.finished"), *failed = block("select.failed");
  auto *unreachableArm = block("select.invalid");
  auto *scanEnd = block("select.round");
  ir.CreateBr(scan);

  ir.SetInsertPoint(scan);
  call(observe, {choice});
  auto *version = helper("choiceSnapshot");
  ir.CreateCondBr(call(decided, {choice}), chosen, next);

  ir.SetInsertPoint(next);
  auto *iter = ir.CreatePHI(sizeTy, 2);
  iter->addIncoming(size(0), scan);
  auto *candidate = ir.CreateURem(ir.CreateAdd(start, iter), size(count));
  auto *dispatch = ir.CreateSwitch(candidate, unreachableArm, count);
  for (unsigned i = 0; i < count; ++i) {
    auto &arm = arms[i];
    arm.probe = block("select.probe");
    arm.body = block("select.arm");
    dispatch->addCase(size(i), arm.probe);
    ir.SetInsertPoint(arm.probe);
    if (arm.ticket >= 0) ticketCall(arm, "probe");
    else if (arm.deadline) call(timer, {choice, size(i), arm.deadline});
    ir.CreateBr(poll);
  }
  ir.SetInsertPoint(poll);
  ir.CreateCondBr(call(decided, {choice}), chosen, again);

  ir.SetInsertPoint(again);
  auto *following = ir.CreateAdd(iter, size(1));
  iter->addIncoming(following, again);
  ir.CreateCondBr(ir.CreateICmpULT(following, size(count)), next, scanEnd);
  ir.SetInsertPoint(scanEnd);
  call(observe, {choice});
  ir.CreateCondBr(call(decided, {choice}), chosen, idle);
  ir.SetInsertPoint(idle);
  if (fallback) {
    call(choiceFallback, {choice, size(*fallback)});
    ir.CreateBr(chosen);
  } else {
    helper("choiceWait", {version, earliest});
    ir.CreateBr(scan);
  }

  ir.SetInsertPoint(chosen);
  for (const auto &arm : arms)
    if (arm.ticket >= 0) ticketCall(arm, "unlink");
  auto *outcome = call(decision, {choice});
  auto *error = ir.CreateExtractValue(outcome, 0);
  auto *index = ir.CreateExtractValue(outcome, 1);
  auto *success = block("select.success");
  ir.CreateCondBr(ir.CreateIsNotNull(error), failed, success);
  ir.SetInsertPoint(success);
  auto *which = ir.CreateSwitch(index, unreachableArm, count);
  ir.SetInsertPoint(unreachableArm);
  ir.CreateUnreachable();

  auto initial = initialized;
  std::vector<std::vector<bool>> states{initial};
  std::vector<std::pair<Value *, BasicBlock *>> edges;
  Type *inner = expected && expected->kind == Kind::Result ? expected->parts[0] : nullptr;
  for (unsigned i = 0; i < count; ++i) {
    auto &arm = arms[i];
    which->addCase(size(i), arm.body);
    ir.SetInsertPoint(arm.body);
    merge({initial}, initial.size());
    enter();
    if (arm.moved >= 0) {
      auto local = name(arm.syntax->children[1]->children[0]);
      if (p.drops(local.type)) invalidateOwnedAddresses(local);
      setAlive(arm.moved, false);
    }
    if (arm.syntax->text == "recv" && !arm.syntax->names.empty() && arm.syntax->names.front() != "_") {
      auto payload = ticketCall(arm, "take");
      auto id = bind(arm.syntax->names.front(), payload.type, true, payload.value, arm.syntax->span);
      indexLocal(id, arm.syntax->bindingSpans.front());
      hintBinding(arm.syntax->bindingSpans.front(), payload.type, EditorHints::BindingKind::Pattern);
    }
    auto value = expression(arm.syntax->tail, inner);
    if (live()) {
      if (!inner) inner = value.type;
      if (p.hasReference(inner)) fail(arm.syntax->span, "reference-valued select requires explicit returns", "E230");
      auto *raw = read(coerce(value, inner, arm.syntax->span), arm.syntax->span);
      auto *type = p.aggregate(Kind::Result, {inner});
      auto packed = wrap(type, raw, true);
      leave();
      states.push_back(initialized);
      edges.emplace_back(packed.value, ir.GetInsertBlock());
      ir.CreateBr(finished);
    } else {
      leave();
    }
  }
  if (!inner) inner = p.voidTy;
  auto *type = p.aggregate(Kind::Result, {inner});
  ir.SetInsertPoint(failed);
  edges.emplace_back(wrap(type, error, false).value, failed);
  ir.CreateBr(finished);
  ir.SetInsertPoint(finished);
  auto *out = ir.CreatePHI(type->ir, edges.size());
  for (auto [value, predecessor] : edges) out->addIncoming(value, predecessor);
  merge(states, initial.size());
  leave();
  return {type, out};
}

} // namespace vqr::detail
