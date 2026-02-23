import subprocess
import sys
from pathlib import Path
import os

import numpy as np
import torch

# Add m2d/examples to path for portable_m2d import
_M2D_EXAMPLES = Path(__file__).resolve().parent.parent / 'm2d' / 'examples'
if str(_M2D_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_M2D_EXAMPLES))

from portable_m2d import PortableM2D  # noqa: E402

SAMPLE_RATE = 16000

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEIGHT = os.environ.get(
    "M2D_WEIGHT",
    str(_PROJECT_ROOT / "m2d_clap_vit_base-80x1001p16x16p16kpBpTI-2025" / "checkpoint-30.pth"),
)


class AudioEmbedder:
    def __init__(self, weight_file: str = DEFAULT_WEIGHT):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = PortableM2D(weight_file=weight_file, flat_features=True)
        self.model = self.model.to(self.device)
        self.model.eval()

    def load_audio(self, path: str) -> np.ndarray:
        """Decode any audio format to 16 kHz mono float32 via ffmpeg.
        Avoids librosa/numba entirely — ffmpeg handles resampling and decoding."""
        result = subprocess.run(
            [
                "ffmpeg", "-v", "quiet", "-i", path,
                "-f", "f32le",
                "-acodec", "pcm_f32le",
                "-ar", str(SAMPLE_RATE),
                "-ac", "1",
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        )
        return np.frombuffer(result.stdout, dtype=np.float32).copy()

    def embed(self, waveform: np.ndarray) -> np.ndarray:
        # M2D-CLAP expects a batch tensor (B, T) at 16 kHz
        audio = torch.from_numpy(waveform).unsqueeze(0).to(self.device)  # (1, T)
        with torch.inference_mode():
            emb = self.model.encode_clap_audio(audio)  # (1, 768)
        return emb.squeeze(0).cpu().numpy().astype(np.float32)

    def encode_text(self, text: str) -> np.ndarray:
        """Encode a text description into the same 768-D CLAP space as audio.

        Uses M2D-CLAP's joint text-audio projection so that text queries
        like 'punchy acoustic kick drum' land near matching audio embeddings.
        """
        with torch.inference_mode():
            emb = self.model.encode_clap_text([text])  # (1, 768)
        return emb.squeeze(0).cpu().numpy().astype(np.float32)
