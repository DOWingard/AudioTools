import base64
import hashlib
import os
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import httpx
import pytz
import stripe
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from jose import jwt, JWTError
from svix.webhooks import Webhook, WebhookVerificationError

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auth")

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_URL          = os.environ["DATABASE_URL"]
CLERK_SECRET_KEY      = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_WEBHOOK_SECRET  = os.environ.get("CLERK_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_STANDARD = os.environ.get("STRIPE_PRICE_ID_STANDARD", "")
STRIPE_PRICE_PREMIUM  = os.environ.get("STRIPE_PRICE_ID_PREMIUM", "")
APP_URL               = os.environ.get("APP_URL", "http://localhost:7860")
COMPUTE_API_URL       = os.environ.get("COMPUTE_API_URL", "http://localhost:8000")
INTERNAL_SECRET       = os.environ.get("INTERNAL_SECRET", "")
DAILY_FREE_QUOTA      = 3

stripe.api_key = STRIPE_SECRET_KEY

# ── Clerk JWKS ────────────────────────────────────────────────────────────────

def _clerk_fapi_url() -> str:
    pk = os.environ.get("VITE_CLERK_PUBLISHABLE_KEY", "")
    for prefix in ("pk_test_", "pk_live_"):
        if pk.startswith(prefix):
            encoded = pk[len(prefix):]
            encoded += "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded).decode().rstrip("$")
            return f"https://{decoded}"
    raise RuntimeError("VITE_CLERK_PUBLISHABLE_KEY not set or unrecognised format")

_jwks_cache: Optional[dict] = None
_jwks_fetched_at: float = 0.0
JWKS_TTL = 3600  # 1 hour


async def _refresh_jwks() -> dict:
    """Fetch Clerk JWKS via async httpx and update the in-process cache.

    Resolution order:
      1. CLERK_JWKS_URL env var (explicit override)
      2. Clerk Backend API (api.clerk.com) using CLERK_SECRET_KEY — preferred
         server-side path; no VITE_ variable required.
      3. Frontend API derived from VITE_CLERK_PUBLISHABLE_KEY (legacy fallback)
    """
    global _jwks_cache, _jwks_fetched_at

    direct = os.environ.get("CLERK_JWKS_URL", "")
    headers: dict = {}
    if direct:
        url = direct
    elif CLERK_SECRET_KEY:
        url = "https://api.clerk.com/v1/jwks"
        headers = {"Authorization": f"Bearer {CLERK_SECRET_KEY}"}
    else:
        url = f"{_clerk_fapi_url()}/.well-known/jwks.json"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    _jwks_cache = resp.json()
    _jwks_fetched_at = time.monotonic()
    log.info("Refreshed Clerk JWKS from %s (%d keys)", url, len(_jwks_cache.get("keys", [])))
    return _jwks_cache


def _get_jwks() -> dict:
    """Return the cached JWKS. Must be pre-warmed via _refresh_jwks() at startup."""
    if _jwks_cache is None:
        raise RuntimeError("JWKS not loaded — call _refresh_jwks() at startup")
    return _jwks_cache

# ── DB pool + scheduler ───────────────────────────────────────────────────────

db_pool: Optional[asyncpg.Pool] = None
scheduler = AsyncIOScheduler()


async def _reset_daily_downloads():
    """Reset free-tier daily download quota to 3 for all free users."""
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE users
            SET daily_free_downloads    = $1,
                daily_downloads_reset_at = NOW()
            WHERE subscription_active = FALSE
            """,
            DAILY_FREE_QUOTA,
        )
    log.info("Daily quota reset complete: %s", result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    log.info("DB pool created")

    # Pre-warm Clerk JWKS — non-fatal so auth starts even if Clerk is temporarily
    # unreachable. verify_clerk_token will retry on the first incoming request.
    try:
        await _refresh_jwks()
    except Exception as exc:
        log.warning("JWKS pre-warm failed (will retry on first request): %s", exc)

    # Reset free-tier quota every day at 04:00 PST
    scheduler.add_job(
        _reset_daily_downloads,
        CronTrigger(hour=4, minute=0, timezone=pytz.timezone("America/Los_Angeles")),
        id="daily_reset",
        replace_existing=True,
    )
    # Refresh JWKS every hour (Clerk rotates signing keys periodically)
    scheduler.add_job(
        _refresh_jwks,
        "interval",
        hours=1,
        id="jwks_refresh",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started — daily quota reset at 04:00 PST, JWKS refresh every 1h")

    yield

    scheduler.shutdown(wait=False)
    await db_pool.close()
    log.info("Shutdown complete")


app = FastAPI(lifespan=lifespan)

# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_or_create_user(clerk_id: str, email: str) -> asyncpg.Record:
    email_val = email.strip().lower() if email and email.strip() else None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE clerk_id = $1", clerk_id)
        if row:
            return row
        row = await conn.fetchrow(
            """
            INSERT INTO users (clerk_id, email)
            VALUES ($1, $2)
            ON CONFLICT (clerk_id) DO UPDATE SET email = COALESCE(EXCLUDED.email, users.email)
            RETURNING *
            """,
            clerk_id,
            email_val,
        )
        return row


async def verify_clerk_token(authorization: str) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        # If pre-warm failed at startup, try loading JWKS now on first request
        if _jwks_cache is None:
            await _refresh_jwks()
        payload = jwt.decode(token, _get_jwks(), algorithms=["RS256"], options={"verify_aud": False})
        return payload
    except JWTError as exc:
        # Stale key — force a JWKS refresh and retry once
        try:
            fresh_jwks = await _refresh_jwks()
            return jwt.decode(token, fresh_jwks, algorithms=["RS256"], options={"verify_aud": False})
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Auth service not ready — JWKS unavailable")


def _row_to_profile(row: asyncpg.Record) -> dict:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "clerk_id": row["clerk_id"],
        "stripe_id": row["stripe_id"],
        "stripe_subscription_id": row["stripe_subscription_id"],
        "subscription_type": row["subscription_type"],
        "subscription_active": row["subscription_active"],
        "subscription_status": row["subscription_status"],
        "daily_free_downloads": row["daily_free_downloads"],
        "subscribed_at": row["subscribed_at"].isoformat() if row["subscribed_at"] else None,
        "last_active": row["last_active"].isoformat() if row["last_active"] else None,
        "created_at": row["created_at"].isoformat(),
    }


def _map_stripe_status(stripe_status: str) -> tuple[str, bool]:
    if stripe_status in ("active", "trialing"):
        return "active", True
    if stripe_status in ("past_due", "incomplete", "unpaid", "paused"):
        return "past_due", False
    return "cancelled", False


def _map_price_to_type(price_id: str) -> str:
    if price_id == STRIPE_PRICE_PREMIUM:
        return "premium"
    if price_id == STRIPE_PRICE_STANDARD:
        return "standard"
    return "free"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/auth/user/me")
async def get_me(authorization: str = Header(...)):
    token_data = await verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")
    email = (token_data.get("email") or "").lower()
    if not clerk_id:
        raise HTTPException(status_code=401, detail="Cannot determine clerk_id from token")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET last_active = NOW() WHERE clerk_id = $1 RETURNING *",
            clerk_id,
        )
        if not row:
            row = await get_or_create_user(clerk_id, email)

    return _row_to_profile(row)


@app.post("/auth/usage/consume")
async def consume_usage(authorization: str = Header(...)):
    """
    Attempt to consume one free daily download for the authenticated user.
    Returns { allowed: bool, remaining: int | null }.
    Subscribed users always get allowed=true without decrementing.
    """
    token_data = await verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")
    if not clerk_id:
        raise HTTPException(status_code=401, detail="Cannot determine clerk_id")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT subscription_active, daily_free_downloads FROM users WHERE clerk_id = $1",
            clerk_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        # Paid users bypass the quota entirely
        if row["subscription_active"]:
            return {"allowed": True, "remaining": None}

        # Free tier — atomic decrement only if count > 0
        updated = await conn.fetchrow(
            """
            UPDATE users
            SET daily_free_downloads = daily_free_downloads - 1
            WHERE clerk_id = $1 AND daily_free_downloads > 0
            RETURNING daily_free_downloads
            """,
            clerk_id,
        )

    if updated is None:
        return {"allowed": False, "remaining": 0}

    return {"allowed": True, "remaining": updated["daily_free_downloads"]}


@app.post("/auth/webhooks/clerk")
async def clerk_webhook(request: Request):
    if not CLERK_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="CLERK_WEBHOOK_SECRET not configured")

    raw_body = await request.body()
    headers = dict(request.headers)

    try:
        wh = Webhook(CLERK_WEBHOOK_SECRET)
        payload = wh.verify(raw_body, headers)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = payload.get("type")
    data = payload.get("data", {})

    if event_type == "user.created":
        clerk_id = data["id"]
        email = (data.get("email_addresses") or [{}])[0].get("email_address", "")

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (clerk_id, email)
                VALUES ($1, $2)
                ON CONFLICT (clerk_id) DO UPDATE SET email = EXCLUDED.email
                RETURNING *
                """,
                clerk_id, email,
            )

        if STRIPE_SECRET_KEY and not row["stripe_id"]:
            try:
                customer = stripe.Customer.create(email=email, metadata={"clerk_id": clerk_id})
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET stripe_id = $1 WHERE clerk_id = $2",
                        customer.id, clerk_id,
                    )
                log.info("Created Stripe customer %s for clerk_id %s", customer.id, clerk_id)
            except stripe.StripeError as e:
                log.error("Stripe customer creation failed: %s", e)

    elif event_type == "user.updated":
        clerk_id = data["id"]
        email = (data.get("email_addresses") or [{}])[0].get("email_address", "")
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET email = $1 WHERE clerk_id = $2", email, clerk_id)

    elif event_type == "user.deleted":
        clerk_id = data["id"]
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE clerk_id = $1", clerk_id)
        log.info("Deleted user clerk_id=%s", clerk_id)
        # Fire-and-forget: purge Qdrant vectors and S3 objects from compute API
        if INTERNAL_SECRET:
            async with httpx.AsyncClient() as http:
                try:
                    await http.delete(
                        f"{COMPUTE_API_URL}/internal/users/{clerk_id}",
                        headers={"x-internal-secret": INTERNAL_SECRET},
                        timeout=10.0,
                    )
                except Exception as e:
                    log.error("Failed to purge user data for %s: %s", clerk_id, e)

    return Response(status_code=200)


