# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**SyncTag AI** — a SaaS audio tools platform for sync licensing metadata generation, stem separation, karaoke, BPM/key analysis, format conversion, and audio graph search. Deployed as a 5-service Docker stack.

---

## Repository Structure

```
GraphSamples/
├── api/                        # Compute service (FastAPI — Python)
│   ├── __init__.py
│   └── main.py                 # All audio processing endpoints (:8000)
├── auth/                       # Auth/billing service (FastAPI — Python)
│   ├── Dockerfile
│   ├── main.py                 # Clerk JWT + Stripe webhooks + user DB (:8001)
│   └── requirements.txt
├── ui/                         # Frontend (Vite + React — JavaScript)
│   ├── index.html
│   ├── vite.config.js
│   ├── package.json
│   ├── .env.local              # VITE_CLERK_PUBLISHABLE_KEY, VITE_STRIPE_PUBLISHABLE_KEY
│   ├── src/
│   │   ├── main.jsx            # App entry, ClerkProvider
│   │   ├── App.jsx             # Router, nav, tab layout, error boundaries
│   │   ├── AuthContext.jsx     # useAuthContext() — user profile + quota state
│   │   ├── index.css           # Global styles
│   │   └── components/
│   │       ├── SyncTagTab.jsx          # Metadata tagging tool
│   │       ├── StemSeparatorTab.jsx    # Demucs 4-stem separation
│   │       ├── KaraokeTab.jsx          # Vocal removal
│   │       ├── AudioCutterTab.jsx      # Trim audio
│   │       ├── AudioJoinerTab.jsx      # Concatenate audio files
│   │       ├── FormatConverterTab.jsx  # Audio format conversion
│   │       ├── BpmKeyFinderTab.jsx     # BPM + key detection
│   │       ├── AudioAnalyzerTab.jsx    # Comprehensive audio analysis
│   │       ├── MyFilesTab.jsx          # User file graph (premium)
│   │       ├── WaveformPlayer.jsx      # Wavesurfer.js waveform player
│   │       ├── SignInModal.jsx         # Clerk sign-in overlay
│   │       ├── UserMenu.jsx            # Avatar / profile menu
│   │       ├── SubscriptionModal.jsx   # Stripe checkout embedded UI
│   │       ├── LimitModal.jsx          # Free-tier quota exceeded prompt
│   │       └── StorageConfirmModal.jsx # Premium storage confirmation
│   └── dist/                   # Built output (served by nginx in ui container)
├── src/                        # Python audio pipeline library
│   ├── __init__.py
│   ├── embedder.py             # AudioEmbedder — M2D-CLAP (PortableM2D, 768-d)
│   ├── separate.py             # Demucs 4-stem + LARS drum demix
│   ├── advanced_separate.py    # Extended separation variants
│   ├── synctag.py              # SyncTagger — 4-stage metadata pipeline
│   ├── taxonomy.py             # GENRES, MOODS, INSTRUMENTS, TEMPOS, TRACK_TYPES
│   ├── export.py               # CSV/JSON output helpers
│   └── app.py                  # (legacy Gradio entry point)
├── database/
│   └── init.sql                # PostgreSQL schema (users, subscription_events)
├── tests/                      # pytest test suite
│   ├── conftest.py
│   ├── test_synctag_e2e.py     # Full pipeline e2e on real WAV
│   ├── test_embedder.py
│   ├── test_api_e2e.py
│   ├── test_auth_e2e.py
│   ├── test_auth_unit.py
│   ├── test_database.py
│   ├── test_clap.py
│   ├── test_ingest.py
│   ├── test_audio_resample.py
│   ├── test_bg_threads.py
│   ├── test_torch_threads.py
│   ├── test_gradio_audio.py
│   ├── test_e2e_karaoke.py
│   └── test_ogg.py
├── scripts/
│   ├── agent-services.sh       # Docker + webhook bootstrap helper
│   └── wipe-storage.py        # Purge Qdrant + MinIO data
├── AGENT-CONTEXT/              # Agent bug context files (see Agent Workflows below)
│   ├── AGENT-CONTEXT.md        # Architecture overview + triage table
│   ├── BackendFiles.md         # Embedding, storage, auth, API endpoint deep-dive
│   ├── BackendExecution.md     # Async, threading, resource lock bugs
│   ├── FrontendClient.md       # React/Vite/hydration bugs
│   └── InfrastructureEnv.md    # Docker, env var, networking bugs
├── .agent/
│   ├── rules/                  # Agent workspace rules
│   ├── skills/AGENT-SKILLS.md  # Available agent skills
│   └── workflows/              # addBugs.md, auditPlan.md, alignAGENT-.md
├── AGENT-MAP.md                # Root navigation map for agents
├── demucs/                     # Git submodule — facebook/demucs
├── LARS/                       # LARS drum demix TorchScript models
├── m2d/                        # M2D-CLAP model code (PortableM2D)
├── CLAP/                       # LAION CLAP reference implementation
├── docker-compose.yml          # 5-service stack definition
├── Dockerfile                  # (legacy)
├── Dockerfile.compute          # Compute service image
├── Dockerfile.auth             # Auth service image
├── Dockerfile.ui               # Nginx + built Vite app
├── nginx.conf                  # Nginx reverse proxy config
├── requirements.txt            # Python deps (compute/src)
├── requirements.ui.txt         # Python deps (UI/legacy)
├── .env                        # Secrets (never commit)
├── .dockerignore
├── .gitmodules                 # demucs submodule
├── AGENT-MAP.md
├── CICD.md
└── REVIEW.md
```

