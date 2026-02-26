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
# Graph view helpers
# ---------------------------------------------------------------------------

def get_graph_data(selected: list | None = None) -> pd.DataFrame:
    payloads, vectors, ids = db.get_all_points()
    if vectors.shape[0] == 0:
        return pd.DataFrame()

    pca = PCA(n_components=2)
    components = pca.fit_transform(vectors)

    n = len(payloads)
    sel_set = set(selected or [])
    return pd.DataFrame({
        "x": components[:, 0],
        "y": components[:, 1],
        "filename": [p["filename"] for p in payloads],
        "filepath": [p["filepath"] for p in payloads],
        "point_id": ids,
        "selected": ["selected" if i in sel_set else "unselected" for i in range(n)],
    })


def _apply_selection(df: pd.DataFrame, selected: list) -> pd.DataFrame:
    """Return a copy of df with the 'selected' column updated."""
    if df.empty:
        return df
    updated = df.copy()
    sel_set = set(selected)
    updated["selected"] = ["selected" if i in sel_set else "unselected" for i in range(len(df))]
    return updated


def _selected_label(indices: list, df: pd.DataFrame) -> str:
    if not indices or df.empty:
        return "None selected"
    names = [df.iloc[i]["filename"] for i in indices if i < len(df)]
    return f"{len(names)} selected: {', '.join(names)}"


def _resolve_plot_idx(evt: gr.SelectData, df: pd.DataFrame):
    """
    Robustly resolve a ScatterPlot click to a DataFrame row index.

    Gradio 6's ScatterPlot passes click data through Vega-Lite which can
    return evt.index as an int, list, dict, or None depending on the version.
    We try four strategies in order:
      1. evt.index as a plain integer
      2. evt.value dict → match by 'filename'
      3. evt.index as a list/tuple → first element
      4. evt.index as a dict → match by 'filename'
    """
    if df.empty:
        return None

    raw = evt.index
    val = getattr(evt, "value", None)

    # Strategy 1: plain integer index
    if isinstance(raw, int) and raw < len(df):
        return raw

    # Strategy 2: evt.value is the row dict — most reliable in Gradio 6
    if isinstance(val, dict):
        fname = val.get("filename")
        if fname:
            hits = df.index[df["filename"] == fname].tolist()
            if hits:
                return hits[0]

    # Strategy 3: index is a list/tuple
    if isinstance(raw, (list, tuple)) and raw:
        first = raw[0]
        if isinstance(first, int) and first < len(df):
            return first
        if isinstance(first, dict):
            fname = first.get("filename")
            if fname:
                hits = df.index[df["filename"] == fname].tolist()
                if hits:
                    return hits[0]

    # Strategy 4: index is a dict
    if isinstance(raw, dict):
        fname = raw.get("filename")
        if fname:
            hits = df.index[df["filename"] == fname].tolist()
            if hits:
                return hits[0]

    return None


def on_plot_click(evt: gr.SelectData, df: pd.DataFrame, selected: list) -> tuple:
    """Play audio, toggle point selection, and re-render plot with highlight."""
    if df.empty:
        return None, "No data — click 'Load / Refresh Graph' first", selected, "None selected", df, df

    idx = _resolve_plot_idx(evt, df)
    if idx is None:
        dbg = f"Unresolved click — index={evt.index!r}  value={getattr(evt, 'value', None)!r}"
        return None, dbg, selected, _selected_label(selected, df), df, df

    selected = [i for i in selected if i != idx] if idx in selected else selected + [idx]
    updated_df = _apply_selection(df, selected)
    row = df.iloc[idx]
    return (
        row["filepath"],
        row["filename"],
        selected,
        _selected_label(selected, df),
        updated_df,
        updated_df,
    )


def add_samples(files, df: pd.DataFrame) -> tuple[pd.DataFrame, list, str, str]:
    """Embed uploaded files, upsert to Qdrant, return refreshed graph + status."""
    if not files:
        return df, [], _selected_label([], df), "No files provided."

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    added, errors = [], []

    for f in files:
        # Gradio 6 returns FileData with .path; older versions return plain strings
        tmp_path = Path(f.path if hasattr(f, "path") else str(f))
        orig_name = getattr(f, "orig_name", None) or tmp_path.name
        dest = SAMPLES_DIR / orig_name

        import shutil
        shutil.copy2(str(tmp_path), str(dest))

        try:
            waveform = embedder.load_audio(str(dest))
            vec = embedder.embed(waveform)
            db.upsert(vec, str(dest))
            added.append(orig_name)
        except Exception as e:
            errors.append(f"{orig_name}: {e}")

    new_df = get_graph_data()
    parts = []
    if added:
        parts.append(f"Added {len(added)}: {', '.join(added)}")
    if errors:
        parts.append(f"Errors: {'; '.join(errors)}")
    return new_df, [], "None selected", "\n".join(parts) or "Done.", new_df


