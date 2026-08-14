CREATE TABLE IF NOT EXISTS approval_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, approval_event_id TEXT NOT NULL UNIQUE,
 idempotency_key TEXT NOT NULL UNIQUE, case_id TEXT NOT NULL, draft_id TEXT NOT NULL,
 previous_status TEXT NOT NULL, new_status TEXT NOT NULL, decision TEXT NOT NULL,
 reviewer_id TEXT NOT NULL, actor_type TEXT NOT NULL, reviewer_notes TEXT,
 requested_corrections TEXT, created_at TEXT NOT NULL, case_version INTEGER NOT NULL,
 originating_run_id TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
 FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_approval_events_case_time ON approval_events(case_id,created_at);
CREATE INDEX IF NOT EXISTS idx_approval_events_draft ON approval_events(draft_id);
CREATE INDEX IF NOT EXISTS idx_approval_events_reviewer ON approval_events(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_approval_events_status ON approval_events(new_status);

