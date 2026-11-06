CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    profile_data JSONB NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (participant_id, domain_id)
);
