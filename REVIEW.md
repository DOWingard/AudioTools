# Security & Scalability Review
**Scope:** Payment system (`auth/main.py`), Qdrant+S3 storage (`api/main.py`), DB schema (`database/init.sql`)
**Date:** 2026-03-01

---

## Critical

---

### 1. No upload size limits → OOM / denial-of-service

**Files:** `api/main.py:429, 534, 599, 1291` (and every other processing endpoint)

**How it arises:**
Every upload endpoint reads the entire file body into a Python `bytes` object before doing anything:

```python
content = await audio.read()
input_path.write_bytes(content)
```

There is no `Content-Length` check, no streaming write, and no FastAPI size guard. The pattern appears in `/api/tag`, `/api/separate`, `/api/cut`, `/api/join`, `/api/karaoke`, `/api/convert`, and `/api/files/upload` — all seven processing endpoints. An unauthenticated or free-tier user can POST a multi-gigabyte file and exhaust the process heap, crashing the container or causing the OOM killer to intervene.

**Optimal fix:**
Add a streaming size-checked write helper and use it in every endpoint. Do not read the whole file first.

```python
MAX_UPLOAD_BYTES = 250 * 1024 * 1024  # 250 MB

async def _save_upload(upload: UploadFile, dest: Path) -> None:
    total = 0
    with dest.open("wb") as fh:
        async for chunk in upload:  # UploadFile is async-iterable
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit",
                )
            fh.write(chunk)
```

Replace every `content = await audio.read(); input_path.write_bytes(content)` pair with `await _save_upload(audio, input_path)`.

Also add a FastAPI middleware guard as a second line of defence:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_UPLOAD_BYTES:
            return Response("Payload too large", status_code=413)
        return await call_next(request)

app.add_middleware(MaxBodySizeMiddleware)
```

---

### 2. Path traversal via `audio.filename`

**Files:** `api/main.py:424, 532, 598` (and every endpoint that writes `tmp_dir / audio.filename`)

**How it arises:**
User-controlled filenames are joined directly to the temp directory path:

```python
input_path = tmp_dir / (audio.filename or "input.wav")
```

Python's `Path.__truediv__` preserves `..` components. `Path("/tmp/api_tag_abc") / "../../etc/cron.d/evil"` resolves to `/etc/cron.d/evil`. A multipart upload with `Content-Disposition: form-data; name="audio"; filename="../../etc/shadow"` writes the uploaded bytes to an arbitrary path reachable by the process user. On a container running as root (violating the `USER node` requirement in CLAUDE.md) this is a full filesystem write primitive.

**Optimal fix:**
Strip all directory components from the filename before joining. `Path.name` returns only the final component with no separators:

```python
safe_name = Path(audio.filename or "input.wav").name or "input.wav"
input_path = tmp_dir / safe_name
```

Apply this one-liner at every site where `audio.filename` is joined to a path. The `/api/tag` endpoint already has more complex filename logic (MIME fallback); apply the same `.name` strip there too.

---

### 3. Standard subscribers access premium features

**Files:** `api/main.py:1397-1418`

**How it arises:**
The premium gate function only checks the boolean `subscription_active`:

```python
async def _require_premium_profile(authorization: str) -> dict:
    ...
    if not profile.get("subscription_active"):
        raise HTTPException(status_code=403, detail="Premium subscription required")
    return profile
```

`subscription_active` is `True` for both `standard` ($9.99/mo) and `premium` ($14.99/mo) subscribers — see `_map_stripe_status` in `auth/main.py:160`. The database `subscription_type` column distinguishes the tiers, but this function ignores it. A standard subscriber can freely call `/api/files/upload`, `/api/files/search`, and `/api/files/graph` — all gated as premium-only in the UI.

**Optimal fix:**
Accept a `required_type` parameter (defaulting to `"premium"`) and check both fields:

```python
async def _require_premium_profile(
    authorization: str,
    required_type: str = "premium",
) -> dict:
    ...
    profile = resp.json()
    if not profile.get("clerk_id"):
        raise HTTPException(status_code=401, detail="Cannot determine user identity")
    if not profile.get("subscription_active"):
        raise HTTPException(status_code=403, detail="Active subscription required")

    TYPE_RANK = {"free": 0, "standard": 1, "premium": 2}
    user_rank = TYPE_RANK.get(profile.get("subscription_type", "free"), 0)
    required_rank = TYPE_RANK.get(required_type, 2)
    if user_rank < required_rank:
        raise HTTPException(
            status_code=403,
            detail=f"{required_type.capitalize()} subscription required",
        )
    return profile
