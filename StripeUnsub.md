# StripeUnsub — Subscription Management Spec

In-app cancel and downgrade flow. Replaces the Stripe hosted portal redirect
(`openBillingPortal` in `UserMenu.jsx`) with a modal-driven flow that stays
inside the app, gives premium users a downgrade-to-standard offer before
cancelling, and reverts the account to free tier on confirm.

---

## Scope

| User tier | Available actions |
|-----------|-------------------|
| `free` | No action — not shown |
| `standard` | Cancel → revert to free |
| `premium` | Downgrade to Standard ($9.99/mo) **or** Cancel → revert to free |

---

## Architecture Overview

```
UserMenu.jsx
  └─ "Manage subscription" click
       └─ opens ManageSubModal
            ├─ standard user → confirm cancel
            │    └─ POST /auth/billing/cancel
            │         └─ stripe.Subscription.retrieve → release schedule if any
            │              → stripe.Subscription.modify(cancel_at_period_end=True)
            │                   └─ webhook: customer.subscription.updated (cancel_at_period_end=True)
            │                        → DB canceling_at_period_end
            │                   └─ webhook: customer.subscription.deleted → DB free
            └─ premium user → choice screen
                  ├─ "Downgrade to Standard" → confirm → POST /auth/billing/downgrade
                  │    └─ stripe.Subscription.retrieve → SubscriptionSchedule.create
                  │         → SubscriptionSchedule.modify (2 phases, proration_behavior='none')
                  │              └─ webhook: customer.subscription.updated (has_schedule=True)
                  │                   → DB downgrading_at_period_end
                  │              └─ webhook: customer.subscription.updated (period end)
                  │                   → DB standard
                 └─ "Cancel subscription" → confirm cancel (same as standard path)
```

---

## 0. Database migration — `database/init.sql`

**Required before deploying any endpoint in this spec.**

Three changes are needed. The column is currently `VARCHAR(20)` — the new
status strings are 23 and 25 characters, so the type must be widened first.
There are also two CHECK constraints to update: a column-level one (auto-named
`users_subscription_status_check`) and a named table-level one
(`chk_subscription_consistency`).

Before running, verify the auto-named inline CHECK constraint exists with the expected name:
```sql
SELECT conname FROM pg_constraint WHERE conrelid = 'users'::regclass AND contype = 'c';
```
Expected: `users_subscription_status_check`. The `DROP CONSTRAINT IF EXISTS` is safe if the
name differs — PostgreSQL allows multiple CHECK constraints per column, so the ADD below
will succeed either way. Confirm the old constraint is gone before deploying endpoints.

```sql
-- 1. Widen the column — new values exceed VARCHAR(20)
--    canceling_at_period_end    = 23 chars
--    downgrading_at_period_end  = 25 chars
ALTER TABLE users ALTER COLUMN subscription_status TYPE VARCHAR(30);

-- 2. Drop and recreate the column-level CHECK (inline with CREATE TABLE,
--    auto-named users_subscription_status_check by PostgreSQL)
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_subscription_status_check;
ALTER TABLE users ADD CONSTRAINT users_subscription_status_check
    CHECK (subscription_status IN (
        'free', 'active', 'past_due', 'cancelled',
        'canceling_at_period_end', 'downgrading_at_period_end'
    ));

-- 3. Replace the table-level consistency constraint
ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_subscription_consistency;
ALTER TABLE users ADD CONSTRAINT chk_subscription_consistency CHECK (
    (subscription_active = TRUE  AND subscription_status IN (
        'active',
        'canceling_at_period_end',
        'downgrading_at_period_end'
    )) OR
    (subscription_active = FALSE AND subscription_status IN (
        'free', 'past_due', 'cancelled'
    ))
);
```

---

## 1. Backend — `auth/main.py`

### 1a. Webhook handler update — `customer.subscription.updated`

**Insert location:** replace the variable-extraction block at the top of the
`customer.subscription.created / customer.subscription.updated` branch
(currently lines 383–391). No other part of that handler changes.

**Why:** Setting `cancel_at_period_end=True` fires `customer.subscription.updated`
with `status='active'`. Without this fix the handler would overwrite the
optimistic `'canceling_at_period_end'` status with `'active'`. Similarly,
attaching a `SubscriptionSchedule` (downgrade) fires the same event with
`obj['schedule']` set.

