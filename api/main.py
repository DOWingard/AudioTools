"""
Compute API — FastAPI service exposing SyncTag, stem-separation, and audio tool endpoints.

Endpoints:
    GET  /health                   → {"status": "ok"}
    POST /api/tag                  → ZIP (metadata.json + CSV + tagged audio)
    POST /api/separate             → ZIP (all stem WAV files)
    POST /api/cut                  → Trimmed audio file
    POST /api/join                 → Joined audio file (multiple inputs)
    POST /api/karaoke              → Instrumental audio (vocals removed)
    POST /api/convert              → Converted audio file (format change)
    POST /api/bpm-key              → JSON (bpm, key, tempo_category)
    POST /api/analyze              → JSON (comprehensive audio analysis)
    GET  /api/files                → JSON (user's stored files from Qdrant)
    GET  /api/files/{id}/audio     → Audio file (served from persistent storage)
    POST /api/files/upload         → JSON (bulk upload audio into graph — premium)
    POST /api/files/search         → JSON (similarity search in user's graph — premium)
"""

import asyncio
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import uuid as _uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

QDRANT_URL       = os.environ.get("QDRANT_URL", "http://localhost:6333")
AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "http://localhost:8001")
COLLECTION_NAME  = "master_audio_graph"
VECTOR_DIM       = 768

# Object storage (Cloudflare R2 or MinIO-compatible)
R2_ACCOUNT_ID        = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID     = os.environ.get("R2_ACCESS_KEY_ID", "minioadmin")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "minioadmin")
R2_BUCKET_NAME       = os.environ.get("R2_BUCKET_NAME", "syntag-audio")
# S3_ENDPOINT_URL: explicit endpoint (MinIO). Leave blank to auto-derive from R2_ACCOUNT_ID.
S3_ENDPOINT_URL      = os.environ.get("S3_ENDPOINT_URL", "")
# S3_PUBLIC_ENDPOINT_URL: base for presigned URLs seen by browsers.
# Needed only for MinIO where internal host != public host (e.g. minio:9000 vs localhost:9000).
S3_PUBLIC_ENDPOINT_URL = os.environ.get("S3_PUBLIC_ENDPOINT_URL", "")
INTERNAL_SECRET        = os.environ.get("INTERNAL_SECRET", "")

# Maximum upload size — enforced by _save_upload() and MaxBodySizeMiddleware
MAX_UPLOAD_BYTES = 250 * 1024 * 1024  # 250 MB

# Staging dir: files live here between the HTTP handler and the background embed+upload task
STAGING_DIR = Path(os.environ.get("STAGING_DIR", "/var/lib/syntag/staging"))
STAGING_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

# Thread pool for CPU-heavy background embedding (separate from FastAPI async workers)
_embed_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embed")

# Thread pool for O(n²) graph computation (keeps async event loop unblocked)
_graph_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graph")

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:7860").split(",")
    if o.strip()
]

log = logging.getLogger(__name__)

