# Ad Doctor

Ad Doctor is a local-first MVP for advertising intelligence and content creation.

The system will ingest approved advertising and news source links from an XLS file or Google Sheet, fetch articles only from those sources, extract exact news-event topics, qualify topics by source coverage, research locked topics, and generate draft content that requires human approval before export.

## Completed Scope

### Phase 1

Created the base scaffold:

- Streamlit dashboard shell
- SQLite schema
- Python module placeholders
- Template placeholder files
- Requirements and environment example

### Phase 2

Added approved-source input:

- Local `.xls`, `.xlsx`, and `.csv` source import
- Public Google Sheet URL or CSV export import
- Source column validation and normalization
- Upsert into the SQLite `sources` table
- Source list display in the Streamlit dashboard
- Sample source CSV at `data/input/sample_sources.csv`

### Phases 3-10

Added the local MVP pipeline:

- Article fetcher and parser for approved active sources
- Exact news-event topic extraction and matching
- 3 unique-source qualification gate and top-10 prioritization
- Topic locking by run
- Local cited research dossiers
- Configurable campaign dissection from `templates/dissection_roadmap.md`
- Configurable blog and social drafts from template files
- Manual approval queue
- Approved-only export to `data/exports`
- Demo runner that proves the full flow with dummy data
- Daily automation settings and email alert logging

## Product Rules

- Do not randomly search websites for topic discovery.
- Source lists must come only from a user-provided XLS file or Google Sheet.
- The MVP does not automatically publish content.
- Human approval is required before final output.

## Project Structure

```text
ad_doctor/
  app.py
  requirements.txt
  README.md
  .env.example
  data/
    input/
    output/
    exports/
  database/
    ad_doctor.db
    schema.sql
  src/
    config.py
    db.py
    source_manager.py
    article_fetcher.py
    article_parser.py
    topic_extractor.py
    topic_matcher.py
    qualification_engine.py
    prioritization_engine.py
    research_engine.py
    dissection_engine.py
    content_engine.py
    approval_engine.py
    export_engine.py
  templates/
    dissection_roadmap.md
    blog_template.md
    social_post_template.md
  tests/
    test_qualification_engine.py
    test_topic_matching.py
    test_source_import.py
```

## Setup

```powershell
cd ad_doctor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Source List Columns

Required:

- `source_name`
- `website_url`

Optional:

- `rss_url`
- `category`
- `country`
- `credibility_tier`
- `active_status`

`active_status` accepts values like `active`, `inactive`, `yes`, `no`, `true`, `false`, `1`, and `0`.

## Environment

Copy `.env.example` to `.env` when API-backed features are introduced.

```text
OPENAI_API_KEY=
AD_DOCTOR_DATABASE_PATH=database/ad_doctor.db
AD_DOCTOR_SMTP_HOST=
AD_DOCTOR_SMTP_PORT=587
AD_DOCTOR_SMTP_USERNAME=
AD_DOCTOR_SMTP_PASSWORD=
AD_DOCTOR_SMTP_FROM=
AD_DOCTOR_SMTP_USE_TLS=1
```

## Daily Automation And Alerts

Open the `Automation & Alerts` tab in the Streamlit dashboard to:

- Enable or disable daily approved-source scraping.
- Adjust the local run time.
- Set the per-source crawl budget.
- Register users who should receive completion emails.
- Review notification delivery events.

Saving the automation settings creates or updates the local Windows Scheduled Task named `AdDoctorDailyScrape`. The task runs `scripts/run_daily_automation.bat`, which executes the full approved-source pipeline and writes logs to `data/output/daily_automation.log`.

Email delivery requires SMTP settings in `.env`. Until SMTP is configured, notification rows are saved as `skipped` instead of pretending mail was sent.

## Current Status

Phases 1-10 are implemented for the local MVP. Generated content remains pending until a human manually approves it.

## Demo

From the app sidebar, click `Run demo pipeline`, or run:

```powershell
cd ad_doctor
python -B -c "import sys; sys.path.insert(0, '.'); from src.demo_runner import run_demo_pipeline; print(run_demo_pipeline())"
```

The demo seeds approved-source article coverage, extracts topics, applies the 3 unique-source rule, locks topics, builds research/dissection outputs, and creates pending blog/social drafts.

## Approval And Export

Exports only include drafts marked `approved` in the Approval Queue. Pending and rejected drafts are intentionally excluded.