```

If standard subscribers should have storage but not search/graph, call `_require_premium_profile(token, "standard")` for upload and `_require_premium_profile(token, "premium")` for search/graph. Pick a clear tier boundary and enforce it consistently.

---

### 4. Free users get implicit unlimited storage via processing endpoints

**Files:** `api/main.py:477, 543, 594, 650, 700, 760, ...`

**How it arises:**
Every processing endpoint — tag, separate, cut, join, karaoke, convert, analyze — embeds and stores output files for **any authenticated user** with no subscription check:

```python
user_id = None
if authorization:
    token = authorization.removeprefix("Bearer ").strip()
    user_id = await _resolve_user_id(token)
...
if user_id:          # free-tier user passes this check
    dest = _stage_file(user_id, "tag", job_id, ...)
    asyncio.create_task(_schedule_embed(...))
```

A free-tier user (limited to 3 operations/day by quota) still accumulates files in Qdrant and S3 through these endpoints. This bypasses the premium gate on `/api/files/upload` entirely — the premium upload endpoint is redundant. Over time, free users generate unbounded storage costs.

**Optimal fix:**
Decide the intended policy explicitly and enforce it in one place. For this MVP, the simplest correct behaviour is: **only store files for `subscription_active` users**. Add a subscription check before staging:

```python
if user_id:
    sub_type = await _get_subscription_type(token)
    if sub_type and sub_type != "free":
        dest = _stage_file(user_id, "tag", job_id, ...)
        asyncio.create_task(_schedule_embed(...))
```

`_get_subscription_type` already exists at `api/main.py:214` and returns the `subscription_type` string. This single guard applied uniformly across all processing endpoints closes the bypass.

---

### 5. `DEV_USER_ID` auth bypass present in production code path

**Files:** `api/main.py:65, 1321-1322`

**How it arises:**
A development shortcut is wired into the production audio-serving endpoint:

```python
DEV_USER_ID = os.environ.get("DEV_USER_ID", "").strip()

@app.get("/api/files/{point_id}/audio")
async def get_file_audio(...):
    user_id = None
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
        user_id = await _resolve_user_id(token)
    if not user_id and DEV_USER_ID:   # ← bypass
        user_id = DEV_USER_ID
    if not user_id:
        raise HTTPException(status_code=401, ...)
```

If `DEV_USER_ID` is set in any non-local environment — misconfigured `.env`, accidentally copied secret, CI/CD variable leak — every unauthenticated request to `/api/files/<any_valid_id>/audio` resolves as that user. An attacker who knows or guesses a valid Qdrant point ID can download any file belonging to the dev user with no credentials.

**Optimal fix:**
Remove the bypass entirely. If local development without Clerk is needed, use a separate `main_dev.py` entry point or a mock auth middleware that is never imported in production. There is no safe way to keep an auth bypass in the same file as the production endpoint.

```python
# Delete these two lines:
DEV_USER_ID = os.environ.get("DEV_USER_ID", "").strip()

# Delete this block in get_file_audio:
if not user_id and DEV_USER_ID:
    user_id = DEV_USER_ID
```

---

### 6. CORS wildcard on a user-data API

**Files:** `api/main.py:71-76`

**How it arises:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`allow_origins=["*"]` prevents browsers from attaching credentials to cross-origin requests (which is correct per the Fetch spec), but it also means any origin can read the response body of any request that succeeds — including unauthenticated endpoints and error responses that leak internal details. More practically: it makes it impossible to ever add `allow_credentials=True` without rewriting this (you cannot combine `allow_origins=["*"]` with `allow_credentials=True`). Lock down origins now while the origin list is small.

**Optimal fix:**
Restrict to known origins via an environment variable:

```python
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:7860").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

Set `ALLOWED_ORIGINS=https://yourdomain.com` in production `.env`.

---

## High

---

### 7. User deletion doesn't purge Qdrant or S3 (GDPR)

**Files:** `auth/main.py:293-297`

**How it arises:**
The Clerk `user.deleted` webhook only removes the Postgres row:

```python
elif event_type == "user.deleted":
    clerk_id = data["id"]
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE clerk_id = $1", clerk_id)
```

The user's `clerk_id` is stored as `user_id` in every Qdrant payload, and their files are stored under `{clerk_id}/` in S3. Both persist indefinitely after account deletion. Under GDPR Article 17 (right to erasure), this is a compliance violation. Under cost grounds, it is unbounded orphaned storage.