---

## Service Architecture

```
Browser
  │
  ▼
┌──────────────────────┐
│  ui  :7860  (nginx)  │  Vite/React SPA — Clerk auth, Stripe embedded checkout
└──────────┬───────────┘
           │ proxied API calls
     ┌─────┴──────────────────┐
     ▼                        ▼
┌─────────────┐      ┌──────────────┐
│ compute     │      │  auth        │
│ :8000       │      │  :8001       │
│ FastAPI     │◀────▶│  FastAPI     │
│ (audio ops) │      │  (JWT+Stripe)│
└──┬──────────┘      └──────┬───────┘
   │                        │
   ├─── Qdrant :6333        └─── Postgres :5434
   ├─── MinIO  :9010
   └─── /tmp/  (staging)
```

| Service   | Port  | Image / Build     | Role |
|-----------|-------|-------------------|------|
| `compute` | 8000  | `Dockerfile.compute` | Audio processing, embedding, file storage |
| `auth`    | 8001  | `Dockerfile.auth`    | Clerk JWT validation, Stripe billing, user DB |
| `ui`      | 7860  | `Dockerfile.ui`      | Vite React SPA served via nginx |
| `qdrant`  | 6333  | `qdrant/qdrant:v1.9.0` | Vector DB for audio similarity graph |
| `postgres`| 5434  | `postgres:17-alpine` | Users + subscription event log |
| `minio`   | 9010  | `minio/minio`       | S3-compatible object storage (local dev) |

---

## Compute API Endpoints (`api/main.py`)

| Method | Path | Returns | Auth |
|--------|------|---------|------|
| GET | `/health` | `{"status":"ok"}` | None |
| POST | `/api/tag` | ZIP (metadata.json + CSV + audio) | Required |
| POST | `/api/separate` | ZIP (4 stem WAVs) | Required |
| POST | `/api/cut` | Trimmed audio file | Required |
| POST | `/api/join` | Joined audio file | Required |
| POST | `/api/karaoke` | Instrumental audio (no vocals) | Required |
| POST | `/api/convert` | Converted audio file | Required |
| POST | `/api/bpm-key` | `{bpm, key, tempo_category}` | Required |
| POST | `/api/analyze` | Comprehensive JSON analysis | Required |
| GET | `/api/files` | User's stored files (Qdrant) | Required |
| GET | `/api/files/{id}/audio` | Audio file (from MinIO/R2) | Required |
| POST | `/api/files/upload` | Bulk upload to graph (premium) | Required |
| POST | `/api/files/search` | Similarity search (premium) | Required |
| DELETE | `/internal/users/{clerk_id}` | Purge user data | Internal secret |

## Auth API Endpoints (`auth/main.py`)

