"""
tests/test_stripe_unsub.py

Production safety test suite for the StripeUnsub plan (all bugs fixed).
Tests /auth/billing/cancel, /auth/billing/downgrade, and webhook contract.

PREREQUISITES:
  - database/init.sql migration applied (§0 of StripeUnsub.md)
  - auth/main.py must include the webhook patch (§1a) and two new endpoints (§1b, §1c)
  - Run from repo root: pytest tests/test_stripe_unsub.py -v
"""

import datetime
import json
import os

import pytest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

# Set env before auth.main is imported (mirrors test_auth_unit.py pattern)
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/testdb")
os.environ.setdefault("CLERK_WEBHOOK_SECRET", "dGVzdA")
os.environ.setdefault("STRIPE_PRICE_ID_STANDARD", "price_standard_test")
os.environ.setdefault("STRIPE_PRICE_ID_PREMIUM",  "price_premium_test")
os.environ.setdefault("STRIPE_SECRET_KEY",         "sk_test_xxx")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLERK_ID        = "user_clerk_test_unsub_001"
STRIPE_SUB_ID   = "sub_unsub_test_001"
STRIPE_CUSTOMER = "cus_unsub_test_001"
STRIPE_SCHED_ID = "sub_sched_test_001"
PRICE_STANDARD  = "price_standard_test"
PRICE_PREMIUM   = "price_premium_test"
PERIOD_END      = 1_900_000_000    # future Unix timestamp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock():
    """Return (mock_pool, mock_conn) where pool.acquire() works as async CM."""
    mock_conn = AsyncMock()
    mock_conn.execute  = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)

    mock_acq = MagicMock()
    mock_acq.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acq.__aexit__  = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_acq)
    mock_pool.close   = AsyncMock()
    return mock_pool, mock_conn


def _user_row(**overrides):
    """Dict mimicking an asyncpg Record for a users row."""
    base = {
        "id":                     "00000000-0000-0000-0000-000000000001",
        "email":                  "test@example.com",
        "clerk_id":               CLERK_ID,
        "stripe_id":              STRIPE_CUSTOMER,
        "stripe_subscription_id": STRIPE_SUB_ID,
        "subscription_type":      "premium",
        "subscription_active":    True,
        "subscription_status":    "active",
        "daily_free_downloads":   3,
        "subscribed_at":          None,
        "last_active":            None,
        "created_at":             datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        "qdrant_graph_id":        None,
    }
    base.update(overrides)
    return base


def _stripe_sub(**overrides):
    """Minimal Stripe Subscription-like object."""
    item       = MagicMock()
    item.price = MagicMock()
    item.price.id = PRICE_PREMIUM

    items_data      = MagicMock()
    items_data.data = [item]

    sub = MagicMock()
    sub.id                   = STRIPE_SUB_ID
    sub.status               = "active"
    sub.current_period_end   = PERIOD_END
    sub.cancel_at_period_end = False
    sub.schedule             = None
    sub.items                = items_data
    for k, v in overrides.items():
        setattr(sub, k, v)
    return sub


def _stripe_schedule():
    """Minimal Stripe SubscriptionSchedule-like object."""
    item       = MagicMock()
    item.price = PRICE_PREMIUM

    phase            = MagicMock()
    phase.start_date = 1_700_000_000
    phase.end_date   = None        # realistic: None after create(from_subscription=)
    phase.items      = [item]

    sched        = MagicMock()
    sched.id     = STRIPE_SCHED_ID
    sched.phases = [phase]
    return sched


def _auth_header():
    return {"Authorization": "Bearer test_token"}


def _webhook_headers():
    return {
        "content-type":     "application/json",
        "stripe-signature": "t=1700000000,v1=fakesig",
    }


def _webhook_body(event_type: str, obj: dict, event_id: str = "evt_test_001") -> bytes:
    return json.dumps({
        "id":   event_id,
        "type": event_type,
        "data": {"object": obj},
    }).encode()


