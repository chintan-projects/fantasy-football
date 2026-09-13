---
name: code-simplifier
description: "Post-implementation cleanup skill. Triggers after feature completion, or when user asks to clean up, simplify, or refactor code. Removes dead code, ensures consistency with project standards, and checks for duplicated logic."
---

# Code Simplifier Skill

## Purpose

This skill performs post-implementation cleanup and refactoring. It removes unnecessary code, enforces project standards, eliminates duplication, and ensures code quality meets the project baseline.

## Cleanup Checklist

### 1. Remove Dead Code
- Delete unused functions and variables
- Remove unused imports (check with tools like `autoflake` or `vulture`)
- Remove commented-out code blocks
- Clean up debug statements and temporary test code
- Remove TODO comments unless tracked in backlog

### 2. Simplify Functions
- Ensure cyclomatic complexity does not exceed 10
- Break down complex conditional logic
- Extract nested conditionals to separate functions
- Reduce parameter count (prefer dicts for > 5 params)
- Ensure single responsibility principle

### 3. Enforce Naming Standards
- Match CLAUDE.md naming conventions
- Use snake_case for functions and variables
- Use PascalCase for classes
- Use UPPER_CASE for constants
- Use descriptive names (min. 3 chars, avoid single letters except indices)

### 4. Check for Duplicated Logic
- Search for similar code patterns
- Identify candidates for extraction to `_shared/`
- Consolidate repeated conditionals or algorithms
- Check if shared utilities already exist
- Propose consolidation before implementation

### 5. Verify Error Handling Pattern
- Ensure all critical paths use catch-log-recover pattern
- Verify structured logging in error handlers
- Check that errors include context (what failed, why)
- Confirm no silent failures (all errors logged)
- Verify graceful degradation/fallback on errors

### 6. Run Tests After Simplification
- Execute full test suite after any changes
- Confirm coverage does not decrease
- Verify no regressions introduced
- All tests must pass before marking complete

### 7. Update Documentation
- Update CLAUDE.md if naming conventions changed
- Update function docstrings if signatures changed
- Update README if public API changed
- Update inline comments if logic was refactored

## Workflow

1. **Analyze** - Identify areas for simplification
2. **Plan** - Show user what will be cleaned up and why
3. **Ask Approval** - Get explicit permission before changes
4. **Implement** - Make changes (one area at a time is safer)
5. **Test** - Run tests after each area of changes
6. **Verify** - Confirm all tests pass and no regressions

## When to Trigger

- After feature completion (before merge)
- When code review flags complexity issues
- After major refactoring
- When user asks to "clean up" or "simplify"
- Before performance optimization (understand baseline)
- When onboarding requires understandable code

## Changes Require Approval

**Before making ANY changes:**

1. Show specific code examples
2. Explain why change improves code
3. List all affected files
4. Ask: "Should I proceed with this cleanup?"
5. Wait for explicit user approval ("yes", "proceed", etc.)

## Anti-patterns to Flag

- Cyclomatic complexity > 10
- Functions with > 5 parameters
- Commented-out code (delete, don't keep)
- Multiple responsibilities in one function
- Inconsistent error handling
- Missing error context in logs
- Duplicated business logic
- Unused imports or variables
- Deep nesting (> 3 levels)

## Related Skills

- **verify-app**: Run after simplification to catch regressions
- **model-integration**: May involve simplifying gateway integration
