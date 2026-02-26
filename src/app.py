"""
Unified Pipeline — Gradio Web UI

Two tabs:
  1. SyncTag AI      — auto-tag audio for sync licensing
  2. Stem Separator  — three-stage source separation (Demucs → LARS → one-shots)

Run:
    python src/app.py
"""

import sys
import shutil
import tempfile
import zipfile
from pathlib import Path

# Fix: Add project root to sys.path so `src` module is resolvable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gradio as gr
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Lazy-init tagger — loads M2D-CLAP checkpoint once on first request
# ---------------------------------------------------------------------------
_tagger = None


def _get_tagger():
    global _tagger
    if _tagger is None:
        from src.synctag import SyncTagger
        _tagger = SyncTagger()
    return _tagger


# =========================================================================== #
# Tab 1 — SyncTag AI
# =========================================================================== #

def tag_track(audio_file: str, isrc: str) -> tuple:
    """
    Gradio handler: run the full SyncTag pipeline and return outputs.
    """
    if not audio_file:
        return None, None, "**Error:** No audio file provided."

    try:
        tagger = _get_tagger()

        # Copy uploaded file to a clean temp dir, preserving the original name
        tmp_dir = Path(tempfile.mkdtemp(prefix="synctag_ui_"))
        original_name = Path(audio_file).name
        audio_copy = tmp_dir / original_name
        shutil.copy2(audio_file, audio_copy)

        # Run pipeline
        result = tagger.run(audio_copy)

        # Inject ISRC if provided
        isrc = (isrc or "").strip()
        if isrc:
            result.setdefault("llm", {}).setdefault("metadata", {})["ISRC"] = isrc

        # Export: ID3 tags embedded in-place on the copy
        from src.export import write_csv_sidecar, write_id3_tags
        try:
            write_id3_tags(audio_copy, result)
        except Exception as exc:
            print(f"[app] ID3 warning: {exc}")

        # Export: CSV sidecar alongside the audio copy
        csv_path = tmp_dir / f"{audio_copy.stem}.csv"
        write_csv_sidecar(result, csv_path)

        # Build markdown summary
        summary = _build_summary(result)

        return str(audio_copy), str(csv_path), summary

    except Exception as exc:
        return None, None, f"**Error:** {exc}"


def _build_summary(result: dict) -> str:
    """Render a markdown summary card from pipeline results."""
    llm = result.get("llm", {})
    meta = llm.get("metadata", {})
    tags = result.get("tags", {})
    audio_info = result.get("audio_info", {})

    pitch = llm.get("pitch", "")
    comments = meta.get("Comments", "")

    genre = meta.get("Genre") or (tags.get("genre") or [""])[0]
    mood_raw = meta.get("Mood") or tags.get("mood") or []
    mood = "; ".join(mood_raw) if isinstance(mood_raw, list) else str(mood_raw)
    tempo = meta.get("Tempo") or tags.get("tempo", "")
    energy = meta.get("Energy", "")
    vocal = meta.get("Vocal", "")
    track_type = meta.get("Track_Type", "")
    isrc_val = meta.get("ISRC", "")
    duration = audio_info.get("duration", "")

    lines = []
    if pitch:
        lines.append(f"## Pitch\n{pitch}\n")

    lines.append("## Metadata")
    lines.append(f"- **Genre:** {genre}")
    lines.append(f"- **Mood:** {mood}")
    lines.append(f"- **Tempo:** {tempo}")
    lines.append(f"- **Energy:** {energy}/10")
    lines.append(f"- **Vocal:** {vocal}")
    lines.append(f"- **Track Type:** {track_type}")
    if duration:
        lines.append(f"- **Duration:** {duration:.1f}s" if isinstance(duration, float) else f"- **Duration:** {duration}s")
    if isrc_val:
        lines.append(f"- **ISRC:** {isrc_val}")

    if comments:
        lines.append(f"\n## Sync Notes\n{comments}")

    return "\n".join(lines)


# =========================================================================== #
# Tab 2 — Stem Separator
# =========================================================================== #

def separate_audio(audio_file: str, progress=gr.Progress()):
    """
    Run the full three-stage separation pipeline and return state dict
    with all stem paths for dynamic rendering.
    """
    if not audio_file:
        return {}, "**Error:** No audio file provided."

    try:
        import torch
        from src.advanced_separate import run_pipeline

        device = "cuda" if torch.cuda.is_available() else "cpu"
        input_path = Path(audio_file)
        output_dir = Path(tempfile.mkdtemp(prefix="stems_")) / input_path.stem

        progress(0.1, desc="Starting Demucs separation…")
        all_files = run_pipeline(input_path, output_dir, device=device)

        # Convert Path values to strings for Gradio
        stem_dict = {}
        for label, path in sorted(all_files.items()):
            p = Path(path)
            if p.exists():
                stem_dict[label] = str(p)

        return stem_dict, ""

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {}, f"**Error:** {exc}"


