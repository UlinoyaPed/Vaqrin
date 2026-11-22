#include "compiler_internal.hpp"

namespace vqr::detail {
namespace {

enum class Passing { Scalar, Coerced, Indirect, ByValue };

struct Piece {
  llvm::Type *type;
  std::uint64_t offset;
};

struct CValue {
  Type *source;
  Passing passing = Passing::Scalar;
  SmallVector<Piece, 2> pieces;
  bool alignedHfa = false;

  bool indirect() const { return passing == Passing::Indirect || passing == Passing::ByValue; }
};

struct Signature {
  FunctionType *type;
  AttributeList attributes;
  CValue result;
  std::vector<CValue> params;
};

bool scalar(Type *type) {
  return (type->kind == Kind::Int &&
          (type->bits == 8 || type->bits == 16 || type->bits == 32 || type->bits == 64)) ||
         type->kind == Kind::Float ||
         (type->kind == Kind::Pointer && type->ir->isPointerTy()) ||
         (type->kind == Kind::Function && type->cAbi);
}

bool cField(Program &program, Type *type, Span span) {
  if (scalar(type)) return type->kind != Kind::Float || type->bits == 32 || type->bits == 64;
  if (type->kind == Kind::Array) return type->count != 0 && cField(program, type->parts[0], span);
  if (type->kind != Kind::Class || !type->cAbi) return false;
  program.ensureSized(type, span);
  return !type->fields.empty() && !program.drops(type) && !program.customCopy(type) &&
         std::ranges::all_of(type->fields, [&](const auto &field) {
           return cField(program, field.type, span);
         });
}

std::uint64_t sizeOf(const DataLayout &layout, Type *type) {
  return layout.getTypeAllocSize(type->ir).getFixedValue();
}

bool homogeneous(Type *type, llvm::Type *&base, unsigned &count) {
  if (type->kind == Kind::Float) {
    if ((base && base != type->ir) || count == 4) return false;
    base = type->ir;
    ++count;
    return true;
  }
  if (type->kind == Kind::Array) {
    if (type->count > 4) return false;
    for (unsigned i = 0; i < type->count; ++i)
      if (!homogeneous(type->parts[0], base, count)) return false;
    return true;
  }
  if (type->kind != Kind::Class) return false;
  for (const auto &field : type->fields)
    if (!homogeneous(field.type, base, count)) return false;
  return true;
}

struct Eightbyte {
  bool integer = false;
  unsigned doubles = 0, floats = 0, used = 0;
};

void classifySysV(const DataLayout &layout, Type *type, std::uint64_t offset,
                  std::array<Eightbyte, 2> &slots) {
  if (type->kind == Kind::Class) {
    auto *structure = layout.getStructLayout(cast<StructType>(type->ir));
    for (const auto &field : type->fields)
      classifySysV(layout, field.type, offset + structure->getElementOffset(field.index), slots);
  } else if (type->kind == Kind::Array) {
    auto *element = type->parts[0];
    auto stride = sizeOf(layout, element);
    for (std::uint64_t i = 0; i < type->count; ++i)
      classifySysV(layout, element, offset + i * stride, slots);
  } else {
    auto &slot = slots[offset / 8];
    slot.used = std::max(slot.used, static_cast<unsigned>(offset % 8 + sizeOf(layout, type)));
    if (type->kind != Kind::Float) slot.integer = true;
    else if (type->bits == 32) ++slot.floats;
    else ++slot.doubles;
  }
}

CValue classify(Program &program, Type *type, bool result, unsigned &gpr, unsigned &sse, Span span) {
  CValue out{type, Passing::Scalar, {}, false};
  auto triple = program.targetTriple();
  auto &context = program.context;
  auto &layout = program.module->getDataLayout();
  bool sysv = triple.getArch() == Triple::x86_64 && !triple.isOSWindows();
  if (type == program.voidTy && result) return out;
  if (scalar(type)) {
    if (sysv && !result) {
      auto &registers = type->kind == Kind::Float ? sse : gpr;
      if (registers != 0) --registers;
    }
    out.pieces.push_back({type->ir, 0});
    return out;
  }
  if (type->kind != Kind::Class || !cField(program, type, span))
    fail(span, "C ABI requires a supported scalar or a nonempty, trivial #repr: C aggregate", "E290");
  if (!(triple.getArch() == Triple::x86_64 || triple.getArch() == Triple::aarch64) ||
      !layout.isLittleEndian())
    fail(span, "C aggregate ABI is unavailable on this target", "E290");

  const auto bytes = sizeOf(layout, type);
  out.passing = Passing::Coerced;
  auto integer = [&](unsigned bits) { return IntegerType::get(context, bits); };

  if (sysv) {
    if (bytes <= 16) {
      std::array<Eightbyte, 2> slots{};
      classifySysV(layout, type, 0, slots);
      unsigned gp = 0, fp = 0;
      for (unsigned i = 0; i < slots.size(); ++i) {
        const auto &slot = slots[i];
        if (slot.used == 0) continue;
        llvm::Type *piece;
        if (slot.integer) {
          ++gp;
          piece = integer(slot.used * 8);
        } else {
          ++fp;
          piece = slot.doubles != 0 ? llvm::Type::getDoubleTy(context) : llvm::Type::getFloatTy(context);
          if (slot.floats == 2) piece = FixedVectorType::get(piece, 2);
        }
        out.pieces.push_back({piece, i * 8});
      }
      if (result || (gp <= gpr && fp <= sse)) {
        if (!result) {
          gpr -= gp;
          sse -= fp;
        }
        return out;
      }
    }
    out.passing = result ? Passing::Indirect : Passing::ByValue;
  } else if (triple.getArch() == Triple::x86_64) {
    if (bytes == 1 || bytes == 2 || bytes == 4 || bytes == 8) {
      out.pieces.push_back({integer(bytes * 8), 0});
      return out;
    }
    out.passing = Passing::Indirect;
  } else {
    llvm::Type *base = nullptr;
    unsigned count = 0;
    if (homogeneous(type, base, count)) {
      out.pieces.push_back({result ? type->ir : ArrayType::get(base, count), 0});
      out.alignedHfa = !result && triple.isOSLinux();
      return out;
    }
    if (bytes <= 16) {
      if (bytes <= 8) out.pieces.push_back({integer(result ? bytes * 8 : 64), 0});
      else out.pieces.push_back({ArrayType::get(integer(64), 2), 0});
      return out;
    }
    out.passing = Passing::Indirect;
  }
  out.pieces.clear();
  if (sysv && result) --gpr;
  return out;
}

Signature cSignature(Program &program, Type *result, ArrayRef<Type *> params, Span span) {
  unsigned gpr = 6, sse = 8;
  auto ret = classify(program, result, true, gpr, sse, span);
  auto &context = program.context;
  auto &layout = program.module->getDataLayout();
  auto *pointer = PointerType::getUnqual(context);
  SmallVector<llvm::Type *> args;
  SmallVector<std::pair<unsigned, AttributeSet>> attrs;
  auto extend = [&](Type *type, unsigned index) {
    if (program.extendCNarrowInteger() && type->kind == Kind::Int && type->bits < 32)
      attrs.emplace_back(index, AttributeSet::get(context, llvm::Attribute::get(context,
          type->sign ? llvm::Attribute::SExt : llvm::Attribute::ZExt)));
  };
  llvm::Type *output = llvm::Type::getVoidTy(context);
  if (ret.indirect()) {
    AttrBuilder attr(context);
    attr.addStructRetAttr(result->ir).addAttribute(llvm::Attribute::NoAlias)
        .addAlignmentAttr(layout.getABITypeAlign(result->ir));
    attrs.emplace_back(AttributeList::FirstArgIndex, AttributeSet::get(context, attr));
    args.push_back(pointer);
  } else if (ret.pieces.size() == 1) {
    output = ret.pieces.front().type;
    extend(result, AttributeList::ReturnIndex);
  } else if (ret.pieces.size() > 1) {
    SmallVector<llvm::Type *> pieces;
    for (const auto &piece : ret.pieces) pieces.push_back(piece.type);
    output = StructType::get(context, pieces);
  }

  std::vector<CValue> arguments;
  for (auto *type : params) {
    auto part = classify(program, type, false, gpr, sse, span);
    auto index = AttributeList::FirstArgIndex + args.size();
    if (part.indirect()) {
      if (part.passing == Passing::ByValue) {
        AttrBuilder attr(context);
        attr.addByValAttr(type->ir).addAlignmentAttr(
            std::max(Align(8), layout.getABITypeAlign(type->ir)));
        attrs.emplace_back(index, AttributeSet::get(context, attr));
      }
      args.push_back(pointer);
    } else {
      if (part.alignedHfa) {
        AttrBuilder attr(context);
        attr.addStackAlignmentAttr(Align(8));
        attrs.emplace_back(index, AttributeSet::get(context, attr));
      }
      extend(type, index);
      for (const auto &piece : part.pieces) args.push_back(piece.type);
    }
    arguments.push_back(std::move(part));
  }
  return {FunctionType::get(output, args, false), AttributeList::get(context, attrs),
          std::move(ret), std::move(arguments)};
}

AllocaInst *temporary(IRBuilder<> &builder, const DataLayout &layout, const CValue &value) {
  auto bytes = sizeOf(layout, value.source);
  for (const auto &piece : value.pieces)
    bytes = std::max(bytes, piece.offset + layout.getTypeStoreSize(piece.type).getFixedValue());
  auto *storage = ArrayType::get(builder.getInt8Ty(), bytes);
  auto &entry = builder.GetInsertBlock()->getParent()->getEntryBlock();
  IRBuilder<> allocation(&entry, entry.getFirstInsertionPt());
  auto *address = allocation.CreateAlloca(storage);
  address->setAlignment(std::max(Align(16), layout.getABITypeAlign(value.source->ir)));
  builder.CreateStore(ConstantAggregateZero::get(storage), address);
  return address;
}

SmallVector<Value *, 2> toC(IRBuilder<> &builder, const DataLayout &layout, const CValue &type, Value *value) {
  if (type.passing == Passing::Scalar) return {value};
  auto *storage = temporary(builder, layout, type);
  builder.CreateStore(value, storage);
  if (type.indirect()) return {storage};
  SmallVector<Value *, 2> values;
  for (const auto &piece : type.pieces) {
    auto *address = builder.CreateConstGEP1_64(builder.getInt8Ty(), storage, piece.offset);
    values.push_back(builder.CreateAlignedLoad(piece.type, address, Align(1)));
  }
  return values;
}

template <class Next>
Value *fromC(IRBuilder<> &builder, const DataLayout &layout, const CValue &type, Next next) {
  if (type.passing == Passing::Scalar) return next();
  if (type.indirect()) return builder.CreateLoad(type.source->ir, next());
  auto *storage = temporary(builder, layout, type);
  for (const auto &piece : type.pieces) {
    auto *address = builder.CreateConstGEP1_64(builder.getInt8Ty(), storage, piece.offset);
    builder.CreateAlignedStore(next(), address, Align(1));
  }
  return builder.CreateLoad(type.source->ir, storage);
}

Value *callC(IRBuilder<> &builder, const DataLayout &layout, const Signature &sig,
             Value *target, ArrayRef<Value *> args) {
  SmallVector<Value *> lowered;
  Value *result = nullptr;
  if (sig.result.indirect()) {
    result = temporary(builder, layout, sig.result);
    lowered.push_back(result);
  }
  for (unsigned i = 0; i < args.size(); ++i)
    llvm::append_range(lowered, toC(builder, layout, sig.params[i], args[i]));
  auto *call = builder.CreateCall(sig.type, target, lowered);
  call->setAttributes(sig.attributes);
  if (result) return builder.CreateLoad(sig.result.source->ir, result);
  if (sig.result.passing == Passing::Scalar || sig.result.pieces.size() == 1)
    return sig.result.passing == Passing::Scalar ? call :
        fromC(builder, layout, sig.result, [&]() -> Value * { return call; });
  unsigned i = 0;
  return fromC(builder, layout, sig.result, [&] { return builder.CreateExtractValue(call, i++); });
}

} // namespace

bool Program::hasAggregateCAbi(Type *result, ArrayRef<Type *> params) {
  return result->kind == Kind::Class || std::ranges::any_of(params, [](auto *part) {
    return part->kind == Kind::Class;
  });
}

void Program::validateCAbi(Type *result, ArrayRef<Type *> params, Span span) {
  if (hasAggregateCAbi(result, params)) {
    (void)cSignature(*this, result, params, span);
    return;
  }
  if ((result != voidTy && !scalar(result)) || !std::ranges::all_of(params, scalar))
    fail(span, "C ABI requires a supported scalar or a nonempty, trivial #repr: C aggregate", "E290");
}

Function *Program::bridgeC(Function *body, Type *result, ArrayRef<Type *> params, Definition definition) {
  auto &decl = *definition.decl;
  auto sig = cSignature(*this, result, params, decl.span);
  auto &layout = module->getDataLayout();
  auto *bridge = Function::Create(sig.type, GlobalValue::ExternalLinkage, decl.name, *module);
  bridge->setAttributes(sig.attributes);
  bridge->setDoesNotThrow();
  if (decl.external) {
    IRBuilder<> builder(BasicBlock::Create(context, "entry", body));
    SmallVector<Value *> args;
    for (auto &arg : body->args()) args.push_back(&arg);
    auto *value = callC(builder, layout, sig, bridge, args);
    if (result == voidTy) builder.CreateRetVoid();
    else builder.CreateRet(value);
  } else {
    IRBuilder<> builder(BasicBlock::Create(context, "entry", bridge));
    auto arg = bridge->arg_begin();
    auto *destination = sig.result.indirect() ? &*arg++ : nullptr;
    SmallVector<Value *> args;
    for (const auto &part : sig.params)
      args.push_back(fromC(builder, layout, part, [&] { return &*arg++; }));
    auto *value = builder.CreateCall(body, args);
    if (result == voidTy) builder.CreateRetVoid();
    else if (destination) {
      builder.CreateStore(value, destination);
      builder.CreateRetVoid();
    } else {
      auto converted = toC(builder, layout, sig.result, value);
      if (converted.size() == 1) builder.CreateRet(converted.front());
      else {
        Value *packed = PoisonValue::get(sig.type->getReturnType());
        for (unsigned i = 0; i < converted.size(); ++i) packed = builder.CreateInsertValue(packed, converted[i], i);
        builder.CreateRet(packed);
      }
    }
  }
  return bridge;
}

Value *Program::callIndirectC(IRBuilder<> &builder, Value *target, Type *result, ArrayRef<Type *> params,
                              ArrayRef<Value *> args, Span span) {
  return callC(builder, module->getDataLayout(), cSignature(*this, result, params, span), target, args);
}

} // namespace vqr::detail
