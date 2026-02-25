"""
SyncTag AI — Gradio Web UI

Upload an audio file (WAV / MP3 / FLAC / …), optionally supply an ISRC,
and get back:
  • a tagged audio copy (ID3 metadata embedded)
  • a CSV sidecar ready for DISCO / sync-CMS import
  • a markdown summary in the browser

Run:
    ~/.pyenv/versions/3.10.13/bin/python src/app.py
"""

import sys
import shutil
import tempfile
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


# ---------------------------------------------------------------------------
# Pipeline handler
# ---------------------------------------------------------------------------

def tag_track(audio_file: str, isrc: str) -> tuple:
    """
    Gradio handler: run the full SyncTag pipeline and return outputs.

    Parameters
    ----------
    audio_file : str
        Filepath provided by gr.Audio(type="filepath")
    isrc : str
        Optional ISRC code (may be empty string)

    Returns
    -------
    (tagged_audio_path | None, csv_path | None, markdown_summary: str)
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


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="SyncTag AI") as demo:
    gr.Markdown("# SyncTag AI\nAuto-tag audio for sync licensing")

    with gr.Row():
        # Left column — inputs
        with gr.Column(scale=1):
            audio_input = gr.Audio(
                type="filepath",
                label="Audio File",
            )
            isrc_input = gr.Textbox(
                label="ISRC (optional)",
                placeholder="e.g. GB-ABC-25-00001",
                max_lines=1,
            )
            run_btn = gr.Button("Tag Track", variant="primary")

        # Right column — outputs
        with gr.Column(scale=2):
            summary_output = gr.Markdown(label="Summary")

            with gr.Row():
                audio_output = gr.File(label="Download Tagged Audio")
                csv_output = gr.File(label="Download CSV Sidecar")

    run_btn.click(
        fn=tag_track,
        inputs=[audio_input, isrc_input],
        outputs=[audio_output, csv_output, summary_output],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
