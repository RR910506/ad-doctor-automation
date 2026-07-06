# Render Production Deployment Guide

Ad Doctor production now uses an always-on Render service that starts two separate processes:

- Streamlit web UI for Dashboard, Content Studio, Control Room, and Arsenal.
- Scheduler worker for queued daily automation runs and worker heartbeats.

This keeps the UI independent from the scheduler loop while allowing both processes to share one SQLite database on the same Render persistent disk.

## Production Architecture

- Render service type: Web Service.
- Render instance type: Starter or higher.
- Render root directory: `ad_doctor`.
- Render start command:

```bash
python start_render.py
```

- Database path:

```text
/opt/render/project/src/ad_doctor_data/ad_doctor.db
```

- Persistent disk:

```text
name: ad-doctor-data
mountPath: /opt/render/project/src/ad_doctor_data
sizeGB: 1
```

- Scheduler mode:

```text
AD_DOCTOR_SCHEDULER_MODE=worker
```

The web process does not run the scheduler loop. The separate `src.scheduler_worker` process queues every saved timestamp, runs one automation at a time, writes heartbeat/progress fields, and marks interrupted runs as recovered or timed out.

## Required Render Environment Variables

Set these in Render:

```text
AD_DOCTOR_DATABASE_PATH=/opt/render/project/src/ad_doctor_data/ad_doctor.db
AD_DOCTOR_DRAFT_STORE=google
AD_DOCTOR_CLEAR_SQLITE_GENERATED_DATA_FOR_GOOGLE=0
AD_DOCTOR_APP_SCHEDULER_ENABLED=0
AD_DOCTOR_SCHEDULER_MODE=worker
AD_DOCTOR_SCHEDULER_POLL_SECONDS=30
AD_DOCTOR_SCHEDULER_CATCHUP_HOURS=24
AD_DOCTOR_WORKER_STALE_SECONDS=900
AD_DOCTOR_JOB_TIMEOUT_SECONDS=10800
PERPLEXITY_MODEL=sonar-pro
PERPLEXITY_ENDPOINT=https://api.perplexity.ai/v1/sonar
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
GOOGLE_DRAFTS_WORKSHEET_NAME=Drafts
```

Set these secrets when available:

```text
PERPLEXITY_API_KEY
OPENROUTER_API_KEY
OPENAI_API_KEY
GOOGLE_SERVICE_ACCOUNT_JSON
GOOGLE_DRAFTS_SPREADSHEET_ID
GOOGLE_DRAFTS_FOLDER_ID
AD_DOCTOR_SMTP_HOST
AD_DOCTOR_SMTP_USERNAME
AD_DOCTOR_SMTP_PASSWORD
AD_DOCTOR_SMTP_FROM
```

If `PERPLEXITY_API_KEY` is missing, AI research is disabled and the app clearly shows that it is using local approved-source synthesis only.

If `OPENAI_API_KEY` is missing, ChatGPT writing is disabled and the app uses the local Ad Doctor framework generator.

## Render Blueprint Steps

1. Open Render.
2. Choose `New` -> `Blueprint`.
3. Connect `RR910506/ad-doctor-automation`.
4. Select branch `main`.
5. Render should detect `render.yaml`.
6. Confirm the `ad-doctor` service.
7. Keep the Starter plan or higher.
8. Confirm the persistent disk.
9. Add the required environment variables and secrets.
10. Deploy.

## Manual Web Service Settings

If not using Blueprint:

```text
Repository: RR910506/ad-doctor-automation
Branch: main
Root Directory: ad_doctor
Runtime: Python
Instance Type: Starter or higher
Build Command: pip install --upgrade pip && pip install -r requirements.txt
Start Command: python start_render.py
Health Check Path: /_stcore/health
Persistent Disk: /opt/render/project/src/ad_doctor_data
```

## Production Checks

After deployment, verify:

- Dashboard worker status shows `Healthy`.
- Dashboard last heartbeat is recent.
- Control Room shows `Scheduler mode: Worker`.
- Saved `09:00 Asia/Kolkata` run appears in upcoming triggers.
- Old Google-backed Content Studio batches still appear.
- Missing Perplexity/OpenRouter keys show `AI research disabled`.
- Missing OpenAI key shows `Content writing AI disabled`.
- Arsenal shows source start, source complete, source failed, and source timed out events with expandable metadata.

## PostgreSQL Upgrade Note

This setup intentionally keeps web and worker processes inside the same Render service so SQLite can stay safe on one persistent disk. If you later want a physically separate Render Worker service, migrate the shared state database to PostgreSQL first because Render persistent disks are not shared across services.
