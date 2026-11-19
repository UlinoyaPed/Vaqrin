#include "compiler_internal.hpp"

namespace vqr::detail {
namespace {

bool nativeThreadTarget(const Program &p) {
  auto target = p.targetTriple();
  return p.options.profile == "hosted" && target.isOSLinux() && target.isGNUEnvironment() &&
         (target.getArch() == Triple::x86_64 || target.getArch() == Triple::aarch64) && p.pointerBits() == 64;
}

} // namespace

ValueAt Body::threadIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types) {
  if (!nativeThreadTarget(p))
    fail(n->span, "native threads require a hosted GNU/Linux x86-64 or AArch64 target", "E290");

  auto arity = [&](unsigned count, unsigned typeCount = 0) {
    if (n->children.size() != count + 1 || types.size() != typeCount)
      fail(n->span, "wrong native thread intrinsic argument count");
  };
  auto *pointer = ir.getPtrTy();
  auto *null = ConstantPointerNull::get(pointer);
  auto *status = p.integer(32, true);
  auto *word = p.sizeTy->ir;

  if (op == "id") {
    arity(0);
    auto self = p.module->getOrInsertFunction("pthread_self", word);
    return {p.sizeTy, ir.CreateCall(self)};
  }
  if (op == "yield" || op == "cpu_count") {
    arity(0);
    auto host = p.module->getOrInsertFunction(op == "yield" ? "sched_yield" : "get_nprocs", status->ir);
    auto *result = ir.CreateCall(host);
    if (op == "yield") return voidValue();
    auto *count = ir.CreateSelect(ir.CreateICmpSGT(result, ir.getInt32(0)), result, ir.getInt32(1));
    return {p.sizeTy, ir.CreateZExt(count, word)};
  }
  if (op == "join" || op == "detach") {
    arity(1);
    auto id = expression(n->children[1], p.sizeTy);
    if (!live()) return neverValue();
    auto *handle = read(id, n->span);
    if (op == "join") {
      auto join = p.module->getOrInsertFunction("pthread_join", status->ir, word, pointer);
      return {status, ir.CreateCall(join, {handle, null})};
    }
    auto detach = p.module->getOrInsertFunction("pthread_detach", status->ir, word);
    return {status, ir.CreateCall(detach, {handle})};
  }
  if (op == "start") {
    arity(2, 1);
    if (!unsafeDepth) fail(n->span, "starting a raw thread requires unsafe", "E240");
    auto *work = parseType(types[0]);
    if (work->kind != Kind::Class || !work->def.file->standard || work->def.file->package != "thread")
      fail(n->span, "invalid native thread entry type");
    auto methods = p.methods(work, "run", false, true);
    if (methods.size() != 1 || !methods.front().decl->params.empty())
      fail(n->span, "a native thread entry needs one parameterless run method");
    auto id = expression(n->children[1], p.pointer(p.sizeTy, false));
    if (!live()) return neverValue();
    auto *out = read(id, n->span);
    auto context = expression(n->children[2], p.pointer(work, false));
    if (!live()) return neverValue();
    auto *state = read(context, n->span);
    auto &entry = p.function(methods.front(), p.classTypes(work), work, work->comptimeArguments);
    if (entry.result != p.voidTy) fail(n->span, "native thread run method must return void");
    std::string name = (entry.ir->getName() + ".pthread").str();
    auto *thunk = p.module->getFunction(name);
    if (!thunk) {
      auto *signature = FunctionType::get(pointer, {pointer}, false);
      thunk = Function::Create(signature, GlobalValue::InternalLinkage, name, *p.module);
      IRBuilder<> builder(BasicBlock::Create(p.context, "entry", thunk));
      builder.CreateCall(entry.ir, {thunk->getArg(0)});
      builder.CreateRet(null);
      p.lower(entry);
    }
    auto create = p.module->getOrInsertFunction("pthread_create", status->ir, pointer, pointer, pointer, pointer);
    return {status, ir.CreateCall(create, {out, null, thunk, state})};
  }
  if (op == "stop" || op == "set_stop" || op == "clear_stop") {
    auto *slot = p.module->getGlobalVariable("__vqr_current_thread_stop", true);
    if (!slot) {
      slot = new GlobalVariable(*p.module, pointer, false, GlobalValue::InternalLinkage, null,
                                "__vqr_current_thread_stop", nullptr, GlobalVariable::GeneralDynamicTLSModel);
    }
    if (op == "clear_stop") {
      arity(0);
      ir.CreateStore(null, slot);
      return voidValue();
    }
    if (op == "set_stop") {
      arity(1);
      auto flag = expression(n->children[1]);
      if (!live()) return neverValue();
      if (flag.type->kind != Kind::Pointer || !flag.type->ir->isPointerTy())
        fail(n->span, "the stop flag requires a pointer");
      ir.CreateStore(read(flag, n->span), slot);
      return voidValue();
    }
    arity(0, 1);
    auto *flag = ir.CreateLoad(pointer, slot);
    auto *optional = p.aggregate(Kind::Optional, {p.pointer(parseType(types[0]), true)});
    return {optional, ir.CreateSelect(ir.CreateIsNotNull(flag), wrap(optional, flag, true).value,
                                      Constant::getNullValue(optional->ir))};
  }
  fail(n->span, "unknown native thread intrinsic");
}

ValueAt Body::syncIntrinsic(std::string_view op, const Ast &n) {
  if (!nativeThreadTarget(p))
    fail(n->span, "blocking synchronization requires a hosted GNU/Linux x86-64 or AArch64 target", "E290");
  if ((op != "wait" && op != "wake") || n->children.size() != 3)
    fail(n->span, "invalid futex operation");
  auto *u32 = p.integer(32, false);
  auto address = expression(n->children[1], p.pointer(u32, true));
  if (!live()) return neverValue();
  auto *location = read(address, n->span);
  auto argument = expression(n->children[2], u32);
  if (!live()) return neverValue();
  auto *word = p.sizeTy->ir;
  auto *signature = FunctionType::get(word, {word}, true);
  auto syscall = p.module->getOrInsertFunction("syscall", signature);
  auto *number = ConstantInt::get(word, p.targetTriple().isX86() ? 202 : 98);
  // Linux FUTEX_WAIT_PRIVATE and FUTEX_WAKE_PRIVATE compare or wake a process-local word.
  auto *command = ConstantInt::get(word, op == "wait" ? 128 : 129);
  auto *value = ir.CreateZExt(read(argument, n->span), word);
  auto *null = ConstantPointerNull::get(ir.getPtrTy());
  ir.CreateCall(syscall, {number, location, command, value, null, null, ConstantInt::get(word, 0)});
  return voidValue();
}

} // namespace vqr::detail
