import math
import os
import tempfile

import numpy as np
import pytest
import soundfile as sf
from unittest.mock import MagicMock, patch

from src.embedder import AudioEmbedder, SAMPLE_RATE


def _make_embedder(mock_model: MagicMock, mock_fe: MagicMock) -> AudioEmbedder:
    # .to(device) must return the same mock so self.model.get_audio_features() is the right call
    mock_model.to.return_value = mock_model
    with (
        patch("src.embedder.ClapModel.from_pretrained", return_value=mock_model),
        patch("src.embedder.ClapFeatureExtractor.from_pretrained", return_value=mock_fe),
    ):
        embedder = AudioEmbedder()
    return embedder


def _write_wav(path: str, sample_rate: int, duration_s: float = 1.0) -> None:
    samples = int(sample_rate * duration_s)
    data = np.zeros(samples, dtype=np.float32)
    sf.write(path, data, sample_rate)


class TestLoadAudio:
    def test_load_audio_returns_float32(self, mock_clap_model, mock_feature_extractor, tmp_path):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        wav_path = str(tmp_path / "test.wav")
        _write_wav(wav_path, SAMPLE_RATE)
        waveform = embedder.load_audio(wav_path)
        assert waveform.dtype == np.float32

    def test_load_audio_resamples_to_48000(self, mock_clap_model, mock_feature_extractor, tmp_path):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        wav_path = str(tmp_path / "test_22k.wav")
        _write_wav(wav_path, sample_rate=22050, duration_s=1.0)
        waveform = embedder.load_audio(wav_path)
        # 1.0 s at 48 kHz → ~48000 samples (allow ±100 for rounding)
        assert abs(len(waveform) - 48000) < 100


class TestEmbed:
    def test_embed_output_shape(self, mock_clap_model, mock_feature_extractor, dummy_waveform_short):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        result = embedder.embed(dummy_waveform_short)
        assert result.shape == (512,)

    def test_embed_output_dtype(self, mock_clap_model, mock_feature_extractor, dummy_waveform_short):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        result = embedder.embed(dummy_waveform_short)
        assert result.dtype == np.float32

    def test_embed_is_l2_normalized(self, mock_clap_model, mock_feature_extractor, dummy_waveform_short):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        result = embedder.embed(dummy_waveform_short)
        norm = float(np.linalg.norm(result))
        assert abs(norm - 1.0) < 1e-5

    def test_embed_calls_get_audio_features(self, mock_clap_model, mock_feature_extractor, dummy_waveform_short):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        embedder.embed(dummy_waveform_short)
        mock_clap_model.get_audio_features.assert_called_once()

    def test_embed_short_audio(self, mock_clap_model, mock_feature_extractor, dummy_waveform_short):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        result = embedder.embed(dummy_waveform_short)
        assert result.shape == (512,)

    def test_embed_long_audio(self, mock_clap_model, mock_feature_extractor, dummy_waveform_long):
        embedder = _make_embedder(mock_clap_model, mock_feature_extractor)
        result = embedder.embed(dummy_waveform_long)
        assert result.shape == (512,)
