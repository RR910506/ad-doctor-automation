# Render Deployment Guide

This project is a Streamlit app. The app entry point is `app.py`, and the Render start command is:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true --browser.gatherUsageStats false
```

## Current Deployment Shape

- Framework: Streamlit.
- Service type: Render Web Service.
- Runtime: Python.
- Service root directory: `ad_doctor`.
- Database today: SQLite through `src/db.py`.
- SQLite storage on Render: persistent disk mounted at `/opt/render/project/src/ad_doctor_data`.
- Database file path on Render: `/opt/render/project/src/ad_doctor_data/ad_doctor.db`.
- Health check path: `/_stcore/health`.

## Required Render Environment Variables

Set these in Render. Do not store real values in code.

```text
AD_DOCTOR_DATABASE_PATH=/opt/render/project/src/ad_doctor_data/ad_doctor.db
AD_DOCTOR_APP_SCHEDULER_ENABLED=1
AD_DOCTOR_SCHEDULER_POLL_SECONDS=60
AD_DOCTOR_SCHEDULER_CATCHUP_HOURS=12
PERPLEXITY_API_KEY=your_key_here
PERPLEXITY_MODEL=sonar-pro
PERPLEXITY_ENDPOINT=https://api.perplexity.ai/v1/sonar
OPENROUTER_API_KEY=your_key_here_if_used
AD_DOCTOR_SMTP_HOST=your_smtp_host_if_email_is_enabled
AD_DOCTOR_SMTP_PORT=587
AD_DOCTOR_SMTP_USERNAME=your_smtp_username_if_email_is_enabled
AD_DOCTOR_SMTP_PASSWORD=your_smtp_password_if_email_is_enabled
AD_DOCTOR_SMTP_FROM=your_sender_email_if_email_is_enabled
AD_DOCTOR_SMTP_USE_TLS=1
```

## PostgreSQL Decision

PostgreSQL is not required for the first Render web deployment because the code currently uses SQLite directly.

Use PostgreSQL later if you need:

- Multiple app instances.
- Stronger concurrency during long scraping runs.
- Managed backups and production-grade database operations.
- Separate Render Cron Jobs that write to the same production data store.

Important: the current code does not use `DATABASE_URL`. Adding `DATABASE_URL` should be part of a deliberate PostgreSQL migration, not a small environment-only change.

## Daily Automation Note

The Streamlit web service now includes an app-level scheduler. It reads the run times saved in the UI and records each scheduled slot in SQLite before running, so a single slot does not trigger twice.

For the current SQLite deployment, use this app scheduler instead of a separate Render Cron Job:

- Set `AD_DOCTOR_APP_SCHEDULER_ENABLED=1`.
- Keep the web service running on a paid Render plan.
- Save one or more run times from `Schedule & Keys` in the app UI.

Render Cron Jobs are still a good later option after migrating to PostgreSQL. Cron jobs cannot use the web service persistent disk, so SQLite is not the right storage if a separate cron job must update the same production data.

## Non-Developer Render Steps

1. Open Render and sign in.
2. Click `New`.
3. Choose `Blueprint`.
4. Connect the GitHub repository `RR910506/ad-doctor-automation`.
5. Select branch `main`.
6. Render should detect `render.yaml` at the repository root.
7. Review the service named `ad-doctor`.
8. Add the secret values Render asks for, especially `PERPLEXITY_API_KEY`.
9. Keep `AD_DOCTOR_DATABASE_PATH` as `/opt/render/project/src/ad_doctor_data/ad_doctor.db`.
10. Deploy the Blueprint only when you are ready.
11. After deploy, open the service URL.
12. If the health check fails, change the Health Check Path to `/` or disable HTTP health checks and use Render's default TCP check.

## Manual Web Service Settings

If you do not use Blueprint, create a Web Service manually:

```text
Repository: RR910506/ad-doctor-automation
Branch: main
Root Directory: ad_doctor
Runtime: Python
Build Command: pip install --upgrade pip && pip install -r requirements.txt
Start Command: streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true --browser.gatherUsageStats false
Health Check Path: /_stcore/health
Persistent Disk Mount Path: /opt/render/project/src/ad_doctor_data
Persistent Disk Size: 5 GB
```

Do not add real API keys to GitHub.
