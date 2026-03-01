# BackendFiles — Embedding + File Storage + Streaming System

> **Scope:** Every file, function, config var, and data path involved in audio embedding, object storage (R2/MinIO), vector storage (Qdrant), and streaming playback. This is the single source of truth for agents operating on this subsystem.

---

## 1. Architecture Overview

```
┌────────────┐    Bearer JWT     ┌────────────┐    HTTP /auth/user/me     ┌────────────┐
│   Browser   │ ──────────────▸  │  Compute   │ ──────────────────────▸  │    Auth     │
│  (Vite UI)  │                  │  (FastAPI)  │                          │  (FastAPI)  │
│  :7860      │  ◂── ZIP/audio   │  :8000      │                          │  :8001      │
└────────────┘                   └──────┬──────┘                          └──────┬──────┘
                                        │                                        │
                      ┌─────────────────┼─────────────────────┐                  │
                      ▼                 ▼                     ▼                  ▼
               ┌─────────────┐  ┌─────────────┐       ┌───────────┐      ┌───────────┐
               │   Qdrant    │  │  R2 / MinIO │       │  Staging  │      │ PostgreSQL│
               │  :6333      │  │  :9010      │       │  /tmp/    │      │  :5434    │
               │  768-D CLAP │  │  syntag-    │       │  syntag_  │      │  users    │
               │  vectors    │  │  audio      │       │  staging  │      │  table    │
               └─────────────┘  └─────────────┘       └───────────┘      └───────────┘
```

### Container Layout (`docker-compose.yml`)

| Service      | Image / Build          | Port  | Purpose                                  |
|-------------|------------------------|-------|------------------------------------------|
| `minio`     | `minio/minio:latest`   | 9010  | S3-compatible object storage (dev)       |
| `minio-init`| `minio/mc:latest`      | —     | Creates bucket `syntag-audio` on startup |
| `qdrant`    | `qdrant/qdrant:v1.9.0` | 6333  | Vector DB — cosine similarity search     |
| `postgres`  | `postgres:17-alpine`   | 5434  | User accounts, subscriptions, quotas     |
| `auth`      | `./auth/Dockerfile`    | 8001  | Clerk JWT + Stripe webhooks + user CRUD  |
| `compute`   | `./Dockerfile.compute` | 8000  | ML inference + embedding + file storage  |
| `ui`        | `./Dockerfile.ui`      | 7860  | Vite frontend (static serve via nginx)   |

---

## 2. Source File Map

### 2.1 `api/main.py` — Compute API (1565 lines)

**Primary responsibility:** All HTTP endpoints for audio processing, embedding, file persistence, and streaming.

#### Config Constants (L39–68)

| Variable               | Default                          | Purpose                                         |
|------------------------|----------------------------------|-------------------------------------------------|
| `QDRANT_URL`           | `http://localhost:6333`          | Qdrant gRPC endpoint                            |
| `AUTH_SERVICE_URL`     | `http://localhost:8001`          | Auth service base URL                           |
| `COLLECTION_NAME`     | `master_audio_graph`             | Single Qdrant collection for all users          |
| `VECTOR_DIM`          | `768`                            | M2D-CLAP embedding dimensionality               |
| `R2_ACCOUNT_ID`       | `""`                             | Cloudflare R2 account (prod)                    |
| `R2_ACCESS_KEY_ID`    | `minioadmin`                     | S3 access key                                   |
| `R2_SECRET_ACCESS_KEY`| `minioadmin`                     | S3 secret key                                   |
| `R2_BUCKET_NAME`      | `syntag-audio`                   | Object storage bucket                           |
| `S3_ENDPOINT_URL`     | `""`                             | Explicit S3 endpoint (MinIO override)           |
| `S3_PUBLIC_ENDPOINT_URL`| `""`                           | Public-facing endpoint for presigned URLs       |
| `STAGING_DIR`         | `/tmp/syntag_staging`            | Temp staging before R2 upload                   |
| `DEV_USER_ID`         | `""`                             | Auth bypass for dev/test                        |
| `_embed_pool`         | `ThreadPoolExecutor(max_workers=2)` | Background embedding threads               |

#### Lazy-Init Singletons (L81–145)

