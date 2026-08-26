PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    website_url TEXT NOT NULL,
    rss_url TEXT,
    category TEXT,
    country TEXT,
    credibility_tier INTEGER DEFAULT 1,
    active_status INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (website_url)
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    article_url TEXT NOT NULL,
    headline TEXT,
    published_date TEXT,
    author TEXT,
    meta_description TEXT,
    article_text TEXT,
    fetch_run_id TEXT,
    fetch_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources (id),
    UNIQUE (article_url)
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exact_topic TEXT NOT NULL,
    normalized_topic TEXT NOT NULL,
    representative_headline TEXT,
    status TEXT NOT NULL DEFAULT 'extracted',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (normalized_topic)
);

CREATE TABLE IF NOT EXISTS topic_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    coverage_url TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (topic_id) REFERENCES topics (id),
    FOREIGN KEY (source_id) REFERENCES sources (id),
    FOREIGN KEY (article_id) REFERENCES articles (id),
    UNIQUE (topic_id, source_id, article_id)
);

CREATE TABLE IF NOT EXISTS topic_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    active_source_count INTEGER NOT NULL,
    unique_source_coverage_count INTEGER NOT NULL,
    minimum_required_sources INTEGER NOT NULL,
    coverage_percentage REAL NOT NULL,
    credibility_score REAL DEFAULT 0,
    freshness_score REAL DEFAULT 0,
    data_richness_score REAL DEFAULT 0,
    official_reference_score REAL DEFAULT 0,
    priority_boost_applied INTEGER NOT NULL DEFAULT 0,
    total_score REAL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (topic_id) REFERENCES topics (id),
    UNIQUE (topic_id)
);

CREATE TABLE IF NOT EXISTS locked_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    locked_topic TEXT NOT NULL,
    locked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (topic_id) REFERENCES topics (id),
    UNIQUE (topic_id, run_id)
);

CREATE TABLE IF NOT EXISTS research_dossiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    locked_topic_id INTEGER NOT NULL,
    dossier_json TEXT,
    source_urls_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (locked_topic_id) REFERENCES locked_topics (id),
    UNIQUE (locked_topic_id)
);

CREATE TABLE IF NOT EXISTS dissection_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    locked_topic_id INTEGER NOT NULL,
    template_version TEXT,
    output_markdown TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (locked_topic_id) REFERENCES locked_topics (id)
);

CREATE TABLE IF NOT EXISTS content_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    locked_topic_id INTEGER NOT NULL,
    content_type TEXT NOT NULL CHECK (content_type IN ('blog', 'social')),
    template_version TEXT,
    draft_markdown TEXT,
    approval_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (locked_topic_id) REFERENCES locked_topics (id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_output_id INTEGER,
    approval_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_notes TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_output_id) REFERENCES content_outputs (id)
);

CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_time TEXT NOT NULL DEFAULT '09:00',
    schedule_times TEXT NOT NULL DEFAULT '["09:00"]',
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    task_name TEXT NOT NULL DEFAULT 'AdDoctorDailyScrape',
    notify_on_success INTEGER NOT NULL DEFAULT 1,
    notify_on_failure INTEGER NOT NULL DEFAULT 1,
    fetch_source_time_budget_seconds INTEGER NOT NULL DEFAULT 90,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    display_name TEXT,
    notifications_enabled INTEGER NOT NULL DEFAULT 1,
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (email)
);

CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    recipient_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    smtp_host TEXT,
    smtp_port INTEGER NOT NULL DEFAULT 587,
    smtp_username TEXT,
    smtp_password TEXT,
    smtp_from TEXT,
    use_tls INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_provider_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider_key TEXT NOT NULL DEFAULT 'gemini',
    api_key TEXT,
    model TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
    endpoint TEXT NOT NULL DEFAULT 'https://generativelanguage.googleapis.com',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_writer_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider_key TEXT NOT NULL DEFAULT 'gemini',
    enabled INTEGER NOT NULL DEFAULT 1,
    api_key TEXT,
    model TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
    endpoint TEXT NOT NULL DEFAULT 'https://generativelanguage.googleapis.com',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_framework_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    framework_name TEXT NOT NULL DEFAULT 'The Ad Doctor Diagnostic Carousel',
    brand_worldview TEXT NOT NULL,
    qualification_rule TEXT NOT NULL,
    content_pillars TEXT NOT NULL,
    carousel_structure TEXT NOT NULL,
    tone_rules TEXT NOT NULL,
    psychology_lenses TEXT NOT NULL,
    consumer_lenses TEXT NOT NULL,
    culture_lenses TEXT NOT NULL,
    caption_rules TEXT NOT NULL,
    fact_check_rules TEXT NOT NULL,
    designer_rules TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_key TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    run_id TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    current_phase TEXT,
    current_source TEXT,
    total_sources INTEGER NOT NULL DEFAULT 0,
    completed_sources INTEGER NOT NULL DEFAULT 0,
    failed_sources INTEGER NOT NULL DEFAULT 0,
    articles_scanned INTEGER NOT NULL DEFAULT 0,
    qualified_topics INTEGER NOT NULL DEFAULT 0,
    social_drafts_created INTEGER NOT NULL DEFAULT 0,
    blog_drafts_created INTEGER NOT NULL DEFAULT 0,
    research_calls INTEGER NOT NULL DEFAULT 0,
    tracked_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_credits INTEGER NOT NULL DEFAULT 0,
    heartbeat_at TEXT,
    worker_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (schedule_key)
);