```python
    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        stripe_customer_id   = obj.get("customer")
        subscription_id      = obj.get("id")
        stripe_status        = obj.get("status", "")
        price_id             = (obj.get("items", {}).get("data") or [{}])[0].get("price", {}).get("id", "")
        cancel_at_period_end = obj.get("cancel_at_period_end", False)
        has_schedule         = bool(obj.get("schedule"))

        local_status, active = _map_stripe_status(stripe_status)
        sub_type             = _map_price_to_type(price_id)

        # Preserve user-facing scheduled-action statuses so this webhook does
        # not overwrite the optimistic state set by /billing/cancel or
        # /billing/downgrade.  Both statuses leave subscription_active=TRUE
        # (access continues until the period boundary).
        if cancel_at_period_end and local_status == "active":
            local_status = "canceling_at_period_end"
        elif has_schedule and local_status == "active" and sub_type == "premium":
            # Guard on sub_type: after the downgrade completes, the schedule remains
            # attached (see §1c note), so has_schedule stays True on subsequent renewals.
            # Without the sub_type check, every future webhook for a standard user with
            # a schedule would incorrectly overwrite 'active' → 'downgrading_at_period_end'.
            local_status = "downgrading_at_period_end"

        # ── rest of the existing handler continues unchanged ──
```

### 1b. `POST /auth/billing/cancel`

Schedules end-of-period cancellation. Guards against:
- double-cancel (idempotent early return)
- subscription attached to a schedule (cancel schedule first, then modify)

Append after the closing `return` of `create_portal` (currently line 691).

```python
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
        # Retrieve the subscription to check for an attached schedule.
        # If the user previously scheduled a downgrade, a SubscriptionSchedule
        # is attached and stripe.Subscription.modify() would raise:
        #   InvalidRequestError: Cannot modify a subscription attached to a schedule.
        # Cancel the schedule first so the subscription is free to be modified.
        sub = stripe.Subscription.retrieve(row["stripe_subscription_id"])
        if sub.schedule:
            # Release (not cancel) the schedule so the subscription is not immediately
            # terminated. SubscriptionSchedule.cancel() cancels both the schedule AND the
            # subscription immediately. SubscriptionSchedule.release() detaches the schedule
            # while leaving the subscription active, so the cancel_at_period_end modify below
            # can take effect at the billing boundary as intended.
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

    # Optimistic DB update — webhook customer.subscription.updated (with
    # cancel_at_period_end=True) will confirm this; customer.subscription.deleted
    # fires at period end and reverts to free.
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
            # Prefix "api:" distinguishes API-initiated audit entries from webhook entries
            # (which store Stripe evt_... IDs). Subscription ID is the best available ref.
            f"api:{row['stripe_subscription_id']}",
        )

    log.info("billing/cancel: clerk_id=%s sub=%s", clerk_id, row["stripe_subscription_id"])
    return _row_to_profile(updated)
```

### 1c. `POST /auth/billing/downgrade`

Schedules a downgrade to Standard at period end using a `SubscriptionSchedule`.
Key correctness requirements:

- **Phase boundary**: uses `sub.current_period_end` (not
  `schedule.phases[0].end_date`, which is `None` after `create(from_subscription=)`).
- **Proration**: `proration_behavior='none'` on both phases prevents Stripe
  from generating an unexpected proration invoice at the transition.
- **Dangling schedule**: if `SubscriptionSchedule.modify()` fails after
  `create()` succeeds, the dangling one-phase schedule is cancelled before
  re-raising, keeping the subscription in a clean, retryable state.
- **Audit log**: writes to `subscription_events` consistently with cancel.

```python
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
        # Retrieve subscription for current price + period boundary.
        sub = stripe.Subscription.retrieve(row["stripe_subscription_id"])
        current_price      = sub.items.data[0].price.id
        current_period_end = sub.current_period_end   # explicit phase boundary (not schedule.phases[0].end_date)

        # Step 1: convert the existing subscription into a scheduled subscription.
        # No idempotency key on create(): if modify() fails and the dangling schedule is
        # cancelled (see except block below), a retry must create a *new* schedule.
        # An idempotency key would return the already-cancelled schedule on retry, causing
        # a follow-up modify() to fail. The DB-level early return above is the primary
        # dedup guard for double-clicks / concurrent requests.
        schedule = stripe.SubscriptionSchedule.create(from_subscription=sub.id)

        # Step 2: append the Standard downgrade phase.
        # On modify failure: cancel the dangling schedule so the user is not
        # stuck — subsequent retries will succeed via create(from_subscription=).
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

    # Optimistic DB update — webhook customer.subscription.updated (has_schedule=True)
    # will confirm this. The period-end customer.subscription.updated (plan change)
    # will set subscription_type='standard' and subscription_status='active'.
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
```

