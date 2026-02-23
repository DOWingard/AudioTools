"""
NLP Orchestrator — Routes natural-language queries through the audio pipeline.

Reads ``pipeline_brain.md`` as a system prompt for the Gemini LLM, parses the
user's text + optional audio into a deterministic JSON intent, and executes
the matching pipeline action (decompose, search_stem, search_clip, text_search).
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from src.embedder import AudioEmbedder
from src.database import AudioDatabase
from src.separate import run_pipeline

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_BRAIN_PATH = _HERE / "pipeline_brain.md"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def _load_brain() -> str:
    """Load the brainfile system prompt."""
    return _BRAIN_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# LLM Intent Parser
# ---------------------------------------------------------------------------

def _parse_intent(user_text: str, has_audio: bool) -> dict:
    """Call Gemini with the brainfile system prompt and return a JSON intent."""
    try:
        from google import genai
    except ImportError:
        raise ImportError(
            "google-genai is required. Install with: pip install google-genai"
        )

    client = genai.Client(api_key=GEMINI_API_KEY)

    brain = _load_brain()
    user_msg = f"User prompt: \"{user_text}\"\nAudio uploaded: {'yes' if has_audio else 'no'}"

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            {"role": "user", "parts": [{"text": user_msg}]},
        ],
        config={
            "system_instruction": brain,
            "temperature": 0.0,
        },
    )

    raw = response.text.strip()

    # Strip markdown fences if the model wraps them
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        raw = "\n".join(lines).strip()

    try:
        intent = json.loads(raw)
    except json.JSONDecodeError:
        intent = {"action": "error", "message": f"LLM returned non-JSON: {raw[:200]}"}

    return intent


# ---------------------------------------------------------------------------
# Pipeline Executor
# ---------------------------------------------------------------------------

class Orchestrator:
    """Bridges NLP intent to pipeline execution."""

    def __init__(
        self,
        embedder: Optional[AudioEmbedder] = None,
        db: Optional[AudioDatabase] = None,
    ):
        self.embedder = embedder or AudioEmbedder()
        self.db = db or AudioDatabase(host=os.environ.get("QDRANT_HOST", "localhost"))

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def run(
        self,
        user_text: str,
        audio_path: Optional[str] = None,
        search_limit: int = 10,
    ) -> dict:
        """
        Execute the full NLP → Pipeline flow.

        Returns a dict with at least:
          - ``intent``: the parsed JSON intent from the LLM
          - ``results``: action-specific outputs (file paths, search hits, etc.)
        """
        has_audio = audio_path is not None and Path(audio_path).is_file()
        intent = _parse_intent(user_text, has_audio)
        action = intent.get("action", "error")

        if action == "error":
            return {"intent": intent, "results": [], "error": intent.get("message", "")}

        if action == "decompose":
            return self._do_decompose(intent, audio_path)

        if action == "search_stem":
            return self._do_search_stem(intent, audio_path, search_limit)

        if action == "search_clip":
            return self._do_search_clip(audio_path, search_limit, intent)

        if action == "text_search":
            return self._do_text_search(intent, search_limit)

        return {"intent": intent, "results": [], "error": f"Unknown action: {action}"}

    # ------------------------------------------------------------------ #
    # Action handlers
    # ------------------------------------------------------------------ #

    def _do_decompose(self, intent: dict, audio_path: Optional[str]) -> dict:
        if not audio_path:
            return {"intent": intent, "results": [], "error": "No audio file provided."}

        out_dir = Path(tempfile.mkdtemp(prefix="decompose_"))
        device = "cuda" if __import__("torch").cuda.is_available() else "cpu"
        all_files = run_pipeline(audio_path, out_dir, device=device)

        target = intent.get("target", "all")
        if target != "all" and target in all_files:
            all_files = {target: all_files[target]}

        paths = {k: str(v) for k, v in all_files.items()}
        return {"intent": intent, "results": paths}

    def _do_search_stem(
        self, intent: dict, audio_path: Optional[str], limit: int
    ) -> dict:
        if not audio_path:
            return {"intent": intent, "results": [], "error": "No audio file provided."}

        # Decompose first
        out_dir = Path(tempfile.mkdtemp(prefix="search_stem_"))
        device = "cuda" if __import__("torch").cuda.is_available() else "cpu"
        all_files = run_pipeline(audio_path, out_dir, device=device)

        target = intent.get("target", "kick")

        # Try oneshot first, then full stem, then drums_ prefix
        stem_path = None
        for key in [f"oneshot_{target}", target, f"drums_{target}"]:
            if key in all_files:
                stem_path = all_files[key]
                break

        if stem_path is None:
            return {
                "intent": intent,
                "results": [],
                "error": f"Stem '{target}' not found in pipeline output. Available: {list(all_files.keys())}",
            }

        # Embed the extracted stem and search
        waveform = self.embedder.load_audio(str(stem_path))
        query_vec = self.embedder.embed(waveform)
        results = self.db.search(query_vec, limit=limit)
        results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

        return {
            "intent": intent,
            "stem_used": str(stem_path),
            "results": results,
        }

    def _do_search_clip(
        self, audio_path: Optional[str], limit: int, intent: dict
    ) -> dict:
        if not audio_path:
            return {"intent": intent, "results": [], "error": "No audio file provided."}

        waveform = self.embedder.load_audio(audio_path)
        query_vec = self.embedder.embed(waveform)
        results = self.db.search(query_vec, limit=limit)
        results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

        return {"intent": intent, "results": results}

    def _do_text_search(self, intent: dict, limit: int) -> dict:
        query_text = intent.get("query", "")
        if not query_text:
            return {"intent": intent, "results": [], "error": "Empty text query."}

        query_vec = self.embedder.encode_text(query_text)
        results = self.db.search(query_vec, limit=limit)
        results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

        return {"intent": intent, "results": results}
