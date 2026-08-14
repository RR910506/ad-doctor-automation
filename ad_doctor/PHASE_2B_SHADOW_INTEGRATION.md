# Phase 2B Shadow Integration

Phase 2B connects qualification, research and content generation to the Case File foundation without changing legacy reads or outputs.

## Modes

`AD_DOCTOR_CASE_FILES_MODE=off` is the default. `shadow` dual-writes Case state while legacy tables remain authoritative. `active` is reserved and currently behaves like shadow. The Phase 2A boolean flag still maps `true` to shadow for compatibility.

Shadow failures are written to `run_logs` with run ID, topic ID, stage and error type. They do not roll back successful legacy pipeline output.

## Atomic lifecycle

`CaseLifecycleService` uses `BEGIN IMMEDIATE`. A Case status/version update and its workflow transition are committed together. Evidence insertion plus Case linkage plus the `RESEARCH_COMPLETE` transition are also atomic.

## Evidence

`PerplexityEvidenceAdapter` performs no API call. It normalizes and deduplicates URLs, distinguishes qualifying publications, preserves provider citations, and rejects verified facts without catalogued source support.

## Consistency

`check_shadow_consistency(path)` is read-only. It reports missing/mismatched Cases, publication counts, Evidence Packs, draft links, workflow stages and duplicates. It never repairs data.