def _group_stems(stem_dict: dict) -> dict:
    """Group stems into categories for display."""
    groups = {
        "🎵 Main Stems": [],
        "🥁 Drum Kit": [],
        "🔊 One-Shots": [],
    }
    for label, path in stem_dict.items():
        if label.startswith("oneshot_"):
            display = label.replace("oneshot_", "").replace("_", " ").title() + " (One-Shot)"
            groups["🔊 One-Shots"].append((display, path))
        elif label.startswith("drums_"):
            display = label.replace("drums_", "").replace("_", " ").title()
            groups["🥁 Drum Kit"].append((display, path))
        else:
            display = label.replace("_", " ").title()
            groups["🎵 Main Stems"].append((display, path))
    return {k: v for k, v in groups.items() if v}


def create_zip(stem_dict: dict) -> str | None:
    """Bundle all stems into a single ZIP for download."""
    if not stem_dict:
        return None
    zip_dir = Path(tempfile.mkdtemp(prefix="stems_zip_"))
    zip_path = zip_dir / "all_stems.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for label, filepath in stem_dict.items():
            p = Path(filepath)
            if p.exists():
                zf.write(p, arcname=p.name)
    return str(zip_path)


# =========================================================================== #
# Gradio UI
# =========================================================================== #

with gr.Blocks(
    title="Audio Pipeline",
    theme=gr.themes.Soft(
        primary_hue="violet",
        secondary_hue="slate",
        neutral_hue="slate",
    ),
    css="""
    .main-title { text-align: center; margin-bottom: 0.2em; }
    .subtitle { text-align: center; color: #8b8b9e; margin-bottom: 1.5em; font-size: 1.05em; }
    .stem-group-title { font-size: 1.15em; font-weight: 600; margin-top: 1em; }
    """,
) as demo:
    gr.Markdown("# 🎛️ Audio Pipeline", elem_classes=["main-title"])
    gr.Markdown("SyncTag AI  •  Stem Separator", elem_classes=["subtitle"])

    with gr.Tabs():
        # ── Tab 1: SyncTag AI ─────────────────────────────────────────── #
        with gr.Tab("🏷️ SyncTag AI", id="synctag"):
            gr.Markdown("Upload audio to auto-tag for sync licensing. Get a tagged copy, CSV sidecar, and metadata summary.")

            with gr.Row():
                with gr.Column(scale=1):
                    st_audio_input = gr.Audio(
                        type="filepath",
                        label="Audio File",
                    )
                    st_isrc_input = gr.Textbox(
                        label="ISRC (optional)",
                        placeholder="e.g. GB-ABC-25-00001",
                        max_lines=1,
                    )
                    st_run_btn = gr.Button("🚀 Tag Track", variant="primary", size="lg")

                with gr.Column(scale=2):
                    st_summary_output = gr.Markdown(label="Summary")
                    with gr.Row():
                        st_audio_output = gr.File(label="Download Tagged Audio")
                        st_csv_output = gr.File(label="Download CSV Sidecar")

            st_run_btn.click(
                fn=tag_track,
                inputs=[st_audio_input, st_isrc_input],
                outputs=[st_audio_output, st_csv_output, st_summary_output],
            )

        # ── Tab 2: Stem Separator ─────────────────────────────────────── #
        with gr.Tab("🎚️ Stem Separator", id="separator"):
            gr.Markdown(
                "Upload a full track to separate into stems. "
                "**Demucs** extracts vocals, drums, bass, & more → "
                "**LARS** splits drums into kick, snare, toms, hihat, & cymbals → "
                "**Gate+Slice** produces one-shot samples from each drum component."
            )

            with gr.Row():
                sep_audio_input = gr.Audio(
                    type="filepath",
                    label="Audio File",
                )
                sep_run_btn = gr.Button("🔀 Separate Stems", variant="primary", size="lg")

            sep_error = gr.Markdown(visible=False)
            sep_state = gr.State({})

            # Dynamic results area
            @gr.render(inputs=sep_state)
            def render_stems(stem_dict):
                if not stem_dict:
                    return

                groups = _group_stems(stem_dict)

                for group_name, stems in groups.items():
                    gr.Markdown(f"### {group_name}", elem_classes=["stem-group-title"])
                    for display_name, filepath in stems:
                        with gr.Row():
                            gr.Audio(
                                value=filepath,
                                label=display_name,
                                type="filepath",
                                interactive=False,
                            )

                # Download all button
                gr.Markdown("---")
                dl_btn = gr.Button("📦 Download All Stems (ZIP)", variant="secondary")
                dl_file = gr.File(label="All Stems ZIP")
                dl_btn.click(
                    fn=lambda: create_zip(stem_dict),
                    inputs=[],
                    outputs=[dl_file],
                )

            def run_separator(audio_file):
                stem_dict, error = separate_audio(audio_file)
                if error:
                    return stem_dict, gr.update(value=error, visible=True)
                return stem_dict, gr.update(value="", visible=False)

            sep_run_btn.click(
                fn=run_separator,
                inputs=[sep_audio_input],
                outputs=[sep_state, sep_error],
            )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
