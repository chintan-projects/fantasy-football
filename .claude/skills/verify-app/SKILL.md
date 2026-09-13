---
name: verify-app
description: "End-to-end verification skill. Triggers on /verify, after major changes, or when user asks to test the app. Checks state recovery, performance budgets, streaming, error handling, and structured logging."
---

# App Verification Skill

## Purpose

This skill provides comprehensive end-to-end verification of the application. It runs a standardized checklist to ensure code quality, performance compliance, error handling, and proper integration patterns.

## Verification Checklist (7 Items)

### 1. Run Tests
- Execute full test suite with pytest
- Require >= 80% code coverage
- All tests must pass
- Report failures and skip remaining checks if tests fail

### 2. State Recovery (Crash Simulation)
- Trigger checkpoint save
- Simulate application crash
- Verify state restores correctly from last checkpoint
- Confirm no data loss or corruption

### 3. Performance Budget Validation
- Measure first-interaction latency (time to first token from model)
- Must be < 3 seconds
- Log all latency measurements
- Flag if any request exceeds budget

### 4. Streaming Response Verification
- Verify all model responses use streaming
- Check that streaming is not wrapped in a synchronous layer
- Confirm token-by-token output works end-to-end
- Test with large responses (1000+ tokens)

### 5. Fallback Chain Verification
- Disable primary model
- Verify application falls back to secondary model
- Confirm fallback chain references are correct
- Check all fallback models are properly configured

### 6. Structured Logging Audit
- Inspect all log output for proper JSON structure
- Verify latency metadata is logged
- Check log levels are appropriate (DEBUG, INFO, WARN, ERROR)
- Confirm no unstructured logging mixed in

### 7. Configuration Hardcoding Check
- Grep for absolute paths to model files outside of config
- Search for direct imports of model libraries outside `_shared/`
- Verify all model configuration is YAML-driven
- No hardcoded model parameters in code

## Failure Handling

**On any check failure:**

1. **Do NOT mark verification as complete**
2. **Report which specific checks failed** (with line numbers/context where applicable)
3. **Suggest fixes** for each failed check
4. **Ask user for approval** before implementing suggested fixes
5. **Do NOT automatically modify code or configuration**

## Success Criteria

All 7 checks must pass for verification to succeed. Verification succeeds only when:

- All tests pass (pytest >= 80% coverage)
- State recovery test completes without data loss
- First-interaction latency < 3000ms
- Streaming responses confirmed working
- Fallback chain triggers and works
- All logs are properly structured JSON
- No hardcoded configurations or direct model imports found

## Usage

Trigger this skill when:

- User explicitly runs `/verify` command
- After major feature additions or refactoring
- Before merging to main/production branches
- When investigating performance or reliability issues
- After changes to model configuration

## Related Skills

- **model-integration**: For model configuration and gateway setup
- **code-simplifier**: For cleaning up code after verification feedback
