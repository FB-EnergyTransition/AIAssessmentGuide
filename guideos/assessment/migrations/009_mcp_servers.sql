CREATE TABLE mcp_servers (
    server_id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL,
    name TEXT NOT NULL,
    server_data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (domain_id, name)
);
