# 🗺️ Agent Codebase Map

> **Start Here** — This document routes you to every area of the codebase.

## Quick Links

| Area | Location | Descriptor |
|------|----------|------------|
| **Agent Context** | `AGENT-CONTEXT/` | [AGENT-CONTEXT.md](AGENT-CONTEXT/AGENT-CONTEXT.md) |
| **Agent Skills** | `.agent/skills/` | [AGENT-SKILLS.md](.agent/skills/AGENT-SKILLS.md) |
| **Scripts** | `scripts/` | `agent-services.sh` — Docker + webhook bootstrap |

## Key Files

| File | Purpose |
|------|---------|-
| `AGENT-MAP.md` | This file. The root of the knowledge graph. |
| `AGENT-CONTEXT/AGENT-CONTEXT.md` | System architecture, file map, and triage table. |
| `AGENT-CONTEXT/BackendFiles.md` | Deep-dive: embedding pipeline, storage, auth, all API endpoints. |
| `docker-compose.yml` | 7-service infrastructure definition. |

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Vite UI │◀──▸ │ Compute  │◀──▸ │   Auth   │
│  :7860   │     │  :8000   │     │  :8001   │
└──────────┘     └────┬─────┘     └────┬─────┘
                      │                │
            ┌─────────┼────────┐       │
            ▼         ▼        ▼       ▼
        ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
        │Qdrant │ │R2/MinIO│ │/tmp/  │ │Postgres│
        │:6333  │ │:9010   │ │staging│ │:5434   │
        └───────┘ └───────┘ └───────┘ └───────┘
```

## Maintenance
Run the agent scaffolding tool to update this map if the folder structure changes significantly.
