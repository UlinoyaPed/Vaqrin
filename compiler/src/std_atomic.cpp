#include "compiler_internal.hpp"

#include <llvm/ADT/StringSwitch.h>

#include <array>

namespace vqr::detail {
namespace {

unsigned nativeAtomicWidth(const Program &p) {
  auto target = p.targetTriple();
  if (target.getArch() == Triple::x86_64 || target.isAArch64()) return 64;
  if (target.getArchName() == "thumbv7m" || target.getArchName() == "thumbv7em") return 32;
  return 0;
}

unsigned atomicWidth(const Program &p, const Type *type) {
  if (type->kind == Kind::Pointer) return p.pointerBits();
  if (type->kind == Kind::Bool) return 8;
  if (type->kind == Kind::Int &&
      (type->bits == 8 || type->bits == 16 || type->bits == 32 || type->bits == 64))
    return type->bits;
  return 0;
}

constexpr std::array orders{
    AtomicOrdering::Monotonic,
    AtomicOrdering::Acquire,
    AtomicOrdering::Release,
    AtomicOrdering::AcquireRelease,
    AtomicOrdering::SequentiallyConsistent,
};

bool legalFailure(AtomicOrdering success, AtomicOrdering failure) {
  if (failure == AtomicOrdering::Monotonic) return true;
  if (failure == AtomicOrdering::Acquire)
    return success == AtomicOrdering::Acquire || success == AtomicOrdering::AcquireRelease ||
           success == AtomicOrdering::SequentiallyConsistent;
  return success == AtomicOrdering::SequentiallyConsistent &&
         failure == AtomicOrdering::SequentiallyConsistent;
}

} // namespace

ValueAt Body::atomicIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types) {
  bool query = op == "supported";
  if (op == "encode" || op == "decode") {
    if (n->children.size() != 2) fail(n->span, "pointer encoding requires one argument");
    if (op == "encode") {
      auto value = expression(n->children[1]);
      if (!live()) return neverValue();
      if (value.type->kind != Kind::Optional || value.type->parts[0]->kind != Kind::Pointer)
        fail(n->span, "expected a nullable pointer");
      auto *encoded = read(value, n->span);
      auto *present = ir.CreateExtractValue(encoded, 0);
      auto *address = ir.CreatePtrToInt(ir.CreateExtractValue(encoded, 1), p.sizeTy->ir);
      return {p.sizeTy, ir.CreateSelect(present, address, ConstantInt::get(p.sizeTy->ir, 0))};
    }
    if (types.size() != 1) fail(n->span, "pointer decoding requires a pointee type");
    auto *pointer = p.pointer(parseType(types[0]), false);
    auto value = expression(n->children[1], p.sizeTy);
    if (!live()) return neverValue();
    auto *address = read(value, n->span);
    auto *type = p.aggregate(Kind::Optional, {pointer});
    auto some = wrap(type, ir.CreateIntToPtr(address, pointer->ir), true);
    auto none = Constant::getNullValue(type->ir);
    return {type, ir.CreateSelect(ir.CreateIsNotNull(address), some.value, none)};
  }
  bool needsValue = op != "load" && op != "fence" && op != "compiler_fence";
  bool comparison = op == "compare" || op == "compare_weak";
  bool fence = op == "fence" || op == "compiler_fence";
  unsigned arity = query ? 0 : fence ? 1 : comparison ? 5 : needsValue ? 3 : 2;
  if (n->children.size() != arity + 1) fail(n->span, "wrong atomic argument count");
  if (query) {
    if (types.size() != 1) fail(n->span, "an atomic capability query requires one type");
    auto bits = atomicWidth(p, parseType(types[0]));
    bool supported = bits && bits <= nativeAtomicWidth(p);
    return {p.boolTy, ir.getInt1(supported)};
  }
  if (!nativeAtomicWidth(p)) fail(n->span, "this target has no native atomic profile", "E290");

  Type *type = p.voidTy;
  Value *address = nullptr, *input = nullptr, *desired = nullptr;
  llvm::Type *storage = nullptr;
  Align alignment;
  if (!fence) {
    auto receiver = expression(n->children[1]);
    if (!live()) return neverValue();
    if (receiver.type->kind != Kind::Pointer || (needsValue && receiver.type->readOnly))
      fail(n->span, "atomic operations require a suitably qualified pointer");
    type = receiver.type->parts[0];
    auto bits = atomicWidth(p, type);
    if (!bits || bits > nativeAtomicWidth(p))
      fail(n->span, "this target does not support native atomics of this type", "E290");
    storage = type == p.boolTy ? ir.getInt8Ty() : type->ir;
    alignment = p.module->getDataLayout().getABITypeAlign(storage);
    address = read(receiver, n->span);
    auto readOperand = [&](unsigned index) -> Value * {
      auto operand = expression(n->children[index], type);
      if (!live()) return nullptr;
      auto *value = read(operand, n->span);
      return type == p.boolTy ? ir.CreateZExt(value, storage) : value;
    };
    if (needsValue) {
      input = readOperand(2);
      if (!input) return neverValue();
    }
    if (comparison) {
      desired = readOperand(3);
      if (!desired) return neverValue();
    }
  }
  auto orderValue = [&](unsigned index) -> Value * {
    auto order = expression(n->children[index]);
    if (!live()) return nullptr;
    if (order.type->kind != Kind::Enum || !order.type->def.file->standard ||
        order.type->def.file->package != "atomic" || order.type->def.decl->name != "Order")
      fail(n->span, "atomic memory order must have type atomic.Order");
    auto *value = ir.CreateExtractValue(read(order, n->span), 0);
    return ir.CreateZExtOrTrunc(value, ir.getInt32Ty());
  };
  auto *order = orderValue(arity - unsigned(comparison));
  if (!order) return neverValue();
  if (comparison) {
    auto *failure = orderValue(arity);
    if (!failure) return neverValue();
    guard(ir, ir.CreateAnd(ir.CreateICmpULT(order, ir.getInt32(orders.size())),
                           ir.CreateICmpULT(failure, ir.getInt32(orders.size()))));
    order = ir.CreateAdd(ir.CreateMul(order, ir.getInt32(orders.size())), failure);
  }

  auto rmw = llvm::StringSwitch<AtomicRMWInst::BinOp>(op)
                 .Case("swap", AtomicRMWInst::Xchg)
                 .Case("add", AtomicRMWInst::Add)
                 .Case("sub", AtomicRMWInst::Sub)
                 .Case("and", AtomicRMWInst::And)
                 .Case("or", AtomicRMWInst::Or)
                 .Case("xor", AtomicRMWInst::Xor)
                 .Case("max", type->sign ? AtomicRMWInst::Max : AtomicRMWInst::UMax)
                 .Case("min", type->sign ? AtomicRMWInst::Min : AtomicRMWInst::UMin)
                 .Default(AtomicRMWInst::BAD_BINOP);
  if (rmw != AtomicRMWInst::BAD_BINOP && rmw != AtomicRMWInst::Xchg && type->kind != Kind::Int)
    fail(n->span, "atomic arithmetic and bitwise updates require integer types");

  auto *result = comparison               ? p.aggregate(Kind::Tuple, {type, p.boolTy})
                 : fence || op == "store" ? p.voidTy
                                          : type;
  auto *invalid = block("atomic.order.invalid");
  auto *end = block("atomic.end");
  auto *selection = ir.CreateSwitch(order, invalid);
  auto *phi = result == p.voidTy ? nullptr : PHINode::Create(result->ir, 0, "atomic.result", end);

  auto addCase = [&](unsigned id, AtomicOrdering mode, AtomicOrdering failure) {
    auto *selected = block("atomic.order");
    selection->addCase(ir.getInt32(id), selected);
    ir.SetInsertPoint(selected);
    Value *output = nullptr;
    if (fence) {
      if (mode != AtomicOrdering::Monotonic)
        ir.CreateFence(mode, op == "compiler_fence" ? SyncScope::SingleThread : SyncScope::System);
    } else if (op == "load") {
      auto *load = ir.CreateAlignedLoad(storage, address, alignment);
      load->setAtomic(mode);
      output = load;
    } else if (op == "store") {
      auto *store = ir.CreateAlignedStore(input, address, alignment);
      store->setAtomic(mode);
    } else if (comparison) {
      auto *cas = ir.CreateAtomicCmpXchg(address, input, desired, alignment, mode, failure);
      cas->setWeak(op == "compare_weak");
      auto *previous = ir.CreateExtractValue(cas, 0);
      if (type == p.boolTy) previous = ir.CreateTrunc(previous, type->ir);
      output = ir.CreateInsertValue(PoisonValue::get(result->ir), previous, 0);
      output = ir.CreateInsertValue(output, ir.CreateExtractValue(cas, 1), 1);
    } else {
      if (rmw == AtomicRMWInst::BAD_BINOP) fail(n->span, "unknown atomic operation");
      output = ir.CreateAtomicRMW(rmw, address, input, alignment, mode);
    }
    if (phi) {
      if (result == p.boolTy) output = ir.CreateTrunc(output, result->ir);
      phi->addIncoming(output, selected);
    }
    ir.CreateBr(end);
  };
  for (unsigned i = 0; i < orders.size(); ++i) {
    auto mode = orders[i];
    if (comparison) {
      for (unsigned j = 0; j < orders.size(); ++j)
        if (legalFailure(mode, orders[j])) addCase(i * orders.size() + j, mode, orders[j]);
    } else if ((op != "load" ||
                (mode != AtomicOrdering::Release && mode != AtomicOrdering::AcquireRelease)) &&
               (op != "store" || (mode != AtomicOrdering::Acquire && mode != AtomicOrdering::AcquireRelease)))
      addCase(i, mode, AtomicOrdering::NotAtomic);
  }
  ir.SetInsertPoint(invalid);
  ir.CreateCall(Intrinsic::getOrInsertDeclaration(p.module.get(), Intrinsic::trap));
  ir.CreateUnreachable();
  ir.SetInsertPoint(end);
  return phi ? ValueAt{result, phi} : voidValue();
}

} // namespace vqr::detail
