CREATE TABLE chat_states (
    owner TEXT PRIMARY KEY,
    chat_data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
