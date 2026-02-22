import subprocess
import numpy as np
import torch
from transformers import ClapModel, ClapFeatureExtractor

MODEL_ID = "laion/clap-htsat-fused"
SAMPLE_RATE = 48000


class AudioEmbedder:
    def __init__(self, model_id: str = MODEL_ID):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = ClapModel.from_pretrained(model_id).to(self.device)
        self.model.eval()
        self.feature_extractor = ClapFeatureExtractor.from_pretrained(model_id)

    def load_audio(self, path: str) -> np.ndarray:
        """Decode any audio format to 48 kHz mono float32 via ffmpeg.
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
        inputs = self.feature_extractor(
            waveform,
            sampling_rate=SAMPLE_RATE,
            return_tensors="pt",
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.inference_mode():
            output = self.model.get_audio_features(**inputs)
        embedding = output.pooler_output.squeeze(0).cpu().numpy()
        return embedding.astype(np.float32)