**Optimal fix:**
The auth service cannot directly talk to Qdrant or S3. Add a deletion task to a queue that the compute API consumes, or expose an internal `/internal/users/{clerk_id}` DELETE endpoint on the compute API that the auth service calls:

In `auth/main.py`, after deleting from Postgres:
```python
elif event_type == "user.deleted":
    clerk_id = data["id"]
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE clerk_id = $1", clerk_id)
    # Fire-and-forget purge call to compute API
    async with httpx.AsyncClient() as client:
        try:
            await client.delete(
                f"{COMPUTE_API_URL}/internal/users/{clerk_id}",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=10.0,
            )
        except Exception as e:
            log.error("Failed to schedule user data purge for %s: %s", clerk_id, e)
```

In `api/main.py`, add the internal endpoint:
```python
@app.delete("/internal/users/{clerk_id}")
async def purge_user_data(clerk_id: str, x_internal_secret: str = Header(...)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403)
    # Delete from Qdrant by filter
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    _get_qdrant().delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=clerk_id))]
        ),
    )
    # Delete from S3 (list + batch delete)
    s3 = _get_s3_upload()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=f"{clerk_id}/"):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=R2_BUCKET_NAME, Delete={"Objects": keys})
```

---

### 8. Missing `invoice.payment_failed` webhook handler

**Files:** `auth/main.py:316`

**How it arises:**
The Stripe webhook handler covers `subscription.created`, `subscription.updated`, and `subscription.deleted`. It does not handle `invoice.payment_failed`. When a renewal payment fails, Stripe marks the subscription `past_due` and begins retrying. `subscription.updated` fires with status `past_due` — which `_map_stripe_status` correctly maps to `active=False`. However, Stripe's dunning cycle can take days. If the first failed payment event is missed (webhook delivery failure, cold-start race), the user retains access until the next status-change event.

More critically, `customer.subscription.updated` with status `past_due` does set `subscription_active=FALSE`, but only if delivered. `invoice.payment_failed` fires immediately and is a separate, earlier signal. Without it, there is a window between the failed charge and the subscription status update where the user still has access.

**Optimal fix:**
Add an explicit handler:

```python
elif event_type == "invoice.payment_failed":
    stripe_customer_id = obj.get("customer")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET subscription_active  = FALSE,
                subscription_status  = 'past_due'
            WHERE stripe_id = $1
            """,
            stripe_customer_id,
        )
    log.warning("invoice.payment_failed for customer %s — access suspended", stripe_customer_id)
```

Also add `invoice.payment_succeeded` to re-activate on successful retry:

```python
elif event_type == "invoice.payment_succeeded":
    stripe_customer_id = obj.get("customer")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET subscription_active = TRUE,
                subscription_status = 'active'
            WHERE stripe_id = $1 AND subscription_status = 'past_due'
            """,
            stripe_customer_id,
        )
```

---

### 9. No Stripe idempotency keys

**Files:** `auth/main.py:392, 409`

**How it arises:**
```python
session = stripe.checkout.Session.create(**session_kwargs)
portal = stripe.billing_portal.Session.create(customer=..., return_url=APP_URL)
```

Both calls are Stripe POST requests with no `idempotency_key`. If the client double-clicks the subscribe button, a network timeout causes a retry, or a load balancer duplicates the request, multiple checkout sessions are created. Stripe will not deduplicate them. Your own CLAUDE.md engineering standards explicitly require idempotency keys on all Stripe POST requests.

**Optimal fix:**
Derive a deterministic idempotency key from the user and intent:

```python
import hashlib, time

# Checkout — stable per user+plan within a 10-minute window
window = int(time.time()) // 600
idempotency_key = hashlib.sha256(
    f"checkout:{clerk_id}:{plan}:{window}".encode()
).hexdigest()

session = stripe.checkout.Session.create(
    **session_kwargs,
    idempotency_key=idempotency_key,
)
```

For the portal, a per-user-per-minute key is sufficient since portal sessions are idempotent by nature:

```python
window = int(time.time()) // 60
idempotency_key = hashlib.sha256(f"portal:{clerk_id}:{window}".encode()).hexdigest()
portal = stripe.billing_portal.Session.create(
    customer=row["stripe_id"],
    return_url=APP_URL,
    idempotency_key=idempotency_key,
)
```

---

### 10. 500 responses expose internal error details

**Files:** `api/main.py:500, 574, 1212, 1257, 1490`

**How it arises:**
The catch-all pattern throughout the compute API is:

```python
except Exception as exc:
    raise HTTPException(status_code=500, detail=str(exc)) from exc
```

