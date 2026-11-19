#include "compiler_internal.hpp"

namespace vqr::detail {

ValueAt Body::taskIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types) {
  if (!p.hostedLinux64())
    fail(n->span, "lightweight tasks require a hosted GNU/Linux x86-64 or AArch64 target", "E290");
  auto *pointer = ir.getPtrTy();
  auto *raw = p.pointer(p.integer(8, false), false);
  if (op == "validate" && n->children.size() == 1 && types.empty()) return voidValue();
  auto optionalPointer = [&](Value *address) -> ValueAt {
    auto *type = p.aggregate(Kind::Optional, {raw});
    return {type, ir.CreateSelect(ir.CreateIsNotNull(address), wrap(type, address, true).value,
                                  Constant::getNullValue(type->ir))};
  };
  if (op == "create" && n->children.size() == 2 && types.empty()) {
    auto workers = expression(n->children[1], p.sizeTy);
    if (!live()) return neverValue();
    auto create = p.module->getOrInsertFunction("__vqr_rt_group_create", pointer, p.sizeTy->ir, pointer);
    return optionalPointer(ir.CreateCall(create, {read(workers, n->span), ConstantPointerNull::get(pointer)}));
  }
  if (op == "current" && n->children.size() == 1 && types.empty()) {
    auto current = p.module->getOrInsertFunction("__vqr_rt_task_current", pointer);
    return optionalPointer(ir.CreateCall(current));
  }
  if (op == "record_error" && n->children.size() == 2 && types.empty()) {
    auto error = expression(n->children[1], p.errorTy);
    if (!live()) return neverValue();
    auto record = p.module->getOrInsertFunction("__vqr_rt_task_error", ir.getVoidTy(), ir.getInt32Ty());
    ir.CreateCall(record, {read(error, n->span)});
    return voidValue();
  }
  if (op == "submit" && n->children.size() == 4 && types.size() == 2) {
    if (!unsafeDepth) fail(n->span, "submitting a raw task requires unsafe", "E240");
    auto *work = parseType(types[0]);
    auto *result = parseType(types[1]);
    if (work->kind != Kind::Class || !work->def.file->standard || work->def.file->package != "task")
      fail(n->span, "invalid task entry type");
    auto group = expression(n->children[1], raw);
    if (!live()) return neverValue();
    auto *scheduler = read(group, n->span);
    auto value = expression(n->children[2], p.pointer(work, false));
    if (!live()) return neverValue();
    auto *context = read(value, n->span);
    auto stored = expression(n->children[3], p.pointer(result, false));
    if (!live()) return neverValue();
    auto *completion = read(stored, n->span);
    auto callback = [&](std::string_view name, bool isStatic, Type *parameter) {
      auto methods = p.methods(work, name, isStatic, true);
      if (methods.size() != 1) fail(n->span, "task entry needs a '" + std::string(name) + "' callback");
      auto &entry = p.function(methods.front(), p.classTypes(work), work, work->comptimeArguments);
      if (entry.result != p.voidTy || entry.parameters.size() != 1 ||
          entry.parameters[0] != p.pointer(parameter, false))
        fail(n->span, "wrong task callback type for '" + std::string(name) + "'");
      p.lower(entry);
      return entry.ir;
    };
    auto *run = callback("run", false, work);
    auto *discard = callback("discard", true, result);
    auto submit = p.module->getOrInsertFunction("__vqr_rt_task_submit", pointer, pointer, pointer,
                                                pointer, pointer, pointer);
    return optionalPointer(ir.CreateCall(submit, {scheduler, context, run, completion, discard}));
  }
  fail(n->span, "invalid lightweight task intrinsic");
}

} // namespace vqr::detail
