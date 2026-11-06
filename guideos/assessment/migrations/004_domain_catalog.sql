CREATE TABLE domain_pack_drafts (
    domain_id TEXT NOT NULL,
    version TEXT NOT NULL,
    pack_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (domain_id, version)
);

CREATE TABLE domain_active_versions (
    domain_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