app = FastAPI(title="SyncTag Compute API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_UPLOAD_BYTES:
            return Response("Payload too large", status_code=413)
        return await call_next(request)

app.add_middleware(MaxBodySizeMiddleware)


import time as _time  # noqa: E402


@app.on_event("startup")
async def _startup():
    """Sweep staging dir for orphaned files older than 1 hour."""
    cutoff = _time.time() - 3600
    try:
        for f in STAGING_DIR.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("Startup staging cleanup failed: %s", exc)


# ---------------------------------------------------------------------------
# Lazy-init singletons — ML models and S3 clients load once on first request
# ---------------------------------------------------------------------------
_tagger   = None
_embedder = None
_qdrant   = None
_s3_upload  = None   # boto3 client for PUT (internal endpoint)
_s3_presign = None   # boto3 client for presigned GET (public endpoint)

_tagger_lock    = threading.Lock()
_embedder_lock  = threading.Lock()
_qdrant_lock    = threading.Lock()
_s3_upload_lock  = threading.Lock()
_s3_presign_lock = threading.Lock()


def _get_tagger():
    global _tagger
    if _tagger is not None:
        return _tagger
    with _tagger_lock:
        if _tagger is None:
            from src.synctag import SyncTagger
            _tagger = SyncTagger()
    return _tagger


def _get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is None:
            from src.embedder import AudioEmbedder
            _embedder = AudioEmbedder()
    return _embedder


def _s3_internal_endpoint() -> str:
    """Endpoint used by the compute container to upload objects."""
    if S3_ENDPOINT_URL:
        return S3_ENDPOINT_URL
    if R2_ACCOUNT_ID:
        return f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    raise RuntimeError("No S3/R2 endpoint configured. Set R2_ACCOUNT_ID or S3_ENDPOINT_URL in .env.")


def _s3_public_endpoint() -> str:
    """Endpoint embedded in presigned URLs that the browser will fetch from."""
    return S3_PUBLIC_ENDPOINT_URL or _s3_internal_endpoint()


def _get_s3_upload():
    """boto3 client for uploading objects (uses internal endpoint)."""
    global _s3_upload
    if _s3_upload is not None:
        return _s3_upload
    with _s3_upload_lock:
        if _s3_upload is None:
            import boto3
            _s3_upload = boto3.client(
                "s3",
                endpoint_url=_s3_internal_endpoint(),
                aws_access_key_id=R2_ACCESS_KEY_ID,
                aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                region_name="auto",
            )
    return _s3_upload


def _get_s3_presign():
    """boto3 client for generating presigned URLs (uses public endpoint)."""
    global _s3_presign
    if _s3_presign is not None:
        return _s3_presign
    with _s3_presign_lock:
        if _s3_presign is None:
            import boto3
            _s3_presign = boto3.client(
                "s3",
                endpoint_url=_s3_public_endpoint(),
                aws_access_key_id=R2_ACCESS_KEY_ID,
                aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                region_name="auto",
            )
    return _s3_presign


# ---------------------------------------------------------------------------
# Qdrant setup
# ---------------------------------------------------------------------------

def _get_qdrant():
    """Return the Qdrant client, creating the collection if it doesn't exist."""
    global _qdrant
    if _qdrant is not None:
        return _qdrant
    with _qdrant_lock:
        if _qdrant is None:
            from qdrant_client import QdrantClient
            from qdrant_client.models import (
                Distance,
                PayloadSchemaType,
                PointStruct,  # noqa: F401 — imported here so callers can use it
                VectorParams,
            )

            client = QdrantClient(url=QDRANT_URL, timeout=10, check_compatibility=False)

            try:
                client.get_collection(COLLECTION_NAME)
            except Exception:
                client.create_collection(
                    COLLECTION_NAME,
                    vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
                )
                # Payload index on user_id enables fast per-user filtered search
                client.create_payload_index(
                    COLLECTION_NAME,
                    field_name="user_id",
                    field_schema=PayloadSchemaType.KEYWORD,
                )
                print(f"[qdrant] Created collection '{COLLECTION_NAME}' with user_id index")

            _qdrant = client
    return _qdrant


# ---------------------------------------------------------------------------
# Auth helper — resolve clerk_id from bearer token via auth service
# ---------------------------------------------------------------------------

async def _resolve_user_id(token: str) -> Optional[str]:
    """Call the auth service to validate the token and return the clerk_id."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{AUTH_SERVICE_URL}/auth/user/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                clerk_id = data.get("clerk_id")
                if not clerk_id:
                    print(f"[auth] /auth/user/me returned 200 but no clerk_id — payload: {data}")
                return clerk_id
            else:
                print(f"[auth] /auth/user/me returned HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        print(f"[auth] User resolution failed (network/timeout): {exc}")
    return None


async def _get_subscription_type(token: str) -> Optional[str]:
    """Return subscription_type for the authenticated user, or None on failure."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{AUTH_SERVICE_URL}/auth/user/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                profile = resp.json()
                return profile.get("subscription_type", "free")
    except Exception as exc:
        print(f"[qdrant] Subscription check failed: {exc}")
    return None


# ---------------------------------------------------------------------------
# File persistence + background embedding helpers
# ---------------------------------------------------------------------------

def _stage_file(user_id: str, process_type: str, job_id: str, filename: str, src_path: Path) -> Path:
    """
    Copy a processed audio file into the staging dir so the background embed task
    can read it after the HTTP handler's temp dir is cleaned up.
    The embed task uploads to R2 then deletes the staged copy.
    """
    dest_dir = STAGING_DIR / user_id / process_type / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    shutil.copy2(str(src_path), str(dest_path))
    return dest_path


def _get_duration(file_path: Path) -> float:
    """Return audio duration in seconds via ffprobe, or 0.0 on failure."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(file_path)],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(probe.stdout).get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


def _embed_and_store_sync(
    staged_path: Path,
    user_id: str,
    process_type: str,
    subgroup: str,
    original_filename: str,
    job_id: str,
    extra_payload: Optional[dict] = None,
) -> None:
    """
    Synchronous (runs in thread-pool):
      1. Embed staged_path with M2D-CLAP
      2. Upload staged_path to R2/MinIO → r2_key
      3. Upsert into Qdrant with r2_key in payload
      4. Delete staged_path (cleanup)
    """
    import traceback
    r2_key = f"{user_id}/{process_type}/{job_id}/{staged_path.name}"
    print(f"[r2] Starting embed+upload: {staged_path.name} → {r2_key}")
    try:
        from qdrant_client.models import PointStruct

        # 1. Embed from local staging file
        embedder = _get_embedder()
        waveform = embedder.load_audio(str(staged_path))
        embedding = embedder.embed(waveform)  # numpy (768,)
        duration = _get_duration(staged_path)
        file_size = staged_path.stat().st_size

        # 2. Upload to R2/MinIO
        s3 = _get_s3_upload()
        s3.upload_file(str(staged_path), R2_BUCKET_NAME, r2_key)
        print(f"[r2] ✓ Uploaded → s3://{R2_BUCKET_NAME}/{r2_key}")

        # 3. Upsert into Qdrant — point_id is deterministic on (user_id, r2_key)
        point_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{user_id}:{r2_key}"))
        payload = {
            "user_id": user_id,
            "filename": staged_path.name,
            "original_filename": original_filename,
            "process_type": process_type,
            "subgroup": subgroup,
            "r2_key": r2_key,
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration": round(duration, 2),
            "file_size": file_size,
        }
        if extra_payload:
            payload.update(extra_payload)

        qdrant = _get_qdrant()
        qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(id=point_id, vector=embedding.tolist(), payload=payload)],
        )
        print(f"[qdrant] ✓ Stored {staged_path.name} ({process_type}/{subgroup}) — point_id={point_id}")

        # 4. Cleanup staging file
        staged_path.unlink(missing_ok=True)
        try:
            staged_path.parent.rmdir()
        except OSError:
            pass

    except Exception as exc:
        print(f"[r2] ✗ Embed/upload failed for {staged_path}: {exc}")
        traceback.print_exc()


async def _schedule_embed(
    file_path: Path,
    user_id: str,
    process_type: str,
    subgroup: str,
    original_filename: str,
    job_id: str,
    extra_payload: Optional[dict] = None,
) -> None:
    """Schedule a single file embedding in the background thread pool."""
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(
        _embed_pool,
        _embed_and_store_sync,
        file_path, user_id, process_type, subgroup, original_filename, job_id, extra_payload,
    )

    def _on_done(fut: asyncio.Future) -> None:
        exc = fut.exception()
        if exc:
            import traceback
            print(f"[qdrant] Thread-pool future raised for {file_path.name}: {exc}")
            traceback.print_exception(type(exc), exc, exc.__traceback__)

    future.add_done_callback(_on_done)


def _stem_subgroup(stem_key: str) -> str:
    """Map a stem key name to its display subgroup."""
    if stem_key.startswith("oneshot_"):
        return "oneshots"
    if stem_key.startswith("drums_"):
        return "drums"
    return "main"


async def _save_upload(upload: UploadFile, dest: Path) -> None:
    """Stream an UploadFile to disk, raising HTTP 413 if it exceeds MAX_UPLOAD_BYTES."""
    total = 0
    with dest.open("wb") as fh:
        while True:
            chunk = await upload.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit",
                )
            fh.write(chunk)


# Audio magic-byte signatures for format validation (issue #19)
_AUDIO_MAGIC: dict = {
    b"RIFF": "wav",
    b"fLaC": "flac",
    b"OggS": "ogg",
    b"ID3":  "mp3",
    b"\xff\xfb": "mp3",
    b"\xff\xf3": "mp3",
    b"\xff\xf2": "mp3",
}

MAX_FILES_STANDARD = 50


def _validate_audio_magic(path: Path) -> None:
    """Raise HTTP 415 if the file doesn't begin with a known audio magic sequence."""
    header = path.read_bytes()[:12]
    for magic in _AUDIO_MAGIC:
        if header.startswith(magic):
            return
    raise HTTPException(
        status_code=415,
        detail="Unsupported or invalid audio file format",
    )


async def _get_user_file_count(user_id: str) -> int:
    """Return the number of files stored in Qdrant for this user."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    loop = asyncio.get_running_loop()
    count_result = await loop.run_in_executor(
        None,
        lambda: _get_qdrant().count(
            collection_name=COLLECTION_NAME,
            count_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            exact=False,
        ),
    )
    return count_result.count


async def _check_user_quota(user_id: str, subscription_type: str = "premium") -> bool:
    """Return True if the user can store at least one more file, False if quota exceeded.

    Limits:
        standard → MAX_FILES_STANDARD (50)
        premium  → unlimited
    """
    if subscription_type == "premium":
        return True
    count = await _get_user_file_count(user_id)
    return count < MAX_FILES_STANDARD


def _scroll_all(qdrant_client, collection: str, scroll_filter, with_vectors: bool = False) -> list:
    """Paginate through all matching Qdrant records, following the next_page_offset cursor."""
    records: list = []
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


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/tag
# ---------------------------------------------------------------------------

@app.post("/api/tag")
async def tag_audio(
    audio: UploadFile = File(...),
    isrc: str = Form(default=""),
    title: str = Form(default=""),
    artist: str = Form(default=""),
    album: str = Form(default=""),
    bpm: str = Form(default=""),
    genre: str = Form(default=""),
    authorization: str = Header(default=None),
):
    """
    Accept an audio upload, run the full SyncTag pipeline, and return a ZIP
    containing:
      - metadata.json
      - <stem>.csv
      - <stem>_tagged.<ext>
    Optional form fields (title, artist, album, bpm, genre, isrc) override or
    fill the corresponding pipeline-detected values before tagging.
    When an Authorization token is provided, the tagged audio is also
    persisted to user storage and embedded into Qdrant asynchronously.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_tag_"))
    try:
        # Resolve user NOW — before heavy computation — so the Clerk JWT (60s TTL)
        # doesn't expire during the SyncTag pipeline (which can take 100+ seconds).
        user_id = None
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
            user_id = await _resolve_user_id(token)

        # Save upload — preserve the original filename (and therefore extension)
        # so write_id3_tags dispatches to the correct mutagen handler.
        # Fall back to content_type sniffing so we never call _tag_wav on an MP3.
        _MIME_EXT = {
            "audio/wav": ".wav", "audio/wave": ".wav", "audio/x-wav": ".wav",
            "audio/flac": ".flac", "audio/x-flac": ".flac",
            "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
            "audio/aac": ".aac", "audio/x-aac": ".aac",
            "audio/ogg": ".ogg",
        }
        if audio.filename:
            safe_name = Path(audio.filename).name or "input.wav"
        else:
            ext = _MIME_EXT.get((audio.content_type or "").lower(), ".wav")
            safe_name = f"input{ext}"
        input_path = tmp_dir / safe_name
        await _save_upload(audio, input_path)
        _validate_audio_magic(input_path)

        # Run pipeline
        tagger = _get_tagger()
        result = tagger.run(input_path)

        # Apply caller-supplied overrides into metadata (non-empty values win)
        meta = result.setdefault("metadata", {})
        if (v := (title or "").strip()):
            meta["Title"] = v
        if (v := (artist or "").strip()):
            meta["Artist"] = v
        if (v := (album or "").strip()):
            meta["Album"] = v
        if (v := (isrc or "").strip()):
            meta["ISRC"] = v
        if (v := (genre or "").strip()):
            meta["Genre"] = v
        if (v := (bpm or "").strip()):
            try:
                meta["BPM"] = int(float(v))
            except ValueError:
                pass

        # Embed metadata into a tagged COPY via FFmpeg (-codec:a copy).
        # Writing to a new file (not in-place) guarantees the original bytes
        # are never at risk, and FFmpeg writes standard container metadata
        # (RIFF LIST/INFO for WAV, ID3 for MP3, Vorbis for FLAC, etc.)
        # so the result plays correctly in browsers and is visible in DAWs/OS.
        from src.export import write_csv_sidecar, write_tags_ffmpeg
        tagged_name = f"{input_path.stem}_tagged{input_path.suffix}"
        tagged_path = tmp_dir / tagged_name
        try:
            write_tags_ffmpeg(input_path, tagged_path, result)
        except Exception as exc:
            log.warning("[api/tag] ffmpeg tag warning: %s — falling back to original", exc)
            shutil.copy2(str(input_path), str(tagged_path))

        # Write CSV sidecar
        csv_path = tmp_dir / f"{input_path.stem}.csv"
        write_csv_sidecar(result, csv_path)

        # Write metadata JSON
        meta_path = tmp_dir / "metadata.json"
        meta_path.write_text(json.dumps(result, indent=2, default=str))

        # Persist tagged audio + schedule embedding if authenticated and subscribed
        if user_id:
            sub_type = await _get_subscription_type(token)
            if sub_type and sub_type != "free":
                if await _check_user_quota(user_id, sub_type):
                    job_id = str(_uuid.uuid4())
                    dest = _stage_file(user_id, "tag", job_id, tagged_path.name, tagged_path)
                    asyncio.create_task(
                        _schedule_embed(dest, user_id, "tag", "tagged", tagged_path.name, job_id)
                    )

        # Pack into ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(meta_path, arcname="metadata.json")
            zf.write(csv_path, arcname=csv_path.name)
            zf.write(tagged_path, arcname=tagged_name)
        buf.seek(0)

        filename = f"{input_path.stem}_synctag.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        log.exception("Unhandled error in /api/tag")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/separate
# ---------------------------------------------------------------------------

@app.post("/api/separate")
async def separate_audio(
    audio: UploadFile = File(...),
    authorization: str = Header(default=None),
):
    """
    Accept an audio upload, run the three-stage separation pipeline, and
    return a ZIP containing all stem WAV files keyed by stem name.
    Output stems are persisted and embedded into Qdrant asynchronously when
    an Authorization token is provided.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_sep_"))
    try:
        import torch

        # Resolve user NOW — before heavy computation — so the Clerk JWT (60s TTL)
        # doesn't expire during Demucs + LARS pipeline (which takes 60-180+ seconds).
        user_id = None
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
            user_id = await _resolve_user_id(token)

        # Save upload
        _safe_name = Path(audio.filename or "input.wav").name or "input.wav"
        input_path = tmp_dir / _safe_name
        await _save_upload(audio, input_path)
        _validate_audio_magic(input_path)

        output_dir = tmp_dir / "stems" / input_path.stem
        device = "cuda" if torch.cuda.is_available() else "cpu"

        from src.advanced_separate import run_pipeline
        all_files = run_pipeline(input_path, output_dir, device=device)

        # Persist + schedule embedding if authenticated and subscribed
        if user_id:
            sub_type = await _get_subscription_type(token)
            if sub_type and sub_type != "free":
                current_count = await _get_user_file_count(user_id)
                remaining_slots = (
                    len(all_files)  # unlimited for premium
                    if sub_type == "premium"
                    else max(0, MAX_FILES_STANDARD - current_count)
                )
                job_id = str(_uuid.uuid4())
                stems_saved = 0
                for stem_key, filepath in all_files.items():
                    if stems_saved >= remaining_slots:
                        break
                    p = Path(filepath)
                    if p.exists():
                        subgroup = _stem_subgroup(stem_key)
                        dest = _stage_file(user_id, "separate", job_id, f"{stem_key}.wav", p)
                        asyncio.create_task(
                            _schedule_embed(
                                dest, user_id, "separate", subgroup,
                                Path(audio.filename or "input.wav").name or "input.wav", job_id,
                            )
                        )
                        stems_saved += 1

        # Pack all existing stem files into ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for key, filepath in all_files.items():
                p = Path(filepath)
                if p.exists():
                    zf.write(p, arcname=f"{key}.wav")
        buf.seek(0)

        filename = f"{input_path.stem}_stems.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        log.exception("Unhandled error in /api/separate")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/cut — Audio Cutter / Trimmer
# ---------------------------------------------------------------------------

@app.post("/api/cut")
async def cut_audio(
    audio: UploadFile = File(...),
    start: float = Form(default=0.0),
    end: float = Form(default=0.0),
    authorization: str = Header(default=None),
):
    """Trim audio to the specified start/end times (seconds). Returns WAV."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_cut_"))
    try:
        user_id = None
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
            user_id = await _resolve_user_id(token)

        _safe_name = Path(audio.filename or "input.wav").name or "input.wav"
        input_path = tmp_dir / _safe_name
        await _save_upload(audio, input_path)
        _validate_audio_magic(input_path)

        output_path = tmp_dir / f"{input_path.stem}_trimmed.wav"

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-ss", str(start),
        ]
        if end > start:
            cmd += ["-to", str(end)]
        cmd += ["-c", "pcm_s16le", str(output_path)]

        subprocess.run(cmd, capture_output=True, check=True)

        # Persist + schedule embedding if authenticated and subscribed
        if user_id:
            sub_type = await _get_subscription_type(token)
            if sub_type and sub_type != "free":
                if await _check_user_quota(user_id, sub_type):
                    job_id = str(_uuid.uuid4())
                    dest = _stage_file(user_id, "cut", job_id, output_path.name, output_path)
                    asyncio.create_task(
                        _schedule_embed(dest, user_id, "cut", "trimmed", Path(audio.filename or "input.wav").name or "input.wav", job_id)
                    )

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{output_path.name}"'},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {exc.stderr.decode()}") from exc
    except Exception as exc:
        log.exception("Unhandled error in /api/cut")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/join — Audio Joiner
