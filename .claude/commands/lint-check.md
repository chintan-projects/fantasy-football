Run a full lint and type-check sweep across the project. Report every issue found.

## Steps

1. **Detect project type** by checking for `pyproject.toml`/`setup.py` (Python) and `tsconfig.json`/`package.json` (TypeScript/JavaScript) in the current project directory.

2. **Python checks** (if Python project detected):
   - Run `ruff check .` from the project root. Report all violations grouped by file.
   - Run `mypy --strict .` from the project root. Report all type errors grouped by file.
   - If either tool is not installed, report that and suggest: `pip install ruff mypy --break-system-packages`

3. **TypeScript/JavaScript checks** (if TS/JS project detected):
   - Run `npx tsc --noEmit` from the project root. Report all type errors grouped by file.
   - Run `npx eslint .` from the project root. Report all lint violations grouped by file.
   - If eslint is not configured, report that and suggest: `npm install -D eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin`

4. **Summary table**: For each check that ran, report:
   | Check | Status | Issues Found |
   |-------|--------|-------------|
   | ruff  | PASS/FAIL | count |
   | mypy  | PASS/FAIL | count |
   | tsc   | PASS/FAIL | count |
   | eslint| PASS/FAIL | count |

5. **Auto-fix offer**: If issues were found, ask whether to run auto-fix commands:
   - Python: `ruff check --fix .`
   - JS/TS: `npx eslint --fix .`
   Note: Type errors (mypy/tsc) cannot be auto-fixed — list them individually for manual resolution.

6. **Do NOT mark task as complete** if any type errors remain. Type errors must be fixed manually.

Use $ARGUMENTS to optionally scope to a specific directory or file pattern.