**Note on perpetually attached SubscriptionSchedule:** Phase 2 of the downgrade schedule
has no `end_date`. With `end_behavior="release"`, the schedule only releases after the
*last* phase's `end_date` passes — so with an open-ended Phase 2 the schedule remains
permanently attached to the subscription. Consequences:

- `customer.subscription.updated` webhooks will always carry `has_schedule=True` for
  this user going forward. The `sub_type == "premium"` guard in §1a prevents this from
  causing incorrect status overwrites.
- If the user later re-upgrades to Premium, `create_checkout` must call
  `stripe.SubscriptionSchedule.release(existing_schedule_id)` before creating the
  Stripe Checkout Session — otherwise Stripe creates a second subscription instead of
  modifying the existing one. **This guard is a required follow-on to this spec.**

### Insert location

Both endpoints (`cancel_subscription`, `downgrade_subscription`) go at the
**end of `auth/main.py`**, after the closing `return` of `create_portal`
(currently line 691). No other file in `auth/` needs changing beyond the
webhook handler block in §1a above.

---

## 2. Frontend — New `ManageSubModal.jsx`

Create `ui/src/components/ManageSubModal.jsx`. Follows the same modal shell
pattern as `SubscriptionModal.jsx`.

### States

```
'choice'   — premium only: "Downgrade to Standard" vs "Cancel subscription"
'confirm'  — shown for both actions before executing
'loading'  — spinner while fetch is in flight
'done'     — success message, auto-closes after 2s
'error'    — inline error, retry available
```

Standard users skip `'choice'` and open directly into `'confirm'` (cancel).

### Full component