`str(exc)` for a `FileNotFoundError` includes the full filesystem path. For a `boto3.exceptions.S3UploadFailedError` it includes the bucket name and key. For a `subprocess.CalledProcessError` it includes the ffmpeg command line. All of this is sent to the client verbatim.

**Optimal fix:**
Log the full exception server-side, return a generic message to the client:

```python
import logging
log = logging.getLogger("api")

except Exception as exc:
    log.exception("Unhandled error in %s", request.url.path)
    raise HTTPException(status_code=500, detail="Internal server error") from exc
```

The one exception (intended) is the S3 fetch in `get_file_audio` at line 1368, which additionally leaks the R2 key in the error message — apply the same treatment there.

---

### 11. S3 errors leak R2 object keys to clients

**Files:** `api/main.py:1368`

**How it arises:**
```python
except Exception as exc:
    raise HTTPException(
        status_code=500,
        detail=f"Failed to fetch audio from storage: {exc}",
    )
```

boto3 raises errors like `NoSuchKey: An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist.` where the key is `{clerk_id}/{process_type}/{job_id}/{filename}`. This exposes the user's `clerk_id`, the internal R2 key structure, and the job UUID — all useful for enumeration attacks.

**Optimal fix:**
```python
except Exception as exc:
    log.error("S3 fetch failed for key %s: %s", r2_key, exc)
    raise HTTPException(status_code=500, detail="Failed to fetch audio file") from exc
```

---

## Medium

---

### 12. `qdrant.scroll` silently truncates large libraries

**Files:** `api/main.py:1240, 1520`

**How it arises:**
Two endpoints use `scroll` with a hardcoded upper limit:

```python
# list_files:
records, _ = qdrant.scroll(..., limit=1000)

# get_graph_data:
records, _ = qdrant.scroll(..., limit=500)
```

Qdrant's `scroll` returns at most `limit` records and a `next_page_offset` cursor. The cursor is discarded (`_`). A user with more than 1,000 files will see only their most recently-inserted 1,000 entries in the file list and have a silently incomplete graph. The UI has no indication of truncation.

**Optimal fix:**
Paginate until the cursor is exhausted:

```python
def _scroll_all(qdrant_client, collection, scroll_filter, with_vectors=False):
    """Collect all matching records, paginating automatically."""
    records = []
    offset = None
    while True:
        batch, offset = qdrant_client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            with_payload=True,
            with_vectors=with_vectors,
            limit=256,
            offset=offset,
        )
        records.extend(batch)
        if offset is None:
            break
    return records
```

Use `_scroll_all(...)` in both `list_files` and `get_graph_data`. For the graph endpoint, also add a hard cap (e.g., 2,000 nodes) with a clear `truncated: true` field in the response so the frontend can inform the user.

---

### 13. O(n²) graph computation runs in the async handler

**Files:** `api/main.py:1582`

**How it arises:**
```python
sim_matrix = normed @ normed.T  # (n, n) dense matrix, n up to 500 (or more after fix #12)
```

For 500 files this is 250,000 dot products of 768-dimensional vectors — roughly 192 million float32 multiplications. PCA on top adds more. This entire computation runs synchronously inside an `async def` handler, blocking the event loop for other requests during the computation. At 1,000+ files it becomes a latency spike measurable in seconds.

**Optimal fix for MVP:**
Offload to the thread pool so the event loop is not blocked:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_graph_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graph")

def _compute_graph(records) -> dict:
    # existing PCA + k-NN logic
    ...
    return {"nodes": nodes, "links": links}

@app.get("/api/files/graph")
async def get_graph_data(...):
    ...
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_graph_pool, _compute_graph, records)
    return JSONResponse(result)
```

**Fix for scale beyond MVP:** Replace the manual k-NN with Qdrant's native `recommend` or indexed ANN search per node. This moves the O(n²) work to Qdrant (which can do it efficiently with HNSW) and eliminates the need to fetch all vectors into Python.

---

### 14. Singleton initialisation race condition (not thread-safe)

**Files:** `api/main.py:152, 118, 133, 96`

**How it arises:**
The pattern used for every singleton — Qdrant client, S3 clients, tagger, embedder — is:

```python
global _qdrant
if _qdrant is not None:
    return _qdrant
# ... initialise
_qdrant = client
return _qdrant
```

The embed thread pool (`max_workers=2`) calls `_get_qdrant()`, `_get_embedder()`, and `_get_s3_upload()` from worker threads concurrently with the async event loop. Without a lock, two threads can both observe `_qdrant is None` simultaneously, each create a separate `QdrantClient`, and the second assignment silently leaks the first connection.

**Optimal fix:**
Use a `threading.Lock` for each singleton. For the async-facing code, use `asyncio.Lock` (or ensure the singletons are initialised eagerly at startup instead):

```python
import threading

