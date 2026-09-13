# Plan Command

Generate a comprehensive implementation plan before writing any code.

## Process

1. **Read Context**
   - Locate and read relevant CLAUDE.md files in the project
   - Understand coding standards, patterns, and conventions
   - Identify project structure and architectural decisions

2. **Identify Scope**
   - List all files that will be created or modified
   - Organize by category (services, config, tests, docs, etc.)
   - Estimate impact on existing code

3. **Service Coverage Analysis**
   - Check `_shared/` directory for available services
   - Verify whether needed functionality is already available
   - Identify gaps that require new service creation
   - Plan integration points with existing services

4. **Detailed Changes**
   - For each file, specify:
     - What will change and why
     - Which CLAUDE.md rules apply
     - Integration dependencies
     - Breaking changes (if any)
   - Include code snippets for complex changes

5. **Risk Assessment**
   - Identify potential issues:
     - State management implications
     - Streaming vs. non-streaming code paths
     - Model gateway interactions
     - Configuration management
   - Propose mitigation strategies for each risk

6. **Verification Strategy**
   - Define test cases to validate changes
   - Specify manual verification steps
   - List acceptance criteria
   - Identify latency/performance checkpoints

7. **Present & Wait**
   - Present the plan in clear sections
   - Request explicit approval before implementation
   - Collect feedback and revise if needed

## Input

Task description from `$ARGUMENTS`

## Output

Structured plan with approval request. Do not proceed to implementation until user confirms.