CREATE TABLE IF NOT EXISTS automation_worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'starting',
    message TEXT,
    worker_id TEXT,
    last_heartbeat_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_flags (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Phase 2A domain foundation. Kept additive for existing SQLite databases.
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY, topic_id INTEGER NOT NULL UNIQUE, canonical_topic TEXT NOT NULL,
    brand TEXT, campaign TEXT, content_type TEXT NOT NULL DEFAULT 'unclassified',
    workflow_status TEXT NOT NULL, qualification_status TEXT NOT NULL,
    unique_publication_count INTEGER NOT NULL DEFAULT 0 CHECK (unique_publication_count >= 0),
    qualification_reason TEXT, rejection_reason TEXT, priority_score REAL, current_run_id TEXT,
    evidence_pack_id TEXT, draft_id TEXT, quality_status TEXT NOT NULL DEFAULT 'pending',
    approval_status TEXT NOT NULL DEFAULT 'pending', publication_status TEXT NOT NULL DEFAULT 'not_published',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    FOREIGN KEY (topic_id) REFERENCES topics (id)
);
CREATE TABLE IF NOT EXISTS case_article_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, article_id INTEGER NOT NULL,
    link_role TEXT NOT NULL DEFAULT 'qualifying_evidence', source_id INTEGER,
    contributed_to_qualification INTEGER NOT NULL DEFAULT 0, relevance_score REAL, originating_run_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES articles (id), UNIQUE (case_id, article_id, link_role)
);
CREATE TABLE IF NOT EXISTS evidence_packs (
    evidence_pack_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, schema_version TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1), research_provider TEXT NOT NULL,
    research_model TEXT NOT NULL, research_completed_at TEXT NOT NULL, payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE,
    UNIQUE (case_id, version)
);
CREATE TABLE IF NOT EXISTS workflow_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, transition_id TEXT NOT NULL UNIQUE, case_id TEXT NOT NULL,
    from_status TEXT NOT NULL, to_status TEXT NOT NULL, actor_type TEXT NOT NULL, actor_id TEXT NOT NULL,
    reason TEXT NOT NULL, occurred_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', failed_stage TEXT,
    FOREIGN KEY (case_id) REFERENCES cases (case_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS approval_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, approval_event_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE,
 case_id TEXT NOT NULL, draft_id TEXT NOT NULL, previous_status TEXT NOT NULL, new_status TEXT NOT NULL,
 decision TEXT NOT NULL, reviewer_id TEXT NOT NULL, actor_type TEXT NOT NULL, reviewer_notes TEXT,
 requested_corrections TEXT, created_at TEXT NOT NULL, case_version INTEGER NOT NULL, originating_run_id TEXT,
 metadata_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sources_active_status ON sources (active_status);
CREATE INDEX IF NOT EXISTS idx_articles_source_id ON articles (source_id);
CREATE INDEX IF NOT EXISTS idx_topic_sources_topic_id ON topic_sources (topic_id);
CREATE INDEX IF NOT EXISTS idx_topic_sources_source_id ON topic_sources (source_id);
CREATE INDEX IF NOT EXISTS idx_locked_topics_run_id ON locked_topics (run_id);
CREATE INDEX IF NOT EXISTS idx_app_users_notifications_enabled ON app_users (notifications_enabled);
CREATE INDEX IF NOT EXISTS idx_notification_events_run_id ON notification_events (run_id);
CREATE INDEX IF NOT EXISTS idx_automation_run_history_scheduled_at ON automation_run_history (scheduled_date, scheduled_time);
CREATE INDEX IF NOT EXISTS idx_automation_run_history_status ON automation_run_history (status);
CREATE INDEX IF NOT EXISTS idx_cases_workflow_status ON cases (workflow_status);
CREATE INDEX IF NOT EXISTS idx_cases_qualification_status ON cases (qualification_status);
CREATE INDEX IF NOT EXISTS idx_cases_editorial_filters ON cases (quality_status, approval_status, publication_status);
CREATE INDEX IF NOT EXISTS idx_cases_updated_at ON cases (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_case_article_links_case_id ON case_article_links (case_id);
CREATE INDEX IF NOT EXISTS idx_case_article_links_source_id ON case_article_links (source_id);
CREATE INDEX IF NOT EXISTS idx_case_article_links_run_id ON case_article_links (originating_run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_packs_case_version ON evidence_packs (case_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_transitions_case_time ON workflow_transitions (case_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_approval_events_case_time ON approval_events(case_id,created_at);
CREATE INDEX IF NOT EXISTS idx_approval_events_draft ON approval_events(draft_id);
CREATE INDEX IF NOT EXISTS idx_approval_events_reviewer ON approval_events(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_approval_events_status ON approval_events(new_status);

-- Phase 3A structured Content Packages and immutable Quality Reviews.
CREATE TABLE IF NOT EXISTS content_packages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, content_package_id TEXT NOT NULL UNIQUE, case_id TEXT NOT NULL,
 evidence_pack_id TEXT NOT NULL, draft_id TEXT, schema_version TEXT NOT NULL, package_version INTEGER NOT NULL,
 parent_package_version INTEGER, revision_reason TEXT, content_type TEXT NOT NULL, generation_provider TEXT NOT NULL,
 generation_model TEXT NOT NULL, generated_at TEXT NOT NULL, gate_status TEXT NOT NULL DEFAULT 'not_reviewed',
 overall_score REAL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE, FOREIGN KEY(evidence_pack_id) REFERENCES evidence_packs(evidence_pack_id), UNIQUE(case_id,package_version));
CREATE TABLE IF NOT EXISTS quality_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, quality_review_id TEXT NOT NULL UNIQUE,case_id TEXT NOT NULL,content_package_id TEXT NOT NULL,
 package_version INTEGER NOT NULL,review_version INTEGER NOT NULL,gate_status TEXT NOT NULL,overall_score REAL NOT NULL,
 auditor_provider TEXT NOT NULL,auditor_model TEXT NOT NULL,reviewed_at TEXT NOT NULL,payload_json TEXT NOT NULL,
 FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE,FOREIGN KEY(content_package_id) REFERENCES content_packages(content_package_id) ON DELETE CASCADE,UNIQUE(content_package_id,review_version));
CREATE INDEX IF NOT EXISTS idx_content_packages_case_version ON content_packages(case_id,package_version DESC);
CREATE INDEX IF NOT EXISTS idx_content_packages_draft ON content_packages(draft_id);
CREATE INDEX IF NOT EXISTS idx_content_packages_gate_score ON content_packages(gate_status,overall_score);
CREATE INDEX IF NOT EXISTS idx_content_packages_created ON content_packages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_case ON quality_reviews(case_id);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_package_version ON quality_reviews(content_package_id,review_version DESC);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_gate_score ON quality_reviews(gate_status,overall_score);
CREATE INDEX IF NOT EXISTS idx_quality_reviews_time ON quality_reviews(reviewed_at DESC);

-- Phase 3B controlled publishing, subscribers and delivery.
CREATE TABLE IF NOT EXISTS publications(id INTEGER PRIMARY KEY AUTOINCREMENT,publication_id TEXT NOT NULL UNIQUE,case_id TEXT NOT NULL,content_package_id TEXT NOT NULL,package_version INTEGER NOT NULL,approval_event_id TEXT NOT NULL,quality_review_id TEXT NOT NULL,publication_version INTEGER NOT NULL,publishing_status TEXT NOT NULL,document_provider TEXT NOT NULL,google_document_id TEXT,google_document_url TEXT,google_drive_folder_id TEXT,google_drive_file_id TEXT,document_title TEXT NOT NULL,document_checksum TEXT NOT NULL,visibility TEXT NOT NULL DEFAULT 'private',published_at TEXT,published_by TEXT NOT NULL,originating_run_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,supersedes_publication_id TEXT,metadata_json TEXT NOT NULL DEFAULT '{}',idempotency_key TEXT NOT NULL UNIQUE,distribution_status TEXT NOT NULL DEFAULT 'not_started',FOREIGN KEY(case_id) REFERENCES cases(case_id),FOREIGN KEY(content_package_id) REFERENCES content_packages(content_package_id),UNIQUE(case_id,publication_version));
CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_one_current ON publications(case_id) WHERE publishing_status='published';CREATE INDEX IF NOT EXISTS idx_publications_package ON publications(content_package_id);CREATE INDEX IF NOT EXISTS idx_publications_status ON publications(publishing_status,published_at);
CREATE TABLE IF NOT EXISTS subscribers(subscriber_id TEXT PRIMARY KEY,email TEXT NOT NULL,normalized_email TEXT NOT NULL UNIQUE,name TEXT,status TEXT NOT NULL,consent_status TEXT NOT NULL,consent_source TEXT,consent_timestamp TEXT,content_preferences_json TEXT NOT NULL DEFAULT '[]',frequency TEXT NOT NULL,segment TEXT,locale TEXT NOT NULL,verification_status TEXT NOT NULL,last_sent_at TEXT,unsubscribe_token_hash TEXT,suppression_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS subscriber_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,subscriber_id TEXT NOT NULL,event_type TEXT NOT NULL,occurred_at TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',FOREIGN KEY(subscriber_id) REFERENCES subscribers(subscriber_id));
CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY AUTOINCREMENT,delivery_id TEXT NOT NULL UNIQUE,publication_id TEXT NOT NULL,subscriber_id TEXT,delivery_type TEXT NOT NULL,recipient_hash TEXT NOT NULL,provider TEXT NOT NULL,provider_message_id TEXT,status TEXT NOT NULL,attempt_number INTEGER NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,subject TEXT NOT NULL,template_version TEXT NOT NULL,queued_at TEXT NOT NULL,attempted_at TEXT,delivered_at TEXT,failed_at TEXT,error_category TEXT,safe_error_message TEXT,retry_after TEXT,originating_run_id TEXT,metadata_json TEXT NOT NULL DEFAULT '{}',FOREIGN KEY(publication_id) REFERENCES publications(publication_id),FOREIGN KEY(subscriber_id) REFERENCES subscribers(subscriber_id));
CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_once ON deliveries(publication_id,subscriber_id,delivery_type) WHERE subscriber_id IS NOT NULL;CREATE INDEX IF NOT EXISTS idx_delivery_status_retry ON deliveries(status,retry_after);CREATE INDEX IF NOT EXISTS idx_subscriber_eligibility ON subscribers(status,consent_status,segment);
CREATE TABLE IF NOT EXISTS distribution_runs(run_id TEXT PRIMARY KEY,publication_id TEXT NOT NULL,status TEXT NOT NULL,queued_count INTEGER NOT NULL DEFAULT 0,sent_count INTEGER NOT NULL DEFAULT 0,failed_count INTEGER NOT NULL DEFAULT 0,suppressed_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,FOREIGN KEY(publication_id) REFERENCES publications(publication_id));
CREATE TABLE IF NOT EXISTS external_operations(operation_id TEXT PRIMARY KEY,operation_type TEXT NOT NULL,idempotency_key TEXT NOT NULL,publication_id TEXT,delivery_id TEXT,state TEXT NOT NULL,external_resource_id TEXT,external_url TEXT,sharing_applied INTEGER NOT NULL DEFAULT 0,error_type TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',UNIQUE(operation_type,idempotency_key));CREATE INDEX IF NOT EXISTS idx_external_reconcile ON external_operations(state,operation_type);