# ---------------------------------------------------------------------------

@app.post("/api/join")
async def join_audio(
    audio: List[UploadFile] = File(...),
    authorization: str = Header(default=None),
):
    """Concatenate multiple audio files in upload order. Returns WAV."""
    if len(audio) < 2:
        raise HTTPException(status_code=400, detail="At least 2 audio files required.")
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_join_"))
    try:
        user_id = None
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
            user_id = await _resolve_user_id(token)

        # Save all uploads
        input_paths = []
        for i, f in enumerate(audio):
            ext = Path(f.filename or "input.wav").suffix or ".wav"
            fpath = tmp_dir / f"input_{i:03d}{ext}"
            await _save_upload(f, fpath)
            _validate_audio_magic(fpath)
            input_paths.append(fpath)

        # Convert each to WAV PCM first for consistent concat
        wav_paths = []
        for i, p in enumerate(input_paths):
            wav_path = tmp_dir / f"norm_{i:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(p), "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", str(wav_path)],
                capture_output=True, check=True,
            )
            wav_paths.append(wav_path)

        # Build concat list file
        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{p}'" for p in wav_paths),
            encoding="utf-8",
        )

        output_path = tmp_dir / "joined.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(output_path)],
            capture_output=True, check=True,
        )

        # Persist + schedule embedding if authenticated and subscribed
        if user_id:
            sub_type = await _get_subscription_type(token)
            if sub_type and sub_type != "free":
                if await _check_user_quota(user_id, sub_type):
                    job_id = str(_uuid.uuid4())
                    dest = _stage_file(user_id, "join", job_id, "joined.wav", output_path)
                    original = ", ".join(f.filename or "input" for f in audio[:3])
                    asyncio.create_task(
                        _schedule_embed(dest, user_id, "join", "joined", original, job_id)
                    )

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="joined.wav"'},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {exc.stderr.decode()}") from exc
    except Exception as exc:
        log.exception("Unhandled error in /api/join")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/karaoke — Vocal Removal (Karaoke)
