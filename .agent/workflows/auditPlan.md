---
description: Audit and ground a build plan with codebase line references, context files, and bug links
---

# Audit Build Plan Workflow

> **Purpose**: Ground a build plan against the codebase by adding precise line references, required context reading, and linking bug documentation to each step.

---

## Phase 1: Load Navigation Context

### Step 1.1: Read AGENT-MAP (Central Router)
// turbo
```bash
cat AGENT-MAP.md
```

### Step 1.2: Read AGENT-CONTEXT Index (Bug Resolution Router)
// turbo
```bash
cat AGENT-CONTEXT/AGENT-CONTEXT.md
```

### Step 1.3: Read AGENT-SKILLS Index
// turbo
```bash
cat .agent/skills/AGENT-SKILLS.md
```

---

## Phase 2: Extract Build Targets

Parse the plan and list all:

| Target Type | What to Capture |
|-------------|-----------------|
| Files to **MODIFY** | Path + specific line ranges |
| Files to **CREATE** | Path + what it connects to |
| Files to **DELETE** | Path + what else references it |

---

## Phase 3: Ground Integration Points

For each build step, verify and document:

### 3.1 Verify Line References
// turbo
```bash
# For each file referenced:
sed -n '<start>,<end>p' <file_path>
```

### 3.2 Add Codebase Reference Table

Each step must have:

```markdown
### Step X.Y Codebase Reference

| Symbol | Location | Connection Point |
|--------|----------|------------------|
| `function` | [file.ts L12](file:///path/to/file.ts#L12) | Called by X |
```

**Link Format (MANDATORY):** `[basename.ts Lxx](file:///absolute/path#Lxx)`

---

## Phase 4: Map Context Files to Build

### 4.1 Bug Context Routing Table

Match each build phase to AGENT-CONTEXT files using the Triage table:

| Build Area | Primary Context | Symptom Match |
|------------|-----------------|---------------|
| Database/SQL | [DatabaseStore.md](../AGENT-CONTEXT/DatabaseStore.md) | "Column not found", Query errors |
| API Routes | [BackendApi.md](../AGENT-CONTEXT/BackendApi.md) | 500/401 errors, Validation |
| Pipeline | [MVPPipeline.md](../AGENT-CONTEXT/MVPPipeline.md) | Ingestion, Hierarchy |
| Frontend | [FrontendClient.md](../AGENT-CONTEXT/FrontendClient.md) | White screen, Hydration |
| Auth/Clerk | [SecurityGuardrails.md](../AGENT-CONTEXT/SecurityGuardrails.md) | Token invalid, 403 |
| Build/Docker | [InfrastructureEnv.md](../AGENT-CONTEXT/InfrastructureEnv.md) | Won't start, CORS |
| Async/Jobs | [BackendExecution.md](../AGENT-CONTEXT/BackendExecution.md) | BullMQ, WebSocket |
| CI/Tests | [CICD.md](../AGENT-CONTEXT/CICD.md) | Pre-push, Cache |

### 4.2 Skills Routing Table

Check `.agent/skills/` for relevant skills.

| Technical Area | Required Skill |
|----------------|----------------|
| Express (server, CJS) | [express-v4-v5-migration](../.agent/skills/express-v4-v5-migration/SKILL.md) |
| Zod schemas | [zod-v4-validation](../.agent/skills/zod-v4-validation/SKILL.md) |
| Vector search | [pgvector-indexing](../.agent/skills/pgvector-indexing/SKILL.md) |
| Queue jobs | [bullmq-job-patterns](../.agent/skills/bullmq-job-patterns/SKILL.md) |

*(Dynamically populate this list based on available skills in `.agent/skills/`)*

---

## Phase 5: Define Interface Tests

For each new function/method, specify:

```markdown
### Test <ID>: <Test Name>

| Input | Expected Output | Validates |
|-------|-----------------|-----------|
| Valid | Expected result | Happy path |
| Invalid | Error type | Validation |
| Edge case | Specific behavior | Boundary |

**Insert Location:** [test_file.test.ts L42](file:///path#L42)
```

---

## Phase 6: Verify Dependency Chain

### 6.1 Check First Step is Rooted

The first build step MUST:
- Reference exact starting location with `[file.ts Lxx](file:///path#Lxx)` links
- Have no unmet dependencies

### 6.2 Verify DAG

Each step must specify:
- **Depends on**: Previous step numbers
- **Enables**: Subsequent step numbers

---

## Phase 7: Update Plan Structure

### 7.1 Add Step 0: Prerequisites & Context Loading

```markdown
## Step 0: Prerequisites & Context Loading

### 0.1 Required Reading (MANDATORY)

| Order | File | Purpose |
|-------|------|---------|
| 1 | [AGENT-MAP.md](../AGENT-MAP.md) | Codebase navigation |
| 2 | [<context>.md](../AGENT-CONTEXT/<context>.md) | <reason> |

### 0.2 Required Skills

| Skill | Path | When Used |
|-------|------|-----------|
| **<name>** | [SKILL.md](../.agent/skills/<skill>/SKILL.md) | <phase> |
```

### 7.2 Add Bug Context Warning Per Phase

```markdown
> [!WARNING]
> **If errors arise during this step, read:**
> - [<ContextFile>.md](../AGENT-CONTEXT/<ContextFile>.md) — <symptom>
```

### 7.3 Add Build Target Per Step

```markdown
### Step X.Y Build Target

| Check | Command | Expected |
|-------|---------|----------|
| File modified | `grep -q "<pattern>" <file>` | Match found |
| TypeScript | `cd <app> && pnpm tsc --noEmit` | No errors |
```

---

## Phase 8: Validate

### 8.1 Verify All References Current
// turbo
```bash
for f in <modified_files>; do wc -l "$f"; done
```

### 8.2 Verify Context Files Exist
// turbo
```bash
ls AGENT-CONTEXT/*.md
ls .agent/skills/*/SKILL.md
```

---

## Output Checklist

| Category | Requirement |
|----------|-------------|
| **Integration Refs** | Every modified file has `[file.ts Lxx](file:///path#Lxx)` links |
| **Step 0** | Required Reading + Required Skills tables present |
| **Bug Context** | Each phase has `> [!WARNING]` block with context file links |
| **Build Targets** | Each step has `grep` verification commands |
| **Dependency Chain** | First step rooted, all steps ordered by dependency |
| **Tests** | Interface points have test specifications with insert locations |

---

## Notes

- **Start at AGENT-MAP.md** — Never explore blindly
- **Match symptoms to context files** — Use the Triage table from AGENT-CONTEXT.md
- **Line references MUST be verified** — Run `sed` or `head` to confirm accuracy
- **Link format**: `[basename.ts Lxx](file:///absolute/path#Lxx)`
