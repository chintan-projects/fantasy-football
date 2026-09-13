#!/bin/bash
###############################################################################
# App Verification Checks
#
# Runs the 7-item verification checklist:
# 1. pytest with coverage
# 2. grep for hardcoded paths (absolute paths to model files)
# 3. grep for direct model library imports outside _shared/
# 4. validate _models/config.yaml exists and is valid yaml
#
# Plus state recovery, performance, streaming, fallback, and logging checks
#
# Usage:
#   bash run-checks.sh [--skip-tests] [--skip-state-recovery]
###############################################################################

set -e

# Configuration
COVERAGE_THRESHOLD=80
LATENCY_BUDGET_MS=3000
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# State tracking
FAILED_CHECKS=()
PASSED_CHECKS=0
TOTAL_CHECKS=7

# Helper functions
log_check() {
    echo -e "${BLUE}[CHECK $((PASSED_CHECKS+1))/$TOTAL_CHECKS]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((PASSED_CHECKS++))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAILED_CHECKS+=("$1")
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

###############################################################################
# CHECK 1: Run Tests with Coverage
###############################################################################
check_1_tests() {
    log_check "Run tests with coverage (pytest >= $COVERAGE_THRESHOLD%)"

    if ! command -v pytest &> /dev/null; then
        log_warn "pytest not found, skipping test check"
        return 0
    fi

    cd "$PROJECT_ROOT"

    # Run pytest with coverage
    if pytest --cov --cov-report=term-missing --cov-fail-under=$COVERAGE_THRESHOLD \
        --tb=short -v 2>/dev/null; then
        log_pass "All tests passed with sufficient coverage"
        return 0
    else
        log_fail "Test suite failed or coverage below $COVERAGE_THRESHOLD%"
        return 1
    fi
}

###############################################################################
# CHECK 2: State Recovery (Crash Simulation)
###############################################################################
check_2_state_recovery() {
    log_check "State recovery (checkpoint save/restore)"

    # Look for checkpoint/state recovery test
    if [ -f "$PROJECT_ROOT/tests/test_state_recovery.py" ]; then
        if pytest "$PROJECT_ROOT/tests/test_state_recovery.py" -v 2>/dev/null; then
            log_pass "State recovery test passed"
            return 0
        else
            log_fail "State recovery test failed"
            return 1
        fi
    else
        log_warn "State recovery test not found (tests/test_state_recovery.py), skipping"
        return 0
    fi
}

###############################################################################
# CHECK 3: Performance Budget (First-token latency < 3s)
###############################################################################
check_3_performance() {
    log_check "Performance budget: first-token latency < ${LATENCY_BUDGET_MS}ms"

    # Look for latency test
    if [ -f "$PROJECT_ROOT/tests/test_performance.py" ]; then
        if pytest "$PROJECT_ROOT/tests/test_performance.py" -v 2>/dev/null; then
            log_pass "Performance budget verified"
            return 0
        else
            log_fail "Performance test failed"
            return 1
        fi
    else
        log_warn "Performance test not found (tests/test_performance.py), skipping"
        return 0
    fi
}

###############################################################################
# CHECK 4: Streaming Response Verification
###############################################################################
check_4_streaming() {
    log_check "Verify streaming responses"

    # Look for streaming test
    if [ -f "$PROJECT_ROOT/tests/test_streaming.py" ]; then
        if pytest "$PROJECT_ROOT/tests/test_streaming.py" -v 2>/dev/null; then
            log_pass "Streaming responses verified"
            return 0
        else
            log_fail "Streaming test failed"
            return 1
        fi
    else
        log_warn "Streaming test not found (tests/test_streaming.py), skipping"
        return 0
    fi
}

###############################################################################
# CHECK 5: Fallback Chain Verification
###############################################################################
check_5_fallback() {
    log_check "Fallback chain verification"

    # Look for fallback test
    if [ -f "$PROJECT_ROOT/tests/test_fallback.py" ]; then
        if pytest "$PROJECT_ROOT/tests/test_fallback.py" -v 2>/dev/null; then
            log_pass "Fallback chain verified"
            return 0
        else
            log_fail "Fallback test failed"
            return 1
        fi
    else
        log_warn "Fallback test not found (tests/test_fallback.py), skipping"
        return 0
    fi
}

###############################################################################
# CHECK 6: Structured Logging Audit
###############################################################################
check_6_logging() {
    log_check "Structured logging audit"

    # Look for logging test
    if [ -f "$PROJECT_ROOT/tests/test_logging.py" ]; then
        if pytest "$PROJECT_ROOT/tests/test_logging.py" -v 2>/dev/null; then
            log_pass "Logging structure verified"
            return 0
        else
            log_fail "Logging test failed"
            return 1
        fi
    else
        log_warn "Logging test not found (tests/test_logging.py), skipping"
        return 0
    fi
}

###############################################################################
# CHECK 7a: Hardcoded Paths Check
###############################################################################
check_7a_hardcoded_paths() {
    log_check "Check for hardcoded model paths"

    # Search for absolute paths to model files outside of config
    # Pattern: look for /path/to/models or ./models/ in non-config Python files
    found_issues=0

    # Look for absolute paths like /home/user/models or /opt/models
    if grep -r "^[[:space:]]*['\"]\/.*models\/.*['\"]" \
        --include="*.py" \
        --exclude-dir=".git" \
        --exclude-dir="__pycache__" \
        "$PROJECT_ROOT" 2>/dev/null | grep -v config.yaml | grep -v "\.claude"; then
        log_warn "Found potential hardcoded absolute paths to models"
        found_issues=1
    fi

    # Look for relative paths with hard-coded model references
    if grep -r "models\s*=\|model_path\s*=\|MODEL_PATH\s*=" \
        --include="*.py" \
        --exclude-dir=".git" \
        --exclude-dir="__pycache__" \
        "$PROJECT_ROOT" 2>/dev/null | grep -v "config\|_shared\|test" | grep "['\"].*models"; then
        log_warn "Found potential hardcoded model paths in code"
        found_issues=1
    fi

    if [ $found_issues -eq 0 ]; then
        log_pass "No hardcoded model paths found"
        return 0
    else
        log_fail "Hardcoded model paths detected - must use YAML config"
        return 1
    fi
}

###############################################################################
# CHECK 7b: Direct Model Imports Check
###############################################################################
check_7b_model_imports() {
    log_check "Check for direct model library imports"

    found_issues=0

    # Look for direct imports of model libraries outside _shared/
    if grep -r "^from\|^import" \
        --include="*.py" \
        --exclude-dir=".git" \
        --exclude-dir="__pycache__" \
        --exclude-dir="_shared" \
        "$PROJECT_ROOT" 2>/dev/null | \
        grep -E "transformers|llama|llama_cpp|gguf|ctransformers" | \
        grep -v "test\|# " ; then
        log_warn "Found direct imports of model libraries outside _shared/"
        found_issues=1
    fi

    if [ $found_issues -eq 0 ]; then
        log_pass "No direct model library imports found outside _shared/"
        return 0
    else
        log_fail "Direct model imports detected - use gateway from _shared/"
        return 1
    fi
}

###############################################################################
# CHECK 7c: Config YAML Validation
###############################################################################
check_7c_config_validation() {
    log_check "Validate _models/config.yaml"

    config_file="$PROJECT_ROOT/_models/config.yaml"

    if [ ! -f "$config_file" ]; then
        log_fail "Config file not found: $config_file"
        return 1
    fi

    # Basic YAML syntax check (valid YAML can be parsed by a basic script)
    if command -v python3 &> /dev/null; then
        if python3 -c "import yaml; yaml.safe_load(open('$config_file'))" 2>/dev/null; then
            # Run custom validation script if it exists
            if [ -f "$SCRIPT_DIR/../scripts/validate-config.py" ]; then
                if python3 "$SCRIPT_DIR/../scripts/validate-config.py" \
                    --config-path "$config_file" 2>/dev/null | grep -q "VALID"; then
                    log_pass "Config YAML is valid and all models are properly configured"
                    return 0
                else
                    log_fail "Config validation failed - see details above"
                    return 1
                fi
            else
                log_pass "Config YAML is syntactically valid"
                return 0
            fi
        else
            log_fail "Config YAML is invalid (parse error)"
            return 1
        fi
    else
        log_warn "Python3 not found, skipping YAML validation"
        return 0
    fi
}

###############################################################################
# Main Execution
###############################################################################
main() {
    echo ""
    echo "================================================================================"
    echo "                    APP VERIFICATION CHECK SUITE"
    echo "================================================================================"
    echo "Project: $PROJECT_ROOT"
    echo "Time: $(date)"
    echo ""

    # Parse arguments
    skip_tests=0
    skip_state_recovery=0

    while [[ $# -gt 0 ]]; do
        case $1 in
            --skip-tests)
                skip_tests=1
                shift
                ;;
            --skip-state-recovery)
                skip_state_recovery=1
                shift
                ;;
            *)
                shift
                ;;
        esac
    done

    # Run checks
    echo "Running 7 verification checks..."
    echo ""

    [ $skip_tests -eq 0 ] && check_1_tests || log_info "Tests skipped (--skip-tests)"
    [ $skip_state_recovery -eq 0 ] && check_2_state_recovery || log_info "State recovery skipped (--skip-state-recovery)"
    check_3_performance
    check_4_streaming
    check_5_fallback
    check_6_logging
    check_7a_hardcoded_paths
    check_7b_model_imports
    check_7c_config_validation

    # Summary
    echo ""
    echo "================================================================================"
    echo "                        VERIFICATION SUMMARY"
    echo "================================================================================"

    if [ ${#FAILED_CHECKS[@]} -eq 0 ]; then
        echo -e "${GREEN}Status: ALL CHECKS PASSED${NC}"
        echo "Passed: $PASSED_CHECKS/$TOTAL_CHECKS"
        echo ""
        return 0
    else
        echo -e "${RED}Status: SOME CHECKS FAILED${NC}"
        echo "Passed: $PASSED_CHECKS/$TOTAL_CHECKS"
        echo ""
        echo "Failed checks:"
        for failed in "${FAILED_CHECKS[@]}"; do
            echo -e "  ${RED}•${NC} $failed"
        done
        echo ""
        return 1
    fi
}

main "$@"