_qdrant_lock = threading.Lock()

def _get_qdrant():
    global _qdrant
    if _qdrant is not None:
        return _qdrant
    with _qdrant_lock:
        if _qdrant is None:   # re-check inside lock
            # ... create client
            _qdrant = client
    return _qdrant
```

The double-checked lock pattern (check → lock → re-check) avoids taking the lock on every call after warm-up. Apply the same pattern to `_s3_upload`, `_s3_presign`, `_tagger`, and `_embedder`.

**Simpler alternative for MVP:** Eagerly initialise all singletons in a `@app.on_event("startup")` handler before the event loop begins serving requests. This avoids the race entirely.

---

### 15. JWKS cache has no TTL and blocks the event loop

**Files:** `auth/main.py:49-59`

**How it arises:**
The JWKS cache is populated once on first use and never refreshed proactively:

```python
_jwks_cache: Optional[dict] = None

def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        url = f"{_clerk_fapi_url()}/.well-known/jwks.json"
        resp = requests.get(url, timeout=10)   # ← synchronous, blocks event loop
        _jwks_cache = resp.json()
    return _jwks_cache
```

Two problems:
1. `requests.get` is a blocking synchronous call. When `_get_jwks()` is called for the first time (or after cache clear), it blocks the asyncio event loop for up to 10 seconds.
2. Clerk rotates signing keys periodically. The cache is only cleared on `JWTError` (line 136). If a key is rotated while the server is running, the error-retry path re-fetches, but there is a window where all incoming tokens with the new key fail before the retry succeeds.

**Optimal fix:**
Replace `requests` with `httpx` and use a TTL-based cache. Call from the async lifespan to pre-warm:

```python
import time
import httpx

_jwks_cache: Optional[dict] = None
_jwks_fetched_at: float = 0.0
JWKS_TTL = 3600  # 1 hour

async def _refresh_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    url = f"{_clerk_fapi_url()}/.well-known/jwks.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
    _jwks_cache = resp.json()
    _jwks_fetched_at = time.monotonic()
    return _jwks_cache

def _get_jwks() -> dict:
    # Called from sync verify_clerk_token — return cached value only
    if _jwks_cache is None:
        raise RuntimeError("JWKS not loaded — call _refresh_jwks() at startup")
    return _jwks_cache
```

In the `lifespan` handler, pre-warm:
```python
async with lifespan(app):
    await _refresh_jwks()
    # schedule periodic refresh
    scheduler.add_job(_refresh_jwks, "interval", hours=1, id="jwks_refresh")
```

---

### 16. Synchronous `requests.get` in async auth path

**Files:** `auth/main.py:55`

**How it arises:**
Even setting aside the TTL issue in #15, `_get_jwks()` uses the `requests` library which makes a synchronous blocking HTTP call. This is called from `verify_clerk_token`, which is called from every authenticated route handler. Until the cache is warm, every new worker process blocks its event loop on this call.

**Optimal fix:**
Addressed in fix #15 above — move the HTTP call to `httpx` and pre-warm at startup. If `verify_clerk_token` must remain synchronous (e.g., it's called from non-async contexts), ensure the cache is always pre-populated before any request is served, so `_get_jwks()` only reads from cache and never makes a network call at request time.

---

### 17. Deprecated `asyncio.get_event_loop()` and blocking S3 read in streaming response

**Files:** `api/main.py:1363, 1373-1378`

**How it arises:**
```python
# Deprecated in Python 3.10+
obj = await asyncio.get_event_loop().run_in_executor(
    None,
    lambda: s3.get_object(Bucket=R2_BUCKET_NAME, Key=r2_key),
)

body = obj["Body"]

def _iter_chunks():       # synchronous generator
    while True:
        chunk = body.read(65536)   # ← blocks the event loop
        if not chunk:
            break
        yield chunk

return StreamingResponse(_iter_chunks(), ...)
```

The `get_object` call is correctly offloaded, but `_iter_chunks()` is a synchronous generator. FastAPI's `StreamingResponse` calls `next()` on it from the event loop — each `body.read(65536)` blocks the event loop while waiting for the S3 TCP socket to deliver the next chunk.

**Optimal fix:**
Use an async generator with `run_in_executor` for each read, or use `aiobotocore`/`s3fs` for truly async S3 access. The minimal fix is:

```python
loop = asyncio.get_running_loop()  # not get_event_loop()

