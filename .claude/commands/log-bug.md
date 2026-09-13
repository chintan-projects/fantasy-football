---
description: Capture a bug without derailing current work. Appends to BUGS.yaml and returns to current task.
---

Interrupt-safe bug capture. Steps:

1. Read BUGS.yaml. Find the highest `BUG-NNN` id under the `bugs:` key, increment to get the next id.
2. Append a new entry under `bugs:` with this exact structure:
```yaml
  - id: "BUG-NNN"
    severity: critical | high | medium | low
    component: "path/to/affected/file.ts"
    status: open
    reported_in: "YYYY-MM-DD"
    title: "Short description of the bug"
    repro: |
      Steps to reproduce the issue.
```
3. Write the updated BUGS.yaml.
4. Print "Bug BUG-NNN logged. Returning to current task."
5. Resume previous work — do NOT start fixing unless critical and blocking.

If BUGS.yaml has no `bugs:` key yet, create one:
```yaml
bugs:
  - id: "BUG-001"
    ...
```

Spend no more than 30 seconds on this.
