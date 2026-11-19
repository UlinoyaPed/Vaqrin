#include "compiler_internal.hpp"

namespace vqr::detail {

ValueAt Body::standardIntrinsic(std::string_view key, const Ast &n, const std::vector<Ast> &types) {
  if (key.starts_with("__vqr_atomic_")) return atomicIntrinsic(key.substr(13), n, types);
  if (key.starts_with("__vqr_bits_")) return bitIntrinsic(key.substr(11), n, types);
  auto arity = [&](unsigned count) {
    if (n->children.size() != count + 1) fail(n->span, "wrong intrinsic argument count");
  };
  if (key == "__vqr_optional_present" || key == "__vqr_result_ok") {
    arity(1);
    auto pointer = expression(n->children[1]);
    if (!live()) return neverValue();
    auto kind = key == "__vqr_optional_present" ? Kind::Optional : Kind::Result;
    if (pointer.type->kind != Kind::Pointer || pointer.type->parts[0]->kind != kind)
      fail(n->span, "tag inspection requires a pointer to the matching wrapper type");
    auto *address = read(pointer, n->span);
    auto *tag = ir.CreateStructGEP(pointer.type->parts[0]->ir, address, 0);
    if (kind == Kind::Optional) return {p.boolTy, ir.CreateLoad(ir.getInt1Ty(), tag)};
    return {p.boolTy, ir.CreateICmpEQ(ir.CreateLoad(ir.getInt32Ty(), tag), ir.getInt32(0))};
  }
  if (key == "__vqr_unreachable") {
    arity(0);
    if (!unsafeDepth) fail(n->span, "unreachableUnchecked requires unsafe", "E240");
    ir.CreateUnreachable();
    return neverValue();
  }
  if (key == "__vqr_optional_pointer" || key == "__vqr_result_pointer" || key == "__vqr_result_error") {
    arity(1);
    auto pointer = expression(n->children[1]);
    if (!live()) return neverValue();
    bool optional = key == "__vqr_optional_pointer";
    bool error = key == "__vqr_result_error";
    if (pointer.type->kind != Kind::Pointer ||
        pointer.type->parts[0]->kind != (optional ? Kind::Optional : Kind::Result))
      fail(n->span, "payload access requires a pointer to the matching wrapper type");
    auto *wrapper = pointer.type->parts[0];
    auto *element = error ? p.errorTy : p.pointer(wrapper->parts[0], pointer.type->readOnly);
    auto *type = p.aggregate(Kind::Optional, {element});
    auto *address = read(pointer, n->span);
    auto *tag = ir.CreateLoad(optional ? ir.getInt1Ty() : ir.getInt32Ty(),
                              ir.CreateStructGEP(wrapper->ir, address, 0));
    auto *valid = optional ? tag : error ? ir.CreateIsNotNull(tag) : ir.CreateIsNull(tag);
    Value *payload = error ? static_cast<Value *>(tag) : ir.CreateStructGEP(wrapper->ir, address, 1);
    return {type, ir.CreateSelect(valid, wrap(type, payload, true).value,
                                  Constant::getNullValue(type->ir))};
  }
  if (key == "__vqr_char") {
    arity(1);
    auto scalar = expression(n->children[1], p.integer(32, false));
    if (!live()) return neverValue();
    auto *raw = read(scalar, n->span);
    auto *surrogate =
        ir.CreateAnd(ir.CreateICmpUGE(raw, ir.getInt32(0xd800)), ir.CreateICmpULE(raw, ir.getInt32(0xdfff)));
    guard(ir, ir.CreateAnd(ir.CreateICmpULE(raw, ir.getInt32(0x10ffff)), ir.CreateNot(surrogate)));
    return {p.charTy, raw};
  }
  if (key == "__vqr_total_less") {
    arity(2);
    auto left = expression(n->children[1]);
    if (!live()) return neverValue();
    auto *a = read(left, n->span, false);
    auto right = expression(n->children[2], left.type);
    if (!live()) return neverValue();
    auto *b = read(right, n->span, false);
    if (left.type->kind == Kind::Int)
      return {p.boolTy, binaryNumber(ir, "<", a, b, left.type->sign, left.type->sign)};
    if (left.type->kind != Kind::Float) fail(n->span, "total ordering requires numbers");
    auto *bits = ir.getIntNTy(left.type->bits);
    auto *sign = ConstantInt::get(bits, APInt::getSignMask(left.type->bits));
    auto order = [&](Value *value) {
      auto *raw = ir.CreateBitCast(value, bits);
      auto *negative = ir.CreateICmpSLT(raw, ConstantInt::get(bits, 0));
      return ir.CreateSelect(negative, ir.CreateNot(raw), ir.CreateXor(raw, sign));
    };
    return {p.boolTy, ir.CreateICmpULT(order(a), order(b))};
  }
  if (key == "__vqr_type_name" || key == "__vqr_alloc_size" || key == "__vqr_alignment" ||
      key == "__vqr_bit_width" || key == "__vqr_copyable" || key == "__vqr_movable" ||
      key == "__vqr_trivial_drop") {
    arity(0);
    if (types.size() != 1) fail(n->span, "metadata requires one type argument");
    auto *type = parseType(types[0]);
    p.ensureSized(type, n->span);
    if (key == "__vqr_type_name") {
      SmallVector<Constant *> fields{p.bytes(type->name), ConstantInt::get(p.sizeTy->ir, type->name.size())};
      return {p.viewTy, ConstantStruct::get(cast<StructType>(p.viewTy->ir), fields)};
    }
    if (key == "__vqr_copyable") return {p.boolTy, ir.getInt1(p.copyable(type))};
    if (key == "__vqr_movable") return {p.boolTy, ir.getInt1(p.movable(type))};
    if (key == "__vqr_trivial_drop") return {p.boolTy, ir.getInt1(!p.drops(type))};
    const auto &layout = p.module->getDataLayout();
    auto *storage = p.storage(type);
    auto count = layout.getTypeAllocSize(storage).getFixedValue();
    if (key == "__vqr_alignment") count = layout.getABITypeAlign(storage).value();
    if (key == "__vqr_bit_width") count = layout.getTypeSizeInBits(storage).getFixedValue();
    return {p.sizeTy, ConstantInt::get(p.sizeTy->ir, count)};
  }
  if (!unsafeDepth) fail(n->span, "raw storage intrinsic requires an unsafe block", "E240");
  if (key == "__vqr_text_from_utf8") {
    arity(1);
    if (!p.heapAvailable()) fail(n->span, "this target has no allocator for owning text", "E290");
    auto data = expression(n->children[1], p.slice(p.integer(8, false), true));
    if (!live()) return neverValue();
    auto *source = read(data, n->span);
    auto *length = ir.CreateExtractValue(source, 1);
    auto *sizeTy = p.sizeTy->ir;
    const auto &layout = p.module->getDataLayout();
    auto headerSize = layout.getTypeAllocSize(sizeTy).getFixedValue();
    auto size = [&](std::uint64_t n) { return ConstantInt::get(sizeTy, n); };
    auto *ptr = PointerType::getUnqual(p.context);
    auto *result = p.aggregate(Kind::Result, {p.textTy});
    auto failed = wrap(result, ir.getInt32(0xfffffffc), false);
    auto *start = ir.GetInsertBlock(), *allocate = block("text.allocate"), *copy = block("text.copy"),
         *end = block("text.end");
    auto limit = (std::uint64_t{1} << (p.pointerBits() - 1)) - headerSize;
    ir.CreateCondBr(ir.CreateICmpULT(length, size(limit)), allocate, end);
    ir.SetInsertPoint(allocate);
    auto malloc = p.module->getOrInsertFunction("malloc", FunctionType::get(ptr, {sizeTy}, false));
    auto *owner = ir.CreateCall(malloc, {ir.CreateAdd(size(headerSize), length)});
    ir.CreateCondBr(ir.CreateIsNotNull(owner), copy, end);
    ir.SetInsertPoint(copy);
    ir.CreateStore(size(1), owner);
    auto *bytes = ir.CreateConstGEP1_64(ir.getInt8Ty(), owner, headerSize);
    // Empty slices are allowed to have a null data pointer.
    auto *input = ir.CreateExtractValue(source, 0);
    input = ir.CreateSelect(ir.CreateICmpEQ(length, size(0)), bytes, input);
    ir.CreateMemCpy(bytes, Align(1), input, Align(1), length);
    auto *value = ir.CreateInsertValue(Constant::getNullValue(p.textTy->ir), bytes, 0);
    value = ir.CreateInsertValue(value, length, 1);
    value = ir.CreateInsertValue(value, owner, 2);
    auto ready = wrap(result, value, true);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(result->ir, 3);
    phi->addIncoming(failed.value, start);
    phi->addIncoming(failed.value, allocate);
    phi->addIncoming(ready.value, copy);
    return {result, phi};
  }
  if (types.size() != 1) fail(n->span, "raw storage intrinsic requires an element type");
  auto *element = parseType(types[0]);
  p.ensureSized(element, n->span);
  auto *ptr = p.pointer(element, false);
  const auto &layout = p.module->getDataLayout();
  auto *sizeType = p.sizeTy->ir;
  auto size = [&](std::uint64_t value) { return ConstantInt::get(sizeType, value); };
  bool heap = p.heapAvailable();
  if (key == "__vqr_alloc") {
    arity(1);
    if (!heap) fail(n->span, "this target has no allocator for growing collections", "E290");
    auto count = expression(n->children[1], p.sizeTy);
    if (!live()) return neverValue();
    auto *length = read(count, n->span);
    auto align =
        std::max<std::uint64_t>(p.pointerBits() / 8, layout.getABITypeAlign(p.storage(element)).value());
    auto stride = layout.getTypeAllocSize(p.storage(element)).getFixedValue();
    auto limit = (std::uint64_t{1} << (p.pointerBits() - 1)) - align;
    auto *valid = stride ? ir.CreateICmpULE(length, size(limit / stride)) : ir.getTrue();
    auto *from = ir.GetInsertBlock();
    auto *allocate = block("buffer.allocate"), *end = block("buffer.end");
    ir.CreateCondBr(valid, allocate, end);
    ir.SetInsertPoint(allocate);
    auto *bytes = ir.CreateMul(length, size(stride));
    auto *padded = ir.CreateAnd(ir.CreateAdd(bytes, size(align - 1)), size(~(align - 1)));
    padded = ir.CreateSelect(ir.CreateICmpEQ(padded, size(0)), size(align), padded);
    auto allocator = p.module->getOrInsertFunction("aligned_alloc",
                                                   FunctionType::get(ptr->ir, {sizeType, sizeType}, false));
    auto *address = ir.CreateCall(allocator, {size(align), padded});
    auto *result = p.aggregate(Kind::Result, {ptr});
    auto failed = wrap(result, ir.getInt32(0xfffffffc), false);
    auto *out = ir.CreateSelect(ir.CreateIsNotNull(address), wrap(result, address, true).value, failed.value);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    auto *phi = ir.CreatePHI(result->ir, 2);
    phi->addIncoming(failed.value, from);
    phi->addIncoming(out, allocate);
    return {result, phi};
  }
  if (key == "__vqr_slice" || key == "__vqr_const_slice") {
    arity(2);
    auto data = expression(n->children[1], p.aggregate(Kind::Optional, {ptr}));
    if (!live()) return neverValue();
    auto *raw = read(data, n->span);
    auto length = expression(n->children[2], p.sizeTy);
    if (!live()) return neverValue();
    auto *slice = p.slice(element, key == "__vqr_const_slice");
    auto *base = ir.CreateInsertValue(Constant::getNullValue(slice->ir), ir.CreateExtractValue(raw, 1), 0);
    return {slice, ir.CreateInsertValue(base, read(length, n->span), 1)};
  }
  if (key != "__vqr_free" && key != "__vqr_slot_take" && key != "__vqr_slot_write" &&
      key != "__vqr_slot_drop")
    fail(n->span, "unknown standard-library intrinsic");
  arity(key == "__vqr_slot_write" ? 2 : 1);
  auto data = expression(n->children[1], ptr);
  if (!live()) return neverValue();
  auto *address = read(data, n->span);
  if (key == "__vqr_free") {
    // A freestanding empty Vec can be destroyed without linking an allocator.
    if (heap)
      freeAllocation(address);
    else
      guard(ir, ir.getFalse());
  } else if (key == "__vqr_slot_take") {
    if (!p.movable(element)) fail(n->span, "buffer element cannot be moved");
    return {element, ir.CreateLoad(p.storage(element), address)};
  } else if (key == "__vqr_slot_drop") {
    drop(element, address);
  } else {
    auto value = expression(n->children[2], element);
    if (!live()) return neverValue();
    ir.CreateStore(read(value, n->span), address);
  }
  return voidValue();
}

} // namespace vqr::detail
