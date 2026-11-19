#include "codegen.hpp"

#include <llvm/Analysis/ConstantFolding.h>
#include <llvm/Analysis/ValueTracking.h>
#include <llvm/IR/ConstantFold.h>
#include <llvm/IR/IntrinsicInst.h>
#include <llvm/IR/Module.h>

#include <unordered_map>
#include <variant>

namespace vqr {
namespace {
using namespace llvm;

struct Cell;
struct Address {
  std::weak_ptr<Cell> cell;
  std::vector<unsigned> path;
};
using Data = std::variant<Constant *, Address>;
struct Cell {
  Data value;
};

class Interpreter {
public:
  explicit Interpreter(Span span) : span(span) {}
  Data call(Function &fn, ArrayRef<Data> args, unsigned depth);

private:
  Span span;
  unsigned fuel = 1000000;
  std::vector<std::string> stack;

  [[noreturn]] void error(std::string message) const {
    for (auto it = stack.rbegin(); it != stack.rend(); ++it)
      message += "\n  in " + *it;
    fail(span, message, "E220");
  }

  Constant *constant(const Data &data) const {
    if (auto p = std::get_if<Constant *>(&data)) {
      if (isa<PoisonValue, UndefValue>(*p))
        error("uninitialized value or trap during compile-time evaluation");
      return *p;
    }
    error("compile-time local address cannot be serialized as a constant");
  }

  Data load(const Address &address) const {
    auto cell = address.cell.lock();
    if (!cell) error("compile-time use of expired storage");
    if (address.path.empty()) return cell->value;
    auto *value = constant(cell->value);
    for (unsigned i : address.path) {
      value = value->getAggregateElement(i);
      if (!value) error("invalid compile-time aggregate access");
    }
    return value;
  }

