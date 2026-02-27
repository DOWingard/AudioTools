import librosa
import sys

print(f"librosa version: {librosa.__version__}")
try:
    y, sr = librosa.load("dummy.wav", sr=None)
    import soundfile as sf
    sf.write("test.ogg", y, sr, format="OGG")
    print("OGG written successfully.")
except Exception as e:
    print(f"Error: {e}")
