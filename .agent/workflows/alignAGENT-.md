---
description: Auto-discover project structure, create missing AGENT- files, and hydrate context
---

# 🔄 Agent Alignment & Initialization Workflow

> **Trigger**: "Align yourself", "Setup project", "Hydrate context", or run `/align`
> **Purpose**: Autonomously map the current codebase, create missing descriptors, and populate `AGENT-CONTEXT`.

## Phase 1: Exploration & Mapping

### 1. Scan Directory Structure
// turbo
List top-level directories to understand the project shape:

```bash
ls -F -1 | grep "/" | grep -v "node_modules" | grep -v ".git" | grep -v ".agent"
```

### 2. Identify Key Components
Based on the `ls` output and common patterns, identify:
- **Source Code**: `src/`, `app/`, `lib/`, `pkg/`
- **Infrastructure**: `infra/`, `k8s/`, `terraform/`, `docker/`
- **Docs**: `docs/`, `documentation/`
- **Tests**: `tests/`, `spec/`

### 3. Update AGENT-MAP.md
Edit `AGENT-MAP.md` to map these discovered folders.
- **New Folders**: If a folder (e.g., `src/`) lacks a descriptor (e.g., `src/AGENT-SRC.md`), **create it** using a basic template and add to `AGENT-MAP.md`.
- **Deleted Folders**: If `AGENT-MAP.md` lists a folder that no longer exists, **remove** the entry.
- **Renamed Folders**: Update the path in `AGENT-MAP.md`.

**Descriptor Template:**
```markdown
# 📁 <Name> Context

> **Scope**: Contents of `<folder>/`

## Contents
- ...
```

## Phase 2: Context Hydration

### 1. Analyze Tech Stack
Check for dependency files to understand the stack:
// turbo
```bash
ls package.json requirements.txt Cargo.toml go.mod pom.xml build.gradle Gemfile composer.json 2>/dev/null
```

**Action**:
- **If missing**: Create `AGENT-CONTEXT/TECH-STACK.md` with a summary of frameworks/languages.
- **If exists**: Read it, compare with current dependency files, and **update** if versions or libraries have changed significantly.

### 2. Analyze Database
Check for database schemas:
// turbo
```bash
find . -name "schema.prisma" -o -name "*.sql" -o -name "models.py" | grep -v node_modules
```

**Action**:
- **If missing**: Create `AGENT-CONTEXT/DB.md` with a schema summary.
- **If exists**: Check if the schema files have a newer modification date than the last edit to `DB.md`. If so, append a note: *"Schema changed on [Date]. Update this file."*

### 3. Analyze API
Check for API definitions:
// turbo
```bash
find . -name "openapi.yaml" -o -name "swagger.json" -o -name "routes.ts" -o -name "urls.py" | grep -v node_modules
```

**Action**:
- **If missing**: Create `AGENT-CONTEXT/API.md`.

## Phase 3: Alignment Verification

### 1. Discover All AGENT- Files
// turbo
Find all agent files to ensure nothing is missed in the map:

```bash
find . -name "AGENT-*" -type f -o -name "AGENT-*" -type d | grep -v node_modules | grep -v .git | sort
```

### 2. Validate AGENT-MAP.md Links
// turbo
Ensure all links in the map are valid:

```bash
grep -oP '\[AGENT-[^\]]+\]\([^)]+\)' AGENT-MAP.md | while read link; do
  path=$(echo "$link" | grep -oP '\([^)]+\)' | tr -d '()')
  if [ ! -f "$path" ]; then
    echo "BROKEN: $link -> $path"
  else
    echo "OK: $path"
  fi
done
```

**Action**: Remove any "BROKEN" links from `AGENT-MAP.md`.

### 3. Summary Report
Report what was initialized/updated:
- New `AGENT-*.md` files created.
- `AGENT-MAP.md` entries added/removed.
- Context files hydrated or flagged for update.

---

## Completion Criteria
*   [ ] `AGENT-MAP.md` accurately reflects the *actual* project folder structure (no dead links).
*   [ ] `AGENT-CONTEXT/` has basic files (`TECH-STACK.md`, etc.) based on real code.
*   [ ] The agent can navigate from `AGENT-MAP.md` to any major part of the codebase.
