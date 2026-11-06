CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    assessment_session_id TEXT,
    title TEXT NOT NULL,
    conversation_data JSONB NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX conversations_participant_updated_idx
    ON conversations (participant_id, updated_at);
