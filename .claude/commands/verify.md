# Verify Command

Comprehensive verification loop to validate code quality, architecture, and runtime behavior.

## Checks

### 1. Test Suite
- Execute full test suite
- Report pass/fail status for all test groups
- Capture coverage metrics
- List any failing tests with error details

### 2. Code Standards
- Check modified files follow CLAUDE.md conventions
- Verify naming conventions (snake_case, CamelCase as appropriate)
- Ensure proper docstrings and type hints
- Validate import organization

### 3. Configuration & Imports
- Grep for absolute file paths (should use config/env vars)
- Search for hardcoded model paths or model identifiers
- Verify all model imports go through gateway
- Check no direct LLM instantiation outside gateway module
- Ensure config values loaded from env, not hardcoded

### 4. State Management
- Test checkpoint/restore cycle
- Verify state serialization/deserialization
- Confirm state recovery after simulated failure
- Check no state loss in streaming operations

### 5. Streaming Verification
- Identify all LLM response paths
- Confirm streaming enabled on all response handlers
- Verify no blocking calls in streaming pipelines
- Check proper stream closure and cleanup

### 6. Latency Analysis
- Measure first-interaction latency (cold start)
- Must be < 3 seconds
- Report baseline and any anomalies
- Identify bottlenecks if threshold exceeded

### 7. Error Handling
- Search for silent error swallowing (try/except with pass)
- Verify errors logged with context
- Check error recovery doesn't lose state
- Confirm user-facing errors have helpful messages

### 8. Logging
- Verify all services emit structured JSON logs
- Check log level assignments (debug, info, warning, error)
- Confirm correlation IDs present in multi-step operations
- Validate no sensitive data in logs

## Output

Report each check as:
```
✓ PASS: [Check Name]
✗ FAIL: [Check Name] - [Reason]
```

**Do not mark complete if any check fails.** Report failures and halt for remediation.