| Global     | Type                  | Init Function       | Notes                                     |
|------------|-----------------------|----------------------|-------------------------------------------|
| `_tagger`  | `SyncTagger`          | `_get_tagger()`      | ML tagging pipeline                       |
| `_embedder`| `AudioEmbedder`       | `_get_embedder()`    | M2D-CLAP model                            |
| `_qdrant`  | `QdrantClient`        | `_get_qdrant()`      | Creates collection + `user_id` index      |
| `_s3_upload` | `boto3.client("s3")` | `_get_s3_upload()`  | Internal endpoint — uploads objects       |
| `_s3_presign`| `boto3.client("s3")` | `_get_s3_presign()` | Public endpoint — presigned GET URLs      |

#### Qdrant Collection Setup — `_get_qdrant()` (L152–184)

```
Collection: "master_audio_graph"
  Vector: 768-D, Distance.COSINE
  Payload index: "user_id" → PayloadSchemaType.KEYWORD
```

- **Single shared collection** — all users' files live here, filtered by `user_id` at query time.
- Auto-creates collection on first access if missing.

#### Auth Resolution (L191–229)

| Function                 | Lines     | Purpose                                      |
|--------------------------|-----------|----------------------------------------------|
| `_resolve_user_id(token)`| L191–211  | `GET /auth/user/me` → returns `clerk_id`     |
| `_get_subscription_type(token)` | L214–229 | Same endpoint → returns `subscription_type` |
| `_require_premium_profile(token)` | L1350–1371 | Asserts `subscription_active == True`, returns full profile |

All three call `AUTH_SERVICE_URL/auth/user/me` with the Bearer token.

---

#### File Persistence Pipeline (L236–366)

This is the core 4-step embed-and-store pipeline. Every processing endpoint follows this pattern:

```
HTTP Handler                Background Thread (_embed_pool)
────────────                ──────────────────────────────
1. Process audio              ┌───────────────────────────┐
2. _stage_file() ──copy──▸    │ _embed_and_store_sync()   │
3. _schedule_embed() ──▸      │  1. embedder.load_audio() │
4. Return ZIP/audio           │  2. embedder.embed()      │
   to client immediately      │  3. s3.upload_file()      │
                              │  4. qdrant.upsert()       │
                              │  5. staged_path.unlink()  │
                              └───────────────────────────┘
```

##### `_stage_file(user_id, process_type, job_id, filename, src_path)` → `Path` (L236–246)

- Copies processed file to `STAGING_DIR/{user_id}/{process_type}/{job_id}/{filename}`
- Ensures file survives after HTTP handler's temp dir cleanup.

##### `_embed_and_store_sync(staged_path, user_id, process_type, subgroup, original_filename, job_id, extra_payload)` (L262–329)

Runs **synchronously in thread pool**:

1. **Embed** — `AudioEmbedder.load_audio()` + `AudioEmbedder.embed()` → 768-D numpy vector
2. **Upload to R2** — `s3.upload_file(staged_path, bucket, r2_key)`
   - `r2_key` format: `{user_id}/{process_type}/{job_id}/{filename}`
3. **Upsert to Qdrant** — deterministic `point_id` via `uuid5(NAMESPACE_URL, "{user_id}:{r2_key}")`
4. **Cleanup** — delete staged file + try rmdir parent

###### Qdrant Point Payload Schema

```json
{
  "user_id":           "clerk_xxx",
  "filename":          "kick.wav",
  "original_filename": "beat.mp3",
  "process_type":      "separate|tag|cut|join|karaoke|convert|analyze|upload",
  "subgroup":          "main|drums|oneshots|tagged|trimmed|joined|instrumental|converted|analyzed|uploaded",
  "r2_key":            "clerk_xxx/separate/uuid/kick.wav",
  "job_id":            "uuid",
  "created_at":        "ISO 8601",
  "duration":          12.34,
  "file_size":         88244,
  // + extra_payload (e.g. bpm, key, energy_rating for /api/analyze)
}
```

##### `_schedule_embed(...)` (L332–356)

- Async wrapper — submits `_embed_and_store_sync` to `_embed_pool` via `loop.run_in_executor()`
- Attaches error callback for logging.

##### `_stem_subgroup(stem_key)` → `str` (L359–365)

Maps stem keys to subgroups: `oneshot_*` → `"oneshots"`, `drums_*` → `"drums"`, else → `"main"`.

---

#### API Endpoints — Processing + Persist

Each processing endpoint follows the same pattern:
1. Resolve `user_id` **before** heavy computation (Clerk JWT has 60s TTL)
2. Save upload to temp dir
3. Run processing (ffmpeg / Demucs / SyncTag)
4. If `user_id`: `_stage_file()` → `_schedule_embed()` (non-blocking)
5. Return result to client immediately

