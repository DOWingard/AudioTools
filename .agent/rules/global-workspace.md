# 🏗️ Global Agent Workspace Rules

> **Trigger**: always_on
> **Priority**: Critical

## 1. 🗺️ The Navigation Protocol

**Critical Instruction**: Do not blindly explore. Minimize context loading by following this protocol:

1.  **Start at AGENT-MAP.md**: The central router at the root. Read this first to locate services.
2.  **Read Folder Descriptors**: Every major directory should contain an `AGENT-<NAME>.md`. Read this before modifying code in that directory.
3.  **Check AGENT-CONTEXT/**: Look for task-specific memory files (e.g., `DB.md`) relevant to your current objective.

## 2. 🛡️ Safety & Stability

1.  **Tests**: Always run tests after modifications if the project has them.
2.  **Linting**: Respect the existing code style. Mimic surrounding code.
3.  **No Ghost Commits**: Do not change files unrelated to your task.

## 3. 🧠 Context Management

*   **Read**: `AGENT-CONTEXT/` files are your long-term memory.
*   **Write**: If you learn something fundamental about the system, update `AGENT-CONTEXT/`.
