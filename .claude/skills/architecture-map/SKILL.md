---
name: architecture-map
description: Generate systems-level architecture map using subagents. Maps files, dependencies, data flow, and architectural patterns.
triggers:
  - "map architecture"
  - "system diagram"
  - "architecture overview"
  - "dependency graph"
---

# Architecture Map Skill

**Iron Law**: Always prefer the complete map over shortcuts.

## Steps

1. **Scout** — Use read-only subagent to explore codebase structure:
   - File tree and key directories
   - Core modules and their responsibilities
   - Entry points (main, server.py, App.tsx, etc.)

2. **Trace Dependencies** — Identify:
   - Import chains (A imports B imports C)
   - Data flow (request → service → database)
   - Service boundaries (frontend / API / ML / database)
   - External integrations (APIs, databases, caches)

3. **Document Patterns**:
   - Layered architecture? Microservices? Monorepo?
   - How requests flow end-to-end
   - Error propagation paths
   - Configuration and secrets

4. **Output** — Markdown with:
   - ASCII-art box diagram of major components
   - Table of file structure with component responsibilities
   - Dependency matrix (which modules depend on which)
   - Critical paths and bottleneck areas
   - Risks and improvement opportunities

