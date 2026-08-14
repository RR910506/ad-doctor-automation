-- Phase 2A: additive Case File domain foundation.
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    topic_id INTEGER NOT NULL UNIQUE,
    canonical_topic TEXT NOT NULL,
    brand TEXT,
    campaign TEXT,
    content_type TEXT NOT NULL DEFAULT 'unclassified',
    workflow_status TEXT NOT NULL,
    qualification_status TEXT NOT NULL,
    unique_publication_count INTEGER NOT NULL DEFAULT 0 CHECK (unique_publication_count >= 0),
    qualification_reason TEXT,
    rejection_reason TEXT,
    priority_score REAL,
    current_run_id TEXT,
    evidence_pack_id TEXT,
    draft_id TEXT,
    quality_status TEXT NOT NULL DEFAULT 'pending',
    approval_status TEXT NOT NULL DEFAULT 'pending',
    publication_status TEXT NOT NULL DEFAULT 'not_published',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    FOREIGN KEY (topic_id) REFERENCES topics (id)
);

CREATE TABLE IF NOT EXISTS case_article_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    article_id INTEGER NOT NULL,
    link_role TEXT NOT NULL DEFAULT 'qualifying_evidence',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES articles (id),
    UNIQUE (case_id, article_id, link_role)
);

CREATE TABLE IF NOT EXISTS evidence_packs (
    evidence_pack_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    research_provider TEXT NOT NULL,
    research_model TEXT NOT NULL,
    research_completed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE,
    UNIQUE (case_id, version)
);

CREATE TABLE IF NOT EXISTS workflow_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id TEXT NOT NULL UNIQUE,
    case_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    failed_stage TEXT,
    FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cases_workflow_status ON cases (workflow_status);
CREATE INDEX IF NOT EXISTS idx_cases_qualification_status ON cases (qualification_status);
CREATE INDEX IF NOT EXISTS idx_cases_editorial_filters ON cases (quality_status, approval_status, publication_status);
CREATE INDEX IF NOT EXISTS idx_cases_updated_at ON cases (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_case_article_links_case_id ON case_article_links (case_id);
CREATE INDEX IF NOT EXISTS idx_evidence_packs_case_version ON evidence_packs (case_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_transitions_case_time ON workflow_transitions (case_id, occurred_at);