async def _aiter_chunks():
    while True:
        chunk = await loop.run_in_executor(None, body.read, 65536)
        if not chunk:
            break
        yield chunk

return StreamingResponse(_aiter_chunks(), media_type=content_type, headers=headers)
```

---

### 18. No per-user storage quota

**Files:** `api/main.py:1264` (and all processing endpoints that call `_schedule_embed`)

**How it arises:**
There is no cap on the number of files or total bytes a premium subscriber can store. The bulk upload endpoint (`/api/files/upload`) accepts an unlimited number of files in a single request and queues them all. A single premium user can upload thousands of large audio files, consuming unbounded S3 storage and Qdrant vector slots at the operator's cost.

**Optimal fix for MVP:**
Enforce a file count quota checked before staging:

```python
MAX_FILES_PER_USER = 500  # adjust per business model

async def _check_user_quota(user_id: str) -> None:
    from qdrant_client.models import FieldCondition, Filter, MatchValue, CountRequest
    count = _get_qdrant().count(
        collection_name=COLLECTION_NAME,
        count_filter=Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        ),
        exact=False,  # approximate count is fine for quota
    ).count
    if count >= MAX_FILES_PER_USER:
        raise HTTPException(
            status_code=429,
            detail=f"Storage quota exceeded ({MAX_FILES_PER_USER} files maximum)",
        )
```

Call `await _check_user_quota(user_id)` in `upload_files_to_graph` before the loop, and in each processing endpoint before calling `_schedule_embed`.

---

### 19. No file type validation (magic bytes)

**Files:** `api/main.py:429, 534` (all upload endpoints)

**How it arises:**
Uploaded files are identified solely by `Content-Type` header and filename extension — both attacker-controlled. A file with extension `.wav` and `Content-Type: audio/wav` containing a crafted payload (zip bomb, malformed media container, file exploiting a known ffmpeg CVE) is passed directly to `ffmpeg`, `ffprobe`, and the M2D model with no validation. ffmpeg has had numerous remote code execution vulnerabilities via crafted media files.

**Optimal fix for MVP:**
Read the first 12 bytes and check against known audio magic signatures before processing:

```python
AUDIO_MAGIC = {
    b"RIFF": "wav",         # WAV
    b"fLaC": "flac",        # FLAC
    b"OggS": "ogg",         # OGG
    b"ID3":  "mp3",         # MP3 with ID3
    b"\xff\xfb": "mp3",     # MP3 without ID3
    b"\xff\xf3": "mp3",
    b"\xff\xf2": "mp3",
    b"FORM": "aiff",        # AIFF
}

def _validate_audio_magic(path: Path) -> None:
    header = path.read_bytes()[:12]
    for magic, fmt in AUDIO_MAGIC.items():
        if header.startswith(magic):
            return
    raise HTTPException(
        status_code=415,
        detail="Unsupported or invalid audio file format",
    )
```

Call `_validate_audio_magic(input_path)` immediately after writing the upload to disk, before passing it to any processing pipeline.

---

## Low

---

### 20. No file deletion endpoint

**How it arises:**
The API exposes no way for a user to delete an individual file from their graph. Files accumulate permanently unless the entire account is deleted. This creates UX friction, makes storage quota management impossible from the client side, and violates GDPR Article 17 for individual-record erasure requests (distinct from full account deletion covered in #7).

**Optimal fix:**
```python
@app.delete("/api/files/{point_id}")
async def delete_file(point_id: str, authorization: str = Header(default=None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = await _resolve_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    qdrant = _get_qdrant()
    records = qdrant.retrieve(
        collection_name=COLLECTION_NAME, ids=[point_id], with_payload=True
    )
    if not records:
        raise HTTPException(status_code=404, detail="File not found")

    payload = records[0].payload
    if payload.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    r2_key = payload.get("r2_key")
    if r2_key:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: _get_s3_upload().delete_object(Bucket=R2_BUCKET_NAME, Key=r2_key),
        )

    qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=[point_id],
    )
    return JSONResponse({"deleted": point_id})