| Method | Path | Role |
|--------|------|------|
| GET | `/health` | Health check |
| GET | `/auth/user/me` | Fetch/create user profile, update last_active |
| POST | `/auth/usage/consume` | Decrement free daily quota (atomic) |
| POST | `/auth/webhooks/clerk` | Svix-verified Clerk lifecycle events |
| POST | `/auth/webhooks/stripe` | Stripe billing events (sub created/updated/deleted) |
| POST | `/auth/billing/checkout` | Create Stripe embedded checkout session |
| POST | `/auth/billing/sync` | Confirm checkout + sync subscription to DB |
| POST | `/auth/billing/portal` | Create Stripe billing portal session |

---

## Audio Pipeline (`src/`)

### SyncTagger (`src/synctag.py`)
4-stage pipeline → JSON metadata:
1. **Ingestion** — load audio via `ffmpeg`, compute duration/RMS
2. **Stem Separation** — Demucs `htdemucs` → vocals/drums/bass/other
3. **Embedding Extraction** — M2D-CLAP embeddings for mix + stems
4. **Zero-Shot Classification** — cosine similarity vs taxonomy text prompts

### AudioEmbedder (`src/embedder.py`)
- Model: `PortableM2D` from `m2d/examples/portable_m2d.py`
- Checkpoint: `m2d_clap_vit_base-80x1001p16x16p16kpBpTI-2025/checkpoint-30.pth`
- Input: `(1, T)` torch tensor at **16 kHz**
- Output: `(1, 768)` embedding via `model.encode_clap_audio(tensor)`
- `flat_features=True` required at instantiation
- `M2D_WEIGHT` env var → absolute path to checkpoint

### Stem Separation (`src/separate.py`)
- Stage 1: Demucs `htdemucs` → vocals, drums, bass, other (44100 Hz stereo WAV)
- Stage 2: LARS TorchScript UNet → drums → kick, snare, toms, hihat, cymbals
- LARS models: `LARS/drums_demix/DrumsDemixUtils/DrumsDemixModels/*.pt`
- Python env: `~/.pyenv/versions/3.10.13/bin/python`

### Taxonomy (`src/taxonomy.py`)
Defines constrained vocabularies: `GENRES`, `MOODS`, `INSTRUMENTS`, `TEMPOS`, `TRACK_TYPES`, `ID3_DISCO_MAPPING`

---

## Database Schema (`database/init.sql`)

### `users`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | `gen_random_uuid()` |
| `clerk_id` | VARCHAR UNIQUE | Clerk user ID |
| `email` | VARCHAR | From Clerk |
| `stripe_id` | VARCHAR UNIQUE | Stripe customer ID |
| `stripe_subscription_id` | VARCHAR UNIQUE | |
| `subscription_type` | VARCHAR | `free` / `standard` / `premium` |
| `subscription_active` | BOOLEAN | |
| `subscription_status` | VARCHAR | `free` / `active` / `past_due` / `cancelled` |
| `daily_free_downloads` | INT | Default 3; reset daily at 04:00 PST |
| `qdrant_graph_id` | VARCHAR | User's Qdrant collection |

### `subscription_events`
Audit log of all Stripe and billing lifecycle events (clerk_id, stripe_id, event_type, stripe_status, local_status, sub_type, raw_event_id).

---

## Auth Flow

1. Frontend signs in via Clerk → receives JWT
2. Every API call sends `Authorization: Bearer <jwt>` to compute or auth service
3. Compute proxies auth checks to auth service (`AUTH_SERVICE_URL`)
4. Auth service verifies JWT against Clerk JWKS (cached 1 hour, auto-refreshed)
5. Free users: `POST /auth/usage/consume` atomically decrements `daily_free_downloads`
6. Paid users: bypass quota check entirely

**Clerk JWKS caching:** pre-warmed at startup, refreshed every hour via APScheduler. Stale key triggers one-shot JWKS refresh and retry.

---

## Common Commands

**Start the full stack:**
```bash
docker-compose up --build
```

**Run Python tests:**
```bash
pytest tests/
```

**Run e2e pipeline test (requires checkpoint + ~135s CPU):**
```bash
pytest tests/test_synctag_e2e.py -v
```

**Run stem separation directly:**
```bash
~/.pyenv/versions/3.10.13/bin/python src/separate.py <input.wav> -o <outdir>
```

