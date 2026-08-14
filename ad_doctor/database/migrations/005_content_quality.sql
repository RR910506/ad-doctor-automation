CREATE TABLE IF NOT EXISTS content_packages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, content_package_id TEXT NOT NULL UNIQUE, case_id TEXT NOT NULL,
 evidence_pack_id TEXT NOT NULL, draft_id TEXT, schema_version TEXT NOT NULL, package_version INTEGER NOT NULL,
 parent_package_version INTEGER, revision_reason TEXT, content_type TEXT NOT NULL, generation_provider TEXT NOT NULL,
 generation_model TEXT NOT NULL, generated_at TEXT NOT NULL, gate_status TEXT NOT NULL DEFAULT 'not_reviewed',
 overall_score REAL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
 FOREIGN KEY(evidence_pack_id) REFERENCES evidence_packs(evidence_pack_id), UNIQUE(case_id,package_version)
);
CREATE TABLE IF NOT EXISTS quality_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, quality_review_id TEXT NOT NULL UNIQUE, case_id TEXT NOT NULL,
 content_package_id TEXT NOT NULL, package_version INTEGER NOT NULL, review_version INTEGER NOT NULL,
 gate_status TEXT NOT NULL, overall_score REAL NOT NULL, auditor_provider TEXT NOT NULL,auditor_model TEXT NOT NULL,
 reviewed_at TEXT NOT NULL,payload_json TEXT NOT NULL,
 FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
 FOREIGN KEY(content_package_id) REFERENCES content_packages(content_package_id) ON DELETE CASCADE,
 UNIQUE(content_package_id,review_version)
);
CREATE INDEX IF NOT EXISTS idx_content_packages_case_version ON content_packages(case_id,package_version DESC);
CREATE INDEX IF NOT EXISTS idx_content_packages_draft ON content_packages(draft_id);
CREATE INDEX IF NOT EXISTS idx_content_packages_gate_score ON content_packages(gate_status,overall_score);
CREATE INDEX IF NOT EXISTS idx_content_packages_created ON content_packages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_case ON quality_reviews(case_id);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_package_version ON quality_reviews(content_package_id,review_version DESC);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_gate_score ON quality_reviews(gate_status,overall_score);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_time ON quality_reviews(reviewed_at DESC);
