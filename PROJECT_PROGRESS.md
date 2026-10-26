# Ad Doctor project progress

Updated: 25 October 2026. Scope: private-team marketing intelligence tool;
free manual Perplexity research initially, with replaceable research and writing tools.

Latest update: 26 October 2026. The user selected Gemini API for research and
writing. This supersedes the earlier preference for free manual Perplexity as
the immediate implementation target.

## Gemini API milestone — 26 October

- [x] Added separate `ad_doctor/gemini_app.py` real-input workspace with a
  password gate; the public demo remains free of provider calls.
- [x] Discover generation models from the configured Google account instead
  of relying on unverified hard-coded model IDs in older adapters.
- [x] Separate research and writing model selection and explicit calls.
- [x] Require human research review before drafting; outputs remain unverified
  research or drafts pending review, never automatically approved evidence.
- [x] Bound request size and output, disable automatic retry/fallback, reuse
  successful identical session requests, and redact provider error details.
- [x] Provide downloadable research and drafts with model and token metadata.
- [x] 316 tests pass, including simulated API flow and access protection.
- [ ] Owner supplies `GEMINI_API_KEY` and `AD_DOCTOR_ACCESS_PASSWORD` securely
  in Streamlit Secrets and deploys the new entrypoint. See `GEMINI_SETUP.md`.
- [ ] Verify actual model listing and research/writing inference with Google.
- [ ] Connect approved-source collection, durable experiment storage, validated
  Evidence Packs, quality gates and case lifecycle to this workspace.

No Gemini key was present in the execution environment or saved environment
requirements when checked. No live API call or free-quota eligibility has been
verified. The new workspace requires pasted source excerpts and manual topic
qualification; it does not browse supplied links or scrape automatically.

## Current release

The `streamlit-community-demo` branch contains a public, read-only sample
workspace. It is an interface demonstration, not the private production system.
The user has confirmed that they can view the interface. Deployment settings and
the public app URL are managed in the user's Streamlit account.

## Completed and verified before this change

- [x] Python environment and dependency installation.
- [x] Streamlit interface, sources, articles, qualification, editorial and operational pages.
- [x] Demo pipeline produces one qualified topic and pending blog/social drafts.
- [x] Default production extraction excludes demo articles.
- [x] Public demo blocks mutation actions and access to the legacy route.
- [x] GitHub deployment branch uploaded; 307 tests passed at that revision.
- [x] Local Windows/Linux launcher files provided. Windows execution was not tested.

## Current simplification work

- [x] Three main destinations: Today, My content, Publishing.
- [x] Technical pages grouped in collapsed Expert workspace.
- [x] Daily collection uses India calendar boundaries and fetched time, not the latest old run.
- [x] Dashboard shows source attribution and original links for today's saved articles.
- [x] Synthetic examples explicitly labelled; cumulative review counts labelled separately.
- [x] Content review reduced to Article, Carousel and Expert details.
- [x] Tab content targets its active tab, fixing content escaping into the parent column.
- [x] Manual research catalog: Perplexity, ChatGPT, Gemini.
- [x] Manual writing catalog: ChatGPT, Gemini, Claude.
- [x] Same-brief comparison: record actual model name, answer, subjective rating and notes.
- [x] Comparisons isolated by stage, topic and supplied evidence; downloadable JSON.
- [x] Manual answers explicitly unverified; they cannot approve evidence or trigger publishing.

## What AI access means here

Collection uses HTTP extraction and optional Playwright, restricted to approved
sources. Research investigates source material and citations. Writing consumes
reviewed evidence. These are distinct jobs.

No Perplexity account or API is connected. Free Perplexity is used manually in
the user's own browser. ChatGPT Plus is also a consumer subscription, not API
credit or authorization for unattended app calls. Gemini API needs its own key;
free quota and available models must be checked against the user's account.
The current model lab changes the tool used for a manual experiment, not the
production scheduler's provider settings. It never claims a model is connected.

## Known gaps and release blockers

| Area | Current evidence / limitation | Required next step |
|---|---|---|
| Private access | Expert navigation is organization only; it is not authorization | Add authenticated team access and enforce roles on actions before live deployment |
| Persistent storage | Public demo uses fixtures; model trials are session-only with export | Choose durable private storage and audited experiment records |
| Real collection | Parser/fetcher tests exist; live approved-source run not validated here | Supply the approved source list and validate a controlled crawl |
| Browser fallback | Chromium download previously blocked in onboarding | Apply network settings, install and test managed Chromium |
| Research import | Manual answers can be compared, not inserted as validated evidence | Add schema validation, citation review, provenance, idempotency and approval |
| Model switching | Legacy adapters exist but production defaults conflict with Perplexity preference | Consolidate provider configuration and test each real adapter with explicit credentials/budgets |
| Model comparison | Human ratings only; no objective performance or cost measurements | Add reviewed reference tasks, latency/usage accounting and repeatable evaluation |
| Writing | Offline draft path and mocked providers tested | Validate grounded output against reviewed evidence with selected real provider |
| Quality | Existing gates and regression tests | Test adversarial/unsupported claims on a real end-to-end package |
| Google Docs | Adapter code exists, no verified live operation in this environment | Configure private access and validate approved-only document creation |
| Email | Delivery code exists, no live-send validation | Validate consent, suppression, duplicate prevention and a user-authorized test send |
| Scheduling | Worker code exists; free web hosting may sleep | Select a durable worker schedule and exercise recovery/retries |
| Secrets/history | A SQLite database is tracked in Git; historical secret audit not completed | Keep private data out of demo; audit history before any visibility change |
| Provider status | Some legacy status labels reflect configuration, not a live probe | Replace them with precise configured/tested/error states |

Passing unit tests does not establish production readiness or prove the absence
of all defects. Never mark live integrations complete based on fixture results.

## Next milestones

1. Accept the simplified interface and manual comparisons.
2. Implement validated research import and persistent, private experiment storage.
3. Connect approved-source collection to one real research-to-draft case.
4. Validate human approval, Docs export and explicitly authorized delivery.
5. Run private-team acceptance checks, recovery tests and a release audit.

Update this file with each milestone's implementation, checks, blockers and
deployment state. Keep completed code, tested integrations and published releases distinct.

## Validation of the simplified release

- Full suite: 311 tests passed, including India midnight boundaries, historical
  data exclusion, model brief separation, manual comparison recording and all
  public-demo pages.
- Browser checks: desktop Expert workspace collapsed by default; Article and
  Expert details show their own content; mobile model-lab page renders.
- Deployment target: existing `streamlit-community-demo` branch. Cloud rebuild
  and the user's public URL must be checked separately after upload.
