import numpy as np
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from src.ingest import run_ingest


def _touch_files(directory: Path, names: list[str]) -> list[Path]:
    paths = []
    for name in names:
        p = directory / name
        p.write_bytes(b"")
        paths.append(p)
    return paths


class TestRunIngest:
    def _run(self, tmp_path: Path, mock_embedder: MagicMock, mock_db: MagicMock) -> None:
        with (
            patch("src.ingest.AudioEmbedder", return_value=mock_embedder),
            patch("src.ingest.AudioDatabase", return_value=mock_db),
        ):
            run_ingest(str(tmp_path), "localhost")

    def _make_embedder(self) -> MagicMock:
        embedder = MagicMock()
        embedder.load_audio.return_value = np.zeros(48000, dtype=np.float32)
        embedder.embed.return_value = np.ones(768, dtype=np.float32) / np.sqrt(768)
        return embedder

    def test_full_scan_embed_upsert(self, tmp_path):
        _touch_files(tmp_path, ["a.wav", "b.mp3", "c.flac"])
        mock_embedder = self._make_embedder()
        mock_db = MagicMock()
        self._run(tmp_path, mock_embedder, mock_db)
        assert mock_embedder.load_audio.call_count == 3
        assert mock_embedder.embed.call_count == 3
        assert mock_db.upsert.call_count == 3

    def test_non_audio_files_skipped(self, tmp_path):
        _touch_files(tmp_path, ["audio.wav", "notes.txt", "image.jpg"])
        mock_embedder = self._make_embedder()
        mock_db = MagicMock()
        self._run(tmp_path, mock_embedder, mock_db)
        assert mock_embedder.load_audio.call_count == 1

    def test_per_file_error_continues(self, tmp_path):
        _touch_files(tmp_path, ["a.wav", "b.wav", "c.wav"])
        mock_embedder = self._make_embedder()
        # Second call raises an error
        mock_embedder.load_audio.side_effect = [
            np.zeros(48000, dtype=np.float32),
            RuntimeError("bad file"),
            np.zeros(48000, dtype=np.float32),
        ]
        mock_db = MagicMock()
        self._run(tmp_path, mock_embedder, mock_db)
        assert mock_db.upsert.call_count == 2

    def test_empty_samples_dir(self, tmp_path):
        mock_embedder = self._make_embedder()
        mock_db = MagicMock()
        self._run(tmp_path, mock_embedder, mock_db)
        mock_db.upsert.assert_not_called()
