# Ad Doctor — private working app

The existing second Streamlit URL remains valid. Its `ad_doctor/gemini_app.py`
entrypoint now forwards to the same provider-independent app as the new
`ad_doctor/ad_doctor_app.py` entrypoint. You do not need to create a third app.
The first public interface remains a separate sample walkthrough.

## Normal use

1. Sign in using the existing workspace password.
2. **Today → Collect stories & prepare content** reads the approved publications,
   groups recent developments, and runs research, writing and quality review.
   No manual topic or source entry is required.
3. **My content** shows Article, Carousel and Expert details. Review and approve
   a quality-passed draft, or reject it. Failed evidence/quality never becomes
   sample content or automatic approval.
4. **Publishing** downloads approved content, creates a private Google document
   if configured, or sends explicitly approved messages to consenting recipients
   if SMTP is configured. Subscriber consent and suppression are saved. Delivery
   is never part of an unattended run.
5. **Expert workspace** contains AI settings, Model Lab, Automation and Source
   coverage. Technical reports stay separate from daily content.

## Choose AI models independently

In **AI settings**, select a provider and exact model ID for each stage, then
save. Research, writing/strategy and quality review can all use different models.
Gemini is the temporary testing configuration; it is not the identity of the app.

| Provider | Secret in Streamlit / worker | Access |
|---|---|---|
| Gemini | `GEMINI_API_KEY` | Already supplied for testing |
| OpenAI | `OPENAI_API_KEY` | Supply when ready to use OpenAI models |
| Perplexity | `PERPLEXITY_API_KEY` | Supply when ready; enter an account-supported Sonar model ID |
| Claude | `ANTHROPIC_API_KEY` | Supply when ready to use Claude |
| Compatible API | `OPENAI_COMPATIBLE_API_KEY` and `OPENAI_COMPATIBLE_BASE_URL` | HTTPS endpoint supporting chat completions |

Keys go in **Streamlit Manage app → Settings → Secrets**, never in source code,
chat, model prompts or database records. `AD_DOCTOR_` key-name aliases are also
supported where the development platform reserves `OPENAI_` names.

Model listing is an explicit check and does not guarantee inference access or
free quota. The Model Lab records actual responses, usage and latency against a
shared brief; its experiments remain unverified and cannot approve evidence.
Consumer ChatGPT Plus/Perplexity subscriptions do not automatically supply API
access. No provider/model is switched silently when a request fails.

Research in this release analyzes collected approved-source text. The crawler
performs discovery and extraction. External model search results do not become
validated evidence without an exact supporting quotation in that collected text.

## Remaining owner requirements

1. **Durable private storage:** create a free Supabase project and run
   `deployment/supabase_automation.sql`; add `SUPABASE_URL` and
   `SUPABASE_SERVICE_ROLE_KEY` to Streamlit. Full steps:
   [AUTOMATION_SETUP.md](AUTOMATION_SETUP.md).
2. **Unattended worker:** place the workflow on GitHub's default branch; add the
   same storage secrets and the selected providers' keys in GitHub Actions;
   enable `AD_DOCTOR_AUTOMATION_ENABLED=true`. Save the daily India time in the
   app. Streamlit alone cannot reliably schedule jobs while sleeping.
3. **Other AI providers:** add only the keys you choose to use. Their live calls
   need validation with your account after keys are supplied. Gemini can remain
   selected while those keys are pending.
4. **Google Docs / email, when needed:** supply authorized Google OAuth and SMTP
   access; see [PUBLISHING_SETUP.md](PUBLISHING_SETUP.md). Neither was live-tested
   and no real documents or emails were created during development.

Local SQLite trial mode works without Supabase but cannot guarantee survival
across free Streamlit host restarts. The dashboard labels this limitation.
Provider free quotas, Actions minutes and Supabase capacity are service limits,
not guarantees. The app does not automatically buy services or enable billing.

## Collection boundaries

Each run has a time/page budget per source. The frontier saves discovered unread
URLs, retries transient failures with backoff, and reports robots rules, access
blocks, paywalls, extraction failures and queue overflow. Browser rendering is
used for public JavaScript content; it does not bypass access controls.

At least three independent approved publisher groups must cover the same recent
development with full text and a parseable publication date. If none qualify,
collection is still saved and the app explains why no draft was produced.
Complete coverage of every publisher article cannot be guaranteed.

The private Publishing page manages subscriber consent and unsubscribe records,
and can send approved content in batches of at most 100. Each recipient's active
consent is checked again before delivery. Duplicate successful deliveries are
reused; uncertain outcomes are held for reconciliation. The older subscriber
database is not automatically imported into the new private record store.
