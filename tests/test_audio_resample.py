import gradio as gr
from gradio.components.audio import Audio
import soundfile as sf
import numpy as np

# Create 44.1kHz audio
audio_data = np.random.randn(44100).astype(np.float32)
sf.write("dummy.wav", audio_data, 44100)

a = Audio(type="filepath")
processed = a.postprocess("dummy.wav")
print("Processed tuple:", processed)
if hasattr(a, 'preprocess'):
    # try to preprocess it like it came from UI
    # In newer gradio, preprocess takes the frontend value
    pass
