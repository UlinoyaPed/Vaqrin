#include "compiler_internal.hpp"

namespace vqr::detail {

ValueAt Body::threadIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types) {
  const bool darwin = p.hostedMac64();
  if (!p.hostedLinux64() && !darwin)
    fail(n->span, "native threads require a hosted GNU/Linux or macOS x86-64 or AArch64 target", "E290");

  auto arity = [&](unsigned count, unsigned typeCount = 0) {
    if (n->children.size() != count + 1 || types.size() != typeCount)
      fail(n->span, "wrong native thread intrinsic argument count");
  };
  auto *pointer = ir.getPtrTy();
  auto *null = ConstantPointerNull::get(pointer);
  auto *status = p.integer(32, true);
  auto *word = p.sizeTy->ir;
  llvm::Type *pthreadId = darwin ? static_cast<llvm::Type *>(pointer) : word;

  if (op == "id") {
    arity(0);
    auto self = p.module->getOrInsertFunction("pthread_self", pthreadId);
    auto *handle = ir.CreateCall(self);
    return {p.sizeTy, darwin ? ir.CreatePtrToInt(handle, word) : handle};
  }
  if (op == "yield" || op == "cpu_count") {
    arity(0);
    if (op == "cpu_count" && darwin) {
      auto count = p.module->getOrInsertFunction("__vqr_io_cpu_count", word);
      return {p.sizeTy, ir.CreateCall(count)};
    }
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
    if (darwin) handle = ir.CreateIntToPtr(handle, pointer);
    if (op == "join") {
      auto join = p.module->getOrInsertFunction("pthread_join", status->ir, pthreadId, pointer);
      return {status, ir.CreateCall(join, {handle, null})};
    }
    auto detach = p.module->getOrInsertFunction("pthread_detach", status->ir, pthreadId);
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
  const bool darwin = p.hostedMac64();
  if (!p.hostedLinux64() && !darwin)
    fail(n->span, "blocking synchronization requires a hosted GNU/Linux or macOS x86-64 or AArch64 target", "E290");
  bool timed = op == "wait_until";
  if ((!timed && op != "wait" && op != "wake") || n->children.size() != (timed ? 5U : 3U))
    fail(n->span, "invalid futex operation");
  auto *u32 = p.integer(32, false);
  auto address = expression(n->children[1], p.pointer(u32, true));
  if (!live()) return neverValue();
  auto *location = read(address, n->span);
  auto argument = expression(n->children[2], u32);
  if (!live()) return neverValue();
  auto *expected = read(argument, n->span);
  if (darwin) {
    if (!timed) {
      auto wait = p.module->getOrInsertFunction(op == "wait" ? "__vqr_io_sync_wait" : "__vqr_io_sync_wake",
                                                ir.getVoidTy(), ir.getPtrTy(), ir.getInt32Ty());
      ir.CreateCall(wait, {location, expected});
      return voidValue();
    }
    auto *signedWord = p.integer(64, true);
    auto seconds = expression(n->children[3], signedWord);
    if (!live()) return neverValue();
    auto *whole = read(seconds, n->span);
    auto nanos = expression(n->children[4], signedWord);
    if (!live()) return neverValue();
    auto wait = p.module->getOrInsertFunction("__vqr_io_sync_wait_until", ir.getInt32Ty(), ir.getPtrTy(),
                                              ir.getInt32Ty(), signedWord->ir, signedWord->ir);
    auto *result = ir.CreateCall(wait, {location, expected, whole, read(nanos, n->span)});
    return {p.boolTy, ir.CreateIsNotNull(result)};
  }
  auto *word = p.sizeTy->ir;
  auto *signature = FunctionType::get(word, {word}, true);
  auto syscall = p.module->getOrInsertFunction("syscall", signature);
  auto *number = ConstantInt::get(word, p.targetTriple().isX86() ? 202 : 98);
  constexpr unsigned privateFlag = 128;
  constexpr unsigned wait = 0, wake = 1, waitBitset = 9;
  auto *command = ConstantInt::get(word, privateFlag + (timed ? waitBitset : op == "wait" ? wait : wake));
  auto *value = ir.CreateZExt(expected, word);
  auto *null = ConstantPointerNull::get(ir.getPtrTy());
  if (!timed) {
    ir.CreateCall(syscall, {number, location, command, value, null, null, ConstantInt::get(word, 0)});
    return voidValue();
  }
  auto *signedWord = p.integer(64, true);
  auto seconds = expression(n->children[3], signedWord);
  if (!live()) return neverValue();
  auto *whole = read(seconds, n->span);
  auto nanos = expression(n->children[4], signedWord);
  if (!live()) return neverValue();
  auto *timespec = p.aggregate(Kind::Tuple, {signedWord, signedWord});
  auto *deadline = slot(timespec, "futex.deadline");
  ir.CreateStore(whole, ir.CreateStructGEP(timespec->ir, deadline, 0));
  ir.CreateStore(read(nanos, n->span), ir.CreateStructGEP(timespec->ir, deadline, 1));
  // WAIT_BITSET takes a CLOCK_MONOTONIC absolute deadline by default.
  auto *result = ir.CreateCall(syscall, {number, location, command, value, deadline, null,
                                        ConstantInt::get(word, 0xffffffff)});
  auto errnoLocation = p.module->getOrInsertFunction("__errno_location", ir.getPtrTy());
  auto *error = ir.CreateLoad(ir.getInt32Ty(), ir.CreateCall(errnoLocation));
  constexpr unsigned timedOut = 110;
  return {p.boolTy, ir.CreateAnd(ir.CreateICmpNE(result, ConstantInt::get(word, 0)),
                                ir.CreateICmpEQ(error, ir.getInt32(timedOut)))};
}

} // namespace vqr::detail
