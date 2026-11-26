#pragma once

#include "syntax.hpp"

#include <array>
#include <chrono>
#include <set>
#include <tuple>

#include <llvm/ADT/STLFunctionalExtras.h>
#include <llvm/IR/IRBuilder.h>
#include <llvm/Target/TargetMachine.h>
#include <llvm/TargetParser/Triple.h>

namespace vqr {

class Inputs;

struct EditorHints {
  enum class BindingKind { Local, Short, Loop, Pattern };
  struct Binding {
    std::size_t begin, end;
    std::string type;
    BindingKind kind;
  };
  std::string source;
  std::vector<Binding> bindings;
  bool symbols = false;
  bool workspace = false;
  // Byte ranges for each occurrence and its declaration in the selected source.
  std::set<std::tuple<std::size_t, std::size_t, std::size_t, bool>> occurrences;
  std::set<std::tuple<std::string, std::size_t, std::size_t, std::string, std::size_t, bool>>
      workspaceOccurrences;

  void index(Span occurrence, Span declaration, bool definition) {
    if (!occurrence.source || !declaration.source || occurrence.begin == occurrence.end) return;
    if (workspace) {
      workspaceOccurrences.emplace(occurrence.source->path, occurrence.begin, occurrence.end,
                                   declaration.source->path, declaration.begin, definition);
    } else if (occurrence.source == declaration.source && occurrence.source->path == source) {
      occurrences.emplace(occurrence.begin, occurrence.end, declaration.begin, definition);
    }
  }
};

struct CompileTimings {
  using Clock = std::chrono::steady_clock;
  enum Phase { Other, Types, Instantiation, Ctfe, Lowering, Count };
  std::array<Clock::duration, Count> durations{};
  Phase active = Other;
  Clock::time_point started = Clock::now();

  void advance(Phase next) {
    auto now = Clock::now();
    durations[active] += now - started;
    active = next;
    started = now;
  }

  struct Scope {
    CompileTimings *timings;
    Phase previous = Other;
    Scope(CompileTimings *timings, Phase next)
        : timings(timings && timings->active != next ? timings : nullptr) {
      if (this->timings) {
        previous = timings->active;
        timings->advance(next);
      }
    }
    ~Scope() {
      if (timings) timings->advance(previous);
    }
  };
};

struct Options {
  std::string triple, cpu, features;
  std::string profile = "hosted";
  std::string emit = "exe";
  unsigned optimization = 0;
  bool debug = false;
  bool check = false;
  EditorHints *editorHints = nullptr;
  CompileTimings *compileTimings = nullptr;
  bool auditGenericCopy = false;
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
void guard(llvm::IRBuilder<> &builder, llvm::Value *valid, llvm::function_ref<void()> report = {});

llvm::Constant *interpret(llvm::Function &function, std::vector<llvm::Constant *> args, Span span);

std::unique_ptr<llvm::TargetMachine> makeTarget(Options &options);
std::unique_ptr<llvm::Module> compile(llvm::LLVMContext &context, llvm::TargetMachine &target,
                                      const Options &options, Inputs &inputs);
} // namespace vqr
