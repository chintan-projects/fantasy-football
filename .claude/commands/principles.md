---
description: Audit the codebase against CLAUDE.md
---

Audit against `CLAUDE.md`. For each finding give file, line, the principle violated, and the
smallest fix.

Check in this order:

1. **§2.0 Simplicity** — any abstraction, interface, base class or config knob with only one
   caller. This is the most common violation and the easiest to miss. Name the layer and
   propose deleting it.
2. **§2.2 Modularity** — network or I/O imports, `datetime.now()`, or `os.environ` inside
   `domain/`. Business logic inside an `api/` route handler.
3. **§2.4 Robustness** — a response parsed before its status code is checked. A required
   source that should be optional. Any retry faster than a second.
4. **§2.5 Observability** — `print()` instead of the structured logger. A recommendation
   stored without the inputs that produced it.
5. **§3 Honesty** — a claim in code comments, docs or UI copy stated more confidently than
   the evidence supports. Folk heuristics presented as findings.
6. **§5 Safety** — any write path that does not take an `Approval`. A clamped rather than
   rejected over-budget bid.

Report as a table, most severe first. Do not fix anything without being asked.