```jsx
import { useState, useEffect } from 'react';
import { useAuth } from '@clerk/clerk-react';
import { useAuthContext } from '../AuthContext.jsx';

export default function ManageSubModal({ open, onClose }) {
    const { getToken } = useAuth();
    const { profile, setProfile } = useAuthContext();

    const isPremium = profile?.subscription_type === 'premium';

    const [view,    setView]    = useState('confirm');
    const [action,  setAction]  = useState('cancel');
    const [loading, setLoading] = useState(false);
    const [error,   setError]   = useState('');
    const [done,    setDone]    = useState(false);

    // Reset state cleanly when the modal opens.
    // useEffect is used (not the render body) to avoid triggering
    // extra renders in React 19 Strict Mode.
    useEffect(() => {
        if (open) {
            setView(isPremium ? 'choice' : 'confirm');
            setAction(isPremium ? null : 'cancel');
            setError('');
            setDone(false);
        }
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleClose = () => {
        setLoading(false);
        onClose();
    };

    const chooseAction = (chosen) => {
        setAction(chosen);
        setView('confirm');
    };

    const execute = async () => {
        setLoading(true);
        setError('');
        try {
            const token    = await getToken();
            const endpoint = action === 'downgrade'
                ? '/auth/billing/downgrade'
                : '/auth/billing/cancel';
            const resp = await fetch(endpoint, {
                method:  'POST',
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!resp.ok) {
                // FastAPI returns JSON { "detail": "..." } on errors.
                let msg;
                try {
                    const data = await resp.json();
                    msg = data.detail || `Request failed (${resp.status})`;
                } catch {
                    msg = await resp.text() || `Request failed (${resp.status})`;
                }
                throw new Error(msg);
            }
            const updatedProfile = await resp.json();
            setProfile(updatedProfile);
            setDone(true);
            setTimeout(handleClose, 2200);
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    if (!open) return null;

    const CONFIRM_COPY = {
        cancel: {
            heading:  'Cancel subscription?',
            body:     'Your subscription will end at the end of your current billing period. You will retain access until then, after which your account will revert to the free tier.',
            cta:      'Yes, cancel subscription',
            ctaColor: '#dc2626',
        },
        downgrade: {
            heading:  'Downgrade to Standard?',
            body:     'You will be moved to the Standard plan ($9.99/mo). Premium features (file storage, smart retrieval) will be removed at the end of the current billing period.',
            cta:      'Yes, downgrade to Standard',
            ctaColor: '#7c3aed',
        },
    };

    return (
        <div className="modal-overlay" onClick={handleClose}>
            <div
                className="modal-content"
                style={{ maxWidth: 480 }}
                onClick={(e) => e.stopPropagation()}
            >
                <button className="modal-close" onClick={handleClose} aria-label="Close">✕</button>

                {done ? (
                    <div style={{ textAlign: 'center', padding: '2rem 1rem' }}>
                        <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>✓</div>
                        <p style={{ fontWeight: 600, fontSize: '1.1rem', margin: 0 }}>
                            {action === 'downgrade' ? 'Downgrade scheduled.' : 'Cancellation scheduled.'}
                        </p>
                        <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem', fontSize: '0.9rem' }}>
                            {action === 'downgrade'
                                ? 'Your plan will downgrade to Standard at the end of the billing period.'
                                : 'Your subscription will end at the end of your billing period.'}
                        </p>
                    </div>
                ) : view === 'choice' ? (
                    <>
                        <h2 style={{ marginTop: 0, marginBottom: '0.5rem' }}>Manage subscription</h2>
                        <p style={{ color: 'var(--text-secondary)', marginTop: 0, marginBottom: '1.5rem', fontSize: '0.9rem' }}>
                            You are on the <strong>Premium</strong> plan ($14.99/mo).
                        </p>

                        {/* Downgrade option */}
                        <div
                            style={{ border: '1px solid #E0E0E0', borderRadius: 8, padding: '1.25rem', marginBottom: '1rem', cursor: 'pointer' }}
                            onClick={() => chooseAction('downgrade')}
                        >
                            <div style={{ fontWeight: 700, marginBottom: '0.25rem' }}>
                                Downgrade to Standard — $9.99/mo
                            </div>
                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                                Keep unlimited use of all tools. Lose file storage and smart retrieval.
                            </div>
                            <button
                                className="btn btn-primary"
                                style={{ marginTop: '1rem', background: '#7c3aed', borderColor: '#7c3aed' }}
                                onClick={(e) => { e.stopPropagation(); chooseAction('downgrade'); }}
                            >
                                Switch to Standard
                            </button>
                        </div>

                        {/* Cancel option */}
                        <div
                            style={{ border: '1px solid #E0E0E0', borderRadius: 8, padding: '1.25rem', cursor: 'pointer' }}
                            onClick={() => chooseAction('cancel')}
                        >
                            <div style={{ fontWeight: 700, marginBottom: '0.25rem' }}>
                                Cancel subscription
                            </div>
                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                                Revert to the free tier (3 uses/day). All stored files will be removed.
                            </div>
                            <button
                                className="btn btn-primary"
                                style={{ marginTop: '1rem', background: '#dc2626', borderColor: '#dc2626' }}
                                onClick={(e) => { e.stopPropagation(); chooseAction('cancel'); }}
                            >
                                Cancel subscription
                            </button>
                        </div>
                    </>
                ) : (
                    /* Confirm screen */
                    <>
                        {isPremium && (
                            <button
                                onClick={() => setView('choice')}
                                style={{
                                    background: 'none', border: 'none', cursor: 'pointer',
                                    fontSize: '1rem', color: 'var(--text-secondary)',
                                    padding: '0 0 1rem', display: 'block',
                                }}
                            >
                                ← Back
                            </button>
                        )}
                        <h2 style={{ marginTop: 0, marginBottom: '0.75rem' }}>
                            {CONFIRM_COPY[action].heading}
                        </h2>
                        <p style={{ color: 'var(--text-secondary)', marginTop: 0, marginBottom: '1.5rem', fontSize: '0.9rem', lineHeight: 1.6 }}>
                            {CONFIRM_COPY[action].body}
                        </p>

                        {error && (
                            <p style={{ color: '#dc2626', fontSize: '0.875rem', marginBottom: '1rem' }}>
                                {error}
                            </p>
                        )}

                        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
                            <button
                                className="btn"
                                style={{ border: '1px solid #E0E0E0', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
                                onClick={handleClose}
                                disabled={loading}
                            >
                                Keep my plan
                            </button>
                            <button
                                className="btn btn-primary"
                                style={{ background: CONFIRM_COPY[action].ctaColor, borderColor: CONFIRM_COPY[action].ctaColor }}
                                onClick={execute}
                                disabled={loading}
                            >
                                {loading ? 'Processing…' : CONFIRM_COPY[action].cta}
                            </button>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
```

---

## 3. Wire-up — `App.jsx`

Import `ManageSubModal` and add open/close state alongside the existing
`subModalOpen` / `setSubModalOpen` state.

```jsx
// In App.jsx — add state
const [manageSubOpen, setManageSubOpen] = useState(false);

// In JSX — add alongside <SubscriptionModal>
<ManageSubModal open={manageSubOpen} onClose={() => setManageSubOpen(false)} />
```

