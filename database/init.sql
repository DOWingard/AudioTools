CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                   VARCHAR(255) UNIQUE NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_users_email      ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_stripe     ON users(stripe_id);
CREATE INDEX IF NOT EXISTS idx_users_stripe_sub ON users(stripe_subscription_id);