| Endpoint             | Lines       | Input                  | Output                | Persists As            |
|----------------------|-------------|------------------------|-----------------------|------------------------|
| `POST /api/tag`      | L381–461    | audio + isrc           | ZIP (metadata+CSV+tagged audio) | `process_type="tag", subgroup="tagged"` |
| `POST /api/separate` | L468–535    | audio                  | ZIP (all stem WAVs)   | One point per stem: `process_type="separate", subgroup={main\|drums\|oneshots}` |
| `POST /api/cut`      | L542–592    | audio + start + end    | WAV (trimmed)         | `process_type="cut", subgroup="trimmed"` |
| `POST /api/join`     | L599–668    | audio[] (2+ files)     | WAV (concatenated)    | `process_type="join", subgroup="joined"` |
| `POST /api/karaoke`  | L675–737    | audio                  | WAV (instrumental)    | `process_type="karaoke", subgroup="instrumental"` |
| `POST /api/convert`  | L756–809    | audio + format         | Converted file        | `process_type="convert", subgroup="converted"` |
| `POST /api/analyze`  | L1130–1175  | audio                  | JSON (BPM/key/loudness) | `process_type="analyze", subgroup="analyzed"` + analysis metadata in payload |

##### Format Converter Map (`_FORMAT_MAP`, L744–753)

| Format | Extension | Codec         | MIME         |
|--------|-----------|---------------|--------------|
| mp3    | .mp3      | libmp3lame    | audio/mpeg   |
| wav    | .wav      | pcm_s16le     | audio/wav    |
| flac   | .flac     | flac          | audio/flac   |
| aiff   | .aiff     | pcm_s16be     | audio/aiff   |
| aac    | .aac      | aac           | audio/aac    |
| m4a    | .m4a      | aac           | audio/mp4    |
| ogg    | .ogg      | libvorbis     | audio/ogg    |
| opus   | .opus     | libopus       | audio/ogg    |
| alac   | .m4a      | alac          | audio/mp4    |

---

#### API Endpoints — File Retrieval + Graph

| Endpoint                        | Lines       | Auth   | Gate    | Description                                                    |
|---------------------------------|-------------|--------|---------|----------------------------------------------------------------|
| `GET /api/files`                | L1182–1218  | JWT    | any     | Scroll Qdrant for user's points, return payload list (no vectors) sorted by `created_at` desc, limit 1000 |
| `GET /api/files/{point_id}/audio` | L1272–1343 | JWT   | any     | Fetch `r2_key` from Qdrant payload → `s3.get_object()` → `StreamingResponse` with 64KB chunks |
| `POST /api/files/upload`        | L1225–1265  | JWT    | premium | Bulk upload: stage each file → `_schedule_embed()`. Returns `{queued, job_id}` |
| `POST /api/files/search`        | L1374–1446  | JWT    | premium | Audio OR text query → embed → `qdrant.query_points()` with `user_id` filter. Pagination via offset/limit |
| `GET /api/files/graph`          | L1453–1562  | JWT    | premium | Scroll all user points WITH vectors → PCA→3D coords → k=4 NN edges (cosine ≥ 0.45) → `{nodes, links}` |

##### Streaming Playback — `GET /api/files/{point_id}/audio` (L1272–1343)

```
1. Validate JWT → resolve user_id
2. qdrant.retrieve(point_id) → payload
3. Assert payload.user_id == user_id (403 if mismatch)
4. r2_key = payload.r2_key
5. s3.get_object(Bucket, r2_key) → streaming body
6. StreamingResponse(64KB chunks, Content-Type by extension)
   Headers: Accept-Ranges: bytes, Cache-Control: no-store
```

MIME mapping at L1310–1312: `{mp3→audio/mpeg, wav→audio/wav, flac→audio/flac, ogg→audio/ogg, m4a→audio/mp4, aac→audio/aac}`. Default: `audio/mpeg`.

---

### 2.2 `src/embedder.py` — AudioEmbedder (85 lines)

**Primary responsibility:** M2D-CLAP model wrapper for audio and text embedding.

| Constant          | Value                                                        |
|-------------------|--------------------------------------------------------------|
| `SAMPLE_RATE`     | 16000 Hz                                                     |
| `_TARGET_SAMPLES` | 160,000 (10 seconds × 16kHz)                                |
| `DEFAULT_WEIGHT`  | `m2d_clap_vit_base-80x1001p16x16p16kpBpTI-2025/checkpoint-30.pth` |

