# Context: BackendExecution — Async, Threading & Runtime

> **Scope:** Bugs and failure patterns involving async execution, thread-pool usage, background tasks, streaming I/O, and compatibility between sync and async code paths in `api/main.py` and `auth/main.py`.

---

## Failure Patterns & Remediation

### 2. `subprocess.run()` blocks asyncio event loop in async FastAPI routes *(Found: 2026-03-02)*

*   **Symptom:** Under concurrent load, `POST /api/convert` (and other ffmpeg-heavy routes) causes all in-flight async requests to stall for the duration of the transcode. No error is raised; latency spikes correlate with audio file size and are especially visible with FLAC/WAV outputs.
*   **Root Cause:** `subprocess.run(cmd, capture_output=True, check=True)` is a **synchronous blocking call** inside an `async def` FastAPI route. It occupies the event loop thread, preventing asyncio from dispatching any other coroutines until ffmpeg exits.
*   **Remediation Plan:**
    1.  Replace with the async equivalent:
        ```python
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=stderr)
        ```
    2.  The existing `except subprocess.CalledProcessError` handler continues to work — same exception type, same `.stderr` payload.
    3.  **Pyre2 false positives:** The static analyser flags `asyncio.subprocess` as `missing-attribute` and `proc.returncode` as `int | None`. Both are **incorrect** — `asyncio.subprocess` is a valid sub-module in Python 3.10+ and `returncode` is `int` after `communicate()` completes. Suppress or ignore these.
    4.  Other `subprocess.run()` calls in `api/main.py` (approx. lines 311, 782, 842, 857, 1176, 1301, 1320) are also inside async routes and should be migrated with the same pattern when those routes are next touched.

### 1. `UploadFile` async iteration incompatible with Starlette TestClient *(Found: 2026-03-01)*

*   **Symptom:** `'async for' requires an object with __aiter__ method, got UploadFile` — HTTP 500 on every processing endpoint (`/api/tag`, `/api/separate`, `/api/cut`, `/api/join`, `/api/karaoke`, `/api/convert`, `/api/analyze`) when exercised via pytest `TestClient`. The endpoint works correctly under real Uvicorn/ASGI.
*   **Root Cause:** `_save_upload()` (`api/main.py`) used the async-iteration protocol (`async for chunk in upload`) which was added to Starlette's `UploadFile` in v0.20.0. The installed Starlette version presents a `UploadFile` object without `__aiter__` when requests are made through the synchronous `TestClient` (which wraps `requests`, not `httpx`). The bug was latent: it was introduced when bug #1 (upload size limits) added `_save_upload`, and only surfaced when new tests with `TestClient` were added.
*   **Remediation Plan:**
    1.  Replace `async for chunk in upload:` with a `while True: chunk = await upload.read(65536); if not chunk: break` loop.
    2.  `UploadFile.read(n)` has been available since Starlette's initial release and works identically in both ASGI and TestClient contexts.
    3.  This pattern preserves the streaming/chunked behaviour and the 250 MB size check; no functional difference in production.
    4.  Verify with: `python -m pytest tests/test_api_e2e.py::test_synctag_pipeline -v` — should pass without the 500.