# ---------------------------------------------------------------------------

@app.post("/api/karaoke")
async def karaoke_audio(
    audio: UploadFile = File(...),
    authorization: str = Header(default=None),
):
    """Remove vocals using Demucs and return the instrumental mix."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_karaoke_"))
    try:
        import torch

        # Resolve user NOW — before Demucs (60-180s) — so the Clerk JWT (60s TTL) is fresh.
        user_id = None
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
            user_id = await _resolve_user_id(token)
        _safe_name = Path(audio.filename or "input.wav").name or "input.wav"
        input_path = tmp_dir / _safe_name
        await _save_upload(audio, input_path)
        _validate_audio_magic(input_path)

        device = "cuda" if torch.cuda.is_available() else "cpu"

        from src.separate import separate_stems
        stems = separate_stems(input_path, tmp_dir / "stems", device=device)

        # Build instrumental by summing all non-vocal stems
        import torchaudio
        instrumental = None
        sr = None
        for name, path in stems.items():
            if name == "vocals":
                continue
            wav, s = torchaudio.load(str(path))
            sr = s
            instrumental = wav if instrumental is None else instrumental + wav

        if instrumental is None:
            raise HTTPException(status_code=500, detail="No stems produced")

        output_path = tmp_dir / f"{input_path.stem}_karaoke.wav"
        torchaudio.save(str(output_path), instrumental, sr)

        # Persist + schedule embedding if authenticated and subscribed
        if user_id:
            sub_type = await _get_subscription_type(token)
            if sub_type and sub_type != "free":
                if await _check_user_quota(user_id, sub_type):
                    job_id = str(_uuid.uuid4())
                    dest = _stage_file(user_id, "karaoke", job_id, output_path.name, output_path)
                    asyncio.create_task(
                        _schedule_embed(dest, user_id, "karaoke", "instrumental", Path(audio.filename or "input.wav").name or "input.wav", job_id)
                    )

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{output_path.name}"'},
        )
    except Exception as exc:
        log.exception("Unhandled error in /api/karaoke")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/convert — Format Converter
# ---------------------------------------------------------------------------

_FORMAT_MAP = {
    "mp3":  {"ext": ".mp3",  "codec": "libmp3lame", "mime": "audio/mpeg",    "extra": ["-q:a", "2"]},
    "wav":  {"ext": ".wav",  "codec": "pcm_s16le",  "mime": "audio/wav",     "extra": []},
    "flac": {"ext": ".flac", "codec": "flac",       "mime": "audio/flac",    "extra": []},
    "aac":  {"ext": ".aac",  "codec": "aac",        "mime": "audio/aac",     "extra": ["-b:a", "256k"]},
    "m4a":  {"ext": ".m4a",  "codec": "aac",        "mime": "audio/mp4",     "extra": ["-b:a", "256k", "-movflags", "+faststart"]},
    "ogg":  {"ext": ".ogg",  "codec": "libvorbis",  "mime": "audio/ogg",     "extra": ["-q:a", "6"]},
}

@app.post("/api/convert")
async def convert_audio(
    audio: UploadFile = File(...),
    format: str = Form(default="mp3"),
    authorization: str = Header(default=None),
):
    """Convert audio to the requested format."""
    fmt = format.lower().strip()
    if fmt not in _FORMAT_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}. Use: {list(_FORMAT_MAP.keys())}")

    spec = _FORMAT_MAP[fmt]
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_convert_"))
    try:
        user_id = None
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
            user_id = await _resolve_user_id(token)

        _safe_name = Path(audio.filename or "input.wav").name or "input.wav"
        input_path = tmp_dir / _safe_name
        await _save_upload(audio, input_path)
        _validate_audio_magic(input_path)

        # M4A needs an mp4 container — use a distinct output name
        out_stem = input_path.stem
        output_name = f"{out_stem}_converted{spec['ext']}"
        output_path = tmp_dir / output_name

        cmd = ["ffmpeg", "-y", "-i", str(input_path), "-c:a", spec["codec"]]
        cmd += spec["extra"]
        cmd.append(str(output_path))

        subprocess.run(cmd, capture_output=True, check=True)

        # Persist + schedule embedding if authenticated and subscribed
        if user_id:
            sub_type = await _get_subscription_type(token)
            if sub_type and sub_type != "free":
                if await _check_user_quota(user_id, sub_type):
                    job_id = str(_uuid.uuid4())
                    dest = _stage_file(user_id, "convert", job_id, output_path.name, output_path)
                    asyncio.create_task(
                        _schedule_embed(dest, user_id, "convert", "converted", Path(audio.filename or "input.wav").name or "input.wav", job_id)
                    )

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type=spec["mime"],
            headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {exc.stderr.decode()}") from exc
    except Exception as exc:
        log.exception("Unhandled error in /api/convert")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/bpm-key — BPM & Key Finder (Enhanced)
# ---------------------------------------------------------------------------

_KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler key profiles (standard music cognition profiles)
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _correct_bpm(raw_bpm: float) -> float:
    """Half/double correction: normalize BPM into the 70-180 'sweet spot' range."""
    bpm = raw_bpm
    while bpm < 70:
        bpm *= 2.0
    while bpm > 180:
        bpm /= 2.0
    return round(bpm, 1)


def _chroma_key_detect(chroma_mean: np.ndarray) -> tuple:
    """
    Key detection via Krumhansl profile correlation.
    Returns (key_string, confidence_score).
    """
    major_corrs = []
    minor_corrs = []
    for shift in range(12):
        rolled = np.roll(chroma_mean, shift)
        major_corrs.append(np.corrcoef(rolled, _MAJOR_PROFILE)[0, 1])
        minor_corrs.append(np.corrcoef(rolled, _MINOR_PROFILE)[0, 1])

    best_major_idx = int(np.argmax(major_corrs))
    best_minor_idx = int(np.argmax(minor_corrs))
    best_major_score = major_corrs[best_major_idx]
    best_minor_score = minor_corrs[best_minor_idx]

    if best_major_score >= best_minor_score:
        return f"{_KEY_NAMES[best_major_idx]} Major", float(best_major_score)
    else:
        return f"{_KEY_NAMES[best_minor_idx]} Minor", float(best_minor_score)


def _windowed_key_detect(y: np.ndarray, sr: int, segment_secs: float = 8.0) -> tuple:
    """
    Windowed key detection: split audio into segments, gate by energy,
    detect key per segment, and vote across all segments.
    """
    import librosa

    seg_samples = int(segment_secs * sr)
    total = len(y)
    if total < seg_samples:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        return _chroma_key_detect(chroma.mean(axis=1))

    global_rms = np.sqrt(np.mean(y ** 2))
    energy_threshold = global_rms * 0.25

    key_votes = {}
    n_segments = max(1, total // seg_samples)

    for i in range(n_segments):
        start = i * seg_samples
        end = min(start + seg_samples, total)
        segment = y[start:end]

        seg_rms = np.sqrt(np.mean(segment ** 2))
        if seg_rms < energy_threshold:
            continue

        chroma = librosa.feature.chroma_cqt(y=segment, sr=sr)
        key_str, conf = _chroma_key_detect(chroma.mean(axis=1))

        if key_str not in key_votes:
            key_votes[key_str] = [0, 0.0]
        key_votes[key_str][0] += 1
        key_votes[key_str][1] += conf

    if not key_votes:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        return _chroma_key_detect(chroma.mean(axis=1))

    winner = max(key_votes.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    avg_conf = winner[1][1] / winner[1][0]
    return winner[0], avg_conf


def _clap_key_crosscheck(audio_path: str, chroma_key: str, chroma_conf: float) -> str:
    """
    Cross-check key detection using M2D-CLAP: embed the audio, then compute
    cosine similarity against text descriptions for all 24 keys.
    """
    try:
        embedder = _get_embedder()

        waveform = embedder.load_audio(audio_path)
        audio_emb = embedder.embed(waveform)           # (768,)
        audio_emb = audio_emb / (np.linalg.norm(audio_emb) + 1e-10)

        key_labels = []
        for name in _KEY_NAMES:
            key_labels.append(f"{name} Major")
            key_labels.append(f"{name} Minor")

        best_key = chroma_key
        best_sim = -1.0

        for label in key_labels:
            prompt = f"music in the key of {label}"
            text_emb = embedder.encode_text(prompt)  # (768,)
            text_emb = text_emb / (np.linalg.norm(text_emb) + 1e-10)
            sim = float(np.dot(audio_emb.flatten(), text_emb.flatten()))
            if sim > best_sim:
                best_sim = sim
                best_key = label

        if chroma_conf >= 0.85:
            return chroma_key
        elif chroma_conf < 0.7:
            return best_key
        else:
            if best_key == chroma_key:
                return chroma_key
            else:
                return best_key

    except Exception as e:
        print(f"[bpm-key] CLAP cross-check failed, using chroma: {e}")
        return chroma_key


def _madmom_key_detect(audio_path: str) -> tuple:
    """
    Key detection using madmom's CNN-based key recognition.
    Returns (key_string, confidence_score) or (None, 0.0) on failure.
    """
    try:
        from madmom.features.key import CNNKeyRecognitionProcessor

        wav_path = audio_path + ".madmom.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-ar", "44100", "-ac", "1",
             "-c:a", "pcm_s16le", wav_path],
            capture_output=True, check=True,
        )

        proc = CNNKeyRecognitionProcessor()
        key_probs = proc(wav_path)

        Path(wav_path).unlink(missing_ok=True)

        key_probs = np.array(key_probs)
        if key_probs.ndim > 1:
            key_probs = key_probs.mean(axis=0)
        key_probs = key_probs.flatten()

        best_idx = int(np.argmax(key_probs))
        confidence = float(key_probs[best_idx])

        key_idx = best_idx // 2
        is_minor = best_idx % 2 == 1
        mode = "Minor" if is_minor else "Major"
        key_str = f"{_KEY_NAMES[key_idx]} {mode}"

        return key_str, confidence

    except Exception as e:
        print(f"[bpm-key] madmom CNN key detection failed: {e}")
        Path(audio_path + ".madmom.wav").unlink(missing_ok=True)
        return None, 0.0


def _consensus_key(audio_path: str, y: np.ndarray, sr: int) -> tuple:
    """Key detection: madmom CNN primary, windowed chroma fallback."""
    madmom_key, madmom_conf = _madmom_key_detect(audio_path)

    if madmom_key is not None:
        return madmom_key, madmom_conf, "madmom"

    chroma_key, chroma_conf = _windowed_key_detect(y, sr)
    return chroma_key, chroma_conf, "chroma"


def _detect_bpm_key(audio_path: str) -> dict:
    """Enhanced BPM and musical key detection."""
    import librosa

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    raw_bpm = float(tempo[0] if hasattr(tempo, '__len__') else tempo)
    bpm = _correct_bpm(raw_bpm)

    key, key_conf, key_source = _consensus_key(audio_path, y, sr)

    if bpm < 70:
        tempo_cat = "Very Slow"
    elif bpm < 100:
        tempo_cat = "Slow"
    elif bpm < 130:
        tempo_cat = "Medium"
    elif bpm < 160:
        tempo_cat = "Fast"
    else:
        tempo_cat = "Very Fast"

    return {
        "bpm": bpm,
        "key": key,
        "key_confidence": round(key_conf, 3),
        "key_source": key_source,
        "tempo_category": tempo_cat,
        "duration": round(duration, 2),
    }


@app.post("/api/bpm-key")
async def bpm_key(
    audio: UploadFile = File(...),
):
    """Detect BPM and musical key of an audio file."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_bpm_"))
    try:
        _safe_name = Path(audio.filename or "input.wav").name or "input.wav"
        input_path = tmp_dir / _safe_name
        await _save_upload(audio, input_path)
        _validate_audio_magic(input_path)

        result = _detect_bpm_key(str(input_path))
        return JSONResponse(result)

    except Exception as exc:
        log.exception("Unhandled error in /api/bpm-key")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/analyze — Comprehensive Audio Analyzer