#### Class: `AudioEmbedder`

| Method              | Signature                                   | Returns          | Notes                                                 |
|---------------------|---------------------------------------------|------------------|-------------------------------------------------------|
| `__init__`          | `(weight_file=DEFAULT_WEIGHT)`              | —                | Loads `PortableM2D`, moves to GPU if available        |
| `load_audio`        | `(path: str) → np.ndarray`                  | float32 waveform | ffmpeg decode → 16kHz mono → int16 quantization       |
| `embed`             | `(waveform: np.ndarray) → np.ndarray`       | float32 (768,)   | Tiles short samples to 10s, `encode_clap_audio()`     |
| `encode_text`       | `(text: str) → np.ndarray`                  | float32 (768,)   | `encode_clap_text()` — joint text-audio CLAP space    |

**Critical detail:** Short samples (< 10s) are **tiled** to fill the full 80×1001 mel window. Without this, all short transients collapse to near-identical embeddings.

---

### 2.3 `auth/main.py` — Auth Service (411 lines)

**Primary responsibility:** Clerk JWT validation, Stripe subscription lifecycle, user CRUD, usage quotas.

#### Config (L23–33)

| Variable                | Source            |
|-------------------------|-------------------|
| `DATABASE_URL`          | env (required)    |
| `CLERK_SECRET_KEY`      | env               |
| `CLERK_WEBHOOK_SECRET`  | env               |
| `STRIPE_SECRET_KEY`     | env               |
| `STRIPE_WEBHOOK_SECRET` | env               |
| `STRIPE_PRICE_STANDARD` | env               |
| `STRIPE_PRICE_PREMIUM`  | env               |
| `DAILY_FREE_QUOTA`      | `3`               |

#### Endpoints

| Endpoint                      | Lines     | Purpose                                          |
|-------------------------------|-----------|--------------------------------------------------|
| `GET /auth/user/me`           | L183–199  | JWT → clerk_id → upsert `last_active` → return profile |
| `POST /auth/usage/consume`    | L202–240  | Atomic daily quota decrement (free tier only)     |
| `POST /auth/webhooks/clerk`   | L243–299  | user.created → DB insert + Stripe customer create; user.updated → email sync; user.deleted → DB delete |
| `POST /auth/webhooks/stripe`  | L302–360  | subscription.created/updated → map status+type → DB update; subscription.deleted → reset to free |
| `POST /auth/billing/checkout` | L363–393  | Create Stripe Checkout Session (embedded UI mode) |
| `POST /auth/billing/portal`   | L396–410  | Create Stripe Billing Portal session              |

#### Subscription Mapping

```python
# _map_stripe_status(stripe_status) → (local_status, active_bool)
"active" | "trialing"                        → ("active", True)
"past_due" | "incomplete" | "unpaid" | "paused" → ("past_due", False)
anything else                                → ("cancelled", False)

# _map_price_to_type(price_id)
STRIPE_PRICE_PREMIUM  → "premium"
STRIPE_PRICE_STANDARD → "standard"
anything else         → "free"
```

---

### 2.4 `database/init.sql` — PostgreSQL Schema (26 lines)

```sql
CREATE TABLE users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                   VARCHAR(255) UNIQUE NOT NULL,
    clerk_id                VARCHAR(255) UNIQUE NOT NULL,
    stripe_id               VARCHAR(255) UNIQUE,
    stripe_subscription_id  VARCHAR(255) UNIQUE,
    subscription_type       VARCHAR(20) CHECK (IN ('free','standard','premium')) DEFAULT 'free',
    subscription_active     BOOLEAN DEFAULT FALSE,
    subscription_status     VARCHAR(20) CHECK (IN ('free','active','past_due','cancelled')) DEFAULT 'free',
    daily_free_downloads    INT DEFAULT 3,
    daily_downloads_reset_at TIMESTAMPTZ,
    qdrant_graph_id         VARCHAR(255),
    subscribed_at           TIMESTAMPTZ,
    last_active             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);
-- Indexes: clerk_id, email, stripe_id, stripe_subscription_id
```

---

### 2.5 `master_app.py` — Local Graph Explorer UI (430 lines)

**Primary responsibility:** Gradio-based local sample library viewer (NLP pipeline + PCA graph). Uses `src/database.AudioDatabase` for local Qdrant operations.

> **Note:** This is the **local/dev graph explorer**, separate from the production Compute API file system. It uses a standalone `AudioDatabase` class (not the shared `master_audio_graph` collection) for local sample management.

