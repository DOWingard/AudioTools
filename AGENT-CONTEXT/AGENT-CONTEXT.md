# 🧠 Agent Context Memory

> **Purpose**: Long-term, cross-session memory. Architectural decisions, schemas, and system documentation live here.

---

## Active Context Files

| Context Area | File | Description |
|--------------|------|-------------|
| **Backend System** | [`BackendFiles.md`](BackendFiles.md) | Full reference for embedding pipeline, file storage (R2/MinIO), Qdrant vector DB, auth service, streaming, and all API endpoints. |
| **Backend Execution** | [`BackendExecution.md`](BackendExecution.md) | Async/threading failure patterns: UploadFile iteration, background task pitfalls, sync-vs-async compatibility. |
| **Infrastructure / Env** | [`InfrastructureEnv.md`](InfrastructureEnv.md) | Docker build/deploy mismatches, env var loading, stale container images, service networking. |

---

## System Architecture

```
┌────────────┐  Bearer JWT  ┌────────────┐  HTTP /auth/*  ┌────────────┐
│   Browser   │────────────▸ │  Compute   │──────────────▸ │    Auth     │
│  (Vite/React│              │  (FastAPI)  │               │  (FastAPI)  │
│   :7860)    │◂── ZIP/audio │  :8000      │               │  :8001      │
└────────────┘               └──────┬──────┘               └──────┬──────┘
                                    │                              │
                  ┌─────────────────┼──────────────┐               │
                  ▼                 ▼              ▼               ▼
           ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
           │  Qdrant    │   │ R2/MinIO  │   │  Staging  │   │ PostgreSQL│
           │  :6333     │   │  :9010    │   │  /tmp/    │   │  :5434    │
           │  768-D CLAP│   │  syntag-  │   │  syntag_  │   │  users    │
           │  vectors   │   │  audio    │   │  staging  │   │  table    │
           └───────────┘   └───────────┘   └───────────┘   └───────────┘
```

---

## Source File Map

### Backend

| File | Lines | Purpose |
|------|-------|---------|
| `api/main.py` | ~1571 | Compute API — all audio processing, embed pipeline, file retrieval, graph, search |
| `auth/main.py` | ~411 | Auth service — Clerk JWT validation, Stripe subscriptions, user CRUD, usage quotas |
| `database/init.sql` | 26 | PostgreSQL schema — `users` table with subscription + quota columns |
| `docker-compose.yml` | 152 | 7-service stack: minio, minio-init, qdrant, postgres, auth, compute, ui |

### ML / Processing Modules (`src/`)

| File | Purpose |
|------|---------|
| `src/embedder.py` | `AudioEmbedder` — M2D-CLAP model wrapper (768-D audio+text embeddings) |
| `src/synctag.py` | SyncTag pipeline — audio tagging with metadata extraction |
| `src/separate.py` | Demucs stem separation (wrapper) |
| `src/advanced_separate.py` | Extended separation with oneshot detection |
| `src/app.py` | Gradio app (legacy/local UI) |
| `src/export.py` | Audio export utilities |
| `src/taxonomy.py` | Genre/tag taxonomy definitions |

### Frontend (`ui/`)

| File | Purpose |
|------|---------|
| `ui/src/App.jsx` | Main app shell — tab routing, auth guards |
| `ui/src/AuthContext.jsx` | Clerk auth context provider |
| `ui/src/main.jsx` | React entry point |
| `ui/src/index.css` | Global styles (~28KB) |

#### UI Components (`ui/src/components/`)

| Component | Purpose |
|-----------|---------|
| `StemSeparatorTab.jsx` | Demucs stem separation UI |
| `SyncTagTab.jsx` | Audio tagging + metadata UI |
| `AudioCutterTab.jsx` | Waveform-based audio trimming |
| `AudioJoinerTab.jsx` | Multi-file audio concatenation |
| `KaraokeTab.jsx` | Vocal removal (instrumental extraction) |
| `FormatConverterTab.jsx` | Audio format conversion (mp3/wav/flac/aiff/aac/m4a/ogg) |
| `AudioAnalyzerTab.jsx` | BPM, key, loudness analysis |
| `BpmKeyFinderTab.jsx` | Quick BPM + key detection |
| `MyFilesTab.jsx` | File library, graph viz, similarity search (premium) |
| `WaveformPlayer.jsx` | Shared waveform playback component |
| `LimitModal.jsx` | Free-tier quota exhaustion modal |
| `SignInModal.jsx` | Auth sign-in prompt |
| `SubscriptionModal.jsx` | Stripe pricing / upgrade modal |
| `UserMenu.jsx` | User profile dropdown |

### Other

| File | Purpose |
|------|---------|
| `master_app.py` | Local Gradio graph explorer (dev only) |
| `master_orchestrator.py` | NLP intent router — Gemini LLM → pipeline dispatch |
| `master_separate.py` | Standalone separation script |
| `scripts/agent-services.sh` | Docker + webhook tunnel bootstrap script |
| `AGENT-MAP.md` | Root navigation file for agents |

### Tests (`tests/`)

15 test files covering API e2e, auth e2e, embedder, karaoke, synctag, database, and audio utilities.

---

## Triage Table

> Match a symptom to the file most likely to contain the root cause.

| Symptom | Target File(s) |
|---------|----------------|
| Embedding fails / wrong vector dim | `src/embedder.py`, `api/main.py` (L262–329) |
| R2/MinIO upload error | `api/main.py` (L118–145, L262–329) |
| Qdrant upsert/search failure | `api/main.py` (L152–184) |
| Auth / JWT validation error | `auth/main.py`, `api/main.py` (L191–229) |
| Stripe webhook mismatch | `auth/main.py` (L302–360) |
| Webhook returns 200 but no log output / no DB change | [`InfrastructureEnv.md`](InfrastructureEnv.md) — stale Docker image |
| Subscription gate not working | `auth/main.py` (L183–199), `api/main.py` (L1350–1371) |
| Free-tier quota not enforcing | `auth/main.py` (L202–240) |
| Audio processing error (ffmpeg) | `api/main.py` (endpoint-specific), `src/separate.py` |
| Stem separator permission denied | `api/main.py` (L468–535), Dockerfile.compute (HOME dir) |
| UI not rendering / component crash | `ui/src/App.jsx`, specific component in `ui/src/components/` |
| Docker health check failing | `docker-compose.yml`, respective `Dockerfile.*` |
| DB schema mismatch | `database/init.sql` |
| Graph viz broken (3D) | `api/main.py` (L1453–1562), `MyFilesTab.jsx` |
| Streaming playback failure | `api/main.py` (L1272–1343) |
| `UploadFile` async iteration 500 in tests | [`BackendExecution.md`](BackendExecution.md) |

---

## Rules for Agents

1. **Check Context First** — Before starting a complex task, read `BackendFiles.md` and this file.
2. **Update Context** — If you make a significant architectural change (new endpoint, new table column, auth flow change), **MUST** update `BackendFiles.md`.
3. **Do Not Hallucinate** — If knowledge is missing, find it in the source code, then write it here.
4. **Line Numbers Drift** — Line references are approximate; always verify against the current source.
