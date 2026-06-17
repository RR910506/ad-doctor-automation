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
    fetch_source_time_budget_seconds INTEGER NOT NULL DEFAULT 180,
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

CREATE TABLE IF NOT EXISTS automation_run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_key TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    run_id TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (schedule_key)
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