def remove_selected(selected: list, df: pd.DataFrame) -> tuple:
    """Delete the currently selected points from Qdrant and refresh the graph."""
    if not selected or df.empty:
        return df, [], "None selected", "Nothing selected.", df

    ids_to_delete = [df.iloc[i]["point_id"] for i in selected if i < len(df)]
    db.delete(ids_to_delete)
    new_df = get_graph_data()
    return new_df, [], "None selected", f"Removed {len(ids_to_delete)} sample(s).", new_df


def clear_all_samples() -> tuple:
    """Wipe the entire Qdrant collection and return an empty graph."""
    db.clear()
    empty = pd.DataFrame()
    return empty, [], "None selected", "All samples cleared.", empty


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
            gr.Markdown(
                "Click nodes to select them (click again to deselect). "
                "Use the panel on the right to add or remove samples live."
            )

            df_state = gr.State(pd.DataFrame())
            selected_state = gr.State([])

            with gr.Row():
                # ── Left: plot + player ──────────────────────────────────── #
                with gr.Column(scale=3):
                    audio_plot = gr.ScatterPlot(
                        x="x",
                        y="y",
                        color="selected",
                        tooltip=["filename"],
                        title="Audio Embeddings (PCA)",
                    )
                    player = gr.Audio(label="Now playing", type="filepath", interactive=False)
                    debug_txt = gr.Textbox(label="Last clicked", interactive=False, lines=1)

                # ── Right: controls ──────────────────────────────────────── #
                with gr.Column(scale=1, min_width=260):
                    graph_btn = gr.Button("Load / Refresh Graph", variant="secondary")
                    graph_status = gr.Textbox(label="Status", interactive=False, lines=2)

                    gr.Markdown("---")
                    gr.Markdown("### Add samples")
                    upload_files = gr.File(
                        label="Audio files",
                        file_count="multiple",
                        file_types=["audio", ".wav", ".mp3", ".flac", ".aiff", ".ogg", ".m4a"],
                    )
                    add_btn = gr.Button("Add to Library", variant="primary")

                    gr.Markdown("---")
                    gr.Markdown("### Remove samples")
                    selected_lbl = gr.Textbox(
                        label="Selected",
                        value="None selected",
                        interactive=False,
                        lines=2,
                    )
                    with gr.Row():
                        remove_btn = gr.Button("Remove Selected", variant="stop")
                        clear_btn = gr.Button("Clear All", variant="stop")

            # ── Event wiring ─────────────────────────────────────────────── #

            # Refresh graph — clear selection on refresh
            graph_btn.click(
                fn=get_graph_data, inputs=[], outputs=[df_state]
            ).then(
                fn=lambda df: ([], "None selected", df),
                inputs=[df_state],
                outputs=[selected_state, selected_lbl, audio_plot],
            )

            # Click node → play + toggle selection + re-render with highlight
            audio_plot.select(
                fn=on_plot_click,
                inputs=[df_state, selected_state],
                outputs=[player, debug_txt, selected_state, selected_lbl, df_state, audio_plot],
            )

            # Add uploaded files → embed → upsert → refresh
            add_btn.click(
                fn=add_samples,
                inputs=[upload_files, df_state],
                outputs=[df_state, selected_state, selected_lbl, graph_status, audio_plot],
            )

            # Remove selected points
            remove_btn.click(
                fn=remove_selected,
                inputs=[selected_state, df_state],
                outputs=[df_state, selected_state, selected_lbl, graph_status, audio_plot],
            )

            # Clear entire library
            clear_btn.click(
                fn=clear_all_samples,
                inputs=[],
                outputs=[df_state, selected_state, selected_lbl, graph_status, audio_plot],
            )

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        allowed_paths=[str(SAMPLES_DIR)],
    )
