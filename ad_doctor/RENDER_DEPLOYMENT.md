# Render Free Deployment Guide

This project supports a no-paid-Render setup:

- Render Free hosts the Streamlit review UI.
- GitHub Actions runs the daily automation on a schedule.
- The GitHub Action commits the updated SQLite database back to `main`.
- Render redeploys from `main`, so the UI shows the latest generated drafts.

This design is required because Render Free web services sleep after idle time and cannot attach persistent disks.

## Current Free Architecture

- Render service type: Web Service.
- Render instance type: Free.
- Render root directory: `ad_doctor`.
- Render start command:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true --browser.gatherUsageStats false
```

- Daily runner: `.github/workflows/daily-ad-doctor.yml`.
- Database path: `ad_doctor/database/ad_doctor.db`.
- Scheduler mode: `external`.

## Required Render Environment Variables

Set these in Render:

```text
AD_DOCTOR_DATABASE_PATH=database/ad_doctor.db
AD_DOCTOR_APP_SCHEDULER_ENABLED=0
AD_DOCTOR_SCHEDULER_MODE=external
PERPLEXITY_MODEL=sonar-pro
PERPLEXITY_ENDPOINT=https://api.perplexity.ai/v1/sonar
```

Optional in Render:

```text
PERPLEXITY_API_KEY=your_key_here
OPENROUTER_API_KEY=your_key_here_if_used
```

The daily GitHub Action needs the same provider secrets to generate higher-quality research-backed drafts.

## Required GitHub Secrets

In GitHub, open the repository settings and add:

```text
PERPLEXITY_API_KEY
OPENROUTER_API_KEY
```

Optional:

```text
RENDER_DEPLOY_HOOK_URL
```

If `RENDER_DEPLOY_HOOK_URL` is set, the daily workflow triggers Render after committing new database changes. If it is not set, Render must have auto-deploy enabled for the `main` branch.

## GitHub Actions Schedule

The workflow runs daily at:

```text
03:45 UTC = 09:15 Asia/Kolkata
```

The app default schedule is `09:00 Asia/Kolkata`, so this gives the scheduled slot time to become due before the workflow claims it.

You can also run it manually from GitHub:

```text
Actions -> Daily Ad Doctor Automation -> Run workflow
```

## Non-Developer Render Steps

1. Open Render and sign in.
2. Click `New`.
3. Choose `Blueprint`.
4. Connect the GitHub repository `RR910506/ad-doctor-automation`.
5. Select branch `main`.
6. Render should detect `render.yaml` at the repository root.
7. Confirm the service named `ad-doctor`.
8. Choose the Free instance type if Render asks.
9. Add the environment variables listed above.
10. Deploy the Blueprint.
11. Make sure auto-deploy from `main` is enabled, or add a deploy hook URL to GitHub as `RENDER_DEPLOY_HOOK_URL`.

## Manual Web Service Settings

If you do not use Blueprint, create a Web Service manually:

```text
Repository: RR910506/ad-doctor-automation
Branch: main
Root Directory: ad_doctor
Runtime: Python
Instance Type: Free
Build Command: pip install --upgrade pip && pip install -r requirements.txt
Start Command: streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true --browser.gatherUsageStats false
Health Check Path: /_stcore/health
```

## Important Free-Plan Limits

- Render Free services sleep after idle time.
- Render Free services cannot use persistent disks.
- UI changes made while the app is running can be lost after sleep or redeploy because the database is repo-backed.
- Daily generated drafts are persisted because GitHub Actions commits the updated database to GitHub.
- Some source sites may block datacenter traffic with `403 Forbidden`; those sources will be logged as failures while other sources continue.
- Render Free blocks outbound SMTP ports such as `587`, so email alerts should not be treated as reliable on Free Render.

## Paid Upgrade Path

If you later move to a paid Render plan, use the older always-on worker design:

- Set `plan: starter`.
- Add a persistent disk at `/opt/render/project/src/ad_doctor_data`.
- Set `AD_DOCTOR_DATABASE_PATH=/opt/render/project/src/ad_doctor_data/ad_doctor.db`.
- Set `AD_DOCTOR_SCHEDULER_MODE=worker`.
- Use `startCommand: python start_render.py`.

That paid setup allows approvals and generated data to persist directly on Render without GitHub Actions committing the database.
