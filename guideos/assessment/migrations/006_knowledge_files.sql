CREATE TABLE knowledge_files (
    content_hash TEXT PRIMARY KEY,
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