@app.post("/auth/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET not configured")

    raw_body = await request.body()
    try:
        event = stripe.Webhook.construct_event(raw_body, stripe_signature, STRIPE_WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    obj = event["data"]["object"]
    event_type = event["type"]

    raw_event_id = event.get("id", "")

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        stripe_customer_id   = obj.get("customer")
        subscription_id      = obj.get("id")
        stripe_status        = obj.get("status", "")
        price_id             = (obj.get("items", {}).get("data") or [{}])[0].get("price", {}).get("id", "")
        cancel_at_period_end = obj.get("cancel_at_period_end", False)
        has_schedule         = bool(obj.get("schedule"))

        local_status, active = _map_stripe_status(stripe_status)
        sub_type             = _map_price_to_type(price_id)

        # Preserve user-facing scheduled-action statuses so this webhook does not
        # overwrite the optimistic state set by /billing/cancel or /billing/downgrade.
        if cancel_at_period_end and local_status == "active":
            local_status = "canceling_at_period_end"
        elif has_schedule and local_status == "active" and sub_type == "premium":
            # Guard on sub_type: after downgrade completes the schedule remains
            # permanently attached, so has_schedule stays True on renewals.
            local_status = "downgrading_at_period_end"

        async with db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE users
                SET stripe_id              = COALESCE(stripe_id, $5),
                    subscription_active    = $1,
                    subscription_type      = $2,
                    subscription_status    = $3,
                    stripe_subscription_id = $4,
                    subscribed_at          = CASE
                                              WHEN $1 AND subscribed_at IS NULL THEN NOW()
                                              ELSE subscribed_at
                                            END
                WHERE stripe_id = $5
                """,
                active, sub_type, local_status, subscription_id, stripe_customer_id,
            )

            # Fallback: if stripe_id wasn't in the DB yet, resolve via Stripe customer metadata
            if result == "UPDATE 0":
                try:
                    customer = stripe.Customer.retrieve(stripe_customer_id)
                    clerk_id_meta = (customer.get("metadata") or {}).get("clerk_id", "")
                    if clerk_id_meta:
                        result = await conn.execute(
                            """
                            UPDATE users
                            SET stripe_id              = $1,
                                subscription_active    = $2,
                                subscription_type      = $3,
                                subscription_status    = $4,
                                stripe_subscription_id = $5,
                                subscribed_at          = CASE
                                                           WHEN $2 AND subscribed_at IS NULL THEN NOW()
                                                           ELSE subscribed_at
                                                         END
                            WHERE clerk_id = $6
                            """,
                            stripe_customer_id, active, sub_type, local_status, subscription_id, clerk_id_meta,
                        )
                        log.info("webhook fallback via metadata: clerk_id=%s customer=%s", clerk_id_meta, stripe_customer_id)
                    else:
                        log.warning("webhook: no DB row and no clerk_id metadata for customer=%s", stripe_customer_id)
                except stripe.StripeError as exc:
                    log.error("webhook fallback customer.retrieve failed for %s: %s", stripe_customer_id, exc)

            await conn.execute(
                """
                INSERT INTO subscription_events
                    (clerk_id, stripe_id, event_type, stripe_status, local_status, sub_type, raw_event_id)
                SELECT clerk_id, $1, $2, $3, $4, $5, $6 FROM users WHERE stripe_id = $1
                """,
                stripe_customer_id, event_type, stripe_status, local_status, sub_type, raw_event_id,
            )
        log.info("stripe %s sub=%s status=%s→%s active=%s type=%s",
                 event_type, subscription_id, stripe_status, local_status, active, sub_type)

    elif event_type == "customer.subscription.deleted":
        stripe_customer_id = obj.get("customer")
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                SET subscription_active    = FALSE,
                    subscription_type      = 'free',
                    subscription_status    = 'cancelled',
                    stripe_subscription_id = NULL
                WHERE stripe_id = $1
                """,
                stripe_customer_id,
            )
            await conn.execute(
                """
                INSERT INTO subscription_events
                    (clerk_id, stripe_id, event_type, stripe_status, local_status, sub_type, raw_event_id)
                SELECT clerk_id, $1, $2, 'deleted', 'cancelled', 'free', $3 FROM users WHERE stripe_id = $1
                """,
                stripe_customer_id, event_type, raw_event_id,
            )
        log.info("stripe subscription.deleted for customer %s → cancelled", stripe_customer_id)

    elif event_type == "invoice.payment_failed":
        stripe_customer_id = obj.get("customer")
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                SET subscription_active = FALSE,
                    subscription_status = 'past_due'
                WHERE stripe_id = $1
                """,
                stripe_customer_id,
            )
            await conn.execute(
                """
                INSERT INTO subscription_events
                    (clerk_id, stripe_id, event_type, stripe_status, local_status, raw_event_id)
                SELECT clerk_id, $1, $2, 'payment_failed', 'past_due', $3 FROM users WHERE stripe_id = $1
                """,
                stripe_customer_id, event_type, raw_event_id,
            )
        log.warning("invoice.payment_failed for customer %s — access suspended", stripe_customer_id)

    elif event_type == "invoice.payment_succeeded":
        stripe_customer_id = obj.get("customer")
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                SET subscription_active = TRUE,
                    subscription_status = 'active'
                WHERE stripe_id = $1 AND subscription_status = 'past_due'
                """,
                stripe_customer_id,
            )
            await conn.execute(
                """
                INSERT INTO subscription_events
                    (clerk_id, stripe_id, event_type, stripe_status, local_status, raw_event_id)
                SELECT clerk_id, $1, $2, 'payment_succeeded', 'active', $3 FROM users WHERE stripe_id = $1
                """,
                stripe_customer_id, event_type, raw_event_id,
            )
        log.info("invoice.payment_succeeded for customer %s — access restored", stripe_customer_id)

    return Response(status_code=200)


