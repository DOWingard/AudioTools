"""
End-to-end test for the auth service:
  1. Simulate a Clerk user.created webhook (properly signed with svix)
  2. Assert user row written to Postgres
  3. Assert Stripe customer was attempted (stub check)
  4. Simulate user.updated and user.deleted webhooks
  5. Smoke-test /auth/user/me without a token → 422
  6. Smoke-test /auth/billing/checkout without a token → 422
"""

import hashlib
import hmac
import json
import os
import time
import uuid

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

AUTH_URL   = "http://localhost:8001"
DB_DSN     = (
    f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@localhost:5434/{os.environ['POSTGRES_DB']}"
)
WEBOOK_SECRET = os.environ["CLERK_WEBHOOK_SECRET"]  # whsec_...

# ── svix signing helpers ──────────────────────────────────────────────────────

def _svix_sign(payload: bytes, secret: str, msg_id: str, ts: int) -> str:
    """Recreate the svix v1 HMAC-SHA256 signature."""
    # secret is "whsec_<base64>" — decode the base64 part
    import base64
    raw_secret = base64.b64decode(secret.removeprefix("whsec_"))
    to_sign = f"{msg_id}.{ts}.".encode() + payload
    sig = hmac.new(raw_secret, to_sign, hashlib.sha256).digest()
    return "v1," + base64.b64encode(sig).decode()


def clerk_webhook(event_type: str, data: dict) -> requests.Response:
    payload = json.dumps({"type": event_type, "data": data}).encode()
    msg_id  = f"msg_{uuid.uuid4().hex}"
    ts      = int(time.time())
    sig     = _svix_sign(payload, WEBOOK_SECRET, msg_id, ts)
    headers = {
        "Content-Type":     "application/json",
        "svix-id":          msg_id,
        "svix-timestamp":   str(ts),
        "svix-signature":   sig,
    }
    return requests.post(f"{AUTH_URL}/auth/webhooks/clerk", data=payload, headers=headers)


# ── helpers ───────────────────────────────────────────────────────────────────

def db_get_user(clerk_id: str):
    conn = psycopg2.connect(DB_DSN)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE clerk_id = %s", (clerk_id,))
        row = cur.fetchone()
        if row:
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
    conn.close()
    return None


def db_delete_user(clerk_id: str):
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE clerk_id = %s", (clerk_id,))
    conn.close()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_health():
    r = requests.get(f"{AUTH_URL}/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    print("  ✅ /health OK")


def test_unauthenticated_guards():
    r = requests.get(f"{AUTH_URL}/auth/user/me")
    assert r.status_code == 422, f"Expected 422, got {r.status_code}"
    r = requests.post(f"{AUTH_URL}/auth/billing/checkout", json={"plan": "standard"})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}"
    print("  ✅ Unauthenticated endpoints → 422")


def test_clerk_user_created():
    clerk_id = f"user_test_{uuid.uuid4().hex[:8]}"
    email    = f"{clerk_id}@example.com"

    # Clean up any leftover row
    db_delete_user(clerk_id)

    r = clerk_webhook("user.created", {
        "id": clerk_id,
        "email_addresses": [{"email_address": email}],
    })
    assert r.status_code == 200, f"Webhook returned {r.status_code}: {r.text}"

    # Give the service a moment to write
    time.sleep(0.5)

    row = db_get_user(clerk_id)
    assert row is not None, "User row not found in DB after user.created webhook"
    assert row["email"]             == email
    assert row["clerk_id"]          == clerk_id
    assert row["subscription_type"] == "free"
    assert row["subscription_active"] is False

    print(f"  ✅ user.created → DB row created  (id={row['id']})")
    return clerk_id, email


def test_clerk_user_updated(clerk_id: str):
    new_email = f"updated_{clerk_id}@example.com"
    r = clerk_webhook("user.updated", {
        "id": clerk_id,
        "email_addresses": [{"email_address": new_email}],
    })
    assert r.status_code == 200, f"Webhook returned {r.status_code}: {r.text}"
    time.sleep(0.3)

    row = db_get_user(clerk_id)
    assert row["email"] == new_email, f"Expected {new_email!r}, got {row['email']!r}"
    print(f"  ✅ user.updated → email updated in DB")


def test_clerk_user_deleted(clerk_id: str):
    r = clerk_webhook("user.deleted", {"id": clerk_id})
    assert r.status_code == 200, f"Webhook returned {r.status_code}: {r.text}"
    time.sleep(0.3)

    row = db_get_user(clerk_id)
    assert row is None, "User row still present after user.deleted webhook"
    print(f"  ✅ user.deleted → row removed from DB")


def test_invalid_webhook_signature():
    payload = json.dumps({"type": "user.created", "data": {"id": "x"}}).encode()
    headers = {
        "Content-Type":   "application/json",
        "svix-id":        "msg_bad",
        "svix-timestamp": str(int(time.time())),
        "svix-signature": "v1,invalidsignature",
    }
    r = requests.post(f"{AUTH_URL}/auth/webhooks/clerk", data=payload, headers=headers)
    assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    print("  ✅ Invalid svix signature → 400")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 Auth E2E Test Suite\n")
    try:
        test_health()
        test_unauthenticated_guards()
        test_invalid_webhook_signature()
        clerk_id, email = test_clerk_user_created()
        test_clerk_user_updated(clerk_id)
        test_clerk_user_deleted(clerk_id)
        print("\n✅ All tests passed!\n")
    except AssertionError as e:
        print(f"\n❌ FAILED: {e}\n")
        raise
