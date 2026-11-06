CREATE TABLE assessment_sessions (
    session_id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    domain_version TEXT NOT NULL,
    domain_content_hash TEXT NOT NULL,
    session_data JSONB NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX assessment_sessions_participant_created_idx
    ON assessment_sessions (participant_id, created_at);

CREATE TABLE pilot_feedback (
    feedback_id UUID PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES assessment_sessions(session_id) ON DELETE CASCADE,
    participant_id TEXT NOT NULL,
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (comment IS NULL OR length(comment) <= 2000)
);
