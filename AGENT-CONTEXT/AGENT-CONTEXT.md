# 🧠 Agent Context Memory

> **Purpose**: Long-term, cross-session memory. This is where architectural decisions, schema definitions, and "Long Context" live.

## Active Context Files

| Context Area | File | Description |
|--------------|------|-------------|
| **Database** | `DB.md` | Schema, migrations, and data models. |
| **API** | `API.md` | Endpoints, contracts, and protocols. |
| **Deployment** | `CICD.md` | Build pipelines and deployment strategies. |

## Rules for Agents

1.  **Check Context First**: Before starting a complex task, read the relevant file above.
2.  **Update Context**: If you make a significant architectural change (e.g., "Added a new table", "Changed auth provider"), you **MUST** update the corresponding file here.
3.  **Do Not Hallucinate**: If knowledge is missing, find it in the code, then write it here.
