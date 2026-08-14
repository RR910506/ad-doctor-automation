# Ad Doctor Product Rules

Ad Doctor is a marketing intelligence and opinion platform.

It is not:

* a marketing news aggregator
* a journalist
* a PR content generator
* a campaign archive
* a generic AI summariser

Its approved workflow is:

Publication sources → article extraction → topic clustering → three-distinct-publication qualification → Perplexity research → validated Evidence Pack → Ad Doctor diagnosis → Doctor’s Opinion → marketing prescription → carousel generation → automated quality audit → human approval → formatted Google Doc → subscriber email

Hard product rules:

* A topic qualifies only when the same marketing development is covered by at least three distinct approved publications.
* Research and content writing must remain separate stages.
* Perplexity is the primary research provider.
* OpenAI is the primary diagnosis, strategy, content and quality-review provider.
* AI providers must remain replaceable.
* Content generation must use only the validated Evidence Pack.
* Human approval is mandatory before publishing or email delivery.
* Google Docs must not become the operational database.
* Streamlit must eventually provide a modern, clean, intuitive and highly user-friendly SaaS-style interface.
* UI files must not contain direct database, scraping, AI, scheduler or publishing business logic.
* Secrets must never be added to source code or stored in newly created plaintext database fields.
* Every workflow transition must be traceable.
* Every stage must be designed for idempotency and safe retrying.

