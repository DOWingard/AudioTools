import logging
import os
from pathlib import Path
from typing import List, Optional
from uuid import uuid5, NAMESPACE_URL

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger(__name__)

COLLECTION_NAME = "audio_library"
VECTOR_DIM = 768


class AudioDatabase:
    def __init__(self, host: Optional[str] = None, port: int = 6333):
        if host is None:
            host = os.environ.get("QDRANT_HOST", "localhost")
        self.client = QdrantClient(host=host, port=port)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        names = [c.name for c in self.client.get_collections().collections]
        if COLLECTION_NAME not in names:
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
        else:
            logger.info("Collection '%s' already exists, skipping creation.", COLLECTION_NAME)

    def upsert(self, embedding: np.ndarray, filepath: str) -> str:
        # Deterministic UUID keyed on the absolute filepath — idempotent re-ingest
        point_id = str(uuid5(NAMESPACE_URL, filepath))
        path = Path(filepath)
        payload = {
            "filename": path.name,
            "filepath": str(path),
            "stem": path.stem,
        }
        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(id=point_id, vector=embedding.tolist(), payload=payload)],
        )
        return point_id

    def search(self, query_embedding: np.ndarray, limit: int = 10, offset: int = 0) -> List[dict]:
        # qdrant-client ≥1.10: client.search() removed; use query_points()
        result = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_embedding.tolist(),
            limit=limit,
            offset=offset,
            with_payload=True,
        )
        return [{**hit.payload, "score": hit.score} for hit in result.points]

    def get_all_points(self) -> tuple[List[dict], np.ndarray]:
        """Retrieve all points (payloads and vectors) from the database."""
        result = self.client.scroll(
            collection_name=COLLECTION_NAME,
            limit=10000,
            with_payload=True,
            with_vectors=True,
        )
        points = result[0]
        payloads = [p.payload for p in points]
        vectors = np.array([p.vector for p in points])
        return payloads, vectors