# ---------------------------------------------------------------------------

def _analyze_audio(audio_path: str) -> dict:
    """Comprehensive audio analysis: BPM, key, loudness, spectral info."""
    import librosa

    result = _detect_bpm_key(audio_path)

    y, sr = librosa.load(audio_path, sr=22050, mono=True)

    rms = float(np.sqrt(np.mean(y ** 2)))
    rms_db = float(20 * np.log10(rms + 1e-10))

    peak = float(np.max(np.abs(y)))
    peak_db = float(20 * np.log10(peak + 1e-10))

    spec_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    brightness_hz = float(np.mean(spec_centroid))

    zcr = librosa.feature.zero_crossing_rate(y)
    avg_zcr = float(np.mean(zcr))

    lufs = None
    try:
        lufs_result = subprocess.run(
            ["ffmpeg", "-i", audio_path, "-af", "loudnorm=print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
        stderr = lufs_result.stderr
        json_start = stderr.rfind("{")
        json_end = stderr.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            lufs_data = json.loads(stderr[json_start:json_end])
            lufs = float(lufs_data.get("input_i", 0))
    except Exception:
        pass

    channels = 0
    sample_rate_original = 0
    bit_depth = ""
    codec = ""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", audio_path],
            capture_output=True, text=True, check=True,
        )
        probe_data = json.loads(probe.stdout)
        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "audio":
                channels = int(stream.get("channels", 0))
                sample_rate_original = int(stream.get("sample_rate", 0))
                bit_depth = stream.get("bits_per_sample", stream.get("bits_per_raw_sample", ""))
                codec = stream.get("codec_name", "")
                break
    except Exception:
        pass

    energy_rating = min(10, max(1, int(np.interp(rms_db, [-40, -5], [1, 10]))))

    result.update({
        "rms_db": round(rms_db, 1),
        "peak_db": round(peak_db, 1),
        "lufs": round(lufs, 1) if lufs is not None else None,
        "brightness_hz": round(brightness_hz, 0),
        "zero_crossing_rate": round(avg_zcr, 4),
        "energy_rating": energy_rating,
        "channels": channels,
        "sample_rate": sample_rate_original,
        "bit_depth": str(bit_depth) if bit_depth else "N/A",
        "codec": codec,
    })

    return result


@app.post("/api/analyze")
async def analyze_audio(
    audio: UploadFile = File(...),
    authorization: str = Header(default=None),
):
    """Comprehensive audio analysis returning BPM, key, loudness, spectral info.
    When an Authorization token is provided, the original audio file is persisted
    and embedded into Qdrant with the analysis results stored in the payload.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_analyze_"))
    try:
        user_id = None
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
            user_id = await _resolve_user_id(token)

        _safe_name = Path(audio.filename or "input.wav").name or "input.wav"
        input_path = tmp_dir / _safe_name
        await _save_upload(audio, input_path)
        _validate_audio_magic(input_path)

        result = _analyze_audio(str(input_path))

        # Persist original + schedule embedding if authenticated and subscribed
        if user_id:
            sub_type = await _get_subscription_type(token)
            if sub_type and sub_type != "free":
                await _check_user_quota(user_id, sub_type)
                job_id = str(_uuid.uuid4())
                dest = _stage_file(user_id, "analyze", job_id, input_path.name, input_path)
                extra = {
                    k: result[k] for k in (
                        "bpm", "key", "key_confidence", "key_source", "tempo_category",
                        "energy_rating", "lufs", "rms_db", "peak_db", "brightness_hz",
                        "sample_rate", "channels", "codec",
                    ) if k in result
                }
                asyncio.create_task(
                    _schedule_embed(
                        dest, user_id, "analyze", "analyzed",
                        Path(audio.filename or "input.wav").name or "input.wav", job_id, extra,
                    )
                )

        return JSONResponse(result)

    except Exception as exc:
        log.exception("Unhandled error in /api/analyze")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# GET /api/files — List user's files stored in Qdrant
# ---------------------------------------------------------------------------

@app.get("/api/files")
async def list_files(authorization: str = Header(default=None)):
    """
    Return all audio files stored for the authenticated user, sorted by
    created_at descending. Each entry includes process_type, subgroup,
    filename, duration, and file_size metadata.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.removeprefix("Bearer ").strip()
    user_id = await _resolve_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        qdrant = _get_qdrant()
        records = _scroll_all(
            qdrant, COLLECTION_NAME,
            Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]),
        )

        files = [{"id": str(r.id), **r.payload} for r in records]
        # Sort newest first
        files.sort(key=lambda f: f.get("created_at", ""), reverse=True)

        return JSONResponse({"files": files})

    except Exception as exc:
        log.exception("Unhandled error in /api/files")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