| Feature       | Handler                  | Behavior                       |
|---------------|--------------------------|--------------------------------|
| NLP Pipeline  | `nlp_query()` L26–53     | Text/audio → Orchestrator → search results |
| Graph View    | `get_graph_data()` L77–94 | PCA→2D scatter of all local embeddings |
| Add Samples   | `add_samples()` L190–221  | File → embed → Qdrant upsert    |
| Remove        | `remove_selected()` L224–232 | Delete selected points        |

### 2.6 `master_orchestrator.py` — NLP Intent Router (218 lines)

**Primary responsibility:** Routes natural-language queries through Gemini LLM intent parsing → pipeline execution.

| Action        | Handler                     | Flow                                            |
|---------------|-----------------------------|-------------------------------------------------|
| `decompose`   | `_do_decompose()` L140–153  | Demucs separation → return stem paths           |
| `search_stem` | `_do_search_stem()` L155–193| Decompose → embed target stem → Qdrant search   |
| `search_clip` | `_do_search_clip()` L195–206| Embed raw audio → Qdrant search                 |
| `text_search` | `_do_text_search()` L208–217| `encode_text()` → Qdrant search                 |

---

## 3. Data Flow Diagrams

### 3.1 Upload → Embed → Store (any processing endpoint)

```
User uploads audio file via POST /api/{tag|separate|cut|join|karaoke|convert|analyze}
  │
  ├─▸ Auth: resolve user_id from Bearer JWT BEFORE processing (60s JWT TTL)
  │
  ├─▸ Process: ffmpeg / Demucs / SyncTag pipeline
  │
  ├─▸ Return: ZIP or audio response to client immediately
  │
  └─▸ Background (if user_id):
       ├─▸ _stage_file(): copy output to /tmp/syntag_staging/{user_id}/{type}/{job_id}/
       ├─▸ _schedule_embed(): submit to ThreadPoolExecutor(2)
       │     ├─▸ embedder.load_audio(staged_path) → float32 waveform
       │     ├─▸ embedder.embed(waveform) → 768-D vector
       │     ├─▸ s3.upload_file(staged_path, R2_BUCKET, r2_key)
       │     │     r2_key = "{user_id}/{process_type}/{job_id}/{filename}"
       │     ├─▸ qdrant.upsert(point_id, vector, payload)
       │     │     point_id = uuid5(NAMESPACE_URL, "{user_id}:{r2_key}")
       │     └─▸ staged_path.unlink()
       └─▸ Done (fire-and-forget)
```

### 3.2 File Retrieval + Playback

```
GET /api/files
  ├─▸ JWT → user_id
  └─▸ qdrant.scroll(filter: user_id, limit: 1000, with_vectors: False)
       └─▸ Return [{id, filename, process_type, subgroup, duration, ...}]

GET /api/files/{point_id}/audio
  ├─▸ JWT → user_id
  ├─▸ qdrant.retrieve(point_id) → payload
  ├─▸ Assert payload.user_id == user_id
  ├─▸ s3.get_object(Bucket, payload.r2_key)
  └─▸ StreamingResponse(64KB chunks)
       Content-Type: auto-detected from file extension
```

### 3.3 Similarity Search (Premium Only)

```
POST /api/files/search
  ├─▸ JWT → _require_premium_profile() (asserts subscription_active)
  ├─▸ Input: audio file OR query_text (not both required)
  │     ├─▸ Audio: embedder.load_audio() → embedder.embed() → 768-D
  │     └─▸ Text:  embedder.encode_text() → 768-D (same CLAP space)
  ├─▸ qdrant.query_points(vector, filter: user_id, limit: offset+limit+1)
  └─▸ Return {results: [{id, score, ...payload}], has_more: bool}
```

### 3.4 3D Graph Visualization (Premium Only)

```
GET /api/files/graph
  ├─▸ JWT → _require_premium_profile()
  ├─▸ qdrant.scroll(filter: user_id, limit: 500, with_vectors: True)
  ├─▸ PCA(768-D → 3-D), scale ±150
  ├─▸ k-NN edges: k=4, cosine similarity ≥ 0.45
  └─▸ Return {nodes: [{id, x, y, z, filename, ...}], links: [{source, target, similarity}]}
```

---

## 4. Access Control Matrix

