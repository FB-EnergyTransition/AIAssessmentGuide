CREATE TABLE knowledge_documents (
    document_id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL,
    document_data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX knowledge_documents_domain_idx
    ON knowledge_documents (domain_id, created_at);
