#include "compiler_internal.hpp"

#include <llvm/IR/IntrinsicsAArch64.h>
#include <llvm/IR/IntrinsicsARM.h>

namespace vqr::detail {

ValueAt Body::standardIntrinsic(std::string_view key, const Ast &n, const std::vector<Ast> &types) {
  if (key.starts_with("__vqr_atomic_")) return atomicIntrinsic(key.substr(13), n, types);
  if (key.starts_with("__vqr_bits_")) return bitIntrinsic(key.substr(11), n, types);
  if (key.starts_with("__vqr_thread_")) return threadIntrinsic(key.substr(13), n, types);
  if (key.starts_with("__vqr_task_")) return taskIntrinsic(key.substr(11), n, types);
  if (key.starts_with("__vqr_sync_")) return syncIntrinsic(key.substr(11), n);
  if (key == "__vqr_host_supports_tasks") return {p.boolTy, ir.getInt1(p.hostedLinux64())};
  if (key == "__vqr_host_supports_io") return {p.boolTy, ir.getInt1(p.hostedLinux64() || p.hostedMac64())};
  if (key == "__vqr_host_is_windows") return {p.boolTy, ir.getInt1(p.hostedWindows64())};
  if (key == "__vqr_clock_platform") {
    if (!p.hostedLinux64() && !p.hostedMac64() && !p.hostedWindows64())
      fail(n->span, "native clocks require hosted GNU/Linux, macOS or Windows MSVC on x86-64 or AArch64", "E290");
    return {p.integer(32, true), ir.getInt32(p.hostedWindows64() ? 2 : p.hostedMac64() ? 1 : 0)};
  }
  if (key == "__vqr_posix_would_block") return {p.integer(32, true), ir.getInt32(p.hostedMac64() ? 35 : 11)};
  if (key == "__vqr_default_heap") return {p.boolTy, ir.getInt1(p.heapAvailable())};
  if (key == "__vqr_validate_channel") {
    if (!p.hostedLinux64())
      fail(n->span, "channel requires hosted GNU/Linux x86-64 or AArch64", "E290");
    return voidValue();
  }
  auto arity = [&](unsigned count) {
    if (n->children.size() != count + 1) fail(n->span, "wrong intrinsic argument count");
  };
  if (key == "__vqr_windows_system_entropy" || key == "__vqr_windows_fill_entropy") {
    const bool available = p.hostedWindows64();
    if (key == "__vqr_windows_system_entropy") {
      arity(0);
      return {p.boolTy, ir.getInt1(available)};
    }
    arity(2);
    if (!available)
      fail(n->span, "Windows system entropy requires hosted MSVC x86-64 or AArch64", "E290");
    auto *bytePointer = p.pointer(p.integer(8, false), false);
    auto destination = expression(n->children[1], bytePointer);
    if (!live()) return neverValue();
    auto *buffer = read(destination, n->span);
    auto length = expression(n->children[2], p.integer(32, false));
    if (!live()) return neverValue();
    auto *i32 = ir.getInt32Ty();
    auto *ptr = ir.getPtrTy();
    auto *signature = FunctionType::get(i32, {ptr, ptr, i32, i32}, false);
    auto provider = p.module->getOrInsertFunction("BCryptGenRandom", signature);
    cast<Function>(provider.getCallee())->addFnAttr("vaqrin.bcrypt");
    // The system-preferred provider requires a null algorithm handle.
    constexpr unsigned useSystemPreferredRng = 2;
    auto *status = ir.CreateCall(provider, {ConstantPointerNull::get(ptr), buffer, read(length, n->span),
                                          ir.getInt32(useSystemPreferredRng)});
    return {p.integer(32, true), status};
  }
  if (key == "__vqr_decimal_width") {
    arity(0);
    if (types.size() != 1) fail(n->span, "floating-point parsing requires one target type");
    auto *target = parseType(types[0]);
    if (target->kind != Kind::Float) fail(n->span, "floating-point parsing requires f32 or f64");
    return {p.integer(32, true), ir.getInt32(target->bits)};
  }
  if (key == "__vqr_borrow_from_self") {
    arity(1);
    int self = findLocal("self");
    if (!unsafeDepth || !file.standard || self < 0 || self != returnRoot ||
        (file.package != "coll" && file.package != "cell" && file.package != "sync"))
      fail(n->span, "storage borrow requires an unsafe standard-library receiver contract", "E240");
    auto address = expression(n->children[1]);
    if (!live()) return neverValue();
    if (address.type->kind != Kind::Pointer) fail(n->span, "storage borrow requires a raw pointer");
    if (!address.type->readOnly && locals[self].type->readOnly)
      fail(n->span, "const receiver cannot return a mutable reference", "E230");
    return {p.reference(address.type->parts[0], address.type->readOnly),
            read(address, n->span),
            nullptr,
            false,
            -1,
            false,
            address.temporaryBorrow,
            {self, -1}};
  }
  if (key == "__vqr_encode_system_error" || key == "__vqr_decode_system_error") {
    arity(1);
    constexpr std::uint32_t systemMask = 0xc0000000u, systemTag = 0x40000000u;
    auto *i32 = p.integer(32, true);
    bool encode = key == "__vqr_encode_system_error";
    auto value = expression(n->children[1], encode ? i32 : p.errorTy);
    if (!live()) return neverValue();
    auto *raw = read(value, n->span);
    if (encode) return {p.errorTy, ir.CreateOr(raw, ir.getInt32(systemTag))};
    auto *status = ir.CreateAnd(raw, ir.getInt32(~systemMask));
    auto *present = ir.CreateICmpEQ(ir.CreateAnd(raw, ir.getInt32(systemMask)), ir.getInt32(systemTag));
    auto *type = p.aggregate(Kind::Optional, {i32});
    return {type, ir.CreateSelect(present, wrap(type, status, true).value, Constant::getNullValue(type->ir))};
  }
  if (key == "__vqr_unaligned_load" || key == "__vqr_unaligned_store") {
    bool store = key == "__vqr_unaligned_store";
    arity(store ? 2 : 1);
    if (!unsafeDepth) fail(n->span, "unaligned access requires unsafe", "E240");
    auto reference = expression(n->children[1]);
    if (!live()) return neverValue();
    if (reference.type->kind != Kind::Pointer || (store && reference.type->readOnly))
      fail(n->span, "unaligned access requires a suitably qualified pointer");
    auto *element = reference.type->parts[0];
    if (element->kind != Kind::Int && element->kind != Kind::Float && element->kind != Kind::Bool)
      fail(n->span, "unaligned access requires scalar lanes");
    auto *address = read(reference, n->span);
    if (!store) return {element, ir.CreateAlignedLoad(element->ir, address, Align(1))};
    auto value = expression(n->children[2], element);
    if (!live()) return neverValue();
    ir.CreateAlignedStore(read(value, n->span), address, Align(1));
    return voidValue();
  }
  if (key == "__vqr_pointer_address") {
    arity(1);
    auto pointer = expression(n->children[1]);
    if (!live()) return neverValue();
    if (pointer.type->kind != Kind::Pointer || !pointer.type->ir->isPointerTy())
      fail(n->span, "address conversion requires an ordinary pointer");
    return {p.sizeTy, ir.CreatePtrToInt(read(pointer, n->span), p.sizeTy->ir)};
  }
  if (key == "__vqr_mmio_load" || key == "__vqr_mmio_store" || key == "__vqr_mmio_pointer") {
    auto operation = key == "__vqr_mmio_load" ? "volatileLoad" :
                     key == "__vqr_mmio_store" ? "volatileStore" : "ptrFromInt";
    return builtin(operation, n, types, nullptr);
  }
  if (key == "__vqr_mmio_compiler_barrier" || key == "__vqr_mmio_memory_barrier") {
    arity(0);
    ir.CreateFence(AtomicOrdering::SequentiallyConsistent,
                   key == "__vqr_mmio_compiler_barrier" ? SyncScope::SingleThread : SyncScope::System);
    return voidValue();
  }
  if (key == "__vqr_mmio_instruction_barrier") {
    arity(0);
    auto target = p.targetTriple();
    if (!target.isARM() && !target.isThumb() && !target.isAArch64())
      fail(n->span, "instruction barriers are supported on ARM targets", "E290");
    Intrinsic::ID id = target.isAArch64() ? static_cast<Intrinsic::ID>(Intrinsic::aarch64_isb)
                                         : static_cast<Intrinsic::ID>(Intrinsic::arm_isb);
    ir.CreateCall(Intrinsic::getOrInsertDeclaration(p.module.get(), id), {ir.getInt32(15)});
    return voidValue();
  }
  if (key.starts_with("__vqr_convert_")) {
    auto op = key.substr(14);
    if (op == "float") {
      arity(1);
      if (types.size() != 1) fail(n->span, "toFloat requires one target type");
      auto *to = parseType(types[0]);
      if (to->kind != Kind::Float) fail(n->span, "toFloat requires a floating-point type");
      return castNumber(to, n->children[1], n->span);
    }
    if (op == "trunc") return builtin("truncToInt", n, types, nullptr);
    if (op == "trunc_bits") {
      arity(1);
      if (types.size() != 1) fail(n->span, "truncBits requires one target type");
      auto *target = parseType(types[0]);
      auto value = expression(n->children[1]);
      if (!live()) return neverValue();
      if (target->kind != Kind::Int || value.type->kind != Kind::Int || target->bits >= value.type->bits)
        fail(n->span, "truncBits requires an integer destination narrower than its integer source", "E201");
      return {target, ir.CreateTrunc(read(value, n->span), target->ir)};
    }
    if (op == "floor") return builtin("floorToInt", n, types, nullptr);
    if (op == "ceil") return builtin("ceilToInt", n, types, nullptr);
    if (op == "round") return builtin("roundToInt", n, types, nullptr);
    if (op == "bit_cast") return builtin("bitCast", n, types, nullptr);
    fail(n->span, "unknown conversion operation");
  }
  if (key.starts_with("__vqr_parse_")) {
    auto operation = key.substr(12);
    if (types.size() != 1) fail(n->span, "integer parsing requires one target type");
    auto *target = parseType(types[0]);
    if (target->kind != Kind::Int || target->bits > 128)
      fail(n->span, "integer parsing requires an integer type of at most 128 bits");
    if (operation == "signed") {
      arity(0);
      return {p.boolTy, ir.getInt1(target->sign)};
    }
    auto *wide = p.integer(128, false);
    if (operation == "bits") {
      arity(1);
      auto bits = expression(n->children[1], wide);
      if (!live()) return neverValue();
      return {target, ir.CreateTruncOrBitCast(read(bits, n->span), target->ir)};
    }
    if (operation == "quotient" || operation == "remainder" || operation == "scale") {
      arity(2);
      auto *word = IntegerType::get(ir.getContext(), target->bits <= 32 ? 32 : target->bits <= 64 ? 64 : 128);
      auto value = expression(n->children[1], wide);
      if (!live()) return neverValue();
      auto *amount = ir.CreateTruncOrBitCast(read(value, n->span), word);
      auto radix = expression(n->children[2], p.integer(8, false));
      if (!live()) return neverValue();
      auto *base = ir.CreateZExt(read(radix, n->span), word);
      auto *out = operation == "quotient" ? ir.CreateUDiv(amount, base) :
                  operation == "remainder" ? ir.CreateURem(amount, base) : ir.CreateMul(amount, base);
      return {wide, ir.CreateZExtOrBitCast(out, wide->ir)};
    }
    if (operation != "limit") fail(n->span, "unknown integer parsing operation");
    arity(1);
    auto negative = expression(n->children[1], p.boolTy);
    if (!live()) return neverValue();
    APInt max = target->sign ? APInt::getSignedMaxValue(target->bits) : APInt::getMaxValue(target->bits);
    auto *positiveLimit = ConstantInt::get(ir.getContext(), max.zextOrTrunc(128));
    if (!target->sign) return {wide, positiveLimit};
    auto *negativeLimit = ConstantInt::get(ir.getContext(), APInt::getSignedMinValue(target->bits).zextOrTrunc(128));
    return {wide, ir.CreateSelect(read(negative, n->span), negativeLimit, positiveLimit)};
  }
  if (key == "__vqr_text_borrow_utf8") {
    arity(1);
    if (!unsafeDepth) fail(n->span, "borrowing validated UTF-8 requires unsafe", "E240");
    auto data = expression(n->children[1], p.slice(p.integer(8, false), true));
    if (!live()) return neverValue();
    auto *slice = read(data, n->span);
    auto *view = ir.CreateInsertValue(UndefValue::get(p.viewTy->ir), ir.CreateExtractValue(slice, 0), 0);
    return {p.viewTy, ir.CreateInsertValue(view, ir.CreateExtractValue(slice, 1), 1)};
  }
  if (key == "__vqr_char_code") {
    arity(1);
    auto value = expression(n->children[1], p.charTy);
    if (!live()) return neverValue();
    return {p.integer(32, false), read(value, n->span)};
  }
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
  if (key == "__vqr_alloc_bytes") {
    arity(2);
    if (!p.heapAvailable()) fail(n->span, "this target has no default byte allocator", "E290");
    auto size = expression(n->children[1], p.sizeTy);
    if (!live()) return neverValue();
    auto *bytes = read(size, n->span);
    auto alignment = expression(n->children[2], p.sizeTy);
    if (!live()) return neverValue();
    auto *natural = read(alignment, n->span);
    auto *word = ConstantInt::get(p.sizeTy->ir, p.pointerBits() / 8);
    auto *align = ir.CreateSelect(ir.CreateICmpULT(natural, word), word, natural);
    auto *mask = ir.CreateSub(align, ConstantInt::get(p.sizeTy->ir, 1));
    auto *total = ir.CreateAdd(bytes, mask);
    auto *allocate = block("allocation.bytes"), *end = block("allocation.done");
    auto *start = ir.GetInsertBlock();
    auto *pointer = p.pointer(p.integer(8, false), false);
    auto *optional = p.aggregate(Kind::Optional, {pointer});
    auto *result = p.aggregate(Kind::Result, {optional});
    auto failure = wrap(result, ir.getInt32(0xfffffffc), false);
    ir.CreateCondBr(ir.CreateICmpUGE(total, bytes), allocate, end);
    ir.SetInsertPoint(allocate);
    auto *padded = ir.CreateAnd(total, ir.CreateNot(mask));
    auto *address = allocateAligned(align, padded);
    auto success = wrap(result, wrap(optional, address, true).value, true);
    auto *value = ir.CreateSelect(ir.CreateIsNotNull(address), success.value, failure.value);
    ir.CreateBr(end);
    ir.SetInsertPoint(end);
    auto *out = ir.CreatePHI(result->ir, 2);
    out->addIncoming(value, allocate);
    out->addIncoming(failure.value, start);
    return {result, out};
  }
  if (key == "__vqr_zeroed_byte_array") {
    arity(0);
    if (types.size() != 1) fail(n->span, "zeroed storage requires a byte array type");
    auto *array = parseType(types[0]);
    if (array->kind != Kind::Array || array->parts[0] != p.integer(8, false))
      fail(n->span, "zeroed storage requires a byte array type");
    return {array, Constant::getNullValue(array->ir)};
  }
  if (key == "__vqr_uninitialized") {
    arity(0);
    if (types.size() != 1) fail(n->span, "uninitialized storage requires an element type");
    auto *raw = p.rawStorage(parseType(types[0]), n->span);
    return {raw, UndefValue::get(raw->ir)};
  }
  if (key == "__vqr_raw_address") {
    arity(1);
    auto value = expression(n->children[1]);
    if (!live()) return neverValue();
    if (value.type->kind != Kind::Pointer || value.type->parts[0]->kind != Kind::RawStorage)
      fail(n->span, "expected a pointer to raw storage");
    auto *raw = value.type->parts[0];
    auto *pointer = p.pointer(raw->parts[0], value.type->readOnly);
    return {pointer, ir.CreateStructGEP(raw->ir, read(value, n->span), 0)};
  }
  if (key == "__vqr_little_endian") {
    arity(0);
    return {p.boolTy, ir.getInt1(p.module->getDataLayout().isLittleEndian())};
  }
  if (key == "__vqr_move_bytes") {
    arity(3);
    auto *byte = p.integer(8, false);
    auto destination = expression(n->children[1], p.pointer(byte, false));
    if (!live()) return neverValue();
    auto *output = read(destination, n->span);
    auto source = expression(n->children[2], p.pointer(byte, true));
    if (!live()) return neverValue();
    auto *input = read(source, n->span);
    auto count = expression(n->children[3], p.sizeTy);
    if (!live()) return neverValue();
    ir.CreateMemMove(output, Align(1), input, Align(1), read(count, n->span));
    return voidValue();
  }
  if (key == "__vqr_serde_value_kind") {
    arity(0);
    if (types.size() != 1) fail(n->span, "serde classification requires one type");
    auto *type = parseType(types[0]);
    unsigned code = 0;
    if (type == p.boolTy) code = 1;
    else if (type == p.textTy) code = 2;
    else if (type->kind == Kind::Int) code = type->sign ? 3 : 4;
    else if (type->kind == Kind::Float) code = 5;
    else if (type->kind == Kind::Char) code = 6;
    return {p.integer(8, false), ir.getInt8(code)};
  }
  if (key == "__vqr_has_interface") {
    arity(0);
    if (types.size() != 2) fail(n->span, "hasInterface requires a type and an interface");
    auto *type = parseType(types[0]);
    auto *interface = parseType(types[1]);
    if (interface->kind != Kind::Interface) fail(n->span, "hasInterface requires an interface argument");
    p.ensureSized(type, n->span);
    return {p.boolTy, ir.getInt1(type->conformances.contains(interface))};
  }
  if (key == "__vqr_type_name" || key == "__vqr_alloc_size" || key == "__vqr_alignment" ||
      key == "__vqr_bit_width" || key == "__vqr_copyable" || key == "__vqr_movable" ||
      key == "__vqr_trivial_drop" || key == "__vqr_sendable" || key == "__vqr_shareable") {
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
    if (key == "__vqr_sendable" || key == "__vqr_shareable")
      return {p.boolTy, ir.getInt1(p.transferable(type, key == "__vqr_shareable"))};
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
    auto *address = allocateAligned(size(align), padded);
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
      freeAllocation(address, true);
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
