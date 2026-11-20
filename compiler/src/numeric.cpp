#include "codegen.hpp"

#include <llvm/IR/Intrinsics.h>
#include <llvm/IR/Module.h>

namespace vqr {
using namespace llvm;

Number parseNumber(std::string_view input) {
  auto split = input.find(':');
  std::string_view body = input.substr(0, split);
  Number n;
  if (split != input.npos) n.suffix = input.substr(split + 1);
  unsigned base = 10;
  if (body.starts_with("0x"))
    base = 16;
  else if (body.starts_with("0o"))
    base = 8;
  else if (body.starts_with("0b"))
    base = 2;
  n.floating = base == 10 && (n.suffix.starts_with('f') || body.find_first_of(".eE") != body.npos);
  if (n.floating)
    n.decimal = body;
  else {
    if (base != 10) body.remove_prefix(2);
    n.integer =
        APInt(static_cast<unsigned>(body.size() * 4 + 1), StringRef(body), static_cast<uint8_t>(base));
    n.integer = n.integer.trunc(n.integer.getSignificantBits());
  }
  return n;
}

bool fits(const APInt &n, unsigned width, bool isSigned) {
  return isSigned ? n.isSignedIntN(width) : !n.isNegative() && n.isIntN(width);
}

std::optional<APInt> mathInteger(const Ast &n, bool diagnose) {
  if (n->kind == NodeKind::Number) {
    auto number = parseNumber(n->text);
    if (!number.floating && number.suffix.empty()) return number.integer;
  } else if (n->kind == NodeKind::Unary && (n->text == "-" || n->text == "~")) {
    if (auto x = mathInteger(n->children[0], diagnose)) {
      if (n->text == "~") return ~*x;
      return -x->sext(x->getBitWidth() + 1);
    }
  } else if (n->kind == NodeKind::Binary) {
    const auto &op = n->text;
    if (op != "+" && op != "-" && op != "*" && op != "/" && op != "%" && op != "&" && op != "|" && op != "^")
      return std::nullopt;
    auto a = mathInteger(n->children[0], diagnose);
    auto b = mathInteger(n->children[1], diagnose);
    if (!a || !b) return std::nullopt;
    unsigned width =
        op == "*" ? a->getBitWidth() + b->getBitWidth() : std::max(a->getBitWidth(), b->getBitWidth()) + 1;
    if (width > 65536) fail(n->span, "integer expression exceeds compiler precision limit", "E211");
    auto x = a->sext(width), y = b->sext(width);
    APInt z(width, 0);
    if (op == "+")
      z = x + y;
    else if (op == "-")
      z = x - y;
    else if (op == "*")
      z = x * y;
    else if (op == "&")
      z = x & y;
    else if (op == "|")
      z = x | y;
    else if (op == "^")
      z = x ^ y;
    else {
      if (y.isZero()) {
        if (diagnose) fail(n->span, "division by zero in constant expression", "E211");
        return std::nullopt;
      }
      z = op == "/" ? x.sdiv(y) : x.srem(y);
    }
    return z.trunc(z.getSignificantBits());
  }
  return std::nullopt;
}

void guard(IRBuilder<> &b, Value *valid, function_ref<void()> report) {
  if (auto *constant = dyn_cast<ConstantInt>(valid); constant && constant->isOne()) return;
  auto *function = b.GetInsertBlock()->getParent();
  auto &context = function->getContext();
  auto *safe = BasicBlock::Create(context, "checked", function);
  auto *trap = BasicBlock::Create(context, "trap", function);
  b.CreateCondBr(valid, safe, trap);
  b.SetInsertPoint(trap);
  if (report) report();
  b.CreateCall(Intrinsic::getOrInsertDeclaration(function->getParent(), Intrinsic::trap));
  b.CreateUnreachable();
  b.SetInsertPoint(safe);
}

Value *binaryNumber(IRBuilder<> &b, std::string_view op, Value *a, Value *c, bool sign, bool rightSign) {
  auto *t = a->getType();
  bool floating = t->isFloatingPointTy();
  if (op == "==") return floating ? b.CreateFCmpOEQ(a, c) : b.CreateICmpEQ(a, c);
  if (op == "!=") return floating ? b.CreateFCmpUNE(a, c) : b.CreateICmpNE(a, c);
  if (op == "<")
    return floating ? b.CreateFCmpOLT(a, c) : sign ? b.CreateICmpSLT(a, c) : b.CreateICmpULT(a, c);
  if (op == "<=")
    return floating ? b.CreateFCmpOLE(a, c) : sign ? b.CreateICmpSLE(a, c) : b.CreateICmpULE(a, c);
  if (op == ">")
    return floating ? b.CreateFCmpOGT(a, c) : sign ? b.CreateICmpSGT(a, c) : b.CreateICmpUGT(a, c);
  if (op == ">=")
    return floating ? b.CreateFCmpOGE(a, c) : sign ? b.CreateICmpSGE(a, c) : b.CreateICmpUGE(a, c);
  if (floating) {
    if (op == "+") return b.CreateFAdd(a, c);
    if (op == "-") return b.CreateFSub(a, c);
    if (op == "*") return b.CreateFMul(a, c);
    if (op == "/") return b.CreateFDiv(a, c);
    if (op == "%") return b.CreateFRem(a, c);
    return nullptr;
  }
  auto *integer = dyn_cast<IntegerType>(t);
  if (!integer) return nullptr;
  unsigned width = integer->getBitWidth();
  auto *zero = ConstantInt::get(integer, 0);
  auto *minimum = ConstantInt::get(integer, sign ? APInt::getSignedMinValue(width) : APInt(width, 0));
  auto *maximum =
      ConstantInt::get(integer, sign ? APInt::getSignedMaxValue(width) : APInt::getMaxValue(width));
  const bool shift = op.starts_with("<<") || op == ">>";
  if (shift) {
    unsigned countBits = std::max(32u, c->getType()->getIntegerBitWidth());
    auto *count = b.CreateIntCast(c, b.getIntNTy(countBits), rightSign);
    guard(b, b.CreateICmpULT(count, ConstantInt::get(count->getType(), width)));
    c = b.CreateIntCast(count, integer, false);
    if (op == ">>") return sign ? b.CreateAShr(a, c) : b.CreateLShr(a, c);
    if (op == "<<%" || (!sign && op == "<<")) return b.CreateShl(a, c);
  }
  if (op == "/" || op == "%") {
    auto *valid = b.CreateICmpNE(c, zero);
    if (sign) {
      auto *badPair =
          b.CreateAnd(b.CreateICmpEQ(a, minimum), b.CreateICmpEQ(c, ConstantInt::getAllOnesValue(integer)));
      if (op == "/")
        valid = b.CreateAnd(valid, b.CreateNot(badPair));
      else
        c = b.CreateSelect(badPair, ConstantInt::get(integer, 1), c);
    }
    guard(b, valid);
    if (op == "/") return sign ? b.CreateSDiv(a, c) : b.CreateUDiv(a, c);
    return sign ? b.CreateSRem(a, c) : b.CreateURem(a, c);
  }
  if (op == "&") return b.CreateAnd(a, c);
  if (op == "|") return b.CreateOr(a, c);
  if (op == "^") return b.CreateXor(a, c);
  if (!shift && !op.starts_with('+') && !op.starts_with('-') && !op.starts_with('*')) return nullptr;
  bool saturate = op.ends_with('|');
  if (op.ends_with('%') || (!sign && !saturate)) {
    if (op.starts_with('+')) return b.CreateAdd(a, c);
    if (op.starts_with('-')) return b.CreateSub(a, c);
    return b.CreateMul(a, c);
  }
  // Widen before operating. All mathematical results fit, including a full-width shift.
  unsigned widened = shift || op.starts_with('*') ? width * 2 + !sign : width + 1 + !sign;
  auto *wide = b.getIntNTy(widened);
  auto *left = b.CreateIntCast(a, wide, sign);
  auto *right = b.CreateIntCast(c, wide, sign && !shift);
  Value *result;
  if (shift)
    result = b.CreateShl(left, right);
  else if (op.starts_with('+'))
    result = b.CreateAdd(left, right);
  else if (op.starts_with('-'))
    result = b.CreateSub(left, right);
  else
    result = b.CreateMul(left, right);
  auto *below = b.CreateICmpSLT(result, b.CreateIntCast(minimum, wide, sign));
  auto *above = b.CreateICmpSGT(result, b.CreateIntCast(maximum, wide, sign));
  auto *truncated = b.CreateTrunc(result, integer);
  if (saturate) return b.CreateSelect(below, minimum, b.CreateSelect(above, maximum, truncated));
  guard(b, b.CreateNot(b.CreateOr(below, above)));
  return truncated;
}
} // namespace vqr
