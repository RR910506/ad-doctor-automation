# Browser Research Engine

Production research is HTTP-first and restricted to active domains in the SQLite `sources` table. RSS/Atom, sitemap, and configured category/home pages provide discovery. Playwright Chromium is used only when HTTP article extraction is incomplete or requires JavaScript rendering.

Gemini does not browse. Retrieved documents are normalized with canonical URL, title, publication, publisher network, timestamps, text, author, publication date, extraction method, content hash, and provenance before they can enter an Evidence Pack.

## Local setup

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m src.browser_worker --once
```

Omit `--once` to use continuous mode. The interval and all page, timeout, concurrency, delay, backoff, and circuit-breaker limits are environment-configurable. There is no unrestricted spider mode.

## Render/Linux preparation

Use the portable project command rather than a local browser path:

```bash
python -m playwright install --with-deps chromium
```

The implementation uses Playwright-managed Chromium, `pathlib`, and environment variables. It contains no Windows-specific executable or filesystem assumptions. Deployment is intentionally not activated yet.

## Source authority

SQLite `sources` remains authoritative. Google Sheets can be invoked as an optional import, but absent Sheet credentials or configuration never blocks a browser cycle.

## Gemini availability policy

The primary production model is `gemini-3.7-flash`. Exactly one `gemini-3.5-flash` fallback is permitted only after a safely classified HTTP 503/provider-unavailable response. Authentication, permission, invalid request, missing model, and quota failures never trigger fallback.