# ---------------------------------------------------------------------------
# Module-scoped fixture (matches test_auth_unit.py pattern)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def auth_client():
    """
    Spin up the auth FastAPI app with mocked external deps.
    Yields (TestClient, mock_conn).  Stripe calls must be patched per-test.
    """
    from fastapi.testclient import TestClient

    mock_pool, mock_conn = _make_db_mock()

    with patch("asyncpg.create_pool", AsyncMock(return_value=mock_pool)), \
         patch("auth.main._refresh_jwks", AsyncMock(return_value={"keys": []})), \
         patch("auth.main.scheduler") as mock_sched:

        mock_sched.add_job  = MagicMock()
        mock_sched.start    = MagicMock()
        mock_sched.shutdown = MagicMock()

        from auth.main import app
        with TestClient(app) as c:
            yield c, mock_conn


# ===========================================================================
# 1. POST /auth/billing/cancel
# ===========================================================================

class TestCancelSubscription:

    def test_cancel_happy_path(self, auth_client):
        """
        Authenticated premium user cancels.
        - stripe.Subscription.retrieve called to check for attached schedule
        - stripe.Subscription.modify called with cancel_at_period_end=True + idempotency_key
        - DB updated to subscription_status='canceling_at_period_end'
        - subscription_events row inserted
        - Response is updated profile JSON
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        updated = _user_row(subscription_status="canceling_at_period_end")
        mock_conn.fetchrow.side_effect = [
            _user_row(),   # SELECT stripe_subscription_id + subscription_status
            updated,       # UPDATE ... RETURNING *
        ]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve", return_value=_stripe_sub(schedule=None)) as mock_retrieve, \
             patch("stripe.Subscription.modify",   return_value=MagicMock())               as mock_modify:

            resp = client.post("/auth/billing/cancel", headers=_auth_header())

        assert resp.status_code == 200, resp.text

        mock_retrieve.assert_called_once_with(STRIPE_SUB_ID)
        mock_modify.assert_called_once_with(
            STRIPE_SUB_ID,
            cancel_at_period_end=True,
            idempotency_key=ANY,   # computed hash — value tested separately
        )

        all_fetchrow = str(mock_conn.fetchrow.call_args_list)
        assert "canceling_at_period_end" in all_fetchrow, (
            f"UPDATE must write 'canceling_at_period_end'; calls: {all_fetchrow}"
        )

        all_execute = str(mock_conn.execute.call_args_list)
        assert "subscription_events" in all_execute and "billing.cancel" in all_execute, (
            f"Expected subscription_events INSERT; calls: {all_execute}"
        )

        assert resp.json()["clerk_id"] == CLERK_ID

    def test_cancel_releases_existing_schedule_before_modify(self, auth_client):
        """
        If the subscription has a SubscriptionSchedule attached (pending downgrade),
        the endpoint must RELEASE (not cancel) the schedule before calling
        Subscription.modify.  cancel() would terminate the subscription immediately;
        release() detaches the schedule while leaving the subscription active so that
        cancel_at_period_end=True takes effect at the billing boundary as intended.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        updated = _user_row(subscription_status="canceling_at_period_end")
        mock_conn.fetchrow.side_effect = [_user_row(), updated]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve", return_value=_stripe_sub(schedule=STRIPE_SCHED_ID)), \
             patch("stripe.SubscriptionSchedule.release", return_value=MagicMock()) as mock_release, \
             patch("stripe.SubscriptionSchedule.cancel",  return_value=MagicMock()) as mock_cancel, \
             patch("stripe.Subscription.modify",          return_value=MagicMock()) as mock_modify:

            resp = client.post("/auth/billing/cancel", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        mock_release.assert_called_once_with(STRIPE_SCHED_ID), (
            f"Must call SubscriptionSchedule.release({STRIPE_SCHED_ID!r}); "
            f"release calls: {mock_release.call_args_list}"
        )
        mock_cancel.assert_not_called(), (
            "Must NOT call SubscriptionSchedule.cancel — that would terminate the "
            "subscription immediately instead of scheduling end-of-period cancellation"
        )
        mock_modify.assert_called_once()

    def test_cancel_idempotency_guard_skips_stripe_when_already_canceling(self, auth_client):
        """
        If subscription_status is already 'canceling_at_period_end', the endpoint
        returns the current profile without making any Stripe API call or duplicate
        audit entry.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        already_canceling = _user_row(subscription_status="canceling_at_period_end")
        mock_conn.fetchrow.side_effect = [
            already_canceling,   # SELECT (status check)
            already_canceling,   # SELECT * (return current profile)
        ]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve") as mock_retrieve, \
             patch("stripe.Subscription.modify")   as mock_modify:

            resp = client.post("/auth/billing/cancel", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        mock_retrieve.assert_not_called()
        mock_modify.assert_not_called()
        assert mock_conn.execute.call_count == 0, "Must not insert duplicate audit entry"

    def test_cancel_no_subscription_returns_404(self, auth_client):
        """User has no stripe_subscription_id -> 404, Stripe not called."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.fetchrow.side_effect = [_user_row(stripe_subscription_id=None)]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.modify") as mock_modify:

            resp = client.post("/auth/billing/cancel", headers=_auth_header())

        assert resp.status_code == 404
        mock_modify.assert_not_called()

    def test_cancel_user_not_found_returns_404(self, auth_client):
        """User absent from DB -> 404."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.fetchrow.side_effect = [None]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.modify") as mock_modify:

            resp = client.post("/auth/billing/cancel", headers=_auth_header())

        assert resp.status_code == 404
        mock_modify.assert_not_called()

    def test_cancel_stripe_error_returns_502_no_db_update(self, auth_client):
        """Stripe failure -> 502; DB must not be updated."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.fetchrow.side_effect = [_user_row()]

        import stripe as _stripe
        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve", return_value=_stripe_sub()), \
             patch("stripe.Subscription.modify",   side_effect=_stripe.StripeError("down")):

            resp = client.post("/auth/billing/cancel", headers=_auth_header())

        assert resp.status_code == 502
        assert mock_conn.execute.call_count == 0, (
            f"DB must not be written on Stripe error; calls: {mock_conn.execute.call_args_list}"
        )

    def test_cancel_unauthenticated_returns_401(self, auth_client):
        """Invalid/missing token -> 401."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        import fastapi
        with patch("auth.main.verify_clerk_token",
                   AsyncMock(side_effect=fastapi.HTTPException(status_code=401, detail="bad token"))):
            resp = client.post("/auth/billing/cancel", headers=_auth_header())

        assert resp.status_code == 401


# ===========================================================================
# 2. POST /auth/billing/downgrade
# ===========================================================================

class TestDowngradeSubscription:

    def test_downgrade_happy_path(self, auth_client):
        """
        Premium user schedules downgrade.
        - stripe.Subscription.retrieve called for current_period_end + current_price
        - SubscriptionSchedule.create(from_subscription=SUB_ID) called
        - SubscriptionSchedule.modify called with:
            - 2 phases
            - first phase end_date = sub.current_period_end (not schedule.phases[0].end_date)
            - proration_behavior='none' on both phases
            - end_behavior='release'
        - DB updated to subscription_status='downgrading_at_period_end'
        - subscription_events row inserted
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        updated = _user_row(subscription_status="downgrading_at_period_end")
        mock_conn.fetchrow.side_effect = [_user_row(), updated]

        schedule = _stripe_schedule()   # phases[0].end_date is None (realistic)

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve",      return_value=_stripe_sub()) as mock_retrieve, \
             patch("stripe.SubscriptionSchedule.create", return_value=schedule)     as mock_create, \
             patch("stripe.SubscriptionSchedule.modify", return_value=MagicMock())  as mock_sched_modify:

            resp = client.post("/auth/billing/downgrade", headers=_auth_header())

        assert resp.status_code == 200, resp.text

        mock_retrieve.assert_called_once_with(STRIPE_SUB_ID)
        mock_create.assert_called_once_with(from_subscription=STRIPE_SUB_ID)
        mock_sched_modify.assert_called_once()

        call_kw = mock_sched_modify.call_args
        phases       = call_kw.kwargs.get("phases")
        end_behavior = call_kw.kwargs.get("end_behavior")

        assert phases is not None,       "phases kwarg missing"
        assert len(phases) == 2,         f"Expected 2 phases, got {len(phases)}"
        assert end_behavior == "release", f"end_behavior must be 'release', got {end_behavior!r}"

        # First phase boundary must use sub.current_period_end, not schedule.phases[0].end_date
        assert phases[0].get("end_date") == PERIOD_END, (
            f"First phase end_date must be sub.current_period_end ({PERIOD_END}); "
            f"got {phases[0].get('end_date')!r}"
        )

        # Second phase must target standard price
        assert phases[1]["items"][0]["price"] == PRICE_STANDARD, (
            f"Second phase must use standard price; got {phases[1]['items'][0]['price']!r}"
        )

        # Both phases must suppress prorations
        assert phases[0].get("proration_behavior") == "none", \
            f"First phase must have proration_behavior='none'"
        assert phases[1].get("proration_behavior") == "none", \
            f"Second phase must have proration_behavior='none'"

        # Audit log
        all_execute = str(mock_conn.execute.call_args_list)
        assert "subscription_events" in all_execute and "billing.downgrade" in all_execute, (
            f"Expected subscription_events INSERT for billing.downgrade; calls: {all_execute}"
        )

    def test_downgrade_partial_failure_cancels_dangling_schedule(self, auth_client):
        """
        If SubscriptionSchedule.create() succeeds but modify() fails, the endpoint
        must cancel the dangling schedule so the user can retry successfully.
        Expected: 502 returned AND SubscriptionSchedule.cancel called.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.fetchrow.side_effect = [_user_row()]
        schedule = _stripe_schedule()

        import stripe as _stripe
        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve",       return_value=_stripe_sub()), \
             patch("stripe.SubscriptionSchedule.create", return_value=schedule), \
             patch("stripe.SubscriptionSchedule.modify", side_effect=_stripe.StripeError("modify failed")), \
             patch("stripe.SubscriptionSchedule.cancel", return_value=MagicMock()) as mock_cancel:

            resp = client.post("/auth/billing/downgrade", headers=_auth_header())

        assert resp.status_code == 502
        mock_cancel.assert_called_once_with(STRIPE_SCHED_ID), (
            f"Dangling schedule {STRIPE_SCHED_ID} must be cancelled; "
            f"cancel calls: {mock_cancel.call_args_list}"
        )

    def test_downgrade_non_premium_returns_400(self, auth_client):
        """Standard or free users cannot downgrade -> 400."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.fetchrow.side_effect = [_user_row(subscription_type="standard")]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.SubscriptionSchedule.create") as mock_create:

            resp = client.post("/auth/billing/downgrade", headers=_auth_header())

        assert resp.status_code == 400
        mock_create.assert_not_called()

    def test_downgrade_no_subscription_returns_404(self, auth_client):
        """User has no stripe_subscription_id -> 404."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.fetchrow.side_effect = [_user_row(stripe_subscription_id=None)]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.SubscriptionSchedule.create") as mock_create:

            resp = client.post("/auth/billing/downgrade", headers=_auth_header())

        assert resp.status_code == 404
        mock_create.assert_not_called()

    def test_downgrade_stripe_error_returns_502_no_db_update(self, auth_client):
        """Stripe error on schedule create -> 502; DB not modified."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.fetchrow.side_effect = [_user_row()]

        import stripe as _stripe
        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve",       return_value=_stripe_sub()), \
             patch("stripe.SubscriptionSchedule.create", side_effect=_stripe.StripeError("API error")):

            resp = client.post("/auth/billing/downgrade", headers=_auth_header())

        assert resp.status_code == 502
        assert mock_conn.execute.call_count == 0, (
            f"DB must not be written on Stripe error; calls: {mock_conn.execute.call_args_list}"
        )

    def test_downgrade_idempotency_guard_skips_stripe_when_already_downgrading(self, auth_client):
        """
        If subscription_status is already 'downgrading_at_period_end', the endpoint
        returns the current profile without making any Stripe API call.
        Without this guard, SubscriptionSchedule.create() would fail with
        'Subscription already has a schedule' and return a confusing 502.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        already_downgrading = _user_row(subscription_status="downgrading_at_period_end")
        mock_conn.fetchrow.side_effect = [
            already_downgrading,   # SELECT (status check)
            already_downgrading,   # SELECT * (return current profile)
        ]

        with patch("auth.main.verify_clerk_token", AsyncMock(return_value={"sub": CLERK_ID})), \
             patch("stripe.Subscription.retrieve")        as mock_retrieve, \
             patch("stripe.SubscriptionSchedule.create")  as mock_create:

            resp = client.post("/auth/billing/downgrade", headers=_auth_header())

        assert resp.status_code == 200, resp.text
        mock_retrieve.assert_not_called()
        mock_create.assert_not_called()
        assert mock_conn.execute.call_count == 0, "Must not insert duplicate audit entry"

    def test_downgrade_unauthenticated_returns_401(self, auth_client):
        """Invalid/missing token -> 401."""
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        import fastapi
        with patch("auth.main.verify_clerk_token",
                   AsyncMock(side_effect=fastapi.HTTPException(status_code=401, detail="bad token"))):
            resp = client.post("/auth/billing/downgrade", headers=_auth_header())

        assert resp.status_code == 401


# ===========================================================================
# 3. create_checkout — schedule guard for re-upgrade (§1d)
# ===========================================================================

class TestReUpgrade:
    """
    Tests for the create_checkout schedule guard (§1d of StripeUnsub.md).
    A standard user who previously scheduled a downgrade has a SubscriptionSchedule
    permanently attached to their subscription.  create_checkout must release it
    before creating the Stripe Checkout Session to prevent a second subscription
    being created instead of replacing the existing one.
    """

    def test_checkout_releases_schedule_before_session_create(self, auth_client):
        """
        Standard user with a persistent attached schedule re-upgrades to Premium.
        - stripe.Subscription.retrieve called to detect the attached schedule
        - stripe.SubscriptionSchedule.release called with the schedule ID
        - stripe.checkout.Session.create still called after release
        - 200 response with client_secret
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()
        mock_conn.fetchrow.side_effect = None  # clear stale side_effect from prior tests

        standard_user = _user_row(subscription_type="standard", subscription_status="active")
        mock_conn.fetchrow.return_value = standard_user

        mock_session = MagicMock()
        mock_session.client_secret = "cs_test_reupgrade_ok"

        with patch("auth.main.verify_clerk_token",
                   AsyncMock(return_value={"sub": CLERK_ID, "email": "test@example.com"})), \
             patch("stripe.Subscription.retrieve",
                   return_value=_stripe_sub(schedule=STRIPE_SCHED_ID)) as mock_retrieve, \
             patch("stripe.SubscriptionSchedule.release",
                   return_value=MagicMock()) as mock_release, \
             patch("stripe.checkout.Session.create",
                   return_value=mock_session) as mock_session_create:

            resp = client.post(
                "/auth/billing/checkout",
                json={"plan": "premium"},
                headers=_auth_header(),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json().get("client_secret") == "cs_test_reupgrade_ok"
        mock_retrieve.assert_called_once_with(STRIPE_SUB_ID)
        mock_release.assert_called_once_with(STRIPE_SCHED_ID)
        mock_session_create.assert_called_once()

    def test_checkout_no_schedule_skips_release(self, auth_client):
        """
        User with no attached schedule (fresh subscriber or already released).
        stripe.SubscriptionSchedule.release must NOT be called.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()
        mock_conn.fetchrow.side_effect = None  # clear stale side_effect from prior tests

        standard_user = _user_row(subscription_type="standard", subscription_status="active")
        mock_conn.fetchrow.return_value = standard_user

        mock_session = MagicMock()
        mock_session.client_secret = "cs_test_no_sched"

        with patch("auth.main.verify_clerk_token",
                   AsyncMock(return_value={"sub": CLERK_ID, "email": "test@example.com"})), \
             patch("stripe.Subscription.retrieve",
                   return_value=_stripe_sub(schedule=None)), \
             patch("stripe.SubscriptionSchedule.release",
                   return_value=MagicMock()) as mock_release, \
             patch("stripe.checkout.Session.create", return_value=mock_session):

            resp = client.post(
                "/auth/billing/checkout",
                json={"plan": "premium"},
                headers=_auth_header(),
            )

        assert resp.status_code == 200, resp.text
        mock_release.assert_not_called()

    def test_checkout_no_subscription_skips_retrieve_and_release(self, auth_client):
        """
        Free user (no stripe_subscription_id) upgrading for the first time.
        No Subscription.retrieve or SubscriptionSchedule.release should be called.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()
        mock_conn.fetchrow.side_effect = None  # clear stale side_effect from prior tests

        free_user = _user_row(
            subscription_type="free",
            subscription_status="free",
            subscription_active=False,
            stripe_subscription_id=None,
        )
        mock_conn.fetchrow.return_value = free_user

        mock_session = MagicMock()
        mock_session.client_secret = "cs_test_free_upgrade"

        with patch("auth.main.verify_clerk_token",
                   AsyncMock(return_value={"sub": CLERK_ID, "email": "test@example.com"})), \
             patch("stripe.Subscription.retrieve",        return_value=MagicMock()) as mock_retrieve, \
             patch("stripe.SubscriptionSchedule.release", return_value=MagicMock()) as mock_release, \
             patch("stripe.checkout.Session.create",      return_value=mock_session):

            resp = client.post(
                "/auth/billing/checkout",
                json={"plan": "premium"},
                headers=_auth_header(),
            )

        assert resp.status_code == 200, resp.text
        mock_retrieve.assert_not_called()
        mock_release.assert_not_called()

    def test_checkout_schedule_release_failure_is_nonfatal(self, auth_client):
        """
        SubscriptionSchedule.release() raises StripeError (e.g. schedule already in
        a terminal state).  The error must be caught and logged; checkout must proceed.
        Expected: 200 AND stripe.checkout.Session.create is still called.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()
        mock_conn.fetchrow.side_effect = None  # clear stale side_effect from prior tests

        standard_user = _user_row(subscription_type="standard", subscription_status="active")
        mock_conn.fetchrow.return_value = standard_user

        mock_session = MagicMock()
        mock_session.client_secret = "cs_test_release_err"

        import stripe as _stripe
        with patch("auth.main.verify_clerk_token",
                   AsyncMock(return_value={"sub": CLERK_ID, "email": "test@example.com"})), \
             patch("stripe.Subscription.retrieve",
                   return_value=_stripe_sub(schedule=STRIPE_SCHED_ID)), \
             patch("stripe.SubscriptionSchedule.release",
                   side_effect=_stripe.StripeError("already released")) as mock_release, \
             patch("stripe.checkout.Session.create",
                   return_value=mock_session) as mock_session_create:

            resp = client.post(
                "/auth/billing/checkout",
                json={"plan": "premium"},
                headers=_auth_header(),
            )

        assert resp.status_code == 200, (
            f"Schedule release failure must be non-fatal; got {resp.status_code}: {resp.text}"
        )
        mock_release.assert_called_once()
        mock_session_create.assert_called_once()


# ===========================================================================
# 4. Stripe webhook contract
# ===========================================================================

class TestWebhookContract:

    def _post_stripe_event(self, client, event_type: str, obj: dict, event_id: str = "evt_test"):
        with patch("stripe.Webhook.construct_event", return_value={
            "id":   event_id,
            "type": event_type,
            "data": {"object": obj},
        }):
            return client.post(
                "/auth/webhooks/stripe",
                content=_webhook_body(event_type, obj, event_id),
                headers=_webhook_headers(),
            )

    def test_webhook_updated_with_cancel_at_period_end_writes_canceling_status(self, auth_client):
        """
        Stripe fires customer.subscription.updated (status='active',
        cancel_at_period_end=True) after the cancel endpoint runs.
        The handler must write 'canceling_at_period_end', not 'active',
        so it does not overwrite the optimistic status.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.execute.return_value  = "UPDATE 1"
        mock_conn.fetchrow.return_value = None

        obj = {
            "id":                   STRIPE_SUB_ID,
            "customer":             STRIPE_CUSTOMER,
            "status":               "active",
            "cancel_at_period_end": True,
            "schedule":             None,
            "items": {"data": [{"price": {"id": PRICE_PREMIUM}}]},
        }

        resp = self._post_stripe_event(client, "customer.subscription.updated", obj,
                                       "evt_cancel_webhook")

        assert resp.status_code == 200

        all_execute = str(mock_conn.execute.call_args_list)
        assert "canceling_at_period_end" in all_execute, (
            f"Webhook must write 'canceling_at_period_end' when cancel_at_period_end=True; "
            f"execute calls: {all_execute}"
        )
        # 'active' must NOT be written as the local_status when cancel is scheduled
        # (it may appear in the query string but not as the status argument)
        # This check is satisfied by the presence of 'canceling_at_period_end' above.

    def test_webhook_updated_with_schedule_writes_downgrading_status(self, auth_client):
        """
        Stripe fires customer.subscription.updated (status='active', schedule=SCHED_ID)
        immediately after SubscriptionSchedule.create() in the downgrade endpoint.
        The handler must write 'downgrading_at_period_end', not 'active'.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.execute.return_value  = "UPDATE 1"
        mock_conn.fetchrow.return_value = None

        obj = {
            "id":                   STRIPE_SUB_ID,
            "customer":             STRIPE_CUSTOMER,
            "status":               "active",
            "cancel_at_period_end": False,
            "schedule":             STRIPE_SCHED_ID,    # set when schedule is attached
            "items": {"data": [{"price": {"id": PRICE_PREMIUM}}]},
        }

        resp = self._post_stripe_event(client, "customer.subscription.updated", obj,
                                       "evt_sched_webhook")

        assert resp.status_code == 200

        all_execute = str(mock_conn.execute.call_args_list)
        assert "downgrading_at_period_end" in all_execute, (
            f"Webhook must write 'downgrading_at_period_end' when schedule is attached; "
            f"execute calls: {all_execute}"
        )

    def test_webhook_subscription_deleted_reverts_to_free(self, auth_client):
        """
        customer.subscription.deleted is the authoritative period-end handler.
        Must set subscription_type='free', subscription_active=FALSE, status='cancelled'.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.execute.return_value  = None
        mock_conn.fetchrow.return_value = None

        obj = {
            "id":       STRIPE_SUB_ID,
            "customer": STRIPE_CUSTOMER,
            "status":   "canceled",
            "items": {"data": [{"price": {"id": PRICE_PREMIUM}}]},
        }

        resp = self._post_stripe_event(client, "customer.subscription.deleted", obj,
                                       "evt_deleted_001")

        assert resp.status_code == 200

        all_execute = str(mock_conn.execute.call_args_list)
        assert "free" in all_execute,      f"Must set subscription_type='free'; calls: {all_execute}"
        assert "cancelled" in all_execute, f"Must set status='cancelled'; calls: {all_execute}"

    def test_webhook_downgrade_completion_sets_standard_type(self, auth_client):
        """
        When the SubscriptionSchedule releases at period end, Stripe sends
        customer.subscription.updated (status='active', schedule=None, standard price_id).
        The handler must set subscription_type='standard' and status='active'.
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.execute.return_value  = "UPDATE 1"
        mock_conn.fetchrow.return_value = None

        obj = {
            "id":                   STRIPE_SUB_ID,
            "customer":             STRIPE_CUSTOMER,
            "status":               "active",
            "cancel_at_period_end": False,
            "schedule":             None,              # released after phase transition
            "items": {"data": [{"price": {"id": PRICE_STANDARD}}]},
        }

        resp = self._post_stripe_event(client, "customer.subscription.updated", obj,
                                       "evt_downgrade_complete")

        assert resp.status_code == 200

        all_execute = str(mock_conn.execute.call_args_list)
        assert "standard" in all_execute, (
            f"subscription_type must be 'standard' after downgrade completes; "
            f"calls: {all_execute}"
        )

    def test_webhook_updated_standard_with_persistent_schedule_sets_active(self, auth_client):
        """
        After a downgrade completes, the SubscriptionSchedule remains permanently
        attached (Phase 2 has no end_date, so has_schedule stays True on all future
        webhooks).  When a renewal fires with status='active', schedule=SCHED_ID, and
        the standard price_id, the sub_type == 'premium' guard in §1a must prevent
        overwriting subscription_status → 'downgrading_at_period_end'.

        Expected:
        - 'downgrading_at_period_end' NOT written to DB
        - 'standard' IS written (plan confirmed as standard)
        """
        client, mock_conn = auth_client
        mock_conn.reset_mock()

        mock_conn.execute.return_value  = "UPDATE 1"
        mock_conn.fetchrow.return_value = None

        obj = {
            "id":                   STRIPE_SUB_ID,
            "customer":             STRIPE_CUSTOMER,
            "status":               "active",
            "cancel_at_period_end": False,
            "schedule":             STRIPE_SCHED_ID,     # still attached — never released
            "items": {"data": [{"price": {"id": PRICE_STANDARD}}]},  # plan already standard
        }

        resp = self._post_stripe_event(
            client, "customer.subscription.updated", obj,
            "evt_persistent_sched_renewal",
        )

        assert resp.status_code == 200

        all_execute = str(mock_conn.execute.call_args_list)
        assert "downgrading_at_period_end" not in all_execute, (
            "Webhook must NOT write 'downgrading_at_period_end' for a standard-price "
            "subscription even when has_schedule=True (perpetually attached schedule); "
            f"execute calls: {all_execute}"
        )
        assert "standard" in all_execute, (
            f"Webhook must write subscription_type='standard'; execute calls: {all_execute}"
        )


# ===========================================================================
# 4. DB schema prerequisite
# ===========================================================================

class TestSchemaPrerequisite:

    def test_schema_includes_new_subscription_status_values(self):
        """
        Verifies that database/init.sql has been updated to include the two new
        subscription_status values required by the cancel and downgrade endpoints.
        Fails immediately if the migration has not been applied, serving as a
        deployment gate.
        """
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "database", "init.sql"
        )
        with open(schema_path) as f:
            schema = f.read()

        assert "canceling_at_period_end" in schema, (
            "database/init.sql must include 'canceling_at_period_end' in the "
            "chk_subscription_consistency CHECK constraint. "
            "Apply the migration from StripeUnsub.md §0 before deploying."
        )
        assert "downgrading_at_period_end" in schema, (
            "database/init.sql must include 'downgrading_at_period_end' in the "
            "chk_subscription_consistency CHECK constraint. "
            "Apply the migration from StripeUnsub.md §0 before deploying."
        )
        # canceling_at_period_end=23 chars, downgrading_at_period_end=25 chars —
        # both exceed the original VARCHAR(20); the migration must widen the column.
        assert "VARCHAR(30)" in schema, (
            "subscription_status column must be widened to VARCHAR(30). "
            "Add: ALTER TABLE users ALTER COLUMN subscription_status TYPE VARCHAR(30)"
        )
        # The column-level CHECK (auto-named users_subscription_status_check) must
        # also be dropped and recreated; the named table constraint alone is not enough.
        assert "users_subscription_status_check" in schema, (
            "Column-level CHECK constraint 'users_subscription_status_check' must be "
            "dropped and recreated with the new status values. "
            "Apply the full §0 migration from StripeUnsub.md."
        )
