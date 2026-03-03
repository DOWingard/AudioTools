#!/usr/bin/env python3
"""
wipe-storage.py — Permanently delete every audio file from Qdrant and MinIO/R2.

Scope: ALL users, ALL files.
This script is irreversible. Use with care.

Usage (from repo root):
    python scripts/wipe-storage.py

Reads credentials from .env in the repo root.
Assumes services are accessible at their host-mapped ports:
    Qdrant  → http://localhost:6333
    MinIO   → http://localhost:9010  (S3_PUBLIC_ENDPOINT_URL)
"""

import os
import sys
from pathlib import Path

# ── Load .env ──────────────────────────────────────────────────────────────
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

QDRANT_URL       = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME  = "master_audio_graph"
VECTOR_DIM       = 768

S3_ENDPOINT      = os.environ.get("S3_PUBLIC_ENDPOINT_URL", "http://localhost:9010")
R2_ACCESS_KEY    = os.environ.get("R2_ACCESS_KEY_ID", "minioadmin")
R2_SECRET_KEY    = os.environ.get("R2_SECRET_ACCESS_KEY", "minioadmin")
R2_BUCKET        = os.environ.get("R2_BUCKET_NAME", "syntag-audio")

# ── Confirm ────────────────────────────────────────────────────────────────
print("=" * 60)
print("  WIPE STORAGE — ALL DATA FOR ALL USERS")
print("=" * 60)
print(f"  Qdrant     : {QDRANT_URL}  collection={COLLECTION_NAME}")
print(f"  S3/MinIO   : {S3_ENDPOINT}  bucket={R2_BUCKET}")
print("=" * 60)
ans = input("Type YES to proceed: ").strip()
if ans != "YES":
    print("Aborted.")
    sys.exit(0)

# ── Wipe Qdrant ────────────────────────────────────────────────────────────
print("\n[1/2] Wiping Qdrant collection …")
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    qdrant = QdrantClient(url=QDRANT_URL, timeout=30, check_compatibility=False)

    collections = [c.name for c in qdrant.get_collections().collections]
    if COLLECTION_NAME in collections:
        qdrant.delete_collection(COLLECTION_NAME)
        print(f"  ✓ Deleted collection '{COLLECTION_NAME}'")
    else:
        print(f"  ℹ  Collection '{COLLECTION_NAME}' did not exist — nothing to delete")

    qdrant.create_collection(
        COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
    )
    from qdrant_client.models import PayloadSchemaType
    qdrant.create_payload_index(
        COLLECTION_NAME,
        field_name="user_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    print(f"  ✓ Recreated empty collection '{COLLECTION_NAME}' (dim={VECTOR_DIM}, cosine)")

except Exception as exc:
    print(f"  ✗ Qdrant error: {exc}")
    sys.exit(1)

# ── Wipe S3 / MinIO ────────────────────────────────────────────────────────
print(f"\n[2/2] Wiping S3 bucket '{R2_BUCKET}' …")
try:
    import boto3
    from botocore.config import Config

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )

    paginator = s3.get_paginator("list_objects_v2")
    total_deleted = 0

    for page in paginator.paginate(Bucket=R2_BUCKET):
        objects = page.get("Contents", [])
        if not objects:
            continue
        keys = [{"Key": obj["Key"]} for obj in objects]
        s3.delete_objects(Bucket=R2_BUCKET, Delete={"Objects": keys})
        total_deleted += len(keys)
        print(f"  … deleted {total_deleted} objects so far")

    if total_deleted == 0:
        print("  ℹ  Bucket was already empty")
    else:
        print(f"  ✓ Deleted {total_deleted} objects from '{R2_BUCKET}'")

except Exception as exc:
    print(f"  ✗ S3 error: {exc}")
    sys.exit(1)

print("\n✅  Done — Qdrant collection is empty, S3 bucket is empty.")
