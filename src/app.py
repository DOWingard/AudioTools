import os
from pathlib import Path

import gradio as gr

from src.embedder import AudioEmbedder
from src.database import AudioDatabase

SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", "samples")).resolve()

embedder = AudioEmbedder()
db = AudioDatabase(host=os.environ.get("QDRANT_HOST", "localhost"))


def search_similar(upload_path: str) -> tuple:
    if upload_path is None:
        return (None,) * 5

    waveform = embedder.load_audio(upload_path)
    query_vec = embedder.embed(waveform)
    results = db.search(query_vec, limit=5)

    filepaths = [r["filepath"] for r in results]
    while len(filepaths) < 5:
        filepaths.append(None)

    return tuple(filepaths[:5])


with gr.Blocks(title="Audio Similarity Search") as demo:
    gr.Markdown("## Audio Similarity Search\nUpload or record audio to find the 5 most similar samples in your library.")

    with gr.Row():
        query_audio = gr.Audio(
            label="Query Audio",
            type="filepath",
            sources=["upload", "microphone"],
        )

    search_btn = gr.Button("Search", variant="primary")

    with gr.Row():
        outputs = [gr.Audio(label=f"Result {i + 1}", type="filepath") for i in range(5)]

    search_btn.click(fn=search_similar, inputs=query_audio, outputs=outputs)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, allowed_paths=[str(SAMPLES_DIR)])
