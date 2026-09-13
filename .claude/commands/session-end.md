---
description: Checkpoint the current session. Updates PROGRESS.yaml, archives old sessions, commits state.
---

Run this at the end of every Claude Code session.

## Steps

### 1. Update PROGRESS.yaml (active state only)
- Move completed items to PROGRESS-archive.yaml
- Update completed_summary counts
- Update blockers and pending_decisions

### 2. Append to recent_sessions:
```yaml
- id: "session-NNN"
  date: "YYYY-MM-DD"
  focus: "<what was worked on>"
  completed:
    - "<one-line summary>"
  next_recommended: "<what should happen next>"
```
Use `date +%Y-%m-%d`. Increment session ID.

### 3. Archive old sessions
If recent_sessions has >3 entries, move oldest to PROGRESS-archive.yaml.

### 4. Bug count
"Open bugs: N total (C critical, H high)" — don't do full triage.

### 5. Commit
```bash
git add PROGRESS.yaml PROGRESS-archive.yaml BUGS.yaml
git commit -m "chore: checkpoint session-NNN - <brief summary>"
```

### 6. Print summary
What was done, blockers, next action, bug count.

## Rules
- PROGRESS.yaml must stay under 50 lines of content
- Only 3 recent sessions in active file
- Keep this fast — seconds, not minutes
