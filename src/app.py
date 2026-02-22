import os
from pathlib import Path

import gradio as gr
import plotly.express as px
from sklearn.decomposition import PCA
import numpy as np

from src.embedder import AudioEmbedder
from src.database import AudioDatabase

SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", "samples")).resolve()

embedder = AudioEmbedder()
db = AudioDatabase(host=os.environ.get("QDRANT_HOST", "localhost"))


def initial_search(upload_path: str) -> dict:
    if upload_path is None:
        return {"query_vec": None, "offset": 0, "results": []}

    waveform = embedder.load_audio(upload_path)
    query_vec = embedder.embed(waveform)
    
    if isinstance(query_vec, np.ndarray):
        query_vec = query_vec.tolist()
        
    results = db.search(np.array(query_vec), limit=10, offset=0)
    results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)
    return {"query_vec": query_vec, "offset": 10, "results": results}

def load_more(state: dict) -> dict:
    query_vec = state.get("query_vec")
    if query_vec is None:
        return state
        
    offset = state.get("offset", 0)
    current_results = state.get("results", [])
    
    more_results = db.search(np.array(query_vec), limit=10, offset=offset)
    more_results = sorted(more_results, key=lambda x: x.get("score", 0), reverse=True)
    
    new_results = current_results + more_results
    return {"query_vec": query_vec, "offset": offset + 10, "results": new_results}

import pandas as pd

def get_graph_data():
    payloads, vectors = db.get_all_points()
    if len(vectors) == 0:
        return pd.DataFrame()
    
    pca = PCA(n_components=2)
    components = pca.fit_transform(vectors)
    
    df = pd.DataFrame({
        "x": components[:, 0],
        "y": components[:, 1],
        "filename": [p["filename"] for p in payloads],
        "filepath": [p["filepath"] for p in payloads]
    })
    
    return df

def play_audio(evt: gr.SelectData, df: pd.DataFrame):
     # select event gives the index in the dataframe
     idx = evt.index
     print(f"DEBUG: evt.index = {idx}, type = {type(idx)}")
     
     if isinstance(idx, (list, tuple)) and len(idx) > 0:
         idx = idx[0]
         
     if isinstance(idx, int) and idx < len(df):
         filepath = df.iloc[idx]["filepath"]
         return filepath, f"Index: {evt.index}, File: {filepath}"
     
     return None, f"DEBUG: invalid index or out of bounds. Original evt.index was: {evt.index}"


with gr.Blocks(title="Audio Explorer") as demo:
    gr.Markdown("## Audio Explorer")

    with gr.Tabs():
        with gr.Tab("Similarity Search"):
            gr.Markdown("Upload or record audio to find the most similar samples in your library.")
            with gr.Row():
                query_audio = gr.Audio(
                    label="Query Audio",
                    type="filepath",
                    sources=["upload", "microphone"],
                )
            search_btn = gr.Button("Search", variant="primary")
            
            search_state = gr.State({"query_vec": None, "offset": 0, "results": []})
            
            @gr.render(inputs=search_state)
            def render_results(state):
                results = state.get("results", [])
                if not results:
                    return
                
                for i, res in enumerate(results):
                    score = res.get("score", 0)
                    filepath = res.get("filepath", "")
                    gr.Audio(value=filepath, label=f"Result {i + 1} (Similarity: {score:.4f})", type="filepath", interactive=False)
                
                load_more_btn = gr.Button("Load More Results ⬇️")
                load_more_btn.click(fn=load_more, inputs=[search_state], outputs=[search_state])
                
            search_btn.click(fn=initial_search, inputs=query_audio, outputs=search_state)

        with gr.Tab("Graph View"):
            gr.Markdown("Click a node to play its audio.")
            graph_btn = gr.Button("Load / Refresh Graph")
            
            # Using Gradio's native ScatterPlot which supports select
            audio_plot = gr.ScatterPlot(
                x="x",
                y="y",
                tooltip=["filename"],
                title="Audio Embeddings Projection (PCA)",
            )
            df_state = gr.State(pd.DataFrame())
            player = gr.Audio(label="Audio Player", type="filepath", interactive=False)
            debug_txt = gr.Textbox(label="Debug Info", interactive=False)
            
            # ScatterPlot requires a dataframe directly
            graph_btn.click(fn=get_graph_data, inputs=[], outputs=[df_state]).then(
                fn=lambda x: x, inputs=[df_state], outputs=[audio_plot]
            )

            # Gradio translates click events into SelectData
            def play_audio(evt: gr.SelectData, df: pd.DataFrame):
                idx = evt.index
                print(f"DEBUG: evt.index = {idx}, type = {type(idx)}")
                
                # Sometimes Gradio wraps the index in a list
                if isinstance(idx, (list, tuple)) and len(idx) > 0:
                    idx = idx[0]
                    
                # If it's a valid integer index
                if isinstance(idx, int) and idx < len(df):
                    filepath = df.iloc[idx]["filepath"]
                    return filepath, f"Index: {idx}, File: {filepath}"
                
                return None, f"DEBUG: invalid index {idx}. Type: {type(idx)}"

            audio_plot.select(fn=play_audio, inputs=[df_state], outputs=[player, debug_txt])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, allowed_paths=[str(SAMPLES_DIR)])
