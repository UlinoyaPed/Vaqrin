#pragma once

#include "syntax.hpp"

#include <llvm/IR/IRBuilder.h>
#include <llvm/Target/TargetMachine.h>
#include <llvm/TargetParser/Triple.h>

namespace vqr {

class Inputs;

struct Options {
  std::string triple, cpu, features;
  std::string profile = "hosted";
  std::string emit = "exe";
  unsigned optimization = 0;
  bool debug = false;
  bool check = false;
};

struct Number {
  llvm::APInt integer{1, 0};
  std::string suffix;
  std::string decimal;
  bool floating = false;
};

Number parseNumber(std::string_view text);
std::optional<llvm::APInt> mathInteger(const Ast &node, bool diagnose = false);
bool fits(const llvm::APInt &signedNumber, unsigned width, bool isSigned);
llvm::Value *binaryNumber(llvm::IRBuilder<> &builder, std::string_view op, llvm::Value *left,
                          llvm::Value *right, bool isSigned, bool rightSigned);
void guard(llvm::IRBuilder<> &builder, llvm::Value *valid);

llvm::Constant *interpret(llvm::Function &function, std::vector<llvm::Constant *> args, Span span);

std::unique_ptr<llvm::TargetMachine> makeTarget(Options &options);
std::unique_ptr<llvm::Module> compile(llvm::LLVMContext &context, llvm::TargetMachine &target,
                                      const Options &options, Inputs &inputs);
} // namespace vqr