Expose `setManageSubOpen` through `AuthContext` the same way `setSubModalOpen`
is already exposed (add to the `AuthContext.Provider` value object in
`AuthContext.jsx`).

---

## 4. Trigger — `UserMenu.jsx`

Replace the existing `openBillingPortal` async redirect with a call to open
`ManageSubModal`. For paid users, swap the "Manage subscription" menu item's
`onClick` to `setManageSubOpen(true)` instead of calling the portal endpoint.

```jsx
// Remove useAuth from the import — it is no longer needed in this file
import { UserButton, useUser } from '@clerk/clerk-react';

// Destructure setManageSubOpen from context
const { openSignIn, profile, setSubModalOpen, setManageSubOpen } = useAuthContext();

// In UserButton.MenuItems, paid-user branch:
<UserButton.Action
    label="Manage subscription"
    labelIcon={<span style={{ fontSize: '0.9rem', lineHeight: 1 }}>⚙</span>}
    onClick={() => setManageSubOpen(true)}
/>
```

Delete the entire `openBillingPortal` function and remove `useAuth` from the
`@clerk/clerk-react` import and `getToken` from the destructure.

---

## 5. Stripe Dashboard prerequisite

No special configuration required. Prorations do not apply because:
- Cancel uses `cancel_at_period_end=True` (no mid-cycle charge).
- Downgrade uses `proration_behavior='none'` on both schedule phases.

**Known limitation — re-upgrade path:** After a downgrade is scheduled, the
`SubscriptionSchedule` remains permanently attached (Phase 2 has no `end_date`). If the
user tries to re-upgrade to Premium, `create_checkout` must release the existing schedule
first. This is a **required follow-on task** and is out of scope for this spec.

---

## 6. Webhook coverage

The `customer.subscription.updated` handler **requires the change in §1a**
to avoid overwriting the two new in-flight statuses. All other handlers are
unchanged and already cover both action paths:

| Stripe event | Handler location | Trigger | Result |
|---|---|---|---|
| `customer.subscription.updated` | `auth/main.py:383` (patched) | Immediately after cancel or downgrade API call | Writes `canceling_at_period_end` or `downgrading_at_period_end` |
| `customer.subscription.updated` | `auth/main.py:383` | Period-end schedule release (plan change fires) | Writes `standard / active` |
| `customer.subscription.deleted` | `auth/main.py:449` | Period-end cancellation fires | Writes `free / cancelled / inactive` |

The optimistic DB updates in the two new endpoints keep the UI current
immediately. The webhook re-applies the same state on arrival; the UPDATE
is a no-op if status is already correct.

---

## 7. File change summary

| File | Change |
|------|--------|
| `database/init.sql` | Extend `chk_subscription_consistency` CHECK to include `'canceling_at_period_end'` and `'downgrading_at_period_end'` (§0) |
| `auth/requirements.txt` | Pin stripe to current installed major: `stripe>=11,<12` to prevent silent breaking upgrades |
| `auth/main.py` (`create_checkout`) | **Follow-on (out of scope):** Release any existing `SubscriptionSchedule` before creating a new checkout session, to prevent double-subscription when re-upgrading after a scheduled downgrade |
| `auth/main.py` | Patch `customer.subscription.updated` handler to check `cancel_at_period_end` and `has_schedule` (§1a) |
| `auth/main.py` | Add `POST /auth/billing/cancel` after line 691 (§1b) |
| `auth/main.py` | Add `POST /auth/billing/downgrade` after line 691 (§1c) |
| `ui/src/components/ManageSubModal.jsx` | Create new file (§2) |
| `ui/src/App.jsx` | Import `ManageSubModal`, add `manageSubOpen` state, render modal (§3) |
| `ui/src/AuthContext.jsx` | Add `manageSubOpen`, `setManageSubOpen` to context value (§3) |
| `ui/src/components/UserMenu.jsx` | Replace `openBillingPortal` with `setManageSubOpen(true)`, remove `useAuth` import (§4) |

**Note on `sub.schedule`:** `stripe.Subscription.retrieve()` returns a `StripeObject`
where all Stripe API response fields are accessible as attributes in stripe-python 2.x+.
`sub.schedule` returns the attached schedule ID string (truthy) or `None` (falsy).
This is safe in all modern stripe-python versions.

**Deployment order:**
1. Pin `stripe>=11,<12` in `auth/requirements.txt` and rebuild the auth image.
2. Apply `database/init.sql` migration.
3. Deploy `auth/` container (webhook patch + new endpoints).
4. Deploy `ui/` container (new modal, wired trigger).
