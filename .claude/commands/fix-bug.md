---
description: Focused bug-fix micro-session. TDD-style — regression test first, then fix, then verify.
argument-hint: "BUG-NNN"
---

Fix bug `$ARGUMENTS` using TDD:
1. Load bug from BUGS.yaml, set status to investigating
2. Reproduce the bug
3. Write regression test (should FAIL)
4. Implement minimal fix
5. Verify: regression test passes, full suite passes, lint clean
6. Set status to resolved in BUGS.yaml
7. Return to previous work
