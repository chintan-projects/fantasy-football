---
name: cso
description: Security audit. Secrets archaeology, dependency supply chain, OWASP Top 10, STRIDE threat model.
triggers:
  - "security audit"
  - "security review"
  - "pentesting"
  - "threat model"
---

# CSO Skill

**Iron Law**: Always audit for secrets and dependencies first.

## Steps

1. **Secrets Archaeology** — Search for:
   - Hardcoded API keys, database URLs, tokens
   ```bash
   git log -p | grep -i "password\|token\|secret\|key" | head -20
   grep -r "sk_live_\|OPENAI_KEY=" . --include="*.ts" --include="*.py" --include="*.js"
   ```
   - .env files in git history
   - Exposed credentials in error messages

2. **Dependency Supply Chain**:
   - List dependencies:
     ```bash
     npm list  # or pip list, cargo tree
     ```
   - Check for known vulnerabilities:
     ```bash
     npm audit
     safety check  # Python
     cargo audit
     ```
   - Identify high-risk dependencies (old, unmaintained)

3. **OWASP Top 10** — Audit for:
   - A1: Injection (SQL injection, command injection)
   - A2: Broken Authentication (default passwords, weak tokens)
   - A3: Broken Access Control (missing authz checks)
   - A4: XML External Entities (XXE)
   - A5: Broken Access Control
   - A6: Sensitive Data Exposure (logs, error messages)
   - A7: XML External Entities
   - A8: Insecure Deserialization
   - A9: Using Components with Known Vulnerabilities
   - A10: Insufficient Logging & Monitoring

4. **STRIDE Threat Model** — Consider:
   - **Spoofing**: Can users impersonate others?
   - **Tampering**: Can data be modified in transit or at rest?
   - **Repudiation**: Can users deny their actions?
   - **Information Disclosure**: What data is exposed?
   - **Denial of Service**: Can the system be crashed?
   - **Elevation of Privilege**: Can users gain admin access?

5. **Output** — Security audit report:
   ```
   ## Security Audit

   ### Critical
   - API key hardcoded in config.ts (commit: abc123)
   - SQL injection risk in search endpoint (no parameter binding)

   ### High
   - 3 dependencies with known vulnerabilities
   - No rate limiting on login endpoint (brute force risk)

   ### Medium
   - Error messages expose internal paths
   - Passwords logged in debug mode

   ### Actions
   1. Rotate exposed API keys
   2. Add parameterized queries to search
   3. Run: npm audit fix
   4. Add rate limiting to /login
   ```