| Endpoint                          | Free | Standard | Premium |
|-----------------------------------|------|----------|---------|
| `POST /api/tag`                   | ✓*   | ✓        | ✓       |
| `POST /api/separate`             | ✓*   | ✓        | ✓       |
| `POST /api/cut`                  | ✓*   | ✓        | ✓       |
| `POST /api/join`                 | ✓*   | ✓        | ✓       |
| `POST /api/karaoke`             | ✓*   | ✓        | ✓       |
| `POST /api/convert`             | ✓*   | ✓        | ✓       |
| `POST /api/analyze`             | ✓*   | ✓        | ✓       |
| `POST /api/bpm-key`             | ✓    | ✓        | ✓       |
| `GET /api/files`                 | ✓    | ✓        | ✓       |
| `GET /api/files/{id}/audio`      | ✓    | ✓        | ✓       |
| `POST /api/files/upload`        | ✗    | ✗        | ✓       |
| `POST /api/files/search`        | ✗    | ✗        | ✓       |
| `GET /api/files/graph`          | ✗    | ✗        | ✓       |

`*` = subject to `daily_free_downloads` quota (3/day, reset at 04:00 PST). Quota consumed via `POST /auth/usage/consume`.

**Persist-to-graph behavior:** All processing endpoints persist files + embed vectors to Qdrant only when a valid JWT is present **and** `subscription_type != "free"`. Free-tier authenticated users still receive the processed output but nothing is staged or embedded. No JWT = anonymous use (no storage).

---

## 5. Environment Variables Reference

### Compute Service (`api/main.py`)

| Variable                | Required | Default           | Notes                              |
|-------------------------|----------|-------------------|------------------------------------|
| `QDRANT_URL`            | no       | `http://localhost:6333` |                              |
| `AUTH_SERVICE_URL`      | no       | `http://localhost:8001` |                              |
| `R2_ACCOUNT_ID`         | prod     | `""`              | Cloudflare R2                      |
| `R2_ACCESS_KEY_ID`      | no       | `minioadmin`      | MinIO default                      |
| `R2_SECRET_ACCESS_KEY`  | no       | `minioadmin`      | MinIO default                      |
| `R2_BUCKET_NAME`        | no       | `syntag-audio`    |                                    |
| `S3_ENDPOINT_URL`       | dev      | `""`              | MinIO explicit endpoint            |
| `S3_PUBLIC_ENDPOINT_URL`| dev      | `""`              | For presigned URLs from MinIO      |
| `STAGING_DIR`           | no       | `/tmp/syntag_staging` |                               |
| `DEV_USER_ID`           | dev      | `""`              | Bypass auth in dev                 |
| `M2D_WEIGHT`            | no       | Auto-detected     | Path to M2D-CLAP checkpoint        |

### Auth Service (`auth/main.py`)

| Variable                    | Required | Notes                          |
|-----------------------------|----------|--------------------------------|
| `DATABASE_URL`              | yes      | PostgreSQL connection string   |
| `CLERK_SECRET_KEY`          | yes      | Clerk API key                  |
| `CLERK_WEBHOOK_SECRET`      | yes      | Svix webhook verification      |
| `STRIPE_SECRET_KEY`         | yes      | Stripe API key                 |
| `STRIPE_WEBHOOK_SECRET`     | yes      | Stripe webhook signing secret  |
| `STRIPE_PRICE_ID_STANDARD`  | yes      | Stripe price for $9.99/mo      |
| `STRIPE_PRICE_ID_PREMIUM`   | yes      | Stripe price for $14.99/mo     |
| `VITE_CLERK_PUBLISHABLE_KEY`| yes      | Derives FAPI URL for JWKS      |
| `APP_URL`                   | no       | `http://localhost:7860`        |

---

## 6. Key Implementation Details

### R2 Key Naming Convention
```
{clerk_id}/{process_type}/{job_id}/{filename}
```
Example: `user_2x.../separate/a1b2c3d4-.../vocals.wav`

### Qdrant Point ID Generation
```python
point_id = str(uuid5(NAMESPACE_URL, f"{user_id}:{r2_key}"))
```
**Deterministic** — re-processing the same file with the same job_id overwrites the previous point (upsert).

### Thread Pool Sizing
`max_workers=2` — prevents ML model from saturating CPU/GPU during concurrent embed tasks. Background embedding is fire-and-forget; HTTP responses don't wait for it.

### JWT TTL Workaround
All endpoints resolve `user_id` **before** running heavy computation (Demucs: 60–180s). The Clerk JWT has a 60-second TTL, so authenticating after processing would fail.
