---
name: bug-fix
description: TDD bug-fix workflow. Regression test first, then minimal fix, then verify.
triggers:
  - "fix bug"
  - "BUG-"
  - "regression"
  - "failing test"
---

# Bug Fix Skill

1. Read BUGS.yaml, find the target bug
2. Set status to investigating
3. Write a regression test that demonstrates the bug (should FAIL)
4. Implement the minimal fix
5. Run: regression test passes, full suite passes, lint clean
6. Update BUGS.yaml: status resolved
