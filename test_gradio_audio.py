import gradio as gr
import soundfile as sf
import os
import shutil

# Create a dummy 44.1kHz sine wave
import numpy as np
sr = 44100
t = np.linspace(0, 1, sr)
audio = np.sin(2 * np.pi * 440 * t)
sf.write("dummy_44k.wav", audio, sr)
print("Original SR:", sr)

def process_audio(filepath):
    data, r = sf.read(filepath)
    return f"Gradio gave me SR: {r}"

demo = gr.Interface(fn=process_audio, inputs=gr.Audio(type="filepath"), outputs="text")
# We can't easily script the web interaction, so let's check Gradio's source or just use ffmpeg locally to check if there's compression.
