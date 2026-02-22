import numpy as np
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from qdrant_client.models import Distance

from src.database import AudioDatabase, COLLECTION_NAME, VECTOR_DIM


def _make_db(mock_client: MagicMock) -> AudioDatabase:
    with patch("src.database.QdrantClient", return_value=mock_client):
        db = AudioDatabase(host="localhost", port=6333)
    return db


def _collections_response(names: list[str]) -> MagicMock:
    response = MagicMock()
    # SimpleNamespace gives a real .name attribute (MagicMock(name=n) only sets display name)
    response.collections = [SimpleNamespace(name=n) for n in names]
    return response


class TestEnsureCollection:
    def test_collection_exists_no_create(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([COLLECTION_NAME])
        _make_db(mock_client)
        mock_client.create_collection.assert_not_called()

    def test_collection_missing_creates_it(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([])
        _make_db(mock_client)
        mock_client.create_collection.assert_called_once()

    def test_create_collection_correct_params(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([])
        _make_db(mock_client)
        _, kwargs = mock_client.create_collection.call_args
        vectors_config = kwargs["vectors_config"]
        assert vectors_config.size == VECTOR_DIM
        assert vectors_config.distance == Distance.COSINE


class TestUpsert:
    def test_upsert_calls_client_upsert(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([COLLECTION_NAME])
        db = _make_db(mock_client)
        db.upsert(dummy_embedding, "/audio/kick.wav")
        mock_client.upsert.assert_called_once()

    def test_upsert_payload_structure(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([COLLECTION_NAME])
        db = _make_db(mock_client)
        db.upsert(dummy_embedding, "/audio/kick.wav")
        _, kwargs = mock_client.upsert.call_args
        point = kwargs["points"][0]
        assert point.payload["filename"] == "kick.wav"
        assert point.payload["filepath"] == "/audio/kick.wav"
        assert point.payload["stem"] == "kick"

    def test_upsert_returns_string_uuid(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([COLLECTION_NAME])
        db = _make_db(mock_client)
        result = db.upsert(dummy_embedding, "/audio/kick.wav")
        assert isinstance(result, str)
        assert len(result) == 36

    def test_upsert_is_idempotent(self, dummy_embedding):
        """Same filepath must produce the same UUID (uuid5 deduplication)."""
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([COLLECTION_NAME])
        db = _make_db(mock_client)
        id1 = db.upsert(dummy_embedding, "/audio/kick.wav")
        id2 = db.upsert(dummy_embedding, "/audio/kick.wav")
        assert id1 == id2


class TestSearch:
    def test_search_returns_results(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([COLLECTION_NAME])
        db = _make_db(mock_client)

        hit = MagicMock()
        hit.payload = {"filename": "kick.wav", "filepath": "/audio/kick.wav", "stem": "kick"}
        hit.score = 0.95
        mock_client.query_points.return_value.points = [hit]

        results = db.search(dummy_embedding, limit=5)
        assert len(results) == 1
        assert results[0]["filename"] == "kick.wav"
        assert results[0]["score"] == pytest.approx(0.95)

    def test_search_empty_collection(self, dummy_embedding):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = _collections_response([COLLECTION_NAME])
        db = _make_db(mock_client)
        mock_client.query_points.return_value.points = []
        results = db.search(dummy_embedding, limit=5)
        assert results == []
