# Research without a paid API

For the private-team release, Perplexity is the preferred research tool. Its
consumer website and developer API are separate access paths. Do not assume
that a free website account includes API calls. This workflow uses no paid API.

## Current capabilities

- The automated app can collect approved-source articles and qualify a topic
  only after coverage from at least three distinct approved publications.
- Local source synthesis is available without an AI key. It is not Perplexity
  research and does not independently verify claims.
- The demo uses synthetic sources and offline drafts, solely for development.
- The application does not yet import manually completed Perplexity research.

## Manual Perplexity research

1. Choose a qualified real topic and collect its approved coverage URLs.
2. Open Perplexity using your own account and submit the research brief below.
3. Open each cited source and check that it supports the associated claim.
   Record unsupported claims as unknown; do not reuse invented metrics.
4. Retain the answer, citations, and review notes for a future reviewed import.
   Until that import exists, do not mark the application's Evidence Pack as
   validated on the basis of an answer stored outside the application.

Suggested brief:

```text
Research this exact marketing development: [TOPIC]
Approved coverage from distinct publications: [URLS]

Separate reported facts, publication claims, your interpretations, and unknowns.
For every factual claim or number, give the supporting URL and a short source
excerpt. Do not infer commercial results from publicity or engagement alone.
Explain the brand decision, audience context, execution, competitor context,
and any supported business outcomes. State "data unavailable" where needed.
Treat additional sources as supporting context, not as approved qualifying
publications. Do not write the final Ad Doctor opinion or carousel.
```

## Next integration step

Add a reviewed manual-research import that preserves provider attribution,
citations, claim review, and audit history. Imported text must remain untrusted
until evidence validation completes. Content and delivery still require the
existing quality checks and human approval.

If API automation is enabled later, configure credentials securely, confirm
the account's API allowance and costs, and explicitly enable the provider.
Never automate the consumer website as a substitute for API access or store
account passwords in the repository.