```

---

### 21. Inconsistent subscription state possible in DB schema

**Files:** `database/init.sql:9-13`

**How it arises:**
The schema has two overlapping status columns — `subscription_active` (boolean) and `subscription_status` (enum) — that can drift into contradictory states. For example: `subscription_active=TRUE, subscription_status='cancelled'` is a valid row by the CHECK constraints. If a webhook fires out-of-order (Stripe does not guarantee delivery order), the two columns can be updated independently to contradictory values.

The auth logic in `_require_premium_profile` checks `subscription_active`, while the quota logic in `consume_usage` also checks `subscription_active`. If `subscription_status='cancelled'` but `subscription_active=TRUE` due to a race, the user retains access.

**Optimal fix for MVP:**
Add a DB-level consistency constraint. A generated column or a check that relates the two fields prevents contradictory states:

```sql
ALTER TABLE users ADD CONSTRAINT chk_subscription_consistency CHECK (
    (subscription_active = TRUE  AND subscription_status IN ('active')) OR
    (subscription_active = FALSE AND subscription_status IN ('free', 'past_due', 'cancelled'))
);
```

This makes any contradictory update fail at the DB level rather than silently persisting.

---

### 22. Staging directory is world-readable

**Files:** `api/main.py:61`

**How it arises:**
```python
STAGING_DIR = Path(os.environ.get("STAGING_DIR", "/tmp/syntag_staging"))
STAGING_DIR.mkdir(parents=True, exist_ok=True)
```

`Path.mkdir` defaults to mode `0o777` (modified by umask, typically yielding `0o755`). On a shared host or multi-container environment with a shared `/tmp` mount, other processes can read audio files while they wait in the staging directory for background embedding. The default `/tmp` location is also cleaned by the OS, potentially deleting files before embedding completes.

**Optimal fix:**
Create the staging directory with restricted permissions and use a path outside `/tmp`:

```python
STAGING_DIR = Path(os.environ.get("STAGING_DIR", "/var/lib/syntag/staging"))
STAGING_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
```

Also add a startup sweep to clean up orphaned staging files older than 1 hour (handles crash recovery):

```python
import time as _time

def _cleanup_stale_staging():
    cutoff = _time.time() - 3600
    for f in STAGING_DIR.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
```

---

### 23. No subscription event audit log

**Files:** `auth/main.py:316-360`

**How it arises:**
Stripe webhook events update the `users` table in-place. There is no record of subscription state transitions. If Stripe and the database diverge — missed webhook delivery, processing exception, out-of-order events — there is no history to reconstruct what happened or when. Customer support, billing disputes, and debugging are all blind.

**Optimal fix:**
Add a `subscription_events` table and append to it on every webhook:

```sql
CREATE TABLE IF NOT EXISTS subscription_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_id      VARCHAR(255),
    stripe_id     VARCHAR(255),
    event_type    VARCHAR(100) NOT NULL,
    stripe_status VARCHAR(50),
    local_status  VARCHAR(50),
    sub_type      VARCHAR(20),
    raw_event_id  VARCHAR(255),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

In the webhook handler, after updating `users`, insert a row:

```python
await conn.execute(
    """
    INSERT INTO subscription_events
        (clerk_id, stripe_id, event_type, stripe_status, local_status, sub_type, raw_event_id)
    SELECT clerk_id, $1, $2, $3, $4, $5, $6
    FROM users WHERE stripe_id = $1
    """,
    stripe_customer_id, event_type, stripe_status,
    local_status, sub_type, event["id"],
)
```

---

## Issue Index

| # | Severity | File(s) | Title |
|---|----------|---------|-------|
| 1 | Critical | `api/main.py:429+` | No upload size limits → OOM DoS |
| 2 | Critical | `api/main.py:532+` | Path traversal via `audio.filename` |
| 3 | Critical | `api/main.py:1416` | Standard subscribers access premium features |
| 4 | Critical | `api/main.py:477+` | Free users get implicit storage via processing endpoints |
| 5 | Critical | `api/main.py:65,1321` | `DEV_USER_ID` auth bypass in production code path |
| 6 | Critical | `api/main.py:71` | CORS wildcard on user-data API |
| 7 | High | `auth/main.py:293` | User deletion doesn't purge Qdrant or S3 (GDPR) |
| 8 | High | `auth/main.py:316` | Missing `invoice.payment_failed` webhook handler |
| 9 | High | `auth/main.py:392,409` | No Stripe idempotency keys |
| 10 | High | `api/main.py:500+` | 500 responses expose internal error details |
| 11 | High | `api/main.py:1368` | S3 errors leak R2 object keys to clients |
| 12 | Medium | `api/main.py:1240,1520` | `qdrant.scroll` silently truncates large libraries |
| 13 | Medium | `api/main.py:1582` | O(n²) graph computation blocks the async handler |
| 14 | Medium | `api/main.py:152+` | Singleton initialisation has a thread-safety race |
| 15 | Medium | `auth/main.py:49` | JWKS cache has no TTL and blocks the event loop |
| 16 | Medium | `auth/main.py:55` | Synchronous `requests.get` in async auth path |
| 17 | Medium | `api/main.py:1363` | Deprecated `get_event_loop()` + blocking S3 read in stream |
| 18 | Medium | `api/main.py:1264+` | No per-user storage quota |
| 19 | Medium | `api/main.py:429+` | No file type / magic bytes validation |
| 20 | Low | `api/main.py` | No file deletion endpoint |
| 21 | Low | `database/init.sql` | Inconsistent subscription state possible |
| 22 | Low | `api/main.py:61` | Staging directory is world-readable |
| 23 | Low | `auth/main.py` | No subscription event audit log |