  void store(const Address &address, Data value) const {
    auto cell = address.cell.lock();
    if (!cell) error("compile-time use of expired storage");
    if (address.path.empty())
      cell->value = std::move(value);
    else {
      auto *base = std::get_if<Constant *>(&cell->value);
      if (!base) error("invalid compile-time aggregate store");
      auto *updated = ConstantFoldInsertValueInstruction(*base, constant(value), address.path);
      if (!updated) error("invalid compile-time aggregate store");
      cell->value = updated;
    }
  }
};

Data Interpreter::call(Function &fn, ArrayRef<Data> args, unsigned depth) {
  if (depth > 128) error("compile-time recursion limit exceeded");
  if (fn.isDeclaration()) error("compile-time call requires a Vaqrin definition: " + fn.getName().str());
  stack.push_back(fn.getName().str());
  struct Pop {
    std::vector<std::string> &stack;
    ~Pop() { stack.pop_back(); }
  } pop{stack};
  const auto &layout = fn.getParent()->getDataLayout();
  std::unordered_map<const Value *, Data> values;
  std::vector<std::shared_ptr<Cell>> storage;
  unsigned arg = 0;
  for (const auto &p : fn.args())
    values.emplace(&p, args[arg++]);
  auto get = [&](Value *v) -> Data {
    if (auto *c = dyn_cast<Constant>(v)) return c;
    auto it = values.find(v);
    if (it == values.end()) error("missing compile-time instruction value");
    return it->second;
  };

  BasicBlock *from = nullptr;
  BasicBlock *block = &fn.getEntryBlock();
  for (;;) {
    std::vector<std::pair<const PHINode *, Data>> incoming;
    for (const auto &phi : block->phis())
      incoming.emplace_back(&phi, get(phi.getIncomingValueForBlock(from)));
    for (auto &[phi, value] : incoming)
      values[phi] = std::move(value);
    auto *next = static_cast<BasicBlock *>(nullptr);
    for (auto &inst : *block) {
      if (inst.isAtomic()) error("atomic operation is unavailable during compile-time evaluation");
      if (!fuel--) error("compile-time instruction budget exhausted");
      if (isa<PHINode>(inst)) continue;
      if (auto *br = dyn_cast<BranchInst>(&inst)) {
        next = br->getSuccessor(!br->isUnconditional() &&
                                !cast<ConstantInt>(constant(get(br->getCondition())))->isOne());
        break;
      }
      if (auto *dispatch = dyn_cast<SwitchInst>(&inst)) {
        auto *test = cast<ConstantInt>(constant(get(dispatch->getCondition())));
        next = dispatch->findCaseValue(test)->getCaseSuccessor();
        break;
      }
      if (auto *ret = dyn_cast<ReturnInst>(&inst)) {
        if (ret->getReturnValue()) return get(ret->getReturnValue());
        return ConstantAggregateZero::get(StructType::get(fn.getContext()));
      }
      if (isa<UnreachableInst>(inst)) error("trap during compile-time evaluation");
      if (auto *a = dyn_cast<AllocaInst>(&inst)) {
        storage.push_back(std::make_shared<Cell>(Cell{PoisonValue::get(a->getAllocatedType())}));
        values[a] = Address{storage.back(), {}};
      } else if (auto *i = dyn_cast<LoadInst>(&inst)) {
        if (i->isVolatile() || i->isAtomic()) error("volatile or atomic access is forbidden at compile time");
        auto source = get(i->getPointerOperand());
        if (auto *address = std::get_if<Address>(&source))
          values[i] = load(*address);
        else {
          auto *pointer = constant(source);
          auto *global = dyn_cast<GlobalVariable>(getUnderlyingObject(pointer));
          if (!global || !global->isConstant() || !global->hasInitializer())
            error("compile-time read requires immutable initialized storage");
          auto *value = ConstantFoldLoadFromConstPtr(pointer, i->getType(), layout);
          if (!value) error("compile-time read is outside initialized static storage");
          values[i] = value;
        }
      } else if (auto *i = dyn_cast<StoreInst>(&inst)) {
        if (i->isVolatile() || i->isAtomic()) error("volatile or atomic access is forbidden at compile time");
        auto target = get(i->getPointerOperand());
        auto *address = std::get_if<Address>(&target);
        if (!address) error("compile-time write requires local storage");
        store(*address, get(i->getValueOperand()));
      } else if (auto *i = dyn_cast<GetElementPtrInst>(&inst)) {
        auto pointer = get(i->getPointerOperand());
        auto *address = std::get_if<Address>(&pointer);
        if (!address) {
          auto *base = constant(pointer);
          auto *global = dyn_cast<GlobalVariable>(getUnderlyingObject(base));
          if (!global || !global->isConstant())
            error("compile-time pointer arithmetic requires local or immutable storage");
          std::vector<Constant *> operands{base};
          for (auto &index : i->indices())
            operands.push_back(constant(get(index.get())));
          auto *computed = ConstantFoldInstOperands(i, operands, layout);
          if (!computed) error("invalid compile-time static address");
          values[i] = computed;
          continue;
        }
        auto index = i->idx_begin();
        if (!cast<ConstantInt>(constant(get(index->get())))->isZero())
          error("compile-time pointer arithmetic leaves its storage object");
        for (++index; index != i->idx_end(); ++index) {
          auto n = cast<ConstantInt>(constant(get(index->get())))->getValue();
          if (n.getActiveBits() > 32) error("compile-time index is out of bounds");
          address->path.push_back(static_cast<unsigned>(n.getZExtValue()));
        }
        values[i] = pointer;
      } else if (auto *i = dyn_cast<CallInst>(&inst)) {
        auto *callee = dyn_cast<Function>(constant(get(i->getCalledOperand())));
        if (!callee) error("compile-time call target is not a Vaqrin function");
        if (callee->getIntrinsicID() == Intrinsic::trap) error("trap during compile-time evaluation");
        std::vector<Data> callArgs;
        for (auto &a : i->args())
          callArgs.push_back(get(a.get()));
        auto intrinsic = callee->getIntrinsicID();
        if (intrinsic == Intrinsic::trunc || intrinsic == Intrinsic::roundeven ||
            intrinsic == Intrinsic::floor || intrinsic == Intrinsic::ceil) {
          auto value = cast<ConstantFP>(constant(callArgs[0]))->getValueAPF();
          auto rounding = intrinsic == Intrinsic::trunc ? APFloat::rmTowardZero :
                          intrinsic == Intrinsic::floor ? APFloat::rmTowardNegative :
                          intrinsic == Intrinsic::ceil ? APFloat::rmTowardPositive : APFloat::rmNearestTiesToEven;
          value.roundToIntegral(rounding);
          values[i] = ConstantFP::get(fn.getContext(), value);
        } else if (intrinsic == Intrinsic::ctpop || intrinsic == Intrinsic::ctlz ||
                   intrinsic == Intrinsic::cttz || intrinsic == Intrinsic::bitreverse ||
                   intrinsic == Intrinsic::fshl || intrinsic == Intrinsic::fshr) {
          SmallVector<Constant *> arguments;
          for (auto &argument : callArgs) arguments.push_back(constant(argument));
          auto *folded = ConstantFoldCall(i, callee, arguments);
          if (!folded) error("could not evaluate compile-time bit operation");
          values[i] = folded;
        } else
          values[i] = call(*callee, callArgs, depth + 1);
      } else {
        std::vector<Constant *> operands;
        for (auto &operand : inst.operands())
          operands.push_back(constant(get(operand.get())));
        Constant *result = ConstantFoldInstOperands(&inst, operands, layout);
        if (!result) error("unsupported compile-time instruction: " + std::string(inst.getOpcodeName()));
        values[&inst] = result;
      }
    }
    if (!next) error("compile-time block has no terminator");
    from = block;
    block = next;
  }
}
} // namespace

llvm::Constant *interpret(llvm::Function &fn, std::vector<llvm::Constant *> args, Span span) {
  std::vector<Data> inputs(args.begin(), args.end());
  auto result = Interpreter(span).call(fn, inputs, 0);
  auto *value = std::get_if<llvm::Constant *>(&result);
  if (!value || llvm::isa<llvm::PoisonValue, llvm::UndefValue>(*value))
    fail(span, "compile-time value cannot be stored in the program", "E220");
  return *value;
}
} // namespace vqr
