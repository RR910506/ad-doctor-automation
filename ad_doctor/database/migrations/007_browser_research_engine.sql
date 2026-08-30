ALTER TABLE sources ADD COLUMN domain TEXT;
ALTER TABLE sources ADD COLUMN publisher_network TEXT;
ALTER TABLE sources ADD COLUMN crawl_frequency_minutes INTEGER NOT NULL DEFAULT 60;
ALTER TABLE sources ADD COLUMN crawl_priority INTEGER NOT NULL DEFAULT 100;
ALTER TABLE sources ADD COLUMN browser_policy TEXT NOT NULL DEFAULT 'fallback';
ALTER TABLE sources ADD COLUMN last_crawl TEXT;
ALTER TABLE sources ADD COLUMN last_success TEXT;
ALTER TABLE sources ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE articles ADD COLUMN canonical_url TEXT;
ALTER TABLE articles ADD COLUMN content_hash TEXT;
ALTER TABLE articles ADD COLUMN extraction_method TEXT;

CREATE TABLE IF NOT EXISTS browser_crawl_memory (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL, url TEXT NOT NULL,
 canonical_url TEXT NOT NULL UNIQUE, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 last_fetched TEXT, content_hash TEXT, http_status INTEGER, extraction_method TEXT,
 extraction_status TEXT NOT NULL, published_at TEXT, author TEXT, publisher_network TEXT,
 FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS browser_worker_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, sources_monitored INTEGER NOT NULL DEFAULT 0,
 sources_checked INTEGER NOT NULL DEFAULT 0, urls_discovered INTEGER NOT NULL DEFAULT 0,
 articles_fetched INTEGER NOT NULL DEFAULT 0, http_extractions INTEGER NOT NULL DEFAULT 0,
 browser_fallbacks INTEGER NOT NULL DEFAULT 0, unchanged_skipped INTEGER NOT NULL DEFAULT 0,
 extraction_failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS research_documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, source_url TEXT NOT NULL,
 canonical_url TEXT NOT NULL, page_title TEXT NOT NULL, publication TEXT NOT NULL,
 publisher_network TEXT NOT NULL, retrieved_at TEXT NOT NULL, normalized_text TEXT NOT NULL,
 content_hash TEXT NOT NULL, extraction_method TEXT NOT NULL, provenance TEXT NOT NULL,
 UNIQUE(case_id,canonical_url,content_hash), FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

UPDATE research_provider_settings
SET provider_key='browser', model='approved-source-browser', endpoint='', updated_at=CURRENT_TIMESTAMP
WHERE provider_key='gemini' AND model='gemini-2.5-flash';

UPDATE content_writer_settings
SET model='gemini-3.7-flash', updated_at=CURRENT_TIMESTAMP
WHERE provider_key='gemini' AND model='gemini-2.5-flash';
