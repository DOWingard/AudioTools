---
description: "Full development lifecycle: audited plan → build → bug capture. Use for any non-trivial feature or fix."
argument-hint: "<task description>"
---

# /dev — Guided Development Workflow

Three-stage lifecycle: **Plan (audited) → Build → Bug Capture**

The argument (if provided) is the task description. If none is given, ask the user to describe the task before proceeding.

---

## Stage 1: Plan + Audit

### 1.1 Enter Plan Mode
Call `EnterPlanMode` to switch into planning. All exploration, design, and audit work happens here before presenting anything to the user.

### 1.2 Draft the Implementation Plan
Explore the codebase (Glob, Grep, Read) as needed to understand the full scope of the task. Draft a step-by-step implementation plan.

### 1.3 Run the Audit Plan Workflow
Before writing the final plan or calling `ExitPlanMode`, execute every phase described in `.agent/workflows/auditPlan.md`. Specifically:

1. **Load navigation context** — read `AGENT-MAP.md`, `AGENT-CONTEXT/AGENT-CONTEXT.md`, and `.agent/skills/AGENT-SKILLS.md` (skip files that don't exist).
2. **Extract build targets** — enumerate every file the plan touches (modify / create / delete).
3. **Ground integration points** — for each file/function the plan references, verify the line numbers are current and rewrite references as `[basename.py Lxx](file:///absolute/path#Lxx)` links.
4. **Map bug context** — consult the Triage Table in `AGENT-CONTEXT/AGENT-CONTEXT.md` and attach a `> [!WARNING]` block to each plan step pointing at the relevant context file(s).
5. **Map skills** — check `.agent/skills/` for any skill that applies to the technical area of each step and list them in a Required Skills table.
6. **Add Step 0** — prepend a *Prerequisites & Context Loading* section with a Required Reading table and the Required Skills table.
7. **Define interface tests** — for each new or changed public function/endpoint, specify test inputs, expected outputs, and the insertion location in the test file.
8. **Verify dependency chain** — confirm the first step is fully rooted (no unmet deps), and each subsequent step declares `Depends on:` and `Enables:`.

### 1.4 Finalize and Present
Write the complete audited plan. Call `ExitPlanMode` to show it to the user for approval.

---

## Stage 2: Build

On user approval, implement the plan step by step. While building:

- **Track bugs** — note every unexpected error, test failure, build crash, or non-obvious fix encountered during implementation. For each, record:
  - Symptom (exact error message or failure mode)
  - Root cause (what was actually wrong)
  - Remediation (what fixed it)
- Keep this list in working memory; you will use it in Stage 3.

---

## Stage 3: Bug Capture (conditional)

After the build is complete:

- **If one or more bugs were encountered and fixed** (unexpected errors, test failures, crashes — not counting intentional logic changes): run the `addBugs` workflow.
  - Read `.agent/workflows/addBugs.md` and execute every step it describes.
  - Persist the bugs to the appropriate `AGENT-CONTEXT/` files.

- **If no unexpected bugs arose**: skip Stage 3. Confirm build completion to the user.