@app.post("/auth/billing/checkout")
async def create_checkout(request: Request, authorization: str = Header(...)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    token_data = await verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")
    email = (token_data.get("email") or "").lower()

    body = await request.json()
    plan = body.get("plan", "standard")
    price_id = STRIPE_PRICE_PREMIUM if plan == "premium" else STRIPE_PRICE_STANDARD

    if not price_id:
        raise HTTPException(status_code=500, detail=f"No Stripe price configured for plan '{plan}'")

    # Upsert: Clerk webhook may not have fired yet when the user reaches checkout
    row = await get_or_create_user(clerk_id, email)

    # Guard: if the user has a subscription with a pending SubscriptionSchedule
    # (e.g. a scheduled downgrade from /billing/downgrade), release the schedule
    # before opening a new checkout session.  Without this, Stripe creates a second
    # subscription instead of replacing the existing one.
    if row.get("stripe_subscription_id"):
        try:
            _existing_sub = stripe.Subscription.retrieve(row["stripe_subscription_id"])
            if _existing_sub.schedule:
                stripe.SubscriptionSchedule.release(_existing_sub.schedule)
                log.info(
                    "create_checkout: released schedule %s for clerk_id=%s before re-upgrade",
                    _existing_sub.schedule, clerk_id,
                )
        except stripe.StripeError as exc:
            log.warning(
                "create_checkout: could not release schedule for clerk_id=%s: %s",
                clerk_id, exc,
            )

    session_kwargs = dict(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        ui_mode="embedded",
        return_url=f"{APP_URL}/checkout?session_id={{CHECKOUT_SESSION_ID}}",
    )
    if row["stripe_id"]:
        session_kwargs["customer"] = row["stripe_id"]

    window = int(time.time()) // 600  # 10-minute dedup window
    idempotency_key = hashlib.sha256(
        f"checkout:{clerk_id}:{plan}:{window}".encode()
    ).hexdigest()
    session = stripe.checkout.Session.create(
        **session_kwargs,
        idempotency_key=idempotency_key,
    )
    return {"client_secret": session.client_secret}


@app.post("/auth/billing/sync")
async def sync_billing(request: Request, authorization: str = Header(...)):
    """
    Called by the frontend after Stripe redirects back to /checkout?session_id=...
    Retrieves the checkout session from Stripe, verifies ownership, and syncs
    subscription state to the DB. Returns the updated user profile.

    This is the synchronous confirmation path — webhooks remain the source of
    truth for all subsequent lifecycle events (renewals, cancellations, failures).
    """
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    token_data = await verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")
    email = (token_data.get("email") or "").lower()
    if not clerk_id:
        raise HTTPException(status_code=401, detail="Cannot determine clerk_id from token")

    body = await request.json()
    session_id = body.get("session_id", "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    # Retrieve session from Stripe with subscription expanded inline
    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except stripe.StripeError as exc:
        log.error("Stripe session retrieve failed for session=%s: %s", session_id, exc)
        raise HTTPException(status_code=400, detail="Invalid or expired session_id")

    # Session must be fully complete with confirmed payment
    if session.status != "complete":
        raise HTTPException(status_code=400, detail="Checkout session is not complete")
    if session.payment_status != "paid":
        raise HTTPException(status_code=400, detail="Payment has not been confirmed")

    session_customer = session.customer  # Stripe customer ID string

    # Upsert user row — handles the case where the Clerk webhook hasn't fired yet
    row = await get_or_create_user(clerk_id, email)

    # Verify session ownership: if we already have a stripe_id it must match
    if row["stripe_id"] and row["stripe_id"] != session_customer:
        log.warning(
            "Ownership mismatch: clerk_id=%s db_stripe_id=%s session_customer=%s",
            clerk_id, row["stripe_id"], session_customer,
        )
        raise HTTPException(status_code=403, detail="Session does not belong to this account")

    # Extract subscription data from the expanded Subscription object.
    # Use dict-key access for "items" — StripeObject inherits from dict, so
    # subscription.items resolves to dict.items() (the built-in method) rather
    # than the Stripe ListObject stored under the "items" key.
    subscription = session.subscription
    stripe_subscription_id = subscription.id
    stripe_status = subscription.status
    _items_data = subscription["items"]["data"] if subscription.get("items") else []
    price_id = _items_data[0]["price"]["id"] if _items_data else ""

    local_status, active = _map_stripe_status(stripe_status)
    sub_type = _map_price_to_type(price_id)

    async with db_pool.acquire() as conn:
        updated_row = await conn.fetchrow(
            """
            UPDATE users
            SET stripe_id              = COALESCE(stripe_id, $1),
                stripe_subscription_id = $2,
                subscription_active    = $3,
                subscription_type      = $4,
                subscription_status    = $5,
                subscribed_at          = CASE
                                           WHEN $3 AND subscribed_at IS NULL THEN NOW()
                                           ELSE subscribed_at
                                         END
            WHERE clerk_id = $6
            RETURNING *
            """,
            session_customer,
            stripe_subscription_id,
            active,
            sub_type,
            local_status,
            clerk_id,
        )
        if not updated_row:
            raise HTTPException(status_code=404, detail="User not found")

        await conn.execute(
            """
            INSERT INTO subscription_events
                (clerk_id, stripe_id, event_type, stripe_status, local_status, sub_type, raw_event_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            clerk_id, session_customer,
            "checkout.session.completed",
            stripe_status, local_status, sub_type,
            session_id,
        )

    log.info(
        "billing/sync: clerk_id=%s session=%s sub=%s stripe_status=%s→%s active=%s type=%s",
        clerk_id, session_id, stripe_subscription_id,
        stripe_status, local_status, active, sub_type,
    )

    return _row_to_profile(updated_row)


@app.post("/auth/billing/portal")
async def create_portal(authorization: str = Header(...)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    token_data = await verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT stripe_id FROM users WHERE clerk_id = $1", clerk_id)
    if not row or not row["stripe_id"]:
        raise HTTPException(status_code=404, detail="No Stripe customer found")

    window = int(time.time()) // 60  # 1-minute dedup window
    idempotency_key = hashlib.sha256(
        f"portal:{clerk_id}:{window}".encode()
    ).hexdigest()
    portal = stripe.billing_portal.Session.create(
        customer=row["stripe_id"],
        return_url=APP_URL,
        idempotency_key=idempotency_key,
    )
    return {"url": portal.url}


@app.post("/auth/billing/cancel")
async def cancel_subscription(authorization: str = Header(...)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    token_data = await verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")
    if not clerk_id:
        raise HTTPException(status_code=401, detail="Cannot determine clerk_id")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stripe_subscription_id, subscription_status FROM users WHERE clerk_id = $1",
            clerk_id,
        )
    if not row or not row["stripe_subscription_id"]:
        raise HTTPException(status_code=404, detail="No active subscription found")

    # Idempotency: already scheduled to cancel — return current state without
    # making a redundant Stripe API call or duplicate audit entry.
    if row["subscription_status"] == "canceling_at_period_end":
        async with db_pool.acquire() as conn:
            current = await conn.fetchrow("SELECT * FROM users WHERE clerk_id = $1", clerk_id)
        if not current:
            raise HTTPException(status_code=404, detail="User not found")
        return _row_to_profile(current)

    try:
        sub = stripe.Subscription.retrieve(row["stripe_subscription_id"])
        if sub.schedule:
            # Release (not cancel) the schedule — cancel() terminates the subscription
            # immediately; release() detaches it so cancel_at_period_end can take effect.
            stripe.SubscriptionSchedule.release(sub.schedule)
            log.info("billing/cancel: released existing schedule %s for clerk_id=%s",
                     sub.schedule, clerk_id)

        window = int(time.time()) // 600   # 10-minute dedup window
        idempotency_key = hashlib.sha256(
            f"cancel:{clerk_id}:{window}".encode()
        ).hexdigest()
        stripe.Subscription.modify(
            row["stripe_subscription_id"],
            cancel_at_period_end=True,
            idempotency_key=idempotency_key,
        )
    except stripe.StripeError as exc:
        log.error("Stripe cancel failed for clerk_id=%s: %s", clerk_id, exc)
        raise HTTPException(status_code=502, detail="Stripe error — please try again")

    async with db_pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE users
            SET subscription_status = 'canceling_at_period_end'
            WHERE clerk_id = $1
            RETURNING *
            """,
            clerk_id,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="User not found")
        await conn.execute(
            """
            INSERT INTO subscription_events
                (clerk_id, stripe_id, event_type, stripe_status, local_status, sub_type, raw_event_id)
            SELECT clerk_id, stripe_id, 'billing.cancel', 'canceling', 'canceling_at_period_end',
                   subscription_type, $2
            FROM users WHERE clerk_id = $1
            """,
            clerk_id,
            f"api:{row['stripe_subscription_id']}",
        )

    log.info("billing/cancel: clerk_id=%s sub=%s", clerk_id, row["stripe_subscription_id"])
    return _row_to_profile(updated)


@app.post("/auth/billing/downgrade")
async def downgrade_subscription(authorization: str = Header(...)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    if not STRIPE_PRICE_STANDARD:
        raise HTTPException(status_code=500, detail="Standard price not configured")

    token_data = await verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")
    if not clerk_id:
        raise HTTPException(status_code=401, detail="Cannot determine clerk_id")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stripe_subscription_id, subscription_type, subscription_status FROM users WHERE clerk_id = $1",
            clerk_id,
        )
    if not row or not row["stripe_subscription_id"]:
        raise HTTPException(status_code=404, detail="No active subscription found")
    if row["subscription_type"] != "premium":
        raise HTTPException(status_code=400, detail="Only premium subscriptions can be downgraded")

    # Idempotency: already scheduled to downgrade — return current state without
    # making a redundant Stripe API call (SubscriptionSchedule.create would fail
    # with "Subscription already has a schedule" if called twice).
    if row["subscription_status"] == "downgrading_at_period_end":
        async with db_pool.acquire() as conn:
            current = await conn.fetchrow("SELECT * FROM users WHERE clerk_id = $1", clerk_id)
        if not current:
            raise HTTPException(status_code=404, detail="User not found")
        return _row_to_profile(current)

    try:
        sub = stripe.Subscription.retrieve(row["stripe_subscription_id"])
        current_price      = sub["items"]["data"][0]["price"]["id"]
        current_period_end = sub.current_period_end

        schedule = stripe.SubscriptionSchedule.create(from_subscription=sub.id)

        try:
            stripe.SubscriptionSchedule.modify(
                schedule.id,
                end_behavior="release",
                phases=[
                    {
                        "start_date": schedule.phases[0].start_date,
                        "end_date": current_period_end,
                        "items": [{"price": current_price, "quantity": 1}],
                        "proration_behavior": "none",
                    },
                    {
                        "items": [{"price": STRIPE_PRICE_STANDARD, "quantity": 1}],
                        "proration_behavior": "none",
                    },
                ],
            )
        except stripe.StripeError as modify_exc:
            try:
                stripe.SubscriptionSchedule.cancel(schedule.id)
                log.warning("billing/downgrade: cancelled dangling schedule %s for clerk_id=%s",
                            schedule.id, clerk_id)
            except stripe.StripeError as cancel_exc:
                log.error("billing/downgrade: failed to cancel dangling schedule %s for clerk_id=%s: %s",
                          schedule.id, clerk_id, cancel_exc)
            raise modify_exc

    except stripe.StripeError as exc:
        log.error("Stripe downgrade failed for clerk_id=%s: %s", clerk_id, exc)
        raise HTTPException(status_code=502, detail="Stripe error — please try again")

    async with db_pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE users
            SET subscription_status = 'downgrading_at_period_end'
            WHERE clerk_id = $1
            RETURNING *
            """,
            clerk_id,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="User not found")
        await conn.execute(
            """
            INSERT INTO subscription_events
                (clerk_id, stripe_id, event_type, stripe_status, local_status, sub_type, raw_event_id)
            SELECT clerk_id, stripe_id, 'billing.downgrade', 'active', 'downgrading_at_period_end',
                   subscription_type, $2
            FROM users WHERE clerk_id = $1
            """,
            clerk_id,
            f"api:{row['stripe_subscription_id']}",
        )

    log.info("billing/downgrade: clerk_id=%s premium→standard", clerk_id)
    return _row_to_profile(updated)
