---
name: lint-and-types
description: "Enforces type safety and lint-free code across Python and TypeScript projects. Triggers when user asks to check types, fix lint errors, add type annotations, review type safety, or when writing new functions/modules. Also triggers on 'make it type safe', 'add types', 'fix lint', 'type check', or 'strict mode'."
---

## Core Rules — Type Safety

### Python
1. **Every function** must have complete type annotations: all parameters AND return type.
2. **No `Any`** unless justified with an inline comment: `# type: ignore[assignment] — API returns dynamic shape`
3. **Use modern syntax**: `list[str]` not `List[str]`, `str | None` not `Optional[str]` (Python 3.10+).
4. **TypedDict for dicts** with known shapes. Never pass raw `dict` when structure is predictable.
5. **Dataclasses or Pydantic models** for domain objects. No ad-hoc dicts as data containers.
6. **Generic types** for container functions: `def first(items: Sequence[T]) -> T`
7. **Protocols** for structural subtyping (duck typing with type safety).

```python
# ✅ CORRECT
from dataclasses import dataclass

@dataclass
class ModelConfig:
    name: str
    path: str
    context_length: int
    temperature: float = 0.7

async def invoke_model(config: ModelConfig, prompt: str) -> AsyncIterator[str]:
    ...

# ❌ WRONG
def invoke_model(config, prompt):  # No types
    ...

def invoke_model(config: Any, prompt: Any) -> Any:  # Lazy Any
    ...
```

### TypeScript
1. **Strict mode required**: `"strict": true` in tsconfig.json (enables noImplicitAny, strictNullChecks, etc.).
2. **No `any`** — use `unknown` + type narrowing, generics, or proper interfaces.
3. **`interface` for object shapes**, `type` for unions/intersections/primitives.
4. **Discriminated unions** for variant types (prefer over type assertions).
5. **`readonly`** on props/fields that shouldn't mutate.
6. **`as const`** for literal types and tuple inference.
7. **Return types explicit** on exported functions (inferred is OK for private/internal).

```typescript
// ✅ CORRECT
interface ModelConfig {
  readonly name: string;
  readonly path: string;
  readonly contextLength: number;
  readonly temperature?: number;
}

type ModelResult =
  | { status: "success"; data: string }
  | { status: "error"; error: Error }
  | { status: "degraded"; fallback: string };

async function invokeModel(config: ModelConfig, prompt: string): Promise<ModelResult> {
  ...
}

// ❌ WRONG
function invokeModel(config: any, prompt: any): any { ... }

// @ts-ignore  ← Never without justification
```

## Core Rules — Linting

### Python (ruff)
1. No unused imports or variables.
2. No bare `except:` — always catch specific exceptions.
3. No mutable default arguments (`def f(items=[])` — use `None` + sentinel).
4. No `print()` — use the shared logger.
5. No f-strings in logging calls (use lazy formatting: `logger.info("msg", key=value)`).
6. Imports sorted: stdlib → third-party → local, separated by blank lines.
7. No wildcard imports (`from module import *`).

### TypeScript (eslint)
1. No unused variables or imports.
2. No `console.log` in production code — use structured logging.
3. No `==` — always `===`.
4. No `var` — use `const` (preferred) or `let`.
5. Exhaustive switch statements on discriminated unions.
6. No floating promises — always `await` or explicitly handle.
7. Consistent return types — don't mix `undefined` and `void`.

## Workflow: Adding Types to Existing Code

When asked to add types to untyped code:

1. **Read the file** and understand the data flow.
2. **Identify domain objects** — create interfaces/dataclasses for them first.
3. **Type the public API** — exported functions, class methods, module boundaries.
4. **Type internals** — private functions, local variables where inference isn't obvious.
5. **Run the checker** — `mypy --strict` or `tsc --noEmit`. Fix all errors.
6. **Run the linter** — `ruff check` or `eslint`. Fix all warnings.
7. **Run tests** — types should never change runtime behavior. If tests break, you changed logic, not just types.

## Anti-patterns

- Adding `# type: ignore` without an explanation.
- Using `cast()` to bypass type errors instead of fixing the underlying issue.
- Making everything `Optional` to avoid thinking about nullability.
- Using `@ts-ignore` or `@ts-expect-error` without a justification comment.
- Typing a function's return as `Any`/`any` because "it's complicated."
- Overly broad union types that defeat the purpose (`string | number | boolean | null | undefined`).

## Quick Commands

```bash
# Python — full check
ruff check . && mypy --strict .

# Python — auto-fix what's fixable
ruff check --fix . && black --line-length=100 .

# TypeScript — full check
npx tsc --noEmit && npx eslint .

# TypeScript — auto-fix what's fixable
npx eslint --fix . && npx prettier --write "**/*.{ts,tsx}"
```
