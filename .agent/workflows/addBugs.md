---
description: Review conversation for bugs found during build, add to AGENT-CONTEXT files, update index
---

# Add Bugs to AGENT-CONTEXT Workflow

This workflow extracts bugs discovered during a build session, categorizes them, and persists them in the appropriate `AGENT-CONTEXT/` file for future debugging reference.

## Steps

### 1. Review Conversation for Bugs

Scan the **entire current conversation** for:

| Signal | Example |
|--------|---------|
| Error messages | `TypeError`, `SyntaxError`, stack traces |
| Build failures | `npm run build` failures, Docker exits |
| Runtime crashes | Server 500s, unhandled rejections |
| Fixes applied | Code edits that resolved an issue |

**Extract for each bug:**
- **Symptom**: What error/message appeared?
- **Root Cause**: What was actually wrong?
- **Remediation**: What fixed it (if resolved)?

### 2. List Existing AGENT-CONTEXT Files
// turbo
```bash
ls -1 AGENT-CONTEXT/
```

### 3. Read AGENT-CONTEXT.md Index

View the current debugging router to understand categories:

```bash
cat AGENT-CONTEXT/AGENT-CONTEXT.md
```

### 4. Categorize Each Bug

Map each bug to an existing context file:

| Bug Domain | Target File |
|------------|-------------|
| Build, Docker, env vars, networking | `InfrastructureEnv.md` |
| React, UI, SSR, hydration | `FrontendClient.md` |
| Express, routes, auth, middleware | `BackendApi.md` |
| Async, timeouts, resource locks | `BackendExecution.md` |
| PostgreSQL, migrations, ORM | `DatabaseStore.md` |
| Planning loops, hallucination | `CognitionAndPlanning.md` |
| Context rot, memory issues | `MemoryState.md` |
| Injection, unauthorized actions | `SecurityGuardrails.md` |
| Deadlocks, recovery SOPs | `DevOpsUI.md` |

**If a bug doesn't fit any existing category**, create a new context file.

### 5. Read Target Context Files

For each file you plan to modify, read its current contents to understand the format:

```bash
cat AGENT-CONTEXT/<TargetFile>.md
```

### 6. Add Bug Entries

Append new bug entries to the appropriate file using this format:

```markdown
### N. <Bug Title>
*   **Symptom:** <What error/message appeared>
*   **Root Cause:** <What was actually wrong>
*   **Remediation Plan:**
    1.  <Step 1>
    2.  <Step 2>
```

**Rules:**
- Increment the section number (`### N.`) based on existing entries
- If the bug is similar to an existing entry, update that entry instead
- If creating a new file, follow this template:

```markdown
# Context: <Domain Name>

## Failure Patterns & Remediation

### 1. <First Bug Title>
*   **Symptom:** ...
*   **Root Cause:** ...
*   **Remediation Plan:**
    1.  ...
```

### 7. Update AGENT-CONTEXT.md Index

If you created a **new context file**, update `AGENT-CONTEXT.md`:

1. **Add to Triage Table** (Section 1):
   ```markdown
   | <Symptom Category> | <Specific Indicators> | [<NewFile>.md](./<NewFile>.md) |
   ```

2. **Add to Context Files Section**:
   ```markdown
   #### [<NewFile>.md](./<NewFile>.md)
   **Scope:** <What this file covers>
   
   - <emoji> <Bug pattern 1>
   - <emoji> <Bug pattern 2>
   ```

3. **Choose appropriate emoji**:
   - 🏗️ Infrastructure/Build
   - 🖥️ Frontend/UI
   - 🔌 Backend/API
   - 🗄️ Database
   - 🧠 Cognition/Planning
   - 💾 Memory/State
   - 🛡️ Security
   - 📊 DevOps
   - 🔧 Execution/Runtime

### 8. Validate Changes
// turbo
Ensure all context files are valid markdown and links work:

```bash
cd AGENT-CONTEXT && for f in *.md; do echo "=== $f ===" && head -3 "$f"; done
```

### 9. Summary Report

Produce a final summary:
- Number of bugs extracted from conversation
- Bugs added to existing files (list file → bug title)
- New context files created (if any)
- Updates made to `AGENT-CONTEXT.md` index

---

## Bug Entry Best Practices

1. **Be Specific**: "CORS Error" is too vague. Use "CORS preflight fails on POST to /api/upload".
2. **Include Stack Context**: Note the file, line, or module where the bug manifested.
3. **Actionable Remediation**: Steps should be copy-paste-able, not abstract advice.
4. **Deduplicate**: Before adding, check if the same bug pattern already exists.
5. **Date Major Bugs**: For critical/systemic bugs, add `*(Found: YYYY-MM-DD)*` after the title.

---

## Notes

- Run this workflow at the **end of a debugging session** to capture learnings
- The goal is to help **future agent sessions** avoid repeating the same mistakes
- Focus on bugs that took significant time to diagnose or had non-obvious fixes
- Skip trivial typos or one-liner fixes unless they represent a pattern
