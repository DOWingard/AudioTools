CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                   VARCHAR(255),
    clerk_id                VARCHAR(255) UNIQUE NOT NULL,
    stripe_id               VARCHAR(255) UNIQUE,
    stripe_subscription_id  VARCHAR(255) UNIQUE,
    subscription_type       VARCHAR(20) NOT NULL DEFAULT 'free'
                            CHECK (subscription_type IN ('free', 'standard', 'premium')),
    subscription_active     BOOLEAN NOT NULL DEFAULT FALSE,
    subscription_status     VARCHAR(20) NOT NULL DEFAULT 'free'
                            CHECK (subscription_status IN ('free', 'active', 'past_due', 'cancelled')),
    daily_free_downloads    INT NOT NULL DEFAULT 3,
    daily_downloads_reset_at TIMESTAMPTZ,
    qdrant_graph_id         VARCHAR(255),
    subscribed_at           TIMESTAMPTZ,
    last_active             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_clerk_id   ON users(clerk_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL AND email <> '';
CREATE INDEX IF NOT EXISTS idx_users_stripe     ON users(stripe_id);
CREATE INDEX IF NOT EXISTS idx_users_stripe_sub ON users(stripe_subscription_id);

-- Prevent contradictory subscription state (issue #21)
ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_subscription_consistency;
ALTER TABLE users ADD CONSTRAINT chk_subscription_consistency CHECK (
    (subscription_active = TRUE  AND subscription_status IN ('active')) OR
    (subscription_active = FALSE AND subscription_status IN ('free', 'past_due', 'cancelled'))
);

-- Subscription event audit log (issue #23)
CREATE TABLE IF NOT EXISTS subscription_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_id      VARCHAR(255),
    stripe_id     VARCHAR(255),
    event_type    VARCHAR(100) NOT NULL,
    stripe_status VARCHAR(50),
    local_status  VARCHAR(50),
    sub_type      VARCHAR(20),
    raw_event_id  VARCHAR(255),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sub_events_clerk  ON subscription_events(clerk_id);
CREATE INDEX IF NOT EXISTS idx_sub_events_stripe ON subscription_events(stripe_id);
