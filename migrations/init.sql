-- Banister — schéma initial complet
-- Ce fichier est exécuté automatiquement par postgres:16-alpine au premier démarrage
-- (docker-entrypoint-initdb.d). Il consolide toutes les migrations de Banister
-- et applique les simplifications single-user (pas de table onboarding_state,
-- pas de colonnes disclaimer/onboarding dans users).

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- ── users ──────────────────────────────────────────────────────────────────────
-- Une seule ligne après /setup. telegram_id sert de vérification d'identité.
CREATE TABLE IF NOT EXISTS users (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id             BIGINT      NOT NULL UNIQUE,
    username                VARCHAR(64),
    first_name              VARCHAR(128),
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    reminders_enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
    reminder_hour           SMALLINT    NOT NULL DEFAULT 7,
    reminder_minute         SMALLINT    NOT NULL DEFAULT 30,
    reminder_last_sent_at   DATE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);

-- ── athlete_profiles ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS athlete_profiles (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    profile         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    coach_memory    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    athlete_notes   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN athlete_profiles.coach_memory IS
    'Notes évolutives du coach — liste [{date, category, note}], max 15 entrées.';
COMMENT ON COLUMN athlete_profiles.athlete_notes IS
    'Profil enrichi stable — dict clés libres (style, physique, pattern, historique_events…).';

-- ── training_plans ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS training_plans (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    block_number    INTEGER     NOT NULL DEFAULT 1,
    start_date      DATE        NOT NULL,
    end_date        DATE        NOT NULL,
    plan_technical  JSONB       NOT NULL,
    plan_narrative  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_training_plans_user_id ON training_plans (user_id);

-- ── oauth_connections ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS oauth_connections (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider            VARCHAR(32) NOT NULL,
    access_token        TEXT        NOT NULL,
    refresh_token       TEXT        NOT NULL,
    token_expires_at    TIMESTAMPTZ NOT NULL,
    provider_user_id    VARCHAR(64) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_oauth_user_provider UNIQUE (user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_oauth_connections_user_id ON oauth_connections (user_id);

-- ── session_logs ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS session_logs (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id                     UUID        NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
    week_number                 SMALLINT    NOT NULL,
    day_of_week                 SMALLINT    NOT NULL,
    logged_date                 DATE        NOT NULL,
    status                      VARCHAR(16) NOT NULL,   -- 'done' | 'skipped' | 'unplanned'
    rpe_emoji                   VARCHAR(8),
    duration_minutes_actual     SMALLINT,
    tss_actual                  FLOAT,
    strava_activity_id          BIGINT,
    source                      VARCHAR(16) NOT NULL DEFAULT 'manual',  -- 'manual' | 'strava'
    -- Données physiologiques (Strava)
    avg_heart_rate              SMALLINT,
    avg_power                   SMALLINT,
    normalized_power            SMALLINT,
    kilojoules                  FLOAT,
    environment                 VARCHAR(16),             -- 'outdoor' | 'indoor'
    time_in_zones_s             JSONB,                  -- {"Z2": 1800, "Z3": 900}
    -- Métriques qualité (analyse SessionAnalyzer)
    cardiac_drift_index         FLOAT,
    intervals_consistency_index FLOAT,
    respect_zones_score         FLOAT,
    session_type_real           VARCHAR(16),
    variability_index           FLOAT,
    intensity_factor            FLOAT,
    dominant_zone               VARCHAR(4),
    elevation_gain_m            FLOAT,
    average_temp_c              FLOAT,
    athlete_count               SMALLINT,
    -- Snapshots fitness au moment du webhook
    ctl_at_session              FLOAT,
    atl_at_session              FLOAT,
    tsb_at_session              FLOAT,
    -- KPI d'adhérence
    kpi_contribution            FLOAT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_logs_user_date
    ON session_logs (user_id, logged_date);

CREATE INDEX IF NOT EXISTS idx_session_logs_plan_id
    ON session_logs (plan_id);

CREATE INDEX IF NOT EXISTS idx_session_logs_strava_activity
    ON session_logs (strava_activity_id)
    WHERE strava_activity_id IS NOT NULL;

COMMENT ON COLUMN session_logs.status IS 'done | skipped | unplanned';
COMMENT ON COLUMN session_logs.kpi_contribution IS
    'Points KPI gagnés pour cette séance (0..~2.0). NULL si non calculé.';

-- ── activities ────────────────────────────────────────────────────────────────
-- Historique Strava (indépendant des plans, utilisé pour calculer CTL/ATL seed)
CREATE TABLE IF NOT EXISTS activities (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source              VARCHAR(16) NOT NULL DEFAULT 'strava',
    source_activity_id  BIGINT,
    activity_date       DATE        NOT NULL,
    duration_seconds    INTEGER,
    sport_type          VARCHAR(32),
    environment         VARCHAR(16),
    distance_meters     FLOAT,
    elevation_gain_meters FLOAT,
    avg_watts           FLOAT,
    normalized_watts    FLOAT,
    device_watts        BOOLEAN,
    avg_heartrate       FLOAT,
    max_heartrate       FLOAT,
    suffer_score        SMALLINT,
    kilojoules          FLOAT,
    tss                 FLOAT,
    tss_method          VARCHAR(16),
    ftp_used            SMALLINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activities_user_date
    ON activities (user_id, activity_date DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_source_id
    ON activities (user_id, source, source_activity_id)
    WHERE source_activity_id IS NOT NULL;

-- ── chat_messages ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_messages (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        VARCHAR(16) NOT NULL,          -- 'user' | 'assistant'
    content     TEXT        NOT NULL,
    intent      VARCHAR(64),
    tool_used   VARCHAR(64),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_user_created
    ON chat_messages (user_id, created_at DESC);

-- ── weekly_adherence ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weekly_adherence (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id           UUID        REFERENCES training_plans(id) ON DELETE SET NULL,
    week_number       SMALLINT,
    week_start_date   DATE        NOT NULL,
    sessions_done     SMALLINT    NOT NULL,
    sessions_planned  SMALLINT,
    compliance_pct    FLOAT,
    tss_7d            FLOAT       NOT NULL,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_weekly_adherence_user_week UNIQUE (user_id, week_start_date)
);

CREATE INDEX IF NOT EXISTS idx_weekly_adherence_user_date
    ON weekly_adherence (user_id, week_start_date DESC);
