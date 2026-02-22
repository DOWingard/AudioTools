import math

import numpy as np
import pytest
import torch
from unittest.mock import MagicMock


@pytest.fixture
def dummy_waveform_short() -> np.ndarray:
    """0.5 s @ 16 kHz"""
    return np.zeros(8000, dtype=np.float32)


@pytest.fixture
def dummy_waveform_long() -> np.ndarray:
    """20 s @ 16 kHz"""
    return np.zeros(320000, dtype=np.float32)


@pytest.fixture
def dummy_embedding() -> np.ndarray:
    """Unit-norm 768-d embedding."""
    rng = np.random.default_rng(42)
    vec = rng.standard_normal(768).astype(np.float32)
    return vec / np.linalg.norm(vec)


@pytest.fixture
def mock_m2d_model() -> MagicMock:
    unit_vec = np.ones(768, dtype=np.float32) / math.sqrt(768)
    # encode_clap_audio returns (1, 768) tensor
    fake_tensor = torch.from_numpy(unit_vec).unsqueeze(0)

    model = MagicMock()
    model.to.return_value = model  # model.to(device) must return self
    model.encode_clap_audio.return_value = fake_tensor
    return model
