---
name: improve-codebase-architecture
description: Find refactoring opportunities, identify shallow modules and coupling issues.
triggers:
  - "improve architecture"
  - "refactoring"
  - "modularity"
  - "coupling"
---

# Improve-Codebase-Architecture Skill

**Iron Law**: Always propose before refactoring.

## Steps

1. **Map Modules** — Identify each module:
   - Files, lines of code, dependencies
   - What does it export?
   - Who imports it?

2. **Find Coupling** — Look for:
   - Circular imports (A imports B imports A)
   - High fan-in (many modules import the same utility)
   - High fan-out (one module imports many others)
   - Deep dependency chains (A→B→C→D)

3. **Find Shallow Modules** — Identify:
   - Files <50 lines that could be merged
   - Files whose content is a thin wrapper (no value added)
   - Modules with single function

4. **Find Duplication** — Check for:
   - Repeated logic across modules
   - Shared utilities that should be extracted
   - Similar patterns implemented differently

5. **Output** — Proposal document with:
   ```
   ## Refactoring Opportunities

   ### High Impact
   - Circular dependency: auth.ts ↔ user.ts
     Proposal: Extract shared types to auth/types.ts
     Effort: 2 hours

   - Shallow module: utils/cache.ts (15 lines)
     Proposal: Merge into services/storage.ts
     Effort: 30 min

   ### Medium Impact
   - High fan-out: server.ts imports 12+ modules
     Proposal: Create middleware/plugins structure
     Effort: 1 day
   ```