# ---------------------------------------------------------------------------
# GET /api/files/count — Lightweight file count for quota pre-check
# ---------------------------------------------------------------------------

@app.get("/api/files/count")
async def count_user_files(authorization: str = Header(default=None)):
    """Return the number of files stored for the authenticated user."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = await _resolve_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    count = await _get_user_file_count(user_id)
    return JSONResponse({"count": count})


# ---------------------------------------------------------------------------
# POST /api/files/upload — Bulk upload files into the user's graph (premium)
# ---------------------------------------------------------------------------

@app.post("/api/files/upload")
async def upload_files_to_graph(
    audio: List[UploadFile] = File(...),
    authorization: str = Header(default=None),
):
    """
    Accept one or more audio files, persist them, and embed each into the
    user's Qdrant graph via the background thread pool.

    Standard users: up to MAX_FILES_STANDARD (50) files.
    Premium users: unlimited.
    Returns immediately with the number of files queued for embedding.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.removeprefix("Bearer ").strip()
    profile = await _require_premium_profile(token, required_type="premium")
    user_id = profile["clerk_id"]

    if not await _check_user_quota(user_id, profile["subscription_type"]):
        raise HTTPException(
            status_code=429,
            detail=f"Storage quota exceeded ({MAX_FILES_STANDARD} files maximum for standard plan). Upgrade to Premium for unlimited storage.",
        )
    job_id = str(_uuid.uuid4())
    queued = 0

    for upload in audio:
        tmp_dir = Path(tempfile.mkdtemp(prefix="api_upload_"))
        try:
            fname = upload.filename or f"upload_{queued}.wav"
            input_path = tmp_dir / fname
            await _save_upload(upload, input_path)
            _validate_audio_magic(input_path)

            dest = _stage_file(user_id, "upload", job_id, fname, input_path)
            await _schedule_embed(
                dest, user_id, "upload", "uploaded", fname, job_id,
            )
            queued += 1
        except Exception as exc:
            log.warning("[upload] Failed to queue %s: %s", upload.filename, exc)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return JSONResponse({"queued": queued, "job_id": job_id})


