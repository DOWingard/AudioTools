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

_SEARCH_ACTIONS = {"search_stem", "search_clip", "text_search"}


# ---------------------------------------------------------------------------
# NLP Pipeline handlers
# ---------------------------------------------------------------------------

def nlp_query(user_text: str, audio_path: str | None) -> dict:
    """
    Route the request:
      - No audio  → text-only search (find me X from my samples)
      - Audio     → full pipeline (decompose → embed stem/clip → search)
    Returns a state dict consumed by render_nlp.
    """
    has_audio = audio_path is not None and Path(audio_path).is_file()

    if not user_text and not has_audio:
        return {
            "intent": {},
            "results": [],
            "error": "Please provide a text prompt, an audio file, or both.",
        }

    # Audio-only: default to "find similar sounds to this"
    if not user_text and has_audio:
        user_text = "find similar sounds to this"

    result = orchestrator.run(user_text, audio_path)

    # Attach pagination cursor for search actions
    action = result.get("intent", {}).get("action", "")
    if action in _SEARCH_ACTIONS and not result.get("error"):
        result["offset"] = 10  # next page starts at 10

    return result


def nlp_load_more(state: dict) -> dict:
    """Fetch the next page of 10 results using the stored query vector."""
    query_vec = state.get("query_vec")
    if query_vec is None:
        return state

    offset = state.get("offset", 10)
    more = db.search(np.array(query_vec, dtype=np.float32), limit=10, offset=offset)
    more = sorted(more, key=lambda x: x.get("score", 0), reverse=True)

    return {
        **state,
        "results": state.get("results", []) + more,
        "offset": offset + 10,
    }


# ---------------------------------------------------------------------------
# Graph view
# ---------------------------------------------------------------------------

def get_graph_data():
    payloads, vectors = db.get_all_points()
    if len(vectors) == 0:
        return pd.DataFrame()

    pca = PCA(n_components=2)
    components = pca.fit_transform(vectors)

    return pd.DataFrame({
        "x": components[:, 0],
        "y": components[:, 1],
        "filename": [p["filename"] for p in payloads],
        "filepath": [p["filepath"] for p in payloads],
    })


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="Audio Explorer") as demo:
    gr.Markdown("## Audio Explorer")

    with gr.Tabs():
        # ── NLP Pipeline Tab ──────────────────────────────────────────── #
        with gr.Tab("NLP Pipeline"):
            gr.Markdown(
                "Describe what you're looking for. Upload a reference track to run the full pipeline.\n\n"
                "*No audio: text search — e.g. \"punchy 808 sub bass\"*\n\n"
                "*With audio: full pipeline — e.g. \"find kicks like this\"*"
            )
            with gr.Row():
                nlp_text = gr.Textbox(
                    label="Prompt",
                    placeholder='e.g. "find me punchy kicks like this song"',
                    scale=3,
                )
                nlp_audio = gr.Audio(
                    label="Reference Audio (optional — triggers full pipeline)",
                    type="filepath",
                    sources=["upload", "microphone"],
                    scale=2,
                )
            nlp_btn = gr.Button("Run Pipeline", variant="primary")

            nlp_state = gr.State({})

            @gr.render(inputs=nlp_state)
            def render_nlp(state):
                if not state:
                    return

                intent = state.get("intent", {})
                error = state.get("error", "")
                action = intent.get("action", "")

                gr.Markdown(f"**Intent:** `{intent}`")

                if error:
                    gr.Markdown(f"**Error:** {error}")
                    return

                results = state.get("results", {})

                # ── Decompose: show stems, no pagination ─────────────────── #
                if action == "decompose":
                    gr.Markdown("### Decomposed Stems")
                    if isinstance(results, dict):
                        for label, filepath in results.items():
                            gr.Audio(
                                value=str(filepath),
                                label=label,
                                type="filepath",
                                interactive=False,
                            )
                    return

                # ── Search actions: show results + Load More ──────────────── #
                if action in _SEARCH_ACTIONS:
                    stem_used = state.get("stem_used", "")
                    if stem_used:
                        gr.Markdown(f"### Results (stem: `{Path(stem_used).name}`)")
                    else:
                        query_text = intent.get("query", "")
                        gr.Markdown(
                            f"### Results{f' for: *{query_text}*' if query_text else ''}"
                        )

                    if isinstance(results, list):
                        for i, res in enumerate(results):
                            score = res.get("score", 0)
                            filepath = res.get("filepath", "")
                            gr.Audio(
                                value=filepath,
                                label=f"#{i + 1}  similarity: {score:.4f}",
                                type="filepath",
                                interactive=False,
                            )

                    # Load More button — only when pagination cursor exists
                    if state.get("query_vec") is not None:
                        load_btn = gr.Button("Load 10 more results")
                        load_btn.click(
                            fn=nlp_load_more,
                            inputs=[nlp_state],
                            outputs=[nlp_state],
                        )

            nlp_btn.click(fn=nlp_query, inputs=[nlp_text, nlp_audio], outputs=nlp_state)

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
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        allowed_paths=[str(SAMPLES_DIR)],
    )