**Run SyncTag CLI:**
```bash
python src/synctag.py <audio> [-o output.json]
```

**Wipe local storage (Qdrant + MinIO):**
```bash
python scripts/wipe-storage.py
```

**Bootstrap services + webhooks (local dev):**
```bash
bash scripts/agent-services.sh
```

---

## Environment Variables (`.env`)

| Variable | Used By | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | auth | PostgreSQL connection string |
| `CLERK_SECRET_KEY` | auth | Clerk admin API |
| `CLERK_WEBHOOK_SECRET` | auth | Svix webhook verification |
| `VITE_CLERK_PUBLISHABLE_KEY` | ui | Clerk frontend key |
| `STRIPE_SECRET_KEY` | auth | Stripe API |
| `STRIPE_WEBHOOK_SECRET` | auth | Stripe webhook sig verification |
| `STRIPE_PRICE_ID_STANDARD` | auth | Standard plan price ID |
| `STRIPE_PRICE_ID_PREMIUM` | auth | Premium plan price ID |
| `VITE_STRIPE_PUBLISHABLE_KEY` | ui | Stripe frontend key |
| `APP_URL` | auth | App base URL for Stripe return URLs |
| `INTERNAL_SECRET` | auth/compute | Shared secret for internal service calls |
| `R2_ACCESS_KEY_ID` | compute | MinIO/R2 access key |
| `R2_SECRET_ACCESS_KEY` | compute | MinIO/R2 secret |
| `R2_BUCKET_NAME` | compute | Storage bucket name |
| `S3_ENDPOINT_URL` | compute | MinIO internal endpoint |
| `S3_PUBLIC_ENDPOINT_URL` | compute | MinIO public endpoint |
| `DEV_USER_ID` | compute | Bypass Clerk JWT (dev only) |
| `M2D_WEIGHT` | src/embedder | Absolute path to M2D-CLAP checkpoint |
| `QDRANT_URL` | compute | Qdrant service URL |
| `AUTH_SERVICE_URL` | compute | Auth service URL |

---

## Audio Processing Conventions

- **M2D-CLAP**: resample to **16 kHz** (not 48 kHz — this is M2D, not LAION CLAP)
- **Demucs/LARS**: operates at **44100 Hz**
- Audio loaded via `subprocess ffmpeg` (never `librosa` — triggers numba cache failure in Docker)
- Embed call: `model.encode_clap_audio(tensor)` where tensor is `(1, T)` float32
- Embedding dimension: **768-d** (M2D-CLAP ViT-Base)

---

## Submodules & Third-Party Libraries

| Directory | Source | Purpose |
|-----------|--------|---------|
| `demucs/` | facebook/demucs (git submodule) | 4-stem source separation |
| `LARS/` | LARS drum demix | TorchScript UNet for drum decomposition |
| `m2d/` | nttcslab/m2d | M2D-CLAP audio-text embedding model |
| `CLAP/` | LAION CLAP | Reference CLAP implementation (not used in production) |

---

## UI Design System

### Theme: Hetzner-Inspired
Reference: https://www.hetzner.com/

#### Color Palette
| Token | Value | Usage |
|-------|-------|-------|
| Brand red | `#D50C2D` | CTAs, active states, accents |
| Dark red | `#A8001F` | Hover/pressed states |
| Light red | `#E82240` | Subtle hover tint |
| Red glow | `rgba(213,12,45,0.18)` | Focus rings, shadows |
| Background | `#FFFFFF` | Page background |
| Surface | `#F4F4F4` | Panels, inputs, cards |
| Text primary | `#1A1A1A` | Headings, body |
| Text secondary | `#555555` | Labels, captions |
| Text muted | `#909090` | Placeholders |
| Border | `#E0E0E0` | All borders (solid, not rgba) |
| Success | `#16a34a` | Confirm states |
| Error | `#dc2626` | Error states |

#### Design Principles
- **Flat, not glossy** — no gradients on button backgrounds; solid `#D50C2D` only
- **Minimal shadows** — rely on solid borders (`1px solid #E0E0E0`) rather than box-shadow
- **Sharp corners** — `border-radius` max 10px for cards; 5px for buttons and inputs; never pill-shape buttons
- **High contrast** — text must be readable on white in bright environments (WCAG AA minimum)
- **Red used sparingly** — only on CTAs, active tab, and accent highlights; not on every element
- **Dense layout** — information-rich, no excessive whitespace padding