# ---------------------------------------------------------------------------
# GET /api/files/{point_id}/audio — Serve a persisted audio file
# ---------------------------------------------------------------------------

@app.get("/api/files/{point_id}/audio")
async def get_file_audio(point_id: str, authorization: str = Header(default=None)):
    """
    Serve the audio file associated with the given Qdrant point ID.
    Validates that the authenticated user owns the file.
    """
    user_id = None
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
        user_id = await _resolve_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        qdrant = _get_qdrant()
        records = qdrant.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[point_id],
            with_payload=True,
        )
    except Exception as exc:
        log.exception("Unhandled error in /api/files/%s/audio", point_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    if not records:
        raise HTTPException(status_code=404, detail="File not found")

    payload = records[0].payload
    if payload.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    r2_key = payload.get("r2_key")
    if not r2_key:
        raise HTTPException(status_code=404, detail="No R2 key in record — file predates R2 migration")

    # Determine Content-Type from file extension
    ext = r2_key.rsplit(".", 1)[-1].lower() if "." in r2_key else ""
    mime_map = {
        "mp3":  "audio/mpeg",
        "wav":  "audio/wav",
        "flac": "audio/flac",
        "ogg":  "audio/ogg",
        "m4a":  "audio/mp4",
        "aac":  "audio/aac",
    }
    content_type = mime_map.get(ext, "audio/wav")

    try:
        s3 = _get_s3_upload()
        loop = asyncio.get_running_loop()
        obj = await loop.run_in_executor(
            None,
            lambda: s3.get_object(Bucket=R2_BUCKET_NAME, Key=r2_key),
        )
    except Exception as exc:
        log.error("S3 fetch failed for key %s: %s", r2_key, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch audio file") from exc

    body = obj["Body"]
    content_length = obj.get("ContentLength")

    async def _aiter_chunks():
        while True:
            chunk = await loop.run_in_executor(None, body.read, 65536)
            if not chunk:
                break
            yield chunk

    headers = {}
    if content_length:
        headers["Content-Length"] = str(content_length)
    headers["Accept-Ranges"] = "bytes"
    headers["Cache-Control"] = "no-store"

    return StreamingResponse(
        _aiter_chunks(),
        media_type=content_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /api/files/search — Similarity search in user's personal graph
# ---------------------------------------------------------------------------

async def _require_premium_profile(authorization: str, required_type: str = "premium") -> dict:
    """Validate token, fetch profile, assert subscription tier. Returns profile dict."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{AUTH_SERVICE_URL}/auth/user/me",
                headers={"Authorization": f"Bearer {authorization}"},
                timeout=5.0,
            )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

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


@app.post("/api/files/search")
async def search_files(
    audio: Optional[UploadFile] = File(default=None),
    query_text: str = Form(default=""),
    offset: int = Form(default=0),
    limit: int = Form(default=10),
    authorization: str = Header(default=None),
):
    """
    Similarity search in the user's personal graph. Accepts either an audio
    file upload or a text query (M2D-CLAP text encoder). Supports pagination
    via offset/limit. Requires a premium subscription.

    Returns { results: [...], has_more: bool }
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.removeprefix("Bearer ").strip()
    profile = await _require_premium_profile(token)
    user_id = profile["clerk_id"]

    if audio is None and not query_text.strip():
        raise HTTPException(status_code=400, detail="Provide an audio file or query_text")

    # Clamp pagination params
    offset = max(0, offset)
    limit = max(1, min(20, limit))
    fetch_limit = offset + limit + 1  # +1 to determine has_more

    tmp_dir: Optional[Path] = None
    try:
        embedder = _get_embedder()

        if audio is not None:
            tmp_dir = Path(tempfile.mkdtemp(prefix="api_search_"))
            _safe_name = Path(audio.filename or "query.wav").name or "query.wav"
            input_path = tmp_dir / _safe_name
            await _save_upload(audio, input_path)
            _validate_audio_magic(input_path)
            waveform = embedder.load_audio(str(input_path))
            query_emb = embedder.embed(waveform)
        else:
            query_emb = embedder.encode_text(query_text.strip())

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        qdrant = _get_qdrant()
        all_results = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_emb.tolist(),
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=fetch_limit,
            with_payload=True,
        ).points

        page = all_results[offset : offset + limit]
        has_more = len(all_results) > offset + limit

        hits = [
            {"id": str(r.id), "score": round(r.score, 4), **r.payload}
            for r in page
        ]
        return JSONResponse({"results": hits, "has_more": has_more})

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Unhandled error in /api/files/search")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# GET /api/files/graph — 3D graph data (nodes + k-NN links) for a user
# ---------------------------------------------------------------------------

_GRAPH_MAX_NODES = 2000


def _compute_graph(user_id: str) -> dict:
    """
    Synchronous graph computation, run inside _graph_pool to avoid blocking
    the async event loop (O(n²) matrix multiply for k-NN).
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    from sklearn.decomposition import PCA

    user_filter = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
    records = _scroll_all(_get_qdrant(), COLLECTION_NAME, user_filter, with_vectors=True)

    if not records:
        return {"nodes": [], "links": [], "truncated": False}

    truncated = len(records) > _GRAPH_MAX_NODES
    if truncated:
        records = records[:_GRAPH_MAX_NODES]

    # Build raw vector matrix — handle both unnamed (List[float]) and named
    # (Dict[str, List[float]]) vector formats across qdrant-client versions.
    vecs = []
    for r in records:
        v = r.vector
        if v is None:
            v = [0.0] * VECTOR_DIM
        elif isinstance(v, dict):
            v = v.get("", next(iter(v.values()), [0.0] * VECTOR_DIM))
        vecs.append(v)
    vecs_np = np.array(vecs, dtype=np.float32)

    # PCA → 3D starting positions (scaled to ±100 range)
    n = len(vecs_np)
    n_components = min(3, n)
    if n >= 2:
        coords = PCA(n_components=n_components).fit_transform(vecs_np)
        if coords.shape[1] < 3:
            padding = np.zeros((n, 3 - coords.shape[1]), dtype=np.float32)
            coords = np.hstack([coords, padding])
        scale = 150.0 / (coords.std() + 1e-8)
        coords *= scale
    else:
        coords = np.zeros((n, 3), dtype=np.float32)

    nodes = []
    for i, r in enumerate(records):
        p = r.payload
        nodes.append({
            "id": str(r.id),
            "filename": p.get("filename", ""),
            "process_type": p.get("process_type", ""),
            "subgroup": p.get("subgroup", ""),
            "duration": p.get("duration", 0),
            "created_at": p.get("created_at", ""),
            "original_filename": p.get("original_filename", ""),
            "x": round(float(coords[i, 0]), 2),
            "y": round(float(coords[i, 1]), 2),
            "z": round(float(coords[i, 2]), 2),
        })

    K = 4
    SIM_THRESHOLD = 0.45
    norms = np.linalg.norm(vecs_np, axis=1, keepdims=True)
    normed = vecs_np / np.maximum(norms, 1e-10)
    sim_matrix = normed @ normed.T

    edge_set: set = set()
    links = []
    for i in range(n):
        row = sim_matrix[i].copy()
        row[i] = -1.0
        top_k = np.argsort(row)[::-1][:K]
        for j in top_k:
            sim = float(row[j])
            if sim < SIM_THRESHOLD:
                continue
            key = (min(nodes[i]["id"], nodes[j]["id"]), max(nodes[i]["id"], nodes[j]["id"]))
            if key not in edge_set:
                edge_set.add(key)
                links.append({
                    "source": nodes[i]["id"],
                    "target": nodes[j]["id"],
                    "similarity": round(sim, 3),
                })

    return {"nodes": nodes, "links": links, "truncated": truncated}


@app.get("/api/files/graph")
async def get_graph_data(authorization: str = Header(default=None)):
    """
    Return PCA-positioned graph data for the authenticated user's files.
    Nodes carry 3D coordinates (pre-computed via PCA on 768-d CLAP embeddings).
    Links connect each node to its k=4 most similar neighbours (cosine sim >= 0.45).
    Requires a premium subscription.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.removeprefix("Bearer ").strip()
    profile = await _require_premium_profile(token)
    user_id = profile["clerk_id"]

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_graph_pool, _compute_graph, user_id)
        return JSONResponse(result)

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Unhandled error in /api/files/graph")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


# ---------------------------------------------------------------------------
# DELETE /api/files/{point_id} — Remove a single file from the user's graph
# ---------------------------------------------------------------------------

@app.delete("/api/files/{point_id}")
async def delete_file(point_id: str, authorization: str = Header(default=None)):
    """
    Delete one file from the authenticated user's graph.
    Removes the Qdrant vector and the corresponding R2 object.
    Ownership is verified before deletion.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = await _resolve_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        qdrant = _get_qdrant()
        records = qdrant.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[point_id],
            with_payload=True,
        )
    except Exception as exc:
        log.exception("Unhandled error retrieving point %s for deletion", point_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    if not records:
        raise HTTPException(status_code=404, detail="File not found")

    payload = records[0].payload
    if payload.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    r2_key = payload.get("r2_key")
    if r2_key:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: _get_s3_upload().delete_object(Bucket=R2_BUCKET_NAME, Key=r2_key),
            )
        except Exception as exc:
            log.error("S3 delete failed for key %s: %s", r2_key, exc)

    try:
        qdrant.delete(collection_name=COLLECTION_NAME, points_selector=[point_id])
    except Exception as exc:
        log.exception("Qdrant delete failed for point %s", point_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return JSONResponse({"deleted": point_id})


# ---------------------------------------------------------------------------
# Internal — user data purge (called by auth service on user.deleted)
# ---------------------------------------------------------------------------

@app.delete("/internal/users/{clerk_id}")
async def purge_user_data(clerk_id: str, x_internal_secret: str = Header(...)):
    """Internal endpoint: purge all Qdrant vectors and S3 objects for a deleted user."""
    if not INTERNAL_SECRET or x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    try:
        _get_qdrant().delete(
            collection_name=COLLECTION_NAME,
            points_selector=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=clerk_id))]
            ),
        )
        log.info("Purged Qdrant vectors for user %s", clerk_id)
    except Exception as exc:
        log.error("Qdrant purge failed for user %s: %s", clerk_id, exc)

    try:
        s3 = _get_s3_upload()
        loop = asyncio.get_running_loop()
        paginator = await loop.run_in_executor(
            None, lambda: s3.get_paginator("list_objects_v2")
        )
        pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=f"{clerk_id}/")
        for page in pages:
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                s3.delete_objects(Bucket=R2_BUCKET_NAME, Delete={"Objects": keys})
        log.info("Purged S3 objects for user %s under prefix %s/", clerk_id, clerk_id)
    except Exception as exc:
        log.error("S3 purge failed for user %s: %s", clerk_id, exc)

    return JSONResponse({"purged": clerk_id})
