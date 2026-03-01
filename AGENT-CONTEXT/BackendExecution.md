# Context: BackendExecution — Async, Threading & Runtime

> **Scope:** Bugs and failure patterns involving async execution, thread-pool usage, background tasks, streaming I/O, and compatibility between sync and async code paths in `api/main.py` and `auth/main.py`.

---

## Failure Patterns & Remediation

### 1. `UploadFile` async iteration incompatible with Starlette TestClient *(Found: 2026-03-01)*

*   **Symptom:** `'async for' requires an object with __aiter__ method, got UploadFile` — HTTP 500 on every processing endpoint (`/api/tag`, `/api/separate`, `/api/cut`, `/api/join`, `/api/karaoke`, `/api/convert`, `/api/analyze`) when exercised via pytest `TestClient`. The endpoint works correctly under real Uvicorn/ASGI.
*   **Root Cause:** `_save_upload()` (`api/main.py`) used the async-iteration protocol (`async for chunk in upload`) which was added to Starlette's `UploadFile` in v0.20.0. The installed Starlette version presents a `UploadFile` object without `__aiter__` when requests are made through the synchronous `TestClient` (which wraps `requests`, not `httpx`). The bug was latent: it was introduced when bug #1 (upload size limits) added `_save_upload`, and only surfaced when new tests with `TestClient` were added.
*   **Remediation Plan:**
    1.  Replace `async for chunk in upload:` with a `while True: chunk = await upload.read(65536); if not chunk: break` loop.
    2.  `UploadFile.read(n)` has been available since Starlette's initial release and works identically in both ASGI and TestClient contexts.
    3.  This pattern preserves the streaming/chunked behaviour and the 250 MB size check; no functional difference in production.
    4.  Verify with: `python -m pytest tests/test_api_e2e.py::test_synctag_pipeline -v` — should pass without the 500.
