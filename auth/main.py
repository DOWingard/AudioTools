import base64
import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import pytz
import requests
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

def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        url = f"{_clerk_fapi_url()}/.well-known/jwks.json"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        log.info("Loaded Clerk JWKS from %s (%d keys)", url, len(_jwks_cache.get("keys", [])))
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

    # Reset free-tier quota every day at 04:00 PST
    scheduler.add_job(
        _reset_daily_downloads,
        CronTrigger(hour=4, minute=0, timezone=pytz.timezone("America/Los_Angeles")),
        id="daily_reset",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started — daily quota reset at 04:00 PST")

    yield

    scheduler.shutdown(wait=False)
    await db_pool.close()
    log.info("Shutdown complete")


app = FastAPI(lifespan=lifespan)

# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_or_create_user(clerk_id: str, email: str) -> asyncpg.Record:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE clerk_id = $1", clerk_id)
        if row:
            return row
        row = await conn.fetchrow(
            """
            INSERT INTO users (clerk_id, email)
            VALUES ($1, $2)
            ON CONFLICT (clerk_id) DO UPDATE SET email = EXCLUDED.email
            RETURNING *
            """,
            clerk_id,
            email,
        )
        return row


def verify_clerk_token(authorization: str) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, _get_jwks(), algorithms=["RS256"], options={"verify_aud": False})
        return payload
    except JWTError as exc:
        global _jwks_cache
        _jwks_cache = None
        try:
            return jwt.decode(token, _get_jwks(), algorithms=["RS256"], options={"verify_aud": False})
        except JWTError:
            raise HTTPException(status_code=401, detail=f"Invalid or expired token: {exc}")


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
    token_data = verify_clerk_token(authorization)
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
    token_data = verify_clerk_token(authorization)
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

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        stripe_customer_id = obj.get("customer")
        subscription_id    = obj.get("id")
        stripe_status      = obj.get("status", "")
        price_id           = (obj.get("items", {}).get("data") or [{}])[0].get("price", {}).get("id", "")

        local_status, active = _map_stripe_status(stripe_status)
        sub_type             = _map_price_to_type(price_id)

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                SET subscription_active    = $1,
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
        log.info("stripe subscription.deleted for customer %s → cancelled", stripe_customer_id)

    return Response(status_code=200)


@app.post("/auth/billing/checkout")
async def create_checkout(request: Request, authorization: str = Header(...)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    token_data = verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")

    body = await request.json()
    plan = body.get("plan", "standard")
    price_id = STRIPE_PRICE_PREMIUM if plan == "premium" else STRIPE_PRICE_STANDARD

    if not price_id:
        raise HTTPException(status_code=500, detail=f"No Stripe price configured for plan '{plan}'")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT stripe_id FROM users WHERE clerk_id = $1", clerk_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    session_kwargs = dict(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        ui_mode="embedded",
        return_url=f"{APP_URL}/?checkout=complete&session_id={{CHECKOUT_SESSION_ID}}",
    )
    if row["stripe_id"]:
        session_kwargs["customer"] = row["stripe_id"]

    session = stripe.checkout.Session.create(**session_kwargs)
    return {"client_secret": session.client_secret}


@app.post("/auth/billing/portal")
async def create_portal(authorization: str = Header(...)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    token_data = verify_clerk_token(authorization)
    clerk_id = token_data.get("sub") or token_data.get("user_id")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT stripe_id FROM users WHERE clerk_id = $1", clerk_id)
    if not row or not row["stripe_id"]:
        raise HTTPException(status_code=404, detail="No Stripe customer found")

    portal = stripe.billing_portal.Session.create(customer=row["stripe_id"], return_url=APP_URL)
    return {"url": portal.url}
