# 8+1 Pillars Validation Guide

This document explains the 8+1 Pillars validation for language and conversion skills.

## Table of Contents

- [1. When to Apply Pillars Validation](#1-when-to-apply-pillars-validation)
- [2. The 8 Core Pillars](#2-the-8-core-pillars)
- [3. The 9th Pillar (REPL/Workflow)](#3-the-9th-pillar-replworkflow)
- [4. Scoring System](#4-scoring-system)
- [5. Coverage Thresholds](#5-coverage-thresholds)
- [6. Gap Mitigation Strategies](#6-gap-mitigation-strategies)
- [7. Example Evaluation](#7-example-evaluation)

---

## 1. When to Apply Pillars Validation

Pillars validation applies **only** to skills with names that:

- Start with `lang-` (e.g., `lang-rust-dev`, `lang-python-best-practices`)
- Start with `convert-` (e.g., `convert-python-rust`, `convert-java-kotlin`)

Use the `--pillars` flag to enable:

```bash
uv run python scripts/validate_skill_comprehensive.py ./skills/lang-rust-dev/ --pillars
```

---

## 2. The 8 Core Pillars

Every language skill should cover these 8 fundamental concepts.

### Pillar 1: Module System

**Purpose**: Import/export, visibility, namespacing

**Detection Keywords**:
- `import`, `export`, `module`, `use`, `require`
- `package`, `namespace`, `pub`, `private`

**What to Cover**:
- How to import external dependencies
- How to organize code into modules
- Visibility modifiers (public, private, protected)
- Module path resolution

**Example Section**:
```markdown
## Module System

Rust uses `mod` for module declarations and `use` for imports:

\`\`\`rust
// Declare a module
mod my_module;

// Import from standard library
use std::collections::HashMap;

// Re-export
pub use my_module::SomeType;
\`\`\`
```

### Pillar 2: Error Handling

**Purpose**: Error handling model, exceptions, Result types

**Detection Keywords**:
- `Result`, `Exception`, `Error`, `try`, `catch`
- `?` operator, `unwrap`, `panic`, `throw`

**What to Cover**:
- Primary error handling mechanism
- Recoverable vs unrecoverable errors
- Error propagation patterns
- Custom error types

**Example Section**:
```markdown
## Error Handling

Rust uses `Result<T, E>` for recoverable errors:

\`\`\`rust
fn read_file(path: &str) -> Result<String, std::io::Error> {
    std::fs::read_to_string(path)
}

// Propagate with ? operator
fn process() -> Result<(), Box<dyn Error>> {
    let content = read_file("input.txt")?;
    Ok(())
}
\`\`\`
```

### Pillar 3: Concurrency

**Purpose**: Async, parallelism, synchronization

**Detection Keywords**:
- `async`, `await`, `thread`, `channel`, `spawn`
- `Actor`, `mutex`, `lock`, `atomic`, `parallel`

**What to Cover**:
- Threading model
- Async/await syntax
- Synchronization primitives
- Message passing

**Example Section**:
```markdown
## Concurrency

Rust provides ownership-based thread safety:

\`\`\`rust
use std::thread;
use std::sync::mpsc;

// Spawn thread
let handle = thread::spawn(|| {
    // thread work
});

// Message passing
let (tx, rx) = mpsc::channel();
tx.send(42).unwrap();
\`\`\`
```

### Pillar 4: Metaprogramming

**Purpose**: Macros, reflection, code generation

**Detection Keywords**:
- `macro`, `decorator`, `@`, `derive`, `annotation`
- `quote`, `defmacro`, `#[`, `reflection`

**What to Cover**:
- Macro systems
- Compile-time code generation
- Runtime reflection (if available)
- Attributes/annotations

**Example Section**:
```markdown
## Metaprogramming

Rust uses macros for compile-time code generation:

\`\`\`rust
// Declarative macro
macro_rules! say_hello {
    () => { println!("Hello!") };
}

// Derive macro
#[derive(Debug, Clone)]
struct MyStruct { ... }
\`\`\`
```

### Pillar 5: Zero/Default Values

**Purpose**: Null handling, default values, optionality

**Detection Keywords**:
- `null`, `None`, `nil`, `Option`, `Maybe`
- `default`, `?`, `undefined`, `Optional`

**What to Cover**:
- Null/nil handling approach
- Option/Maybe types
- Default value patterns
- Null safety features

**Example Section**:
```markdown
## Zero/Default Values

Rust uses `Option<T>` instead of null:

\`\`\`rust
let maybe_value: Option<i32> = Some(42);

// Safe access
if let Some(v) = maybe_value {
    println!("Value: {}", v);
}

// Default values
let value = maybe_value.unwrap_or(0);
\`\`\`
```

### Pillar 6: Serialization

**Purpose**: Data encoding/decoding, marshaling

**Detection Keywords**:
- `JSON`, `serde`, `marshal`, `encode`, `decode`
- `parse`, `serialize`, `deserialize`, `pickle`

**What to Cover**:
- JSON serialization
- Binary formats
- Custom serialization
- Recommended libraries

**Example Section**:
```markdown
## Serialization

Rust uses serde for serialization:

\`\`\`rust
use serde::{Serialize, Deserialize};

#[derive(Serialize, Deserialize)]
struct User {
    name: String,
    age: u32,
}

// To JSON
let json = serde_json::to_string(&user)?;

// From JSON
let user: User = serde_json::from_str(&json)?;
\`\`\`
```

### Pillar 7: Build System

**Purpose**: Package management, build systems

**Detection Keywords**:
- `Cargo`, `npm`, `pip`, `mix`, `make`
- `package.json`, `deps`, `go mod`, `Gemfile`

**What to Cover**:
- Package manager usage
- Dependency declaration
- Build configuration
- Project structure

**Example Section**:
```markdown
## Build System

Rust uses Cargo for package management:

\`\`\`toml
# Cargo.toml
[package]
name = "my-project"
version = "0.1.0"

[dependencies]
serde = { version = "1.0", features = ["derive"] }
tokio = { version = "1", features = ["full"] }
\`\`\`

\`\`\`bash
cargo build          # Build project
cargo test           # Run tests
cargo add serde      # Add dependency
\`\`\`
```

### Pillar 8: Testing

**Purpose**: Test framework, assertions, mocking

**Detection Keywords**:
- `test`, `describe`, `it`, `assert`, `expect`
- `mock`, `#[test]`, `pytest`, `jest`

**What to Cover**:
- Test framework syntax
- Assertions
- Test organization
- Mocking patterns

**Example Section**:
```markdown
## Testing

Rust has built-in testing support:

\`\`\`rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_addition() {
        assert_eq!(add(2, 2), 4);
    }

    #[test]
    #[should_panic]
    fn test_panic() {
        panic!("Expected panic");
    }
}
\`\`\`

\`\`\`bash
cargo test           # Run all tests
cargo test test_add  # Run specific test
\`\`\`
```

---

## 3. The 9th Pillar (REPL/Workflow)

**Purpose**: Interactive development, hot reload

**Detection Keywords**:
- `REPL`, `iex`, `ghci`, `clj`, `hot reload`
- `interactive`, `workflow`, `livereload`

### When to Include

Include the 9th pillar when **either** the source OR target language is REPL-centric:

| Language | Include 9th Pillar? | REPL Tool |
|----------|---------------------|-----------|
| Clojure | **Always** | REPL, nREPL |
| Elixir | **Always** | IEx |
| Erlang | **Always** | erl shell |
| Haskell | **Always** | GHCi |
| F# | **Always** | FSI |
| Lisp/Scheme | **Always** | REPL |
| Racket | **Always** | Racket REPL |
| Python | Often | IPython, Jupyter |
| Ruby | Often | IRB, Pry |
| JavaScript | Sometimes | Node REPL |
| Rust | When FROM REPL | evcxr |
| Go | When FROM REPL | gore |

**Example Section**:
```markdown
## Dev Workflow & REPL

Clojure is REPL-driven by design:

\`\`\`clojure
;; Start REPL
$ clj

;; Load namespace
user=> (require '[my.ns :as my])

;; Evaluate expression
user=> (my/process-data {:name "test"})

;; Hot reload changes
user=> (require '[my.ns :as my] :reload)
\`\`\`

Key workflow patterns:
- Evaluate expressions incrementally
- Hot-reload code without restart
- Inspect state at runtime
```

---

## 4. Scoring System

### Individual Pillar Scores

| Score | Status | Criteria |
|-------|--------|----------|
| **1.0** | ✓ Full | Dedicated section with examples AND 3+ keyword matches |
| **0.5** | ~ Partial | 2-4 keyword occurrences, no dedicated section |
| **0.0** | ✗ Missing | 0-1 keyword occurrences |

### Scoring Algorithm

```python
def score_pillar(pillar_name: str, keywords: list[str], body: str) -> float:
    # Count keyword occurrences
    keyword_count = sum(
        len(re.findall(re.escape(kw), body, re.IGNORECASE))
        for kw in keywords
    )

    # Check for dedicated section
    has_section = bool(re.search(rf"##\s*{re.escape(pillar_name)}", body, re.IGNORECASE))

    # Score determination
    if has_section and keyword_count >= 3:
        return 1.0  # Full coverage
    elif keyword_count >= 5:
        return 1.0  # Full coverage (high keyword density)
    elif keyword_count >= 2:
        return 0.5  # Partial coverage
    else:
        return 0.0  # Missing
```

---

## 5. Coverage Thresholds

### For 8 Pillars (Non-REPL Languages)

| Score | Total | Status | Interpretation |
|-------|-------|--------|----------------|
| 8/8 | 100% | Excellent | Complete coverage |
| 6-7.5/8 | 75-94% | Good | Acceptable, minor gaps |
| 4-5.5/8 | 50-69% | Needs Work | Should improve |
| < 4/8 | < 50% | Incomplete | Critical gaps |

### For 9 Pillars (REPL-Centric Languages)

| Score | Total | Status | Interpretation |
|-------|-------|--------|----------------|
| 9/9 | 100% | Excellent | Complete coverage |
| 7-8.5/9 | 78-94% | Good | Acceptable, minor gaps |
| 5-6.5/9 | 56-72% | Needs Work | Should improve |
| < 5/9 | < 56% | Incomplete | Critical gaps |

### Validation Severity

| Coverage | Severity |
|----------|----------|
| < 50% | **MAJOR** - Incomplete skill |
| 50-75% | **MINOR** - Needs improvement |
| > 75% | **PASSED** - Good coverage |

---

## 6. Gap Mitigation Strategies

When a pillar cannot be fully covered (e.g., Go has no macros):

### Strategy 1: Acknowledge the Gap

```markdown
## Metaprogramming

Go does not have a macro system. Code generation is handled via:

- `go generate` directives
- Text templates (`text/template`)
- External tools (e.g., `stringer`)

> **Note:** This is a deliberate design choice for simplicity.
```

### Strategy 2: Provide Alternatives

```markdown
## Metaprogramming

While Go lacks macros, similar goals can be achieved:

| Need | Go Approach |
|------|-------------|
| Code generation | `go generate` |
| Boilerplate reduction | Interfaces, embedding |
| Compile-time validation | Build constraints |
```

### Strategy 3: Cross-Reference

```markdown
## Metaprogramming

Go's approach to code generation is documented at:
- [go generate](https://go.dev/blog/generate)
- [Text templates](https://pkg.go.dev/text/template)

For complex metaprogramming needs, consider using external tools like:
- [jennifer](https://github.com/dave/jennifer) - Code generator
- [protobuf](https://protobuf.dev/) - Schema-driven generation
```

---

## 7. Example Evaluation

### Input: lang-rust-dev Skill

```markdown
---
name: lang-rust-dev
description: Rust development guide with ownership, lifetimes, and async patterns.
---

# Rust Development Guide

## Module System
Use `mod` and `use` for imports...
(15 keyword matches: mod, use, pub, crate, super, etc.)

## Error Handling
Result<T, E> for recoverable errors...
(12 keyword matches: Result, Error, ?, unwrap, etc.)

## Concurrency
Async/await with tokio runtime...
(4 keyword matches: async, await, spawn, thread)

## Metaprogramming
Macros and derive attributes...
(8 keyword matches: macro, derive, #[, attribute, etc.)

## Zero/Default Values
Option<T> instead of null...
(8 keyword matches: Option, None, Some, default, etc.)

## Serialization
Serde for JSON and binary formats...
(10 keyword matches: serde, JSON, serialize, etc.)

## Build System
Cargo for dependencies and builds...
(12 keyword matches: Cargo, toml, build, test, etc.)

## Testing
Built-in #[test] framework...
(15 keyword matches: test, assert, mock, etc.)
```

### Evaluation Output

```
Pillars Coverage: lang-rust-dev
├── Module:          ✓ (1.0) - Full coverage with dedicated section
├── Error:           ✓ (1.0) - Full coverage (12 keyword occurrences)
├── Concurrency:     ~ (0.5) - Partial coverage (4 keyword occurrences)
├── Metaprogramming: ✓ (1.0) - Full coverage with dedicated section
├── Zero/Default:    ✓ (1.0) - Full coverage (8 keyword occurrences)
├── Serialization:   ✓ (1.0) - Full coverage with dedicated section
├── Build:           ✓ (1.0) - Full coverage with dedicated section
└── Testing:         ✓ (1.0) - Full coverage (15 keyword occurrences)

Total: 7.5/8 (93.75%)
Status: Good - Acceptable, minor gaps
Recommendation: Expand Concurrency section with more examples
```

---

## Summary

| Aspect | Requirement |
|--------|-------------|
| **When to Apply** | Skills starting with `lang-` or `convert-` |
| **Core Pillars** | 8 (Module, Error, Concurrency, Meta, Zero, Serde, Build, Test) |
| **9th Pillar** | For REPL-centric languages (Clojure, Elixir, Haskell, etc.) |
| **Full Score** | Dedicated section + 3 keywords, OR 5+ keyword matches |
| **Pass Threshold** | > 75% coverage |
| **Gap Handling** | Acknowledge, provide alternatives, cross-reference |
