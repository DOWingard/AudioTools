"""
Unit tests for auth/main.py — issue #7 (GDPR: user.deleted purge call).

Covers the three behaviours of the user.deleted Clerk webhook handler:
  1. Happy path: DB row deleted AND purge DELETE sent to compute API.
  2. Skip: purge call must be omitted when INTERNAL_SECRET is empty.
  3. Resilience: purge call failure must not prevent a 200 response.

These tests run in-process via FastAPI's TestClient with all external
dependencies (asyncpg, scheduler, svix signature verification, httpx) mocked.
"""

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# auth/main.py reads DATABASE_URL at module import time via os.environ["DATABASE_URL"].
# Set a sentinel value before any import triggers that line.
# load_dotenv() (called in auth/main.py) will NOT override this because python-dotenv
# respects already-set environment variables by default.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/testdb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock():
    """Return (mock_pool, mock_conn) where pool.acquire() works as async CM."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)

    mock_acq = MagicMock()
    mock_acq.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acq.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_acq)
    mock_pool.close = AsyncMock()

    return mock_pool, mock_conn


def _make_httpx_mock():
    """Return an httpx.AsyncClient mock (async CM) with a .delete() coroutine."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    mock_client = AsyncMock()
    mock_client.delete = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _post_clerk_webhook(client, event_type: str, data: dict):
    """POST a signed-looking Clerk webhook; svix.verify is patched away."""
    body = json.dumps({"type": event_type, "data": data}).encode()
    headers = {
        "content-type": "application/json",
        "svix-id": "msg_unit_test_001",
        "svix-timestamp": "1700000000",
        "svix-signature": "v1,fakesig",
    }
    return client.post("/auth/webhooks/clerk", content=body, headers=headers)


# ---------------------------------------------------------------------------
# Fixture: in-process TestClient with all external deps mocked
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def auth_client():
    """
    Spin up the auth FastAPI app with:
      - asyncpg.create_pool replaced by an AsyncMock → avoids real DB connection
      - scheduler replaced by a MagicMock → avoids APScheduler thread startup
    """
    from fastapi.testclient import TestClient

    mock_pool, mock_conn = _make_db_mock()

    with patch("asyncpg.create_pool", AsyncMock(return_value=mock_pool)), \
         patch("auth.main._refresh_jwks", AsyncMock(return_value={"keys": []})), \
         patch("auth.main.scheduler") as mock_sched:

        mock_sched.add_job = MagicMock()
        mock_sched.start = MagicMock()
        mock_sched.shutdown = MagicMock()

        from auth.main import app
        with TestClient(app) as c:
            yield c, mock_conn


# ---------------------------------------------------------------------------
# Issue #7 — user.deleted webhook must purge Qdrant + S3 via compute API
# ---------------------------------------------------------------------------

def test_user_deleted_purge_call_made(auth_client):
    """
    Happy path: after deleting the Postgres row the handler must fire a
    DELETE /internal/users/{clerk_id} request with the correct secret header.
    """
    client, mock_conn = auth_client
    clerk_id = "user_gdpr_purge_abc"
    mock_conn.reset_mock()

    mock_http = _make_httpx_mock()

    with patch("svix.webhooks.Webhook.verify",
               return_value={"type": "user.deleted", "data": {"id": clerk_id}}), \
         patch("auth.main.INTERNAL_SECRET", "test-purge-secret"), \
         patch("auth.main.COMPUTE_API_URL", "http://fake-compute:8000"), \
         patch("httpx.AsyncClient", MagicMock(return_value=mock_http)):

        resp = _post_clerk_webhook(client, "user.deleted", {"id": clerk_id})

    assert resp.status_code == 200

    # DB row must be deleted
    execute_calls = [str(c) for c in mock_conn.execute.call_args_list]
    assert any("DELETE FROM users" in s for s in execute_calls), (
        f"Expected DELETE FROM users call; actual calls: {execute_calls}"
    )

    # Purge HTTP call must be made with the right URL and secret
    mock_http.delete.assert_called_once()
    url_called = mock_http.delete.call_args.args[0]
    assert url_called == f"http://fake-compute:8000/internal/users/{clerk_id}", (
        f"Unexpected purge URL: {url_called}"
    )
    headers_sent = mock_http.delete.call_args.kwargs.get("headers", {})
    assert headers_sent.get("x-internal-secret") == "test-purge-secret", (
        f"Wrong or missing internal secret in headers: {headers_sent}"
    )


def test_user_deleted_no_purge_when_secret_empty(auth_client):
    """
    When INTERNAL_SECRET is empty the purge HTTP call must be skipped entirely.
    This prevents an unauthenticated call to the compute API.
    """
    client, mock_conn = auth_client
    clerk_id = "user_gdpr_nopurge"
    mock_conn.reset_mock()

    mock_http = _make_httpx_mock()

    with patch("svix.webhooks.Webhook.verify",
               return_value={"type": "user.deleted", "data": {"id": clerk_id}}), \
         patch("auth.main.INTERNAL_SECRET", ""), \
         patch("httpx.AsyncClient", MagicMock(return_value=mock_http)):

        resp = _post_clerk_webhook(client, "user.deleted", {"id": clerk_id})

    assert resp.status_code == 200
    mock_http.delete.assert_not_called()


def test_user_deleted_purge_error_still_returns_200(auth_client):
    """
    If the purge HTTP call raises an exception (network error, timeout, etc.)
    the webhook handler must still return 200. The error should be logged but
    must not propagate to the Clerk webhook caller.
    """
    client, mock_conn = auth_client
    clerk_id = "user_gdpr_failpurge"
    mock_conn.reset_mock()

    mock_http = _make_httpx_mock()
    mock_http.delete = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("svix.webhooks.Webhook.verify",
               return_value={"type": "user.deleted", "data": {"id": clerk_id}}), \
         patch("auth.main.INTERNAL_SECRET", "test-purge-secret"), \
         patch("auth.main.COMPUTE_API_URL", "http://unreachable:9999"), \
         patch("httpx.AsyncClient", MagicMock(return_value=mock_http)):

        resp = _post_clerk_webhook(client, "user.deleted", {"id": clerk_id})

    assert resp.status_code == 200, (
        "Webhook must return 200 even when the purge HTTP call fails"
    )
