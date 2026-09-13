---
name: principles-check
description: Audit codebase against engineering principles from CLAUDE.md.
triggers:
  - "check principles"
  - "principles audit"
  - "violations"
---

# Principles-Check Skill

**Iron Law**: Always audit against documented principles.

## Steps

1. **Load Principles** — Read CLAUDE.md (or project-specific doc):
   - Reusability, modularity, extensibility, robustness, observability, consistency, config

2. **Audit Codebase**:
   - **Reusability**: Are utility functions duplicated? Should shared/utils/ exist?
   - **Modularity**: Are files >300 lines? Do modules have single responsibility?
   - **Extensibility**: Is config hardcoded? Are new entity types easy to add?
   - **Robustness**: Are errors logged with context? Are timeouts set on external ops?
   - **Observability**: Are there console.log or print statements? Is logging structured JSON?
   - **Consistency**: Do response envelopes match schema? Are error types hierarchical?
   - **Config**: Are magic numbers defined in config, not inline?

3. **Output** — Markdown with:
   ```
   ## Principles Audit

   ### ✓ Met
   - Reusability: Shared utilities in packages/shared/
   - Consistency: All endpoints return {data, error, meta}

   ### ✗ Violated
   - Robustness: No timeout on HTTP requests to external API
   - Modularity: api/handler.ts is 450 lines (max 300)
   - Observability: 8 console.log statements in production code

   ### Recommendations
   1. Add request timeout: const res = fetch(url, {timeout: 5000})
   2. Split handler.ts into auth.ts (150L) + search.ts (200L) + rankings.ts (100L)
   3. Replace console.log with logger.info() throughout
   ```

