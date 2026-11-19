#include "compiler_internal.hpp"

namespace vqr::detail {

ValueAt Body::bitIntrinsic(std::string_view op, const Ast &n, const std::vector<Ast> &types) {
  unsigned count =
      op == "insert"                                                                                   ? 4
      : op == "extract"                                                                                ? 3
      : (op == "rol" || op == "ror" || op == "test" || op == "set" || op == "clear" || op == "toggle") ? 2
                                                                                                       : 1;
  if (n->children.size() != count + 1) fail(n->span, "wrong bit operation argument count");
  auto value = expression(n->children[1]);
  if (!live()) return neverValue();
  if (op == "mask" && types.size() != 1) fail(n->span, "maskLow requires an integer type");
  auto *type = op == "mask" ? parseType(types[0]) : value.type;
  if (type->kind != Kind::Int) fail(n->span, "bit operations require integer types");
  auto *input = read(value, n->span);
  auto *integer = cast<IntegerType>(type->ir);
  auto constant = [&](std::uint64_t v) { return ConstantInt::get(integer, v); };
  auto *size = cast<IntegerType>(p.sizeTy->ir);
  auto *width = ConstantInt::get(size, type->bits);
  auto sizeArgument = [&](unsigned i) -> Value * {
    auto arg = expression(n->children[i], p.sizeTy);
    return live() ? read(arg, n->span) : nullptr;
  };
  auto mask = [&](Value *length) {
    auto *full = ir.CreateICmpEQ(length, width);
    auto *distance =
        ir.CreateIntCast(ir.CreateSelect(full, ConstantInt::get(size, 0), length), integer, false);
    auto *partial = ir.CreateSub(ir.CreateShl(constant(1), distance), constant(1));
    return ir.CreateSelect(full, ConstantInt::getAllOnesValue(integer), partial);
  };

  if (op == "popcount" || op == "leading" || op == "trailing") {
    auto id = op == "popcount" ? Intrinsic::ctpop : op == "leading" ? Intrinsic::ctlz : Intrinsic::cttz;
    auto *function = Intrinsic::getOrInsertDeclaration(p.module.get(), id, {integer});
    auto *sum =
        op == "popcount" ? ir.CreateCall(function, {input}) : ir.CreateCall(function, {input, ir.getFalse()});
    return {p.sizeTy, ir.CreateZExtOrTrunc(sum, size)};
  }
  if (op == "reverse")
    return {type,
            ir.CreateCall(Intrinsic::getOrInsertDeclaration(p.module.get(), Intrinsic::bitreverse, {integer}),
                          {input})};
  if (op == "bytes") {
    if (type->bits % 8) fail(n->span, "byte reversal requires a multiple of eight bits");
    Value *out = constant(0);
    for (unsigned i = 0; i < type->bits; i += 8) {
      auto *byte = ir.CreateAnd(ir.CreateLShr(input, i), constant(255));
      out = ir.CreateOr(out, ir.CreateShl(byte, type->bits - i - 8));
    }
    return {type, out};
  }
  if (op == "mask") {
    if (value.type != p.sizeTy) fail(n->span, "mask width must be usize");
    guard(ir, ir.CreateICmpULE(input, width));
    return {type, mask(input)};
  }
  if (op == "rol" || op == "ror") {
    auto *amount = sizeArgument(2);
    if (!amount) return neverValue();
    auto id = op == "rol" ? Intrinsic::fshl : Intrinsic::fshr;
    auto *function = Intrinsic::getOrInsertDeclaration(p.module.get(), id, {integer});
    amount = ir.CreateIntCast(ir.CreateURem(amount, width), integer, false);
    return {type, ir.CreateCall(function, {input, input, amount})};
  }

  if (op == "extract" || op == "insert") {
    Value *field = nullptr;
    if (op == "insert") {
      auto item = expression(n->children[2], type);
      if (!live()) return neverValue();
      field = read(item, n->span);
    }
    auto *start = sizeArgument(count - 1);
    if (!start) return neverValue();
    auto *length = sizeArgument(count);
    if (!length) return neverValue();
    guard(ir,
          ir.CreateAnd(ir.CreateICmpULE(start, width), ir.CreateICmpULE(length, ir.CreateSub(width, start))));
    auto *distance = ir.CreateIntCast(
        ir.CreateSelect(ir.CreateICmpEQ(start, width), ConstantInt::get(size, 0), start), integer, false);
    auto *low = mask(length);
    if (!field) return {type, ir.CreateAnd(ir.CreateLShr(input, distance), low)};
    auto *cleared = ir.CreateAnd(input, ir.CreateNot(ir.CreateShl(low, distance)));
    return {type, ir.CreateOr(cleared, ir.CreateShl(ir.CreateAnd(field, low), distance))};
  }

  auto *index = sizeArgument(2);
  if (!index) return neverValue();
  guard(ir, ir.CreateICmpULT(index, width));
  auto *bit = ir.CreateShl(constant(1), ir.CreateIntCast(index, integer, false));
  if (op == "test") return {p.boolTy, ir.CreateICmpNE(ir.CreateAnd(input, bit), constant(0))};
  if (op == "set") return {type, ir.CreateOr(input, bit)};
  if (op == "clear") return {type, ir.CreateAnd(input, ir.CreateNot(bit))};
  if (op == "toggle") return {type, ir.CreateXor(input, bit)};
  fail(n->span, "unknown bit operation");
}

} // namespace vqr::detail
