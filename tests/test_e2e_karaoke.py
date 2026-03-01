"""
E2E test: vocal removal → MinIO storage → Qdrant embedding

Flow:
  1. POST /api/karaoke with a small test audio file (no auth header needed
     because compute runs with DEV_USER_ID set in .env)
  2. Wait for the background embed+upload task to finish (polls Qdrant for
     up to 3 minutes)
  3. Assert a Qdrant point exists with process_type="karaoke"
  4. Assert the audio file exists in MinIO (HEAD request)

Run from the project root:
    python tests/test_e2e_karaoke.py
"""

import io
import os
import struct
import sys
import time
import wave

import boto3
import requests
from qdrant_client import QdrantClient

# ── Config ─────────────────────────────────────────────────────────────────
COMPUTE_URL = os.environ.get("COMPUTE_URL", "http://localhost:8000")
QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")

# MinIO — read from env so the same vars used by docker-compose work here.
# Defaults match the docker-compose.yml no-.env fallbacks.
MINIO_ENDPOINT    = os.environ.get("S3_PUBLIC_ENDPOINT_URL", "http://localhost:9010")
MINIO_ACCESS_KEY  = os.environ.get("R2_ACCESS_KEY_ID", "minioadmin")
MINIO_SECRET_KEY  = os.environ.get("R2_SECRET_ACCESS_KEY", "minioadmin")
MINIO_BUCKET      = os.environ.get("R2_BUCKET_NAME", "syntag-audio")

DEV_USER_ID       = "user_3AFE1IwaiqzlVSDbeadRr2esw2O"
COLLECTION        = "master_audio_graph"
POLL_INTERVAL     = 5   # seconds between Qdrant polls
MAX_WAIT          = 300 # seconds total (Demucs can take ~90s on CPU)


def _make_test_wav(duration_s: float = 3.0, sr: int = 44100) -> bytes:
    """Generate a minimal 3-second stereo sine-wave WAV (pcm_s16le)."""
    import math
    n_samples = int(sr * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        freq = 440.0
        frames = []
        for i in range(n_samples):
            val = int(32767 * math.sin(2 * math.pi * freq * i / sr))
            frames.append(struct.pack("<hh", val, val))
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def step1_call_karaoke(wav_bytes: bytes) -> tuple[str, float]:
    """Returns (stem_name, request_epoch)."""
    print("\n[1] POST /api/karaoke …", flush=True)
    t_start = time.time()
    resp = requests.post(
        f"{COMPUTE_URL}/api/karaoke",
        files={"audio": ("test_song.wav", wav_bytes, "audio/wav")},
        timeout=300,
    )
    if resp.status_code != 200:
        print(f"    ✗ HTTP {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)

    content_type = resp.headers.get("content-type", "")
    size_kb = len(resp.content) / 1024
    print(f"    ✓ Got {content_type}  ({size_kb:.1f} KB)")
    return "test_song", t_start


def step2_poll_qdrant(t_start: float) -> dict:
    """Poll until a Qdrant point with process_type=karaoke created AFTER t_start appears."""
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(t_start, tz=timezone.utc).isoformat()
    print(f"\n[2] Polling Qdrant for process_type=karaoke created after {ts} …", flush=True)
    client = QdrantClient(url=QDRANT_URL, timeout=10, check_compatibility=False)

    deadline = time.time() + MAX_WAIT
    attempt  = 0
    while time.time() < deadline:
        attempt += 1
        try:
            r = client.scroll(
                collection_name=COLLECTION,
                scroll_filter={
                    "must": [
                        {"key": "user_id",      "match": {"value": DEV_USER_ID}},
                        {"key": "process_type", "match": {"value": "karaoke"}},
                    ]
                },
                limit=20,
                with_payload=True,
                with_vectors=False,
            )
            all_points = r[0] if r else []
        except Exception as exc:
            print(f"    attempt {attempt}: qdrant error — {exc}")
            all_points = []

        # Filter to points created after the request was made
        new_points = []
        for pt in all_points:
            payload = pt.payload if hasattr(pt, "payload") else {}
            created = payload.get("created_at", "")
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(created)
                if dt.timestamp() >= t_start:
                    new_points.append((pt, payload))
            except Exception:
                pass

        if new_points:
            pt, payload = new_points[0]
            print(f"    ✓ Found {len(new_points)} new point(s) in Qdrant after {attempt} poll(s)")
            print(f"      point_id  : {pt.id}")
            print(f"      r2_key    : {payload.get('r2_key')}")
            print(f"      subgroup  : {payload.get('subgroup')}")
            print(f"      duration  : {payload.get('duration')}s")
            print(f"      file_size : {payload.get('file_size')} bytes")
            print(f"      created_at: {payload.get('created_at')}")
            return payload

        elapsed = int(time.time() - (deadline - MAX_WAIT))
        print(f"    [{elapsed:>3}s] attempt {attempt}: no new karaoke point yet — waiting {POLL_INTERVAL}s …")
        time.sleep(POLL_INTERVAL)

    print(f"    ✗ Timed out after {MAX_WAIT}s — point never appeared in Qdrant")
    sys.exit(1)


def step3_verify_minio(r2_key: str) -> None:
    print(f"\n[3] Verifying file in MinIO bucket '{MINIO_BUCKET}' …", flush=True)
    s3 = boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="auto",
    )
    try:
        head = s3.head_object(Bucket=MINIO_BUCKET, Key=r2_key)
        size_kb = head["ContentLength"] / 1024
        print(f"    ✓ Object exists — size={size_kb:.1f} KB  ETag={head.get('ETag','?')}")
    except s3.exceptions.NoSuchKey:
        print(f"    ✗ Key not found in MinIO: {r2_key}")
        sys.exit(1)
    except Exception as exc:
        print(f"    ✗ MinIO check failed: {exc}")
        sys.exit(1)


def main():
    print("=" * 60)
    print("E2E: Karaoke → MinIO + Qdrant")
    print("=" * 60)

    # Health check
    try:
        r = requests.get(f"{COMPUTE_URL}/health", timeout=5)
        assert r.json() == {"status": "ok"}, f"Unexpected health response: {r.json()}"
        print(f"[0] Compute API healthy ✓")
    except Exception as exc:
        print(f"[0] Compute API unreachable: {exc}")
        sys.exit(1)

    wav_bytes       = _make_test_wav()
    _, t_start      = step1_call_karaoke(wav_bytes)
    payload         = step2_poll_qdrant(t_start)
    r2_key = payload.get("r2_key")
    if not r2_key:
        print("    ✗ No r2_key in Qdrant payload — upload step may have failed")
        sys.exit(1)
    assert isinstance(r2_key, str)
    step3_verify_minio(r2_key)

    print("\n" + "=" * 60)
    print("✓  E2E PASSED — karaoke file stored in MinIO and indexed in Qdrant")
    print("=" * 60)


if __name__ == "__main__":
    main()