#### Border Radius Scale
| Token | Value |
|-------|-------|
| `--radius-sm` | `4px` |
| `--radius-md` | `8px` |
| `--radius-lg` | `10px` |
| `--radius-full` | `9999px` (for pill badges only, not buttons) |

#### Typography
- Body: 16px minimum (never smaller on mobile)
- Labels: 14px minimum
- Font: `'Inter', -apple-system, sans-serif`

---

### Mobile Best Practices (Required for all UI changes)

All UI work must comply with these rules:

1. **Larger Text** — Headlines and body text must be legible at arm's length. Min 16px body, min 20px headings on mobile.
2. **Concise Copy** — Short paragraphs. Break up long content with headers and visual separators.
3. **Bigger Buttons** — CTA buttons must have a minimum touch target of **44 × 44px**. No exceptions.
4. **Simplified Nav** — Mobile viewport (`≤ 768px`) must use a hamburger menu. Never rely on horizontal-scrolling tabs on mobile.
5. **No Horizontal Scroll** — All containers must use `max-width: 100%` and `overflow-x: hidden` on the root. No table or fixed-width element that bleeds.
6. **Thumb-Friendly Layout** — Place primary actions (download, process, submit) centered or at bottom. Keep top corners free of critical controls.
7. **Spaced Links** — Navigation links need `min-height: 44px` and visible padding between them so thumbs don't misfire.
8. **No Popups on Mobile** — Modals must be `position: fixed; inset: 0` (full-screen) on `≤ 480px`. No floating centered dialogs on small screens.
9. **Optimized Images** — Never use landscape-orientation decorative images. Prefer square or portrait. Always set `max-width: 100%`.
10. **Touch-Only Guards** — Wrap hover transforms (`transform: translateY`, `transform: scale`) in `@media (hover: hover)` so they don't fire on touch devices.
11. **Sticky Navigation** — Top bar must remain `position: sticky; top: 0` at all viewport sizes.
12. **High Contrast** — Use solid `#E0E0E0` borders and `#1A1A1A` text. Avoid low-contrast grays for interactive elements.
13. **Auto-fill on Forms** — Use correct `inputmode`, `autocomplete` attributes. Number inputs show numeric keypad (`inputmode="numeric"`).

---

## Agent Workflows

### Adding Bugs — `.agent/workflows/addBugs.md`

Run **at the end of any debugging session** to capture bugs for future sessions.

**Bug entry format:**
```markdown
### N. <Bug Title>
*   **Symptom:** <What error/message appeared>
*   **Root Cause:** <What was actually wrong>
*   **Remediation Plan:**
    1.  <Step 1>
    2.  <Step 2>
```

**Domain → file mapping:**

| Bug Domain | Target File |
|------------|-------------|
| Build, Docker, env vars, networking | `InfrastructureEnv.md` |
| React, UI, SSR, hydration | `FrontendClient.md` |
| Express/FastAPI routes, auth, middleware | `BackendApi.md` |
| Async, timeouts, resource locks | `BackendExecution.md` |
| PostgreSQL, migrations | `DatabaseStore.md` |
| Planning loops, hallucination | `CognitionAndPlanning.md` |
| Context rot, memory issues | `MemoryState.md` |
| Injection, unauthorized actions | `SecurityGuardrails.md` |
| Deadlocks, recovery SOPs | `DevOpsUI.md` |

---

### Auditing a Build Plan — `.agent/workflows/auditPlan.md`

Run **before implementing any non-trivial build plan** to ground it against the actual codebase.

**Output checklist the audited plan must satisfy:**

| Category | Requirement |
|----------|-------------|
| Integration Refs | Every modified file has `[file.py Lxx](file:///path#Lxx)` links |
| Step 0 | Required Reading + Required Skills tables present |
| Bug Context | Each phase has a `> [!WARNING]` block with AGENT-CONTEXT links |
| Build Targets | Each step has `grep` verification commands |
| Dependency Chain | First step rooted, all steps ordered by dependency |
| Tests | Interface points have test specs with insert locations |
