# Run Ad Doctor without Codex

Streamlit is the private user interface. An independent worker runs the same
collection, research, writing and quality stages and saves results for review.
Research, writing and quality models are selected separately in AI settings.
Gemini is the temporary test provider; no stage is permanently tied to Gemini.
Human approval is always required before publishing or email delivery.

## One-time private shared storage setup

1. Create a free project at https://supabase.com/dashboard.
2. In its SQL Editor, run `deployment/supabase_automation.sql` from this repository.
3. Copy the project URL and server-side `service_role` key. Keep the key private;
   never paste it in chat, commit it, or use it in browser code.
4. Add these server secrets in Streamlit **Manage app → Settings → Secrets**:

   ```toml
   SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
   SUPABASE_SERVICE_ROLE_KEY = "YOUR_PRIVATE_SERVICE_ROLE_KEY"
   ```

5. Save/reboot Streamlit. The app should report shared storage connected.
   Set the desired daily India time in Automation and enable the saved schedule.

Without shared storage, the app uses clearly labelled local SQLite trial mode.
Browser refreshes retain records, but a free Streamlit host restart may erase
them. Local state cannot be shared with the independent cloud worker.

## One-time scheduled worker setup

1. In GitHub repository **Settings → Secrets and variables → Actions → Secrets**,
   add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, using the same project as
   Streamlit.
2. Add only the API keys for providers you actually select:

   | Provider | Secret |
   |---|---|
   | Google Gemini | `GEMINI_API_KEY` |
   | OpenAI API | `OPENAI_API_KEY` |
   | Perplexity API | `PERPLEXITY_API_KEY` |
   | Anthropic Claude | `ANTHROPIC_API_KEY` |
   | Compatible API endpoint | `OPENAI_COMPATIBLE_API_KEY` |

   For a compatible endpoint, also set Actions variable
   `OPENAI_COMPATIBLE_BASE_URL` to its HTTPS API base URL. Put the same settings
   into Streamlit Secrets. Consumer ChatGPT Plus or free Perplexity website
   subscriptions do not supply API access or API credits.
3. The new workflow `.github/workflows/ad-doctor-automation.yml` must exist on
   the repository's default branch before GitHub runs scheduled triggers.
   A workflow present only on a development branch does not activate cron.
4. Under **Actions → Ad Doctor scheduled automation**, run the workflow manually
   once and confirm its saved run is visible after refreshing Streamlit.
5. Under **Actions → Variables**, add `AD_DOCTOR_AUTOMATION_ENABLED` with value
   `true`. The daily schedule inside the app must also be enabled.

No credentials are copied automatically between Codex, Streamlit and Actions.
Do not send keys in chat. Removing the enable variable pauses scheduled jobs.
The legacy manual Perplexity/Google workflow is separate; it is not used here.

## Runtime behavior and limits

The worker checks the saved India-time schedule every 30 minutes. A lightweight
read-only probe skips expensive app/browser installation when there is no due
work. Shared claims prevent repeating the same daily slot. Transient provider
failures may receive one delayed retry, with at most two attempts per daily
slot; successful stage checkpoints are reused. Permanent quota, key and
evidence errors remain visible instead of silently switching providers.

GitHub may delay scheduled jobs. Free Actions minutes, Supabase storage and API
quotas are controlled by those providers; free tiers do not guarantee exact
times or uninterrupted availability. Watch the app's last successful run.
No unattended publishing, email or private data commits occur in this worker.

To run elsewhere, from `ad_doctor/` with dependencies/browser installed:

```sh
python automation_worker.py          # check the saved schedule once
python automation_worker.py --force  # explicit immediate run
```

Use your host's scheduler to call the first command periodically. Credentials
belong in server secret settings. `--allow-local` is for development or a host
with a mounted persistent disk; set `AD_DOCTOR_STATE_DATABASE` and
`AD_DOCTOR_COLLECTION_DATABASE` to separate writable private files.

## Deployment acceptance

- A manual run survives reopening the Streamlit app and appears from both web
  and worker processes.
- Three independent approved publisher groups support each eligible topic.
- Unread/blocked pages and queue limits appear in source coverage reports.
- Changing the research provider leaves writing and quality selections intact.
- Provider failures are recorded without invented sample content.
- Drafts stay pending human approval; approval and delivery are separate actions.
- A scheduled run executes with Streamlit closed and creates no duplicate calls
  when the same slot is checked again.

These checks require actual shared storage and provider credentials. Passing
automated tests alone does not establish that hosted deployment is complete.
