# Phase 2A Domain Foundation

Phase 2A adds provider-independent Case File, Evidence Pack and workflow contracts without switching the existing pipeline to them. `AD_DOCTOR_CASE_FILES_ENABLED` defaults to `false`; existing Streamlit, scheduling, Google and email behaviour is unchanged.

## Activation and backfill

Apply `database/migrations/002_case_file_foundation.sql` only through the explicit migration helper and against a backed-up database. Preview legacy mapping with `backfill_case_files(path, dry_run=True)`. A write backfill records one migration audit event per created Case File and is idempotent by unique `topic_id`.

## Rollback guidance

The migration is additive. Application rollback requires only disabling the feature flag. If physical removal is ever required, first export `cases`, `case_article_links`, `evidence_packs` and `workflow_transitions`; then remove those tables in reverse foreign-key order during an approved maintenance window. Phase 2A contains no automatic destructive rollback.

## Boundaries

Domain models contain validation only. Repository protocols define persistence needs. SQLite adapters contain parameterized SQL. The compatibility module maps existing rows without changing the legacy pipeline. No API credentials are stored in the new tables.