---

## Completion Checklist

Work through these in order. Critical issues first, then High, Medium, Low. Issues within the same batch that touch different files can be done in parallel.

### Critical

- [x] **#5** — Delete `DEV_USER_ID` env read and the auth bypass block in `get_file_audio` (`api/main.py:65, 1321`)
- [x] **#2** — Apply `Path(audio.filename).name` strip at every `tmp_dir / audio.filename` site (`api/main.py:424, 532, 598` and all other processing endpoints)
- [x] **#6** — Replace `allow_origins=["*"]` with explicit origin list from env var (`api/main.py:71`)
- [x] **#1** — Write `_save_upload()` streaming helper with 250 MB cap; replace all `audio.read()` + `write_bytes()` pairs; add `MaxBodySizeMiddleware` (`api/main.py:429+`)
- [x] **#3** — Add `required_type` param and tier-rank check to `_require_premium_profile` (`api/main.py:1397`)
- [x] **#4** — Add subscription check before every `_schedule_embed` call in processing endpoints (`api/main.py:477, 543` and equivalents)

### High

- [x] **#8** — Add `invoice.payment_failed` and `invoice.payment_succeeded` handlers to Stripe webhook (`auth/main.py:316`)
- [x] **#9** — Add deterministic `idempotency_key` to `checkout.Session.create` and `billing_portal.Session.create` (`auth/main.py:392, 409`)
- [x] **#10** — Replace `detail=str(exc)` with `detail="Internal server error"` and `log.exception(...)` at all five catch-all sites (`api/main.py:500, 574, 1212, 1257, 1490`)
- [x] **#11** — Mask S3 error detail in `get_file_audio`; log key server-side only (`api/main.py:1368`)
- [x] **#7** — Add `/internal/users/{clerk_id}` DELETE endpoint to compute API; call it from `user.deleted` Clerk webhook handler (`auth/main.py:293`, `api/main.py`)

### Medium

- [ ] **#14** — Apply double-checked `threading.Lock` to `_get_qdrant`, `_get_s3_upload`, `_get_s3_presign`, `_get_tagger`, `_get_embedder` (`api/main.py:96, 118, 133, 152`)
- [ ] **#5b** — (dependency of #14 alternative) Eagerly initialise all singletons in a `startup` lifespan handler instead of lazy-init, eliminating the race entirely
- [ ] **#15 / #16** — Replace `requests.get` JWKS fetch with `httpx` async call; add 1-hour TTL; pre-warm at startup via scheduler (`auth/main.py:49, 55`)
- [ ] **#17** — Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`; convert `_iter_chunks` sync generator to async generator with `run_in_executor` per chunk (`api/main.py:1363, 1373`)
- [ ] **#19** — Write `_validate_audio_magic()` helper; call it after each upload write, before pipeline entry (`api/main.py:429+`)
- [ ] **#12** — Replace hardcoded `scroll(limit=1000/500)` with `_scroll_all()` paginating helper; add `truncated` flag to graph response (`api/main.py:1240, 1520`)
- [ ] **#18** — Write `_check_user_quota()` using `qdrant.count`; call it before staging in upload and all processing endpoints (`api/main.py:1264+`)
- [ ] **#13** — Move PCA + k-NN computation into `_graph_pool` thread executor in `get_graph_data` (`api/main.py:1582`)

### Low

- [ ] **#20** — Implement `DELETE /api/files/{point_id}` endpoint with ownership check, Qdrant delete, and S3 delete (`api/main.py`)
- [ ] **#22** — Change `STAGING_DIR` default to `/var/lib/syntag/staging`; pass `mode=0o700` to `mkdir`; add stale-file cleanup sweep at startup (`api/main.py:61`)
- [ ] **#21** — Add `CHECK` constraint to `users` table relating `subscription_active` and `subscription_status` (`database/init.sql`)
- [ ] **#23** — Create `subscription_events` table; append a row in every Stripe webhook branch (`database/init.sql`, `auth/main.py:316`)
