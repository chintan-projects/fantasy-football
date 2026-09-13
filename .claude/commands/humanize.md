---
description: Rewrite a file (or the current draft) to sound human-written, not AI-generated
argument-hint: [path/to/file.md]
---

Apply the `humanize` skill (`.claude/skills/humanize/SKILL.md`) in editing mode.

Target: `$ARGUMENTS`

Steps:

1. If `$ARGUMENTS` names a file, read it. If it's empty, ask which file or text to humanize (or humanize the draft currently under discussion).
2. Load the humanize skill's SKILL.md and, for a thorough pass, its `references/word-blocklist.md`.
3. Rewrite the content following the skill: cut the LLM tells, vary sentence rhythm, be specific and take a position, and preserve every fact, number, caveat, and technical term. Do not invent specifics to sound human — flag gaps as `[exact number?]` instead.
4. Keep the file's structure (headers, tables, code blocks, front-matter) intact unless restructuring genuinely improves clarity.
5. Write the result back to the same file. Then give a two-line summary: what kinds of changes you made, and any `[gaps]` you flagged for the user to fill.
