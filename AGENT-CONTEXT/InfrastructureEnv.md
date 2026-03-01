# Context: InfrastructureEnv — Build, Docker, Env Vars, Networking

> **Scope:** Bugs and failure patterns involving Docker image builds, container lifecycle, environment variable loading, service networking, and deployment mismatches.

---

## Failure Patterns & Remediation

### 1. Docker container serves stale `auth/main.py` after on-disk edits *(Found: 2026-03-01)*

*   **Symptom:** Stripe webhook handler returns HTTP 200 but produces no log output and no DB update. New `elif` branches added to `auth/main.py` are silently unreachable. The running container behaves as if the code change never happened.
*   **Root Cause:** `auth/main.py` is baked into the image at build time via `COPY main.py .` in `auth/Dockerfile`. Editing the source file on disk does **not** hot-reload or update the running container — the process executing inside the container still holds the old bytecode. This pattern affects any service whose `Dockerfile` uses `COPY` rather than a bind-mount volume.
*   **Remediation Plan:**
    1.  Rebuild the service image: `docker compose build auth`
    2.  Restart the container with the new image: `docker compose up -d auth`
    3.  Confirm the new code is live: `docker logs syntag_auth | tail -20` — look for expected log lines from the new handler.
    4.  **Diagnostic signal**: if a webhook/route returns 200 but the expected `log.info`/`log.warning` line from the new handler is absent in `docker logs`, stale image is the first thing to rule out.
    5.  For inner-loop development, consider adding a bind-mount (`volumes: - ./auth/main.py:/app/main.py`) in `docker-compose.override.yml` and restarting with `--reload` flag in the Uvicorn command.
