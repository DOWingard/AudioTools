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
    POST /api/files/search         → JSON (similarity search in user's graph — premium)
"""

import asyncio
import io
import json
import os
import shutil
import subprocess
import tempfile
import uuid as _uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

QDRANT_URL      = os.environ.get("QDRANT_URL", "http://localhost:6333")
AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "http://localhost:8001")
USER_FILES_DIR  = Path(os.environ.get("USER_FILES_DIR", "/data/user_files"))
COLLECTION_NAME = "master_audio_graph"
VECTOR_DIM      = 768

# Thread pool for CPU-heavy background embedding (separate from FastAPI async workers)
_embed_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embed")

app = FastAPI(title="SyncTag Compute API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Lazy-init singletons — ML models load once on first request
# ---------------------------------------------------------------------------
_tagger = None
_embedder = None
_qdrant = None


def _get_tagger():
    global _tagger
    if _tagger is None:
        from src.synctag import SyncTagger
        _tagger = SyncTagger()
    return _tagger


def _get_embedder():
    global _embedder
    if _embedder is None:
        from src.embedder import AudioEmbedder
        _embedder = AudioEmbedder()
    return _embedder


# ---------------------------------------------------------------------------
# Qdrant setup
# ---------------------------------------------------------------------------

def _get_qdrant():
    """Return the Qdrant client, creating the collection if it doesn't exist."""
    global _qdrant
    if _qdrant is not None:
        return _qdrant

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

def _persist_file(user_id: str, process_type: str, job_id: str, filename: str, src_path: Path) -> Path:
    """Copy a processed audio file into persistent storage. Returns the dest path."""
    dest_dir = USER_FILES_DIR / user_id / process_type / job_id
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
    file_path: Path,
    user_id: str,
    process_type: str,
    subgroup: str,
    original_filename: str,
    job_id: str,
    extra_payload: Optional[dict] = None,
) -> None:
    """
    Synchronous: embed an audio file with M2D-CLAP and upsert into Qdrant.
    Designed to run in a thread-pool executor so it doesn't block the event loop.
    extra_payload: optional dict of additional fields merged into the Qdrant payload.
    """
    import traceback
    print(f"[qdrant] Starting embed: {file_path.name} ({process_type}/{subgroup}) user={user_id}")
    try:
        from qdrant_client.models import PointStruct

        embedder = _get_embedder()
        waveform = embedder.load_audio(str(file_path))
        embedding = embedder.embed(waveform)  # numpy (768,)

        duration = _get_duration(file_path)

        # Deterministic ID: same file + user always maps to same Qdrant point
        point_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{user_id}:{file_path}"))

        payload = {
            "user_id": user_id,
            "filename": file_path.name,
            "original_filename": original_filename,
            "process_type": process_type,
            "subgroup": subgroup,
            "file_path": str(file_path),
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration": round(duration, 2),
            "file_size": file_path.stat().st_size,
        }
        if extra_payload:
            payload.update(extra_payload)

        qdrant = _get_qdrant()
        qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=point_id,
                    vector=embedding.tolist(),
                    payload=payload,
                )
            ],
        )
        print(f"[qdrant] ✓ Stored {file_path.name} ({process_type}/{subgroup}) for {user_id} — point_id={point_id}")
    except Exception as exc:
        print(f"[qdrant] ✗ Embedding failed for {file_path}: {exc}")
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
    authorization: str = Header(default=None),
):
    """
    Accept an audio upload, run the full SyncTag pipeline, and return a ZIP
    containing:
      - metadata.json
      - <stem>.csv
      - <stem>_tagged.<ext>
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

        # Save upload
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        # Run pipeline
        tagger = _get_tagger()
        result = tagger.run(input_path)

        # Inject ISRC if provided
        isrc = (isrc or "").strip()
        if isrc:
            result.setdefault("llm", {}).setdefault("metadata", {})["ISRC"] = isrc

        # Write ID3 tags onto the audio copy
        from src.export import write_csv_sidecar, write_id3_tags
        try:
            write_id3_tags(input_path, result)
        except Exception as exc:
            print(f"[api/tag] ID3 warning: {exc}")

        # Write CSV sidecar
        csv_path = tmp_dir / f"{input_path.stem}.csv"
        write_csv_sidecar(result, csv_path)

        # Write metadata JSON
        meta_path = tmp_dir / "metadata.json"
        meta_path.write_text(json.dumps(result, indent=2, default=str))

        # Persist tagged audio + schedule embedding if authenticated
        if user_id:
            job_id = str(_uuid.uuid4())
            dest = _persist_file(user_id, "tag", job_id, input_path.name, input_path)
            asyncio.create_task(
                _schedule_embed(dest, user_id, "tag", "tagged", audio.filename or "input.wav", job_id)
            )

        # Pack into ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(meta_path, arcname="metadata.json")
            zf.write(csv_path, arcname=csv_path.name)
            tagged_name = f"{input_path.stem}_tagged{input_path.suffix}"
            zf.write(input_path, arcname=tagged_name)
        buf.seek(0)

        filename = f"{input_path.stem}_synctag.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        output_dir = tmp_dir / "stems" / input_path.stem
        device = "cuda" if torch.cuda.is_available() else "cpu"

        from src.advanced_separate import run_pipeline
        all_files = run_pipeline(input_path, output_dir, device=device)

        # Persist + schedule embedding if authenticated
        if user_id:
            job_id = str(_uuid.uuid4())
            for stem_key, filepath in all_files.items():
                p = Path(filepath)
                if p.exists():
                    subgroup = _stem_subgroup(stem_key)
                    dest = _persist_file(user_id, "separate", job_id, f"{stem_key}.wav", p)
                    asyncio.create_task(
                        _schedule_embed(
                            dest, user_id, "separate", subgroup,
                            audio.filename or "input.wav", job_id,
                        )
                    )

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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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

        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        output_path = tmp_dir / f"{input_path.stem}_trimmed.wav"

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-ss", str(start),
        ]
        if end > start:
            cmd += ["-to", str(end)]
        cmd += ["-c", "pcm_s16le", str(output_path)]

        subprocess.run(cmd, capture_output=True, check=True)

        # Persist + schedule embedding if authenticated
        if user_id:
            job_id = str(_uuid.uuid4())
            dest = _persist_file(user_id, "cut", job_id, output_path.name, output_path)
            asyncio.create_task(
                _schedule_embed(dest, user_id, "cut", "trimmed", audio.filename or "input.wav", job_id)
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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
            content = await f.read()
            fpath.write_bytes(content)
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

        # Persist + schedule embedding if authenticated
        if user_id:
            job_id = str(_uuid.uuid4())
            dest = _persist_file(user_id, "join", job_id, "joined.wav", output_path)
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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

        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

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

        # Persist + schedule embedding if authenticated
        if user_id:
            job_id = str(_uuid.uuid4())
            dest = _persist_file(user_id, "karaoke", job_id, output_path.name, output_path)
            asyncio.create_task(
                _schedule_embed(dest, user_id, "karaoke", "instrumental", audio.filename or "input.wav", job_id)
            )

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{output_path.name}"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/convert — Format Converter
# ---------------------------------------------------------------------------

_FORMAT_MAP = {
    "mp3":  {"ext": ".mp3",  "codec": "libmp3lame", "mime": "audio/mpeg"},
    "wav":  {"ext": ".wav",  "codec": "pcm_s16le",  "mime": "audio/wav"},
    "flac": {"ext": ".flac", "codec": "flac",       "mime": "audio/flac"},
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

        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        output_name = f"{input_path.stem}_converted{spec['ext']}"
        output_path = tmp_dir / output_name

        cmd = ["ffmpeg", "-y", "-i", str(input_path), "-c:a", spec["codec"]]
        if fmt == "mp3":
            cmd += ["-q:a", "2"]  # high quality VBR

        cmd.append(str(output_path))

        subprocess.run(cmd, capture_output=True, check=True)

        # Persist + schedule embedding if authenticated
        if user_id:
            job_id = str(_uuid.uuid4())
            dest = _persist_file(user_id, "convert", job_id, output_path.name, output_path)
            asyncio.create_task(
                _schedule_embed(dest, user_id, "convert", "converted", audio.filename or "input.wav", job_id)
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        result = _detect_bpm_key(str(input_path))
        return JSONResponse(result)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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

        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        result = _analyze_audio(str(input_path))

        # Persist original + schedule embedding if authenticated
        if user_id:
            job_id = str(_uuid.uuid4())
            dest = _persist_file(user_id, "analyze", job_id, input_path.name, input_path)
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
                    audio.filename or "input.wav", job_id, extra,
                )
            )

        return JSONResponse(result)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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
        records, _ = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            with_payload=True,
            with_vectors=False,
            limit=1000,
        )

        files = [{"id": str(r.id), **r.payload} for r in records]
        # Sort newest first
        files.sort(key=lambda f: f.get("created_at", ""), reverse=True)

        return JSONResponse({"files": files})

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/files/{point_id}/audio — Serve a persisted audio file
# ---------------------------------------------------------------------------

@app.get("/api/files/{point_id}/audio")
async def get_file_audio(point_id: str, authorization: str = Header(default=None)):
    """
    Serve the audio file associated with the given Qdrant point ID.
    Validates that the authenticated user owns the file.
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
        raise HTTPException(status_code=500, detail=f"Qdrant error: {exc}") from exc

    if not records:
        raise HTTPException(status_code=404, detail="File not found")

    payload = records[0].payload
    if payload.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    file_path = Path(payload["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    # Guess MIME type from extension
    ext = file_path.suffix.lower()
    mime = {"mp3": "audio/mpeg", "flac": "audio/flac"}.get(ext.lstrip("."), "audio/wav")

    return StreamingResponse(
        io.BytesIO(file_path.read_bytes()),
        media_type=mime,
        headers={
            "Content-Disposition": f'inline; filename="{file_path.name}"',
            "Accept-Ranges": "bytes",
        },
    )


# ---------------------------------------------------------------------------
# POST /api/files/search — Similarity search in user's personal graph
# ---------------------------------------------------------------------------

async def _require_premium_profile(authorization: str) -> dict:
    """Validate token, fetch profile, assert subscription_active. Returns profile dict."""
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
        raise HTTPException(status_code=403, detail="Premium subscription required")
    return profile


@app.post("/api/files/search")
async def search_files(
    audio: Optional[UploadFile] = File(default=None),
    query_text: str = Form(default=""),
    offset: int = Form(default=0),
    limit: int = Form(default=5),
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
            input_path = tmp_dir / (audio.filename or "query.wav")
            content = await audio.read()
            input_path.write_bytes(content)
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# GET /api/files/graph — 3D graph data (nodes + k-NN links) for a user
# ---------------------------------------------------------------------------

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
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        from sklearn.decomposition import PCA

        qdrant = _get_qdrant()
        records, _ = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            with_payload=True,
            with_vectors=True,
            limit=500,
        )

        if not records:
            return JSONResponse({"nodes": [], "links": []})

        # Build raw vector matrix — handle both unnamed (List[float]) and named
        # (Dict[str, List[float]]) vector formats across qdrant-client versions.
        vecs = []
        for r in records:
            v = r.vector
            if v is None:
                v = [0.0] * VECTOR_DIM
            elif isinstance(v, dict):
                # Named vector: take the default "" key or first available key
                v = v.get("", next(iter(v.values()), [0.0] * VECTOR_DIM))
            vecs.append(v)
        vecs_np = np.array(vecs, dtype=np.float32)

        # PCA → 3D starting positions (scaled to ±100 range)
        n = len(vecs_np)
        n_components = min(3, n)
        if n >= 2:
            coords = PCA(n_components=n_components).fit_transform(vecs_np)
            # Pad to 3 columns if fewer than 3 components
            if coords.shape[1] < 3:
                padding = np.zeros((n, 3 - coords.shape[1]), dtype=np.float32)
                coords = np.hstack([coords, padding])
            scale = 150.0 / (coords.std() + 1e-8)
            coords *= scale
        else:
            coords = np.zeros((n, 3), dtype=np.float32)

        # Build node list with positions
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

        # Build k-NN edges using cosine similarity
        K = 4
        SIM_THRESHOLD = 0.45
        norms = np.linalg.norm(vecs_np, axis=1, keepdims=True)
        normed = vecs_np / np.maximum(norms, 1e-10)
        sim_matrix = normed @ normed.T  # (n, n)

        edge_set: set = set()
        links = []
        for i in range(n):
            row = sim_matrix[i].copy()
            row[i] = -1.0  # exclude self
            top_k = np.argsort(row)[::-1][:K]
            for j in top_k:
                sim = float(row[j])
                if sim < SIM_THRESHOLD:
                    continue
                key = (min(nodes[i]["id"], nodes[j]["id"]),
                       max(nodes[i]["id"], nodes[j]["id"]))
                if key not in edge_set:
                    edge_set.add(key)
                    links.append({
                        "source": nodes[i]["id"],
                        "target": nodes[j]["id"],
                        "similarity": round(sim, 3),
                    })

        return JSONResponse({"nodes": nodes, "links": links})

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


