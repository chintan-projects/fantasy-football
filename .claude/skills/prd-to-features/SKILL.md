---
name: prd-to-features
description: Convert PRD to implementation tasks with effort estimates.
triggers:
  - "break down PRD"
  - "convert to tasks"
  - "implementation plan"
  - "estimate effort"
---

# PRD-to-Features Skill

**Iron Law**: Always estimate effort for each task.

## Steps

1. **Parse PRD** — Read the product requirements:
   - User stories
   - Acceptance criteria
   - Constraints

2. **Extract Features** — Break into granular tasks:
   - One feature = one responsibility
   - Features can be implemented and tested independently
   - Example: "Add user registration" → auth logic, API endpoint, validation, error handling

3. **Estimate Effort**:
   - 1-2 days: simple (straightforward logic, few dependencies)
   - 2-4 days: medium (requires design, multiple components)
   - 4+ days: complex (high risk, many unknowns, integration points)

4. **Output** — Markdown with:
   ```
   ## Feature: User Registration

   ### Tasks
   - Add User table + migration (1 day)
   - Implement hash password utility (0.5 day)
   - Build POST /auth/register endpoint (1.5 days)
   - Add form validation (0.5 day)
   - Write E2E test (1 day)

   Total Effort: 4.5 days

   ### Dependencies
   - Database migrations must run first
   - Requires email service connection
   ```

