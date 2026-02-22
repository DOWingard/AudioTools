import math

import numpy as np
import pytest
import torch
from unittest.mock import MagicMock


@pytest.fixture
def dummy_waveform_short() -> np.ndarray:
    """0.5 s @ 48 kHz"""
    return np.zeros(24000, dtype=np.float32)


@pytest.fixture
def dummy_waveform_long() -> np.ndarray:
    """20 s @ 48 kHz"""
    return np.zeros(960000, dtype=np.float32)


@pytest.fixture
def dummy_embedding() -> np.ndarray:
    """Unit-norm 512-d embedding."""
    rng = np.random.default_rng(42)
    vec = rng.standard_normal(512).astype(np.float32)
    return vec / np.linalg.norm(vec)


@pytest.fixture
def mock_clap_model() -> MagicMock:
    unit_vec = np.ones(512, dtype=np.float32) / math.sqrt(512)
    fake_tensor = MagicMock()
    fake_tensor.squeeze.return_value.cpu.return_value.numpy.return_value = unit_vec

    output = MagicMock()
    output.pooler_output = fake_tensor

    model = MagicMock()
    model.get_audio_features.return_value = output
    return model


@pytest.fixture
def mock_feature_extractor() -> MagicMock:
    fe = MagicMock()
    fe.return_value = {
        "input_features": torch.zeros(1, 4, 1001, 64),
        "is_longer": torch.tensor([[True]]),
    }
    return fe
