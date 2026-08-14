ALTER TABLE case_article_links ADD COLUMN source_id INTEGER;
ALTER TABLE case_article_links ADD COLUMN contributed_to_qualification INTEGER NOT NULL DEFAULT 0;
ALTER TABLE case_article_links ADD COLUMN relevance_score REAL;
ALTER TABLE case_article_links ADD COLUMN originating_run_id TEXT;
CREATE INDEX IF NOT EXISTS idx_case_article_links_source_id ON case_article_links (source_id);
CREATE INDEX IF NOT EXISTS idx_case_article_links_run_id ON case_article_links (originating_run_id);

