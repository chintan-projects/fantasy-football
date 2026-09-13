# Health Check Command

System health verification for all core services and configurations.

## Checks

### 1. Shared Services Import
- Attempt to import each service from `_shared/` directory:
  - `config_loader`
  - `logger`
  - `state_manager`
  - `model_gateway`
  - Any other services in `_shared/`
- Verify imports succeed without errors
- Report status for each service

### 2. Model Configuration
- Verify `_models/config.yaml` exists and is valid YAML
- Check all model paths in config are accessible:
  - Load config values
  - For each model entry, verify path exists
  - Check file permissions allow reading
- Report any missing or inaccessible models
- Validate config schema (required fields present)

### 3. Model Gateway
- Import `model_gateway` module
- Attempt to instantiate gateway with loaded config
- Verify initialization completes without errors
- Confirm gateway can list available models
- Report initialization status

### 4. State Manager
- Import state manager service
- Execute checkpoint cycle:
  - Create test state
  - Write checkpoint
  - Clear in-memory state
  - Restore from checkpoint
  - Verify state matches original
- Confirm cycle completes without data loss
- Report checkpoint/restore status

### 5. Logger
- Import logger service
- Emit test messages at each level (debug, info, warning, error)
- Verify output is valid JSON
- Confirm structured fields present:
  - `timestamp`
  - `level`
  - `message`
  - `service` (if applicable)
- Report logger status and output sample

### 6. Config Loader
- Import config loader
- Verify it reads from environment variables
- Test loading a sample config key
- Confirm values resolve from env (not hardcoded)
- Check config merging if multiple sources
- Report config loader status

### 7. Cross-Service Validation
- Verify no circular dependencies between services
- Confirm all services use consistent logging
- Check all services respect config governance
- Validate model gateway is sole access point to models

## Output

Report each service as:
```
✓ HEALTHY: [Service Name]
✗ UNHEALTHY: [Service Name] - [Reason]
```

Provide summary:
- Total services checked
- Services healthy vs. unhealthy
- Critical failures that block operation
- Recommended actions for any failures

Example output format:
```
=== System Health Check ===

✓ HEALTHY: config_loader
✓ HEALTHY: logger (emitting structured JSON)
✓ HEALTHY: state_manager (checkpoint cycle successful)
✗ UNHEALTHY: model_gateway - Path not found: /models/gpt-4/weights.bin

Summary: 3/4 services healthy
Critical issues: 1 (model path missing)
Recommended: Verify model paths in config.yaml
```
