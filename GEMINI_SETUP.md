# Real Gemini API workspace

> Updated 28 October 2026: the existing `gemini_app.py` entrypoint now forwards to
> the provider-independent Ad Doctor app. Gemini is only a temporary testing
> choice. Follow [AD_DOCTOR_SETUP.md](AD_DOCTOR_SETUP.md) for current instructions,
> separate research/writing/quality providers, automatic collection and saved
> content. The instructions below describe the earlier diagnostic milestone.

This is a separate, password-gated entrypoint using real supplied text. It does
not change the public sample demo into a production service. Google API access
has not been tested until the owner supplies a key and a generation succeeds.

## Owner setup

1. Open https://aistudio.google.com/apikey in your Google account and create a
   Gemini API key. Check eligibility, free quota, model availability and billing
   for that project. A free website account does not guarantee free API calls.
2. Create a separate Streamlit app using repository
   `RR910506/ad-doctor-automation`, branch `streamlit-community-demo`, main file
   `ad_doctor/gemini_app.py`, Python 3.12.
3. In Advanced settings → Secrets (or Manage app → Settings → Secrets), add:

   ```toml
   GEMINI_API_KEY = "YOUR_KEY_FROM_GOOGLE_AI_STUDIO"
   AD_DOCTOR_ACCESS_PASSWORD = "YOUR_OWN_RANDOM_PASSWORD_AT_LEAST_16_CHARACTERS"
   ```

   Replace both placeholders privately. Do not commit them or send them in chat.
   Use Streamlit's private viewer access too where available. The shared app
   password is a limited trial gate, not full team authentication or roles.
4. Deploy/reboot, sign in using your workspace password, and select **Check
   connection and load available models**. This checks API listing permissions,
   not inference availability. Choose a model actually returned by your key.
5. Supply the exact topic and real excerpts with URLs from at least three
   approved publications. Confirm qualification, then **Research with Gemini**.
6. Review and correct the research, select the writing model, and **Create draft
   with Gemini**. Download the results. Quality review is still required.

## Explicit limits

- No automatic scraping or URL browsing: paste extracted text as well as URLs.
- Qualification and evidence review are manual confirmations in this entrypoint.
- No automated publishing, email, shared persistent storage or validated
  Evidence Pack import. Closing the session can lose results; download them.
- Generation is explicit: one request, bounded input/output, no fallback or
  automatic retry. Repeated clicks with the same successful inputs reuse the
  session result. This is not durable cross-session idempotency.
- HTTP 429 means quota/rate limits, not a reason to silently switch accounts or
  models. Resolve quota in Google AI Studio. Never disable TLS verification.
- API terms govern handling of supplied data. Use only authorized source text;
  do not paste private subscriber information or credentials.

The next integration milestone is approved-source collection into validated
evidence, then connecting these calls to persistent case and review records.
