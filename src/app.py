import os
from pathlib import Path

import gradio as gr
from sklearn.decomposition import PCA
import numpy as np
import pandas as pd

from src.embedder import AudioEmbedder
from src.database import AudioDatabase
from src.orchestrator import Orchestrator

SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", "samples")).resolve()

embedder = AudioEmbedder()
db = AudioDatabase(host=os.environ.get("QDRANT_HOST", "localhost"))
orchestrator = Orchestrator(embedder=embedder, db=db)


# ---------------------------------------------------------------------------
# Legacy similarity search (kept for backward compatibility)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# NLP Pipeline handler
# ---------------------------------------------------------------------------

def nlp_query(user_text: str, audio_path: str | None) -> dict:
    """Run the orchestrator and return a state dict for rendering."""
    if not user_text and audio_path is None:
        return {"intent": {}, "results": [], "error": "Please provide a text prompt, an audio file, or both."}

    # Default prompt when only audio is uploaded
    if not user_text and audio_path is not None:
        user_text = "find similar sounds to this"

    result = orchestrator.run(user_text, audio_path)
    return result


# ---------------------------------------------------------------------------
# Graph view
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="Audio Explorer") as demo:
    gr.Markdown("## 🎛️ Audio Explorer")

    with gr.Tabs():
        # ── NLP Pipeline Tab ──────────────────────────────────────────── #
        with gr.Tab("🧠 NLP Pipeline"):
            gr.Markdown(
                "Describe what you're looking for in plain language. "
                "Optionally upload a reference track.\n\n"
                "*Examples: \"find me kicks like this song\", \"Metro Boomin 808\", \"break down this track\"*"
            )
            with gr.Row():
                nlp_text = gr.Textbox(
                    label="Prompt",
                    placeholder='e.g. "find me punchy kicks like this song"',
                    scale=3,
                )
                nlp_audio = gr.Audio(
                    label="Reference Audio (optional)",
                    type="filepath",
                    sources=["upload", "microphone"],
                    scale=2,
                )
            nlp_btn = gr.Button("Run Pipeline 🚀", variant="primary")

            nlp_state = gr.State({})

            # Render results dynamically based on action type
            @gr.render(inputs=nlp_state)
            def render_nlp(state):
                if not state:
                    return

                intent = state.get("intent", {})
                error = state.get("error", "")
                action = intent.get("action", "")

                # Show intent
                gr.Markdown(f"**Intent:** `{intent}`")

                if error:
                    gr.Markdown(f"⚠️ **Error:** {error}")
                    return

                results = state.get("results", {})

                # Decompose action — results are paths
                if action == "decompose":
                    gr.Markdown("### 🎚️ Decomposed Stems")
                    if isinstance(results, dict):
                        for label, filepath in results.items():
                            gr.Audio(
                                value=str(filepath),
                                label=label,
                                type="filepath",
                                interactive=False,
                            )
                    return

                # Search actions — results are similarity hits
                if action in ("search_stem", "search_clip", "text_search"):
                    stem_used = state.get("stem_used", "")
                    if stem_used:
                        gr.Markdown(f"### 🔍 Results (stem used: `{Path(stem_used).name}`)")
                    else:
                        query_text = intent.get("query", "")
                        gr.Markdown(f"### 🔍 Results{f' for: *{query_text}*' if query_text else ''}")

                    if isinstance(results, list):
                        for i, res in enumerate(results):
                            score = res.get("score", 0)
                            filepath = res.get("filepath", "")
                            gr.Audio(
                                value=filepath,
                                label=f"Result {i + 1} (Similarity: {score:.4f})",
                                type="filepath",
                                interactive=False,
                            )

            nlp_btn.click(fn=nlp_query, inputs=[nlp_text, nlp_audio], outputs=nlp_state)

        # ── Legacy Similarity Search Tab ──────────────────────────────── #
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

        # ── Graph View Tab ────────────────────────────────────────────── #
        with gr.Tab("Graph View"):
            gr.Markdown("Click a node to play its audio.")
            graph_btn = gr.Button("Load / Refresh Graph")

            audio_plot = gr.ScatterPlot(
                x="x",
                y="y",
                tooltip=["filename"],
                title="Audio Embeddings Projection (PCA)",
            )
            df_state = gr.State(pd.DataFrame())
            player = gr.Audio(label="Audio Player", type="filepath", interactive=False)
            debug_txt = gr.Textbox(label="Debug Info", interactive=False)

            graph_btn.click(fn=get_graph_data, inputs=[], outputs=[df_state]).then(
                fn=lambda x: x, inputs=[df_state], outputs=[audio_plot]
            )

            def play_audio(evt: gr.SelectData, df: pd.DataFrame):
                idx = evt.index
                if isinstance(idx, (list, tuple)) and len(idx) > 0:
                    idx = idx[0]
                if isinstance(idx, int) and idx < len(df):
                    filepath = df.iloc[idx]["filepath"]
                    return filepath, f"Index: {idx}, File: {filepath}"
                return None, f"DEBUG: invalid index {idx}. Type: {type(idx)}"

            audio_plot.select(fn=play_audio, inputs=[df_state], outputs=[player, debug_txt])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, allowed_paths=[str(SAMPLES_DIR)])
